r"""
agent/registry_handler.py

Registers EDR as the file-open interceptor using a belt-and-suspenders approach:

  Strategy A — override the ORIGINAL ProgID command in HKCU:
    HKCU\Software\Classes\Word.Document.12\shell\open\command = our handler
    HKCU\Software\Classes\Excel.Sheet.12\shell\open\command   = our handler
    … etc.
    This works regardless of UserChoice / extension default / Explorer cache,
    because HKCU\Software\Classes takes priority over HKLM for class lookups.

  Strategy B — also redirect the extension to our own ProgID (EDR<EXT>):
    HKCU\Software\Classes\.docx = EDRDOCX
    HKCU\Software\Classes\EDRDOCX\shell\open\command = our handler
    + set FriendlyTypeName so Windows Shell recognises the ProgID as valid
    + delete UserChoice so B is not blocked by the cached default

Both strategies fire at once. Strategy A alone is usually sufficient, but B
provides a safety net when the ProgID is overwritten by Office repair tools.
"""

import ctypes
import logging
import os
import sys
import winreg

logger = logging.getLogger("edr.registry_handler")

_DIR = os.path.dirname(os.path.abspath(__file__))

_PYTHON_EXE = sys.executable
_PYTHONW    = os.path.join(os.path.dirname(_PYTHON_EXE), "pythonw.exe")
_PYTHON     = _PYTHONW if os.path.isfile(_PYTHONW) else _PYTHON_EXE

_HANDLER = os.path.join(_DIR, "file_open_handler.py")
_COMMAND = f'"{_PYTHON}" "{_HANDLER}" "%1"'

_SAVED_BASE = r"Software\EDR\OriginalHandlers"
_UC_BASE    = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"

# Extensions we protect and their well-known system ProgIDs
EXTENSIONS: list[str] = [
    ".docx", ".doc",
    ".xlsx", ".xls",
    ".pptx", ".ppt",
    ".txt",  ".pdf",
]

# Fallback ProgIDs if dynamic lookup fails (may differ by Office version)
_FALLBACK_PROGID: dict[str, str] = {
    ".docx": "Word.Document.12",
    ".doc":  "Word.Document.8",
    ".xlsx": "Excel.Sheet.12",
    ".xls":  "Excel.Sheet.8",
    ".pptx": "PowerPoint.Show.12",
    ".ppt":  "PowerPoint.Show.8",
    ".txt":  "txtfile",
    ".pdf":  "AcroExch.Document.11",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _edr_prog_id(ext: str) -> str:
    return "EDR" + ext.replace(".", "").upper()


def _get_system_prog_id(ext: str) -> str | None:
    """
    Return the ProgID currently registered for ext by the OS/Office installation.
    Reads from HKLM (not HKCU) to get the unmodified system default.
    Falls back to our saved value, then to the hardcoded table.
    """
    # 1. Our previously saved original
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            f"{_SAVED_BASE}\\{ext}") as k:
            pid = winreg.QueryValueEx(k, "original")[0]
        if pid and not pid.startswith("EDR"):
            return pid
    except OSError:
        pass

    # 2. HKLM system default (unaffected by our HKCU changes)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            f"Software\\Classes\\{ext}") as k:
            pid = winreg.QueryValue(k, "")
        if pid and not pid.startswith("EDR"):
            return pid
    except OSError:
        pass

    # 3. UserChoice (before deletion)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            f"{_UC_BASE}\\{ext}\\UserChoice") as k:
            pid = winreg.QueryValueEx(k, "ProgId")[0]
        if pid and not pid.startswith("EDR"):
            return pid
    except OSError:
        pass

    # 4. Hardcoded fallback table
    return _FALLBACK_PROGID.get(ext)


def _get_prog_id_orig_command(prog_id: str) -> str | None:
    """Return the shell\\open\\command currently registered for prog_id (HKCR)."""
    if not prog_id:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            f"{prog_id}\\shell\\open\\command") as k:
            return winreg.QueryValue(k, "")
    except OSError:
        return None


