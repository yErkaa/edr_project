r"""
agent/file_open_handler.py

Launched by the EDR shortcut stub when a user opens a protected file.
argv[1] = absolute path to the LOCKED file (the real document, NOT .edrlock).

Flow
----
1. Check brute-force lockout.
2. Show password dialog (topmost, focus_force).
3. Correct password:
   - icacls /remove:d Everyone  (unlock)
   - unhide the file
   - ShellExecuteW "open"       (open with native app)
   - background thread re-locks after RE_LOCK_DELAY seconds
   - event file_access_granted LOW
4. Wrong password:
   - event unauthorized_access_attempt HIGH
   - after MAX_ATTEMPTS: brute_force HIGH, access permanently denied
5. Cancel / close dialog:
   - event unauthorized_access_attempt HIGH
"""

import ctypes
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import configparser
from datetime import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_DIR = os.path.join(_DIR, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(_LOG_DIR, "file_open_handler.log"),
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("edr.foh")

# ── Config ────────────────────────────────────────────────────────────────────
_cfg = configparser.ConfigParser()
_cfg.read(os.path.join(_DIR, "config.ini"), encoding="utf-8")

SERVER_URL    = (_cfg.get("server", "url",      fallback=None)
                 or _cfg.get("server", "base_url", fallback="http://127.0.0.1:8088")
                 ).rstrip("/")
AGENT_ID      = (_cfg.get("agent",  "id",        fallback=None)
                 or _cfg.get("agent",  "agent_id", fallback=None)
                 or socket.gethostname())
CURRENT_USER  = os.environ.get("USERNAME", "unknown")
PASSWORD      = _cfg.get("protection", "password",     fallback="school2024")
MAX_ATTEMPTS  = _cfg.getint("protection", "max_attempts", fallback=3)
RE_LOCK_DELAY = 60   # seconds before re-locking after successful open

DATA_DIR      = os.path.join(_DIR, "data")
ATTEMPTS_FILE = os.path.join(DATA_DIR, "attempts.json")


# ── Event sender ──────────────────────────────────────────────────────────────

def send_event(event_type: str, severity: str, details: dict):
    def _send():
        try:
            import requests
            requests.post(
                f"{SERVER_URL}/events",
                json={"agent_id": AGENT_ID, "user": CURRENT_USER,
                      "event_type": event_type, "severity": severity,
                      "details": {**details, "source": "file_open_handler"}},
                timeout=5,
            )
            logger.info(f"Event: {event_type} [{severity}]")
        except Exception as e:
            logger.warning(f"send_event({event_type}): {e}")
    threading.Thread(target=_send, daemon=True).start()


# ── Attempt tracking ──────────────────────────────────────────────────────────

def _load() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(ATTEMPTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save(data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ATTEMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_fail_count(fp: str) -> int:
    return _load().get(fp, {}).get("count", 0)

def record_fail(fp: str) -> int:
    data = _load()
    e    = data.get(fp, {"count": 0})
    e["count"] += 1
    e["last"]   = datetime.now().isoformat()
    data[fp]    = e
    _save(data)
    return e["count"]

def reset_fails(fp: str):
    data = _load()
    data.pop(fp, None)
    _save(data)


# ── icacls wrappers (inline, no circular import) ──────────────────────────────

def _unlock(filepath: str) -> bool:
    try:
        r = subprocess.run(
            ["icacls", filepath, "/remove:d", "*S-1-1-0"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception as e:
        logger.error(f"_unlock: {e}")
        return False

def _lock(filepath: str) -> bool:
    try:
        r = subprocess.run(
            ["icacls", filepath, "/deny", "*S-1-1-0:F"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception as e:
        logger.error(f"_lock: {e}")
        return False

def _hide(filepath: str):
    try:
        ctypes.windll.kernel32.SetFileAttributesW(filepath, 0x02)
    except Exception:
        pass

def _unhide(filepath: str):
    try:
        ctypes.windll.kernel32.SetFileAttributesW(filepath, 0x80)
    except Exception:
        pass


# ── Open file with native app ─────────────────────────────────────────────────

_FALLBACK_APPS: dict[str, list[str]] = {
    ".docx": [r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
              r"C:\Program Files (x86)\Microsoft Office\Office16\WINWORD.EXE"],
    ".xlsx": [r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
              r"C:\Program Files (x86)\Microsoft Office\Office16\EXCEL.EXE"],
    ".pptx": [r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
              r"C:\Program Files (x86)\Microsoft Office\Office16\POWERPNT.EXE"],
    ".txt":  [r"C:\Windows\System32\notepad.exe"],
    ".pdf":  [r"C:\Program Files (x86)\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe",
              r"C:\Program Files (x86)\STDU Viewer\STDUViewerApp.exe"],
}
for _x in (".doc", ".xls", ".ppt"):
    _FALLBACK_APPS[_x] = _FALLBACK_APPS[_x + "x"]

def open_file(filepath: str):
    ret = int(ctypes.windll.shell32.ShellExecuteW(
        None, "open", filepath, None, None, 1))
    logger.info(f"ShellExecuteW({os.path.basename(filepath)}): ret={ret}")
    if ret > 32:
        return
    # Fallback
    ext = os.path.splitext(filepath)[1].lower()
    for app in _FALLBACK_APPS.get(ext, []):
        if os.path.isfile(app):
            subprocess.Popen([app, filepath])
            logger.info(f"Fallback open: {app}")
            return
    logger.error(f"No app found for {ext}")


def open_and_relock(filepath: str):
    """Unlock → open → re-lock after RE_LOCK_DELAY seconds."""
    _unhide(filepath)
    if not _unlock(filepath):
        _msgbox("EDR — Error", f"Cannot unlock:\n{os.path.basename(filepath)}")
        return

    open_file(filepath)

    def _relock():
        time.sleep(RE_LOCK_DELAY)
        _hide(filepath)
        _lock(filepath)
        logger.info(f"Re-locked after use: {os.path.basename(filepath)}")

    threading.Thread(target=_relock, daemon=True).start()


# ── Win32 helpers ─────────────────────────────────────────────────────────────

def _force_fg(hwnd: int):
    u = ctypes.windll.user32
    u.ShowWindow(hwnd, 9)
    u.SetForegroundWindow(hwnd)
    u.BringWindowToTop(hwnd)
    u.SetActiveWindow(hwnd)

def _msgbox(title: str, text: str, icon: int = 0x10):
    ctypes.windll.user32.MessageBoxW(
        None, text, title,
        icon | 0x00010000 | 0x00040000,   # MB_SETFOREGROUND | MB_TOPMOST
    )


# ── Password dialog ───────────────────────────────────────────────────────────

class _PasswordDialog:
    def __init__(self, root, fname: str, fail_count: int, max_attempts: int):
        import tkinter as tk

        self.result: str | None = None

        win = tk.Toplevel(root)
        win.title("EDR — Personal Data Protection")
        win.wm_attributes("-topmost", True)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h   = 440, 215
        win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        f = tk.Frame(win, padx=16, pady=12)
        f.pack(fill="both", expand=True)

        tk.Label(f, text="This file is protected — it contains personal data",
                 font=("Segoe UI", 10, "bold"), fg="#c0392b",
                 anchor="w").pack(fill="x")
        tk.Label(f, text=f"File:  {fname}",
                 font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(4, 0))

        if fail_count > 0:
            tk.Label(f,
                     text=f"Warning: {max_attempts - fail_count} attempt(s) remaining",
                     font=("Segoe UI", 9), fg="#e67e22",
                     anchor="w").pack(fill="x", pady=(4, 0))

        tk.Label(f, text="Administrator password:",
                 font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(10, 2))

        self._entry = tk.Entry(win, show="*", font=("Segoe UI", 11))
        self._entry.pack(fill="x", padx=16)
        self._entry.bind("<Return>",  self._confirm)
        self._entry.bind("<Escape>",  lambda _: self._cancel())

        bf = tk.Frame(win)
        bf.pack(fill="x", padx=16, pady=(8, 12))
        tk.Button(bf, text="Cancel",    width=10,
                  command=self._cancel).pack(side="right", padx=(6, 0))
        tk.Button(bf, text="Open File", width=12,
                  command=self._confirm, default="active").pack(side="right")

        self._win = win
        win.grab_set()
        win.update()
        self._entry.focus_set()
        _force_fg(win.winfo_id())

    def _confirm(self, _=None):
        self.result = self._entry.get()
        self._win.destroy()

    def _cancel(self):
        self.result = None
        self._win.destroy()

    def wait(self) -> str | None:
        self._win.wait_window()
        return self.result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logger.info(f"=== Invoked: {sys.argv} ===")

    if len(sys.argv) < 2:
        logger.warning("No file argument")
        return

    filepath = os.path.abspath(sys.argv[1])
    logger.info(f"File: {filepath}")

    if not os.path.isfile(filepath):
        logger.warning("File not found")
        _msgbox("EDR — Error", f"Protected file not found:\n{filepath}")
        return

    fname = os.path.basename(filepath)

    # ── Brute-force pre-check ────────────────────────────────────────────────
    fail_count = get_fail_count(filepath)
    if fail_count >= MAX_ATTEMPTS:
        logger.warning(f"Locked ({fail_count} failures): {filepath}")
        send_event("brute_force_attempt", "HIGH", {
            "file": filepath, "file_name": fname,
            "attempts": fail_count, "reason": "Max attempts exceeded",
        })
        _msgbox("EDR — Access Blocked",
                f"Too many failed attempts.\n\nFile: {fname}\n\n"
                "Contact your system administrator.")
        return

    # ── GUI ──────────────────────────────────────────────────────────────────
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", True)

    _password_loop(root, filepath, fname)
    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass


def _password_loop(root, filepath: str, fname: str):
    fail_count = get_fail_count(filepath)

    if fail_count >= MAX_ATTEMPTS:
        send_event("brute_force_attempt", "HIGH", {
            "file": filepath, "file_name": fname,
            "attempts": fail_count, "reason": "Max attempts reached",
        })
        _msgbox("EDR — Access Blocked",
                f"Too many failed attempts.\n\nFile: {fname}\n\n"
                "Contact your system administrator.")
        root.after(100, root.quit)
        return

    dlg     = _PasswordDialog(root, fname, fail_count, MAX_ATTEMPTS)
    entered = dlg.wait()

    if entered is None:
        # Cancelled / closed
        send_event("unauthorized_access_attempt", "HIGH", {
            "file": filepath, "file_name": fname,
            "reason": "Cancelled", "attempts": fail_count,
        })
        logger.info(f"Cancelled: {fname}")
        root.after(100, root.quit)
        return

    if entered == PASSWORD:
        reset_fails(filepath)
        send_event("file_access_granted", "LOW", {
            "file": filepath, "file_name": fname,
        })
        logger.info(f"Access granted: {fname}")
        open_and_relock(filepath)
        root.after(400, root.quit)
        return

    # Wrong password
    fail_count = record_fail(filepath)
    remaining  = MAX_ATTEMPTS - fail_count
    logger.info(f"Wrong password #{fail_count}: {fname}")

    if remaining <= 0:
        send_event("brute_force_attempt", "HIGH", {
            "file": filepath, "file_name": fname,
            "attempts": fail_count, "reason": "Max attempts reached",
        })
        _msgbox("EDR — Access Blocked",
                f"Incorrect password.\n\nFile: {fname}\n\n"
                "Maximum attempts reached — file is now LOCKED.\n"
                "Contact your system administrator.", icon=0x10)
        root.after(100, root.quit)
    else:
        send_event("unauthorized_access_attempt", "HIGH", {
            "file": filepath, "file_name": fname,
            "attempts": fail_count, "remaining_attempts": remaining,
            "reason": "Incorrect password",
        })
        _msgbox("EDR — Access Denied",
                f"Incorrect password.\n\nFile: {fname}\n\n"
                f"{remaining} attempt(s) remaining.", icon=0x10)
        root.after(100, _password_loop, root, filepath, fname)


if __name__ == "__main__":
    main()
