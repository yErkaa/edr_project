"""Мониторинг USB-накопителей через Win32 GetDriveType."""
import ctypes
import os
import time

_DRIVE_REMOVABLE = 2   # GetDriveType → съёмный диск (флешка)
_DEDUP_WINDOW    = 3   # секунды — не сообщать о том же диске повторно


def _get_removable_drives() -> list[str]:
    """Возвращает список съёмных дисков типа ['E:', 'F:', ...]."""
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if bitmask & 1:
            drive = f"{letter}:\\"
            if ctypes.windll.kernel32.GetDriveType(drive) == _DRIVE_REMOVABLE:
                drives.append(os.path.normpath(drive))
        bitmask >>= 1
    return drives


class USBMonitor:
    def __init__(self):
        self.known: set = set()
        self._last_inserted: dict = {}

    def get_removable_drives(self) -> list[str]:
        return _get_removable_drives()

    def poll(self):
        current = set(self.get_removable_drives())
        now = time.time()
        raw_inserted = current - self.known

        # Дедупликация: не сообщать о диске повторно если он только что появился
        inserted = [
            d for d in raw_inserted
            if d not in self._last_inserted
            or now - self._last_inserted[d] >= _DEDUP_WINDOW
        ]
        for d in inserted:
            self._last_inserted[d] = now

        removed = list(self.known - current)
        self.known = current
        return inserted, removed