def _delete_key_tree(root, path: str):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    sub = winreg.EnumKey(key, 0)
                    _delete_key_tree(root, path + "\\" + sub)
                except OSError:
                    break
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def _refresh_shell():
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)
    except Exception:
        pass
    # Also broadcast WM_SETTINGCHANGE so every window refreshes
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendNotifyMessageW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment"
        )
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def register_handlers() -> bool:
    """Register EDR as the file-open interceptor for all EXTENSIONS."""
    ok = 0
    for ext in EXTENSIONS:
        try:
            edr_pid    = _edr_prog_id(ext)
            saved_path = f"{_SAVED_BASE}\\{ext}"

            # ── Find the system ProgID (e.g. Word.Document.12) ────────────────
            sys_pid = _get_system_prog_id(ext)

            # ── Save original data; backfill original_cmd if missing ─────────
            if sys_pid:
                orig_cmd_hklm = ""
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        f"Software\\Classes\\{sys_pid}\\shell\\open\\command") as k:
                        orig_cmd_hklm = winreg.QueryValue(k, "") or ""
                except OSError:
                    pass

                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, saved_path) as k:
                    # Always write original ProgID
                    winreg.SetValueEx(k, "original", 0, winreg.REG_SZ, sys_pid)
                    # Write original_cmd only if we have a real HKLM value
                    # (avoids overwriting with our own handler if called twice)
                    if orig_cmd_hklm and _HANDLER not in orig_cmd_hklm:
                        winreg.SetValueEx(k, "original_cmd", 0, winreg.REG_SZ, orig_cmd_hklm)

            # ══ STRATEGY A: override the original ProgID command in HKCU ═════
            # Works even if UserChoice / extension default points to Word.Document.12
            # because HKCU\Software\Classes\Word.Document.12 beats HKLM for lookups.
            if sys_pid:
                cmd_path = f"Software\\Classes\\{sys_pid}\\shell\\open\\command"
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cmd_path) as k:
                    winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _COMMAND)
                logger.info(f"Strategy A: overrode {sys_pid} open command")

            # ══ STRATEGY B: redirect the extension to our own EDRDOCX ProgID ═
            # Delete UserChoice so our HKCU\Software\Classes\.docx is used
            uc_key = f"{_UC_BASE}\\{ext}\\UserChoice"
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, uc_key)
                logger.info(f"Deleted UserChoice for {ext}")
            except OSError:
                pass

            # Set extension default
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  f"Software\\Classes\\{ext}") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, edr_pid)

            # Register EDRDOCX ProgID with full structure (friendly name required!)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  f"Software\\Classes\\{edr_pid}") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "EDR Protected File")

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  f"Software\\Classes\\{edr_pid}\\shell") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "open")

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  f"Software\\Classes\\{edr_pid}\\shell\\open") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "&Open")

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  f"Software\\Classes\\{edr_pid}\\shell\\open\\command") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _COMMAND)

            # Add EDRDOCX to OpenWithProgids so Explorer knows it's a valid handler
            op_path = f"{_UC_BASE}\\{ext}\\OpenWithProgids"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, op_path) as k:
                winreg.SetValueEx(k, edr_pid, 0, winreg.REG_NONE, b"")

            logger.info(f"Registered: {ext} -> {edr_pid} (sys_pid={sys_pid})")
            print(f"[OK] {ext:8s}  A:{sys_pid or 'none':25s}  B:{edr_pid}")
            ok += 1

        except Exception as e:
            logger.error(f"Failed to register {ext}: {e}")
            print(f"[ERROR] {ext}: {e}")

    _refresh_shell()
    print(f"\nRegistered {ok}/{len(EXTENSIONS)} extensions.")
    return ok > 0


