"""
agent/quarantine.py

File quarantine system for EDR School.

When a PII file is detected:
  - It is MOVED to C:\\EDR_Quarantine\\YYYYMMDD_HHmmss_filename
  - A stub text file is written at the original path
  - The server is notified via POST /quarantine

The quarantine directory is restricted to Administrators + SYSTEM only.
"""

import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

from pii_scanner import HIGH_PII_TYPES as _HIGH_PII_TYPES


def _pii_sev(pii_types: list, confidence: float) -> str:
    unique = set(pii_types)
    if unique & _HIGH_PII_TYPES:          # IIN / CARD / DOC_NUM → всегда HIGH
        return "HIGH"
    if len(unique) >= 2:                   # 2+ мягких типа (телефон+ФИО, почта+ФИО...) → MEDIUM
        return "MEDIUM"
    return "LOW"                           # один мягкий тип или ничего → LOW

import win32con
import win32file

logger = logging.getLogger("edr.quarantine")

QUARANTINE_DIR  = r"C:\EDR_Quarantine"
QUARANTINE_EXTS = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".pdf"}

# Keep this alias so agent.py can import PROTECTED_EXTS from quarantine
PROTECTED_EXTS = QUARANTINE_EXTS

_FILE_LIST_DIRECTORY        = 0x00000001
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WATCH_FLAGS = (
    win32con.FILE_NOTIFY_CHANGE_FILE_NAME |
    win32con.FILE_NOTIFY_CHANGE_LAST_WRITE
)

_ACTION_CREATED     = 1
_ACTION_DELETED     = 2
_ACTION_MODIFIED    = 3
_ACTION_RENAMED_OLD = 4
_ACTION_RENAMED_NEW = 5

_DEDUP_SECS = 3.0

_STUB_MARKER = "[EDR-QUARANTINE]"
_STUB_TEXT = (
    "Этот файл помещён в карантин системой EDR School.\n"
    "Файл содержал персональные данные и был изъят в целях защиты.\n\n"
    "Для восстановления обратитесь к администратору: http://127.0.0.1:8088\n\n"
    "This file has been quarantined by EDR School system.\n"
    "It contained personal data and was removed for protection.\n\n"
    + _STUB_MARKER
)


# ── Quarantine directory setup ────────────────────────────────────────────────

