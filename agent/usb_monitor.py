"""Мониторинг USB-накопителей."""
import os
import time

import psutil

_DEDUP_WINDOW = 3  # секунды


class USBMonitor:
    def __init__(self):
        self.known: set = set()
        self._last_inserted: dict = {}

    def get_removable_drives(self) -> list:
        drives = []
        try:
            for part in psutil.disk_partitions(all=False):
                opts = (part.opts or "").lower()
                # Windows: removable флаг ИЛИ тип диска cdrom исключаем
                if "removable" in opts and "cdrom" not in opts:
                    # Нормализуем путь (убираем лишние слеши, но оставляем корень)
                    mp = part.mountpoint
                    # На Windows "E:\" -> нормализуем как "E:"
                    norm = os.path.normpath(mp)
                    drives.append(norm)
        except Exception:
            pass
        return drives

    def poll(self):
        current = set(self.get_removable_drives())
        now = time.time()
        raw_inserted = current - self.known

        # Дедупликация: не сообщаем о диске повторно если он уже был недавно
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