def unregister_handlers():
    """Remove all EDR handlers and restore original associations."""
    ok = 0
    for ext in EXTENSIONS:
        try:
            edr_pid    = _edr_prog_id(ext)
            saved_path = f"{_SAVED_BASE}\\{ext}"

            # Retrieve saved originals
            orig_pid = None
            orig_cmd = None
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, saved_path) as k:
                    orig_pid = winreg.QueryValueEx(k, "original")[0]
                    orig_cmd = winreg.QueryValueEx(k, "original_cmd")[0]
            except OSError:
                pass

            # Restore Strategy A — put the original command back (or delete our override)
            if orig_pid:
                override = f"Software\\Classes\\{orig_pid}\\shell\\open\\command"
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, override) as k:
                        cur = winreg.QueryValue(k, "")
                    if _HANDLER in cur:
                        if orig_cmd:
                            # Restore the saved command
                            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                                override,
                                                0, winreg.KEY_SET_VALUE) as k:
                                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, orig_cmd)
                        else:
                            # Just delete our HKCU override; HKLM value returns
                            _delete_key_tree(winreg.HKEY_CURRENT_USER,
                                             f"Software\\Classes\\{orig_pid}")
                except OSError:
                    pass

            # Remove Strategy B
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                                 f"Software\\Classes\\{ext}")
            except OSError:
                pass
            _delete_key_tree(winreg.HKEY_CURRENT_USER,
                             f"Software\\Classes\\{edr_pid}")

            # Remove EDRDOCX from OpenWithProgids
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    f"{_UC_BASE}\\{ext}\\OpenWithProgids",
                                    0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, edr_pid)
            except OSError:
                pass

            logger.info(f"Unregistered: {ext}")
            print(f"[OK] {ext:8s}  restored")
            ok += 1

        except Exception as e:
            logger.error(f"Failed to unregister {ext}: {e}")
            print(f"[ERROR] {ext}: {e}")

    _delete_key_tree(winreg.HKEY_CURRENT_USER, "Software\\EDR")
    _refresh_shell()
    print(f"\nUnregistered {ok}/{len(EXTENSIONS)} extensions.")


def check_registration() -> dict:
    """Return registration status for each extension."""
    status = {}
    for ext in EXTENSIONS:
        edr_pid = _edr_prog_id(ext)
        sys_pid = _get_system_prog_id(ext)

        # Strategy A: is the original ProgID command overridden?
        a_ok = False
        if sys_pid:
            try:
                override = f"Software\\Classes\\{sys_pid}\\shell\\open\\command"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, override) as k:
                    cmd = winreg.QueryValue(k, "")
                a_ok = _HANDLER in cmd
            except OSError:
                pass

        # Strategy B: is extension default pointing to EDRDOCX?
        b_ok = False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                f"Software\\Classes\\{ext}") as k:
                b_ok = winreg.QueryValue(k, "").startswith("EDR")
        except OSError:
            pass

        # UserChoice blocking?
        uc_pid = None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                f"{_UC_BASE}\\{ext}\\UserChoice") as k:
                uc_pid = winreg.QueryValueEx(k, "ProgId")[0]
        except OSError:
            pass

        status[ext] = {
            "protected":   a_ok or b_ok,
            "strategy_a":  a_ok,
            "strategy_b":  b_ok,
            "sys_prog_id": sys_pid,
            "user_choice": uc_pid,
        }
    return status


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="EDR Registry Handler")
    p.add_argument("action",
                   choices=["register", "unregister", "check"],
                   nargs="?", default="register")
    args = p.parse_args()

    if args.action == "register":
        print(f"Python:  {_PYTHON}")
        print(f"Handler: {_HANDLER}")
        print(f"Command: {_COMMAND}\n")
        register_handlers()

    elif args.action == "unregister":
        unregister_handlers()

    elif args.action == "check":
        status = check_registration()
        print("\n=== EDR Handler Registration Status ===")
        print(f"  {'Ext':8s}  {'Protected':10s}  {'A(orig ProgID)':16s}  {'B(ext->EDR)':12s}  {'UserChoice':15s}  {'SysProgID'}")
        print("  " + "-" * 90)
        for ext, s in status.items():
            icon  = "[OK]" if s["protected"] else "[--]"
            uc    = s["user_choice"] or "none"
            a_s   = "OK" if s["strategy_a"] else "--"
            b_s   = "OK" if s["strategy_b"] else "--"
            print(f"  {icon} {ext:8s}  A={a_s:4s}  B={b_s:4s}  UC={uc:30s}  {s['sys_prog_id'] or '?'}")