def setup_quarantine_dir() -> bool:
    """Create C:\\EDR_Quarantine with admin-only permissions."""
    try:
        os.makedirs(QUARANTINE_DIR, exist_ok=True)
        # Remove inherited ACEs
        subprocess.run(
            ["icacls", QUARANTINE_DIR, "/inheritance:r"],
            capture_output=True, timeout=10,
        )
        # S-1-5-32-544 = Administrators (locale-agnostic)
        subprocess.run(
            ["icacls", QUARANTINE_DIR, "/grant", "*S-1-5-32-544:(OI)(CI)F"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["icacls", QUARANTINE_DIR, "/grant", "SYSTEM:(OI)(CI)F"],
            capture_output=True, timeout=10,
        )
        logger.info(f"Quarantine dir ready: {QUARANTINE_DIR}")
        return True
    except Exception as e:
        logger.error(f"setup_quarantine_dir: {e}")
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def quarantine_file(filepath: str) -> tuple[bool, str]:
    """
    Move filepath into QUARANTINE_DIR, write stub at original location.
    Returns (success, quarantine_path).  quarantine_path is '' if already quarantined.
    """
    filepath = os.path.abspath(filepath)
    if not os.path.isfile(filepath):
        return False, ""
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in QUARANTINE_EXTS:
        return False, ""
    if is_quarantine_stub(filepath):
        return True, ""  # already quarantined

    try:
        os.makedirs(QUARANTINE_DIR, exist_ok=True)
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(QUARANTINE_DIR, f"{ts}_{os.path.basename(filepath)}")

        shutil.move(filepath, dst)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(_STUB_TEXT)

        logger.info(f"Quarantined: {os.path.basename(filepath)} → {dst}")
        return True, dst
    except Exception as e:
        logger.error(f"quarantine_file {filepath}: {e}")
        return False, ""


def restore_file(quarantine_path: str, original_path: str) -> bool:
    """Restore file from quarantine to original location."""
    if not os.path.isfile(quarantine_path):
        logger.error(f"restore: not found: {quarantine_path}")
        return False
    try:
        # Remove stub at original path if present
        if os.path.isfile(original_path) and is_quarantine_stub(original_path):
            os.remove(original_path)

        os.makedirs(os.path.dirname(os.path.abspath(original_path)), exist_ok=True)
        shutil.move(quarantine_path, original_path)

        # Reset ACL — quarantine dir has DENY EVERYONE, file may inherit those rights
        try:
            import subprocess
            subprocess.run(
                ["icacls", original_path, "/reset"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

        logger.info(f"Restored: {quarantine_path} → {original_path}")
        return True
    except Exception as e:
        logger.error(f"restore_file: {e}")
        return False


def is_quarantine_stub(filepath: str) -> bool:
    """Return True if the file is a quarantine stub (original was moved out)."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return _STUB_MARKER in f.read(300)
    except Exception:
        return False


# ── Real-time directory watcher ───────────────────────────────────────────────

class DirectoryWatcher:
    """
    Monitors directories with ReadDirectoryChangesW.

    callback(path, event_type, severity, extra: dict)
    """

    def __init__(
        self,
        paths: list[str],
        scanner,
        callback: Callable,
        quarantine_on_detect: bool = True,
    ):
        self.scanner              = scanner
        self.callback             = callback
        self.quarantine_on_detect = quarantine_on_detect
        self._stop                = threading.Event()
        self._threads: list[threading.Thread] = []
        self._pii_files: set[str] = set()
        self._quarantining: set[str] = set()   # paths currently being moved
        self._lock                = threading.Lock()
        self._dedup: dict[str, float] = {}
        self._mtime_cache: dict[str, float] = {}
        self._usb_paths: set[str] = set()
        self._usb_callback: Callable | None = None
        # Cross-dir move tracking: basename → (src_path, monotonic_time)
        self._recent_moves_out: dict[str, tuple[str, float]] = {}
        # Paths recently restored — suppress watcher events for them temporarily
        self._restore_suppressed: set[str] = set()

        self.paths = []
        for p in paths:
            p = os.path.abspath(os.path.expanduser(p))
            if os.path.isdir(p):
                self.paths.append(p)
            else:
                logger.warning(f"Watch path not found: {p}")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        for path in self.paths:
            t = threading.Thread(
                target=self._watch_loop, args=(path,),
                daemon=True, name=f"edr-rdcw-{os.path.basename(path)}",
            )
            t.start()
            self._threads.append(t)
        logger.info(f"DirectoryWatcher started on {len(self.paths)} path(s)")

    def stop(self):
        self._stop.set()

    def add_known_pii(self, path: str):
        with self._lock:
            self._pii_files.add(os.path.abspath(path))

    def suppress_restore_path(self, path: str, duration: float = 20.0):
        """Подавить события вотчера для path на duration секунд (вызывается после restore)."""
        path = os.path.abspath(path)
        with self._lock:
            self._restore_suppressed.add(path)

        def _remove():
            time.sleep(duration)
            with self._lock:
                self._restore_suppressed.discard(path)

        threading.Thread(target=_remove, daemon=True).start()

    def add_usb_path(self, path: str, usb_callback: Callable | None = None):
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            logger.warning(f"USB path not found: {path}")
            return
        with self._lock:
            if path in self._usb_paths:
                return
            self._usb_paths.add(path)
            if usb_callback:
                self._usb_callback = usb_callback
        t = threading.Thread(
            target=self._watch_loop, args=(path,),
            daemon=True, name=f"edr-rdcw-usb-{os.path.basename(path)}",
        )
        t.start()
        self._threads.append(t)
        logger.info(f"USB drive watch started: {path}")

    # ── ReadDirectoryChangesW loop ────────────────────────────────────────────

    def _watch_loop(self, directory: str):
        try:
            hdir = win32file.CreateFile(
                directory,
                _FILE_LIST_DIRECTORY,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                None, win32con.OPEN_EXISTING, _FILE_FLAG_BACKUP_SEMANTICS, None,
            )
        except Exception as e:
            logger.error(f"Cannot open handle for {directory}: {e}")
            return

        logger.info(f"ReadDirectoryChangesW watching: {directory}")
        try:
            while not self._stop.is_set():
                try:
                    results = win32file.ReadDirectoryChangesW(
                        hdir, 65536, True, _WATCH_FLAGS, None, None,
                    )
                except Exception as e:
                    if not self._stop.is_set():
                        logger.error(f"ReadDirectoryChangesW({directory}): {e}")
                    break
                _rename_old = None
                for action, rel_name in results:
                    full = os.path.abspath(os.path.join(directory, rel_name))

                    if action == _ACTION_RENAMED_OLD:
                        # Flush previous unmatched OLD as deletion
                        if _rename_old is not None:
                            self._on_deleted(_rename_old)
                        _rename_old = full

                    elif action == _ACTION_RENAMED_NEW:
                        if _rename_old is not None:
                            # Paired rename in same batch → same-dir rename or move
                            src = _rename_old
                            _rename_old = None
                            threading.Thread(
                                target=self._on_moved, args=(src, full), daemon=True
                            ).start()
                        else:
                            # File moved IN from outside watched dirs → treat as creation
                            threading.Thread(
                                target=self._on_created, args=(full,), daemon=True
                            ).start()

                    else:
                        # Non-rename event — flush any unmatched RENAMED_OLD as deletion
                        if _rename_old is not None:
                            self._record_move_out(_rename_old)
                            self._on_deleted(_rename_old)
                            _rename_old = None
                        self._dispatch(full, action)

                # End of batch — flush any pending unmatched RENAMED_OLD
                if _rename_old is not None:
                    self._record_move_out(_rename_old)
                    self._on_deleted(_rename_old)
                    _rename_old = None
        finally:
            win32file.CloseHandle(hdir)
            logger.info(f"Watcher stopped: {directory}")

    # ── Cross-dir move helpers ────────────────────────────────────────────────

    _MOVE_WINDOW = 2.0  # seconds to correlate RENAMED_OLD → ADDED pairs

    def _record_move_out(self, path: str):
        """Record that a file was renamed/moved out of a watched dir."""
        with self._lock:
            self._recent_moves_out[os.path.basename(path)] = (path, time.monotonic())

    def _pop_move_src(self, basename: str) -> str | None:
        """Return and remove the source path if a matching move-out is recent."""
        with self._lock:
            entry = self._recent_moves_out.get(basename)
            if not entry:
                return None
            src, ts = entry
            if time.monotonic() - ts < self._MOVE_WINDOW:
                del self._recent_moves_out[basename]
                return src
            del self._recent_moves_out[basename]
            return None

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _dispatch(self, path: str, action: int):
        ext = os.path.splitext(path)[1].lower()
        if ext not in QUARANTINE_EXTS:
            return

        key = f"{action}:{path}"
        now = time.monotonic()
        with self._lock:
            if now - self._dedup.get(key, 0.0) < _DEDUP_SECS:
                return
            self._dedup[key] = now

        if action == _ACTION_CREATED:
            threading.Thread(
                target=self._on_created, args=(path,), daemon=True
            ).start()
        elif action == _ACTION_MODIFIED:
            threading.Thread(
                target=self._on_modified, args=(path,), daemon=True
            ).start()
        elif action == _ACTION_DELETED:
            # Record potential cross-dir move-out before calling _on_deleted,
            # so _on_created on the destination can correlate within _MOVE_WINDOW.
            self._record_move_out(path)
            self._on_deleted(path)

    # ── Handlers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _wait_ready(path: str, max_wait: float = 5.0) -> bool:
        """Wait until file size stops changing (write complete). Returns False if file vanishes."""
        step, prev, stable = 0.3, -1, 0
        elapsed = 0.0
        while elapsed < max_wait:
            try:
                size = os.path.getsize(path)
            except OSError:
                return False
            if size > 0 and size == prev:
                stable += 1
                if stable >= 2:   # stable for 2 consecutive checks (~0.6 s)
                    return True
            else:
                stable = 0
            prev = size
            time.sleep(step)
            elapsed += step
        return os.path.isfile(path)

    def _on_created(self, path: str):
        if not self._wait_ready(path):
            return
        if is_quarantine_stub(path):
            return  # our own stub
        with self._lock:
            if path in self._restore_suppressed:
                return  # только что восстановлен — не генерируем события

        # Cache mtime now so spurious _on_modified events are suppressed
        try:
            mtime = os.path.getmtime(path)
            with self._lock:
                self._mtime_cache[path] = mtime
        except Exception:
            pass

        # Cross-dir move detection: if a file with this basename recently left another
        # watched dir via RENAMED_OLD, treat this ADDED event as a move.
        src = self._pop_move_src(os.path.basename(path))
        if src and src != path:
            self._on_moved(src, path)
            return

        try:
            is_pii, confidence, pii_types = self.scanner.scan_file(path)
        except Exception as e:
            logger.info(f"scan {path}: {e}")
            return
        if not is_pii or confidence < 0.15:
            return

        # USB drive check
        path_lower = path.lower()
        with self._lock:
            usb_root = next(
                (u for u in self._usb_paths if path_lower.startswith(u.lower())), None
            )
            cb = self._usb_callback if usb_root else None

        if usb_root and cb:
            logger.warning(f"PII on USB: {path}")
            # Добавляем в _quarantining до вызова cb, чтобы последующее DELETE-событие
            # (от os.remove внутри on_usb_pii_event) не порождало file_deleted_with_pii.
            with self._lock:
                self._quarantining.add(path)
            cb(path)
            return

        with self._lock:
            self._pii_files.add(path)

        extra = {"confidence": confidence, "pii_types": ",".join(pii_types)}
        severity = _pii_sev(pii_types, confidence)
        self.callback(path, "pii_detected", severity, extra)

        if self.quarantine_on_detect:
            with self._lock:
                self._quarantining.add(path)
            try:
                ok, qpath = quarantine_file(path)
            finally:
                with self._lock:
                    self._quarantining.discard(path)
                    self._pii_files.discard(path)  # prevent false file_deleted_with_pii

            if ok and qpath:
                self.callback(path, "file_quarantined", "MEDIUM", {
                    "quarantine_path": qpath, **extra
                })

    def _on_modified(self, path: str):
        if is_quarantine_stub(path):
            return
        with self._lock:
            if path in self._restore_suppressed:
                return  # только что восстановлен — не генерируем события

        # Проверяем что файл реально изменился (mtime), а не просто был прочитан
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            return
        with self._lock:
            known = path in self._pii_files
            last_mtime = self._mtime_cache.get(path, 0.0)
        if mtime <= last_mtime:
            return  # файл не менялся — ложное событие от Windows при копировании
        with self._lock:
            self._mtime_cache[path] = mtime

        if not known:
            if not os.path.isfile(path):
                return
            try:
                is_pii, confidence, pii_types = self.scanner.scan_file(path)
            except Exception:
                return
            if not is_pii or confidence < 0.15:
                return
            extra    = {"confidence": confidence, "pii_types": ",".join(pii_types)}
            severity = _pii_sev(pii_types, confidence)
        else:
            extra    = {}
            severity = "MEDIUM"

        # USB-проверка: если файл на флешке — передать USB-callback вместо file_modified.
        # Так же добавляем в _quarantining, чтобы последующее DELETE не стало false-alarm.
        path_lower = path.lower()
        with self._lock:
            usb_root = next(
                (u for u in self._usb_paths if path_lower.startswith(u.lower())), None
            )
            cb = self._usb_callback if usb_root else None
            already_handled = path in self._quarantining

        if usb_root and cb and not already_handled:
            with self._lock:
                self._quarantining.add(path)
            cb(path)
            return

        if not known:
            with self._lock:
                self._pii_files.add(path)

        self.callback(path, "file_modified", severity, extra)

        if self.quarantine_on_detect and os.path.isfile(path) and not is_quarantine_stub(path):
            with self._lock:
                self._quarantining.add(path)
            try:
                ok, qpath = quarantine_file(path)
            finally:
                with self._lock:
                    self._quarantining.discard(path)
                    self._pii_files.discard(path)
            if ok and qpath:
                self.callback(path, "file_quarantined", "MEDIUM", {"quarantine_path": qpath})

    def _on_deleted(self, path: str):
        with self._lock:
            if path in self._quarantining:
                return  # we triggered this move — not a user deletion
            was_pii = path in self._pii_files
            self._pii_files.discard(path)

        if was_pii:
            self.callback(path, "file_deleted_with_pii", "HIGH", {})

    def _on_moved(self, src: str, dest: str):
        ext = os.path.splitext(dest)[1].lower()

        # Remove source from tracking regardless of extension
        with self._lock:
            self._pii_files.discard(src)

        if ext not in QUARANTINE_EXTS:
            return
        if not os.path.isfile(dest) or is_quarantine_stub(dest):
            return

        try:
            is_pii, confidence, pii_types = self.scanner.scan_file(dest)
        except Exception as e:
            logger.info(f"scan moved {dest}: {e}")
            return

        if not is_pii or confidence < 0.15:
            return

        # USB drive check for destination — only reached if file contains PII
        dest_lower = dest.lower()
        with self._lock:
            usb_root = next(
                (u for u in self._usb_paths if dest_lower.startswith(u.lower())), None
            )
            cb = self._usb_callback if usb_root else None
        if usb_root and cb:
            cb(dest)
            return

        with self._lock:
            self._pii_files.add(dest)

        severity = _pii_sev(pii_types, confidence)
        extra = {"confidence": confidence, "pii_types": ",".join(pii_types), "from": src}
        self.callback(dest, "file_moved", severity, extra)

        if self.quarantine_on_detect and os.path.isfile(dest) and not is_quarantine_stub(dest):
            with self._lock:
                self._quarantining.add(dest)
            try:
                ok, qpath = quarantine_file(dest)
            finally:
                with self._lock:
                    self._quarantining.discard(dest)
                    self._pii_files.discard(dest)
            if ok and qpath:
                self.callback(dest, "file_quarantined", "MEDIUM", {
                    "quarantine_path": qpath, **extra
                })
