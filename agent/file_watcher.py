import os
import threading
import time
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Extensions supported by PIIScanner (including Office formats)
_OFFICE_EXTS = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf"}

# These event types are always HIGH regardless of confidence
_ALWAYS_HIGH = frozenset({"usb_pii_copy_blocked", "clipboard_block", "network_send"})

_DEDUP_WINDOW = 3  # seconds


def _classify_severity(event_type: str, confidence: float) -> str | None:
    """Return severity string, or None to skip the event (confidence too low)."""
    if confidence < 0.15:
        return None

    if event_type in _ALWAYS_HIGH:
        return "HIGH"

    if confidence <= 0.50:
        return "LOW"

    # confidence > 0.50
    if event_type == "file_read":
        return "LOW"
    if event_type in ("file_modified", "file_moved"):
        return "MEDIUM"

    return "LOW"


class PIIFileHandler(FileSystemEventHandler):
    def __init__(
        self,
        scanner,
        callback: Callable,
        get_removable_drives: Callable = None,
        get_known_pii_files: Callable  = None,
    ):
        self.scanner              = scanner
        self.callback             = callback
        self.get_removable_drives = get_removable_drives or (lambda: [])
        self.get_known_pii_files  = get_known_pii_files  or (lambda: set())
        self.lock                 = threading.Lock()
        self.last_events: dict    = {}

    # ── Watchdog callbacks ────────────────────────────────────────────────────

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path, "file_created")

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(event.src_path, "file_modified")

    def on_moved(self, event):
        if not event.is_directory:
            self._handle(event.dest_path, "file_moved")

    def on_deleted(self, event):
        if not event.is_directory:
            self._handle_deleted(event.src_path)

    # ── Internal handlers ─────────────────────────────────────────────────────

    def _handle(self, path: str, event_type: str):
        if path.endswith(".edrlock") or path.endswith(".lnk"):
            return

        with self.lock:
            key = f"{event_type}:{path}"
            now = time.time()
            if key in self.last_events and now - self.last_events[key] < _DEDUP_WINDOW:
                return
            self.last_events[key] = now

        try:
            ext = os.path.splitext(path)[1].lower()

            # Fast path: known PII file — skip re-scanning, use confidence=1.0
            if ext in _OFFICE_EXTS:
                known = self.get_known_pii_files()
                if path in known:
                    severity = _classify_severity(event_type, 1.0)
                    if severity is not None:
                        self.callback(path, event_type, severity, True, 1.0, [])
                    return

            is_pii, confidence, pii_types = self.scanner.scan_file(path)

            # Pass 0.0 for non-PII files so they get filtered by the threshold
            severity = _classify_severity(event_type, confidence if is_pii else 0.0)
            if severity is None:
                return

            if is_pii and ext in _OFFICE_EXTS:
                try:
                    self.get_known_pii_files().add(path)
                except Exception:
                    pass

            self.callback(path, event_type, severity, is_pii, confidence, pii_types)

        except Exception as e:
            print(f"[ERROR] file_watcher._handle({path}): {e}")

    def _handle_deleted(self, path: str):
        if path.endswith(".lnk"):
            return

        with self.lock:
            key = f"file_deleted:{path}"
            now = time.time()
            if key in self.last_events and now - self.last_events[key] < _DEDUP_WINDOW:
                return
            self.last_events[key] = now

        try:
            if path.endswith(".edrlock"):
                self.callback(path, "file_deleted_with_pii", "HIGH", True, 1.0, [])
                return

            known = self.get_known_pii_files()
            if path in known:
                known.discard(path)
                self.callback(path, "file_deleted_with_pii", "HIGH", True, 1.0, [])
                return

            ext = os.path.splitext(path)[1].lower()
            if ext in {".txt", ".csv", ".log", ".json", ".xml", ".tsv", ".md"}:
                self.callback(path, "file_deleted", "LOW", False, 0.0, [])

        except Exception as e:
            print(f"[ERROR] file_watcher._handle_deleted({path}): {e}")


def start_file_watcher(
    path: str,
    scanner,
    callback: Callable,
    get_removable_drives: Callable = None,
    get_known_pii_files:  Callable = None,
):
    observer = Observer()
    handler  = PIIFileHandler(
        scanner,
        callback,
        get_removable_drives=get_removable_drives,
        get_known_pii_files=get_known_pii_files,
    )
    observer.schedule(handler, path=path, recursive=True)
    observer.start()
    return observer
