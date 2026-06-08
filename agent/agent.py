"""EDR Agent — основной процесс агента."""
import sys
import os
import hashlib
import subprocess

# Гарантируем, что директория агента в sys.path
_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import configparser
import json
import logging
import platform
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone

import requests
import psutil

from pii_scanner import PIIScanner, HIGH_PII_TYPES
from process_monitor import scan_processes
from response_handler import handle_high_event, monitor_clipboard
from usb_monitor import USBMonitor
from quarantine import (
    PROTECTED_EXTS, SCREENSHOTS_DIR, DirectoryWatcher,
    quarantine_file, is_quarantine_stub, setup_quarantine_dir,
)

# ── Конфигурация ──────────────────────────────────────────────────────────────

_cfg = configparser.ConfigParser()
_cfg.read(os.path.join(_AGENT_DIR, "config.ini"), encoding="utf-8")

SERVER_BASE = (
    os.environ.get("EDR_SERVER")
    or _cfg.get("server", "base_url", fallback=None)
    or "http://127.0.0.1:8088"
).rstrip("/")

AGENT_ID = (
    os.environ.get("AGENT_ID")
    or _cfg.get("agent", "agent_id", fallback=None)
    or socket.gethostname()
)
CURRENT_USER = os.environ.get("USERNAME", "unknown")

_raw_paths = _cfg.get("agent", "watch_paths", fallback="")
WATCH_PATHS = (
    [p.strip() for p in _raw_paths.split(",") if p.strip()]
    if _raw_paths.strip()
    else [
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~/Downloads"),
    ]
)

BUFFER_DB     = os.path.join(_AGENT_DIR, "agent_buffer.db")

_DATA_DIR      = os.path.join(_AGENT_DIR, "data")
_LOCK_FILE     = os.path.join(_DATA_DIR, "agent.lock")
_SCAN_CACHE_DB = os.path.join(_DATA_DIR, "scan_cache.db")

# ── Флаги из config.ini [protection] ──────────────────────────────────────────

MONITOR_CLIPBOARD = _cfg.getboolean("protection", "monitor_clipboard", fallback=True)

# Пороги confidence читаем из config; дефолты — текущие значения
_IMAGE_EXTS_AGENT = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif"}
_MIN_CONF_DEFAULT = float(_cfg.get("protection", "min_confidence",       fallback="0.15"))
_MIN_CONF_IMAGE   = float(_cfg.get("protection", "min_confidence_image",  fallback="0.40"))

# ── Логирование ───────────────────────────────────────────────────────────────

LOG_DIR = os.path.join(_AGENT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_log_file = os.path.join(LOG_DIR, "agent.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("edr.agent")
logger.info(f"=== EDR Agent запускается. AGENT_ID={AGENT_ID} SERVER={SERVER_BASE} ===")


# ── Вспомогательные ───────────────────────────────────────────────────────────

def _pii_severity(pii_types: list, confidence: float) -> str:
    unique = set(pii_types)
    if unique & HIGH_PII_TYPES:   # IIN / CARD / DOC_NUM → HIGH
        return "HIGH"
    if len(unique) >= 2:           # 2+ мягких типа → MEDIUM
        return "MEDIUM"
    return "LOW"                   # один мягкий тип → LOW

# ── Одиночный экземпляр (lock-файл) ──────────────────────────────────────────

def _acquire_lock() -> bool:
    """Вернуть True если получили блокировку, False если агент уже запущен."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    if os.path.isfile(_LOCK_FILE):
        try:
            with open(_LOCK_FILE) as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                try:
                    proc = psutil.Process(pid)
                    if proc.status() != psutil.STATUS_ZOMBIE:
                        # Проверяем что PID принадлежит именно нашему агенту,
                        # а не другому процессу которому ОС переиспользовала этот PID
                        cmdline = " ".join(proc.cmdline()).lower()
                        if "agent.py" in cmdline:
                            return False   # действительно наш агент работает
                        # PID переиспользован другим процессом — lock устарел
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
        # Устаревший lock файл — удаляем и берём блокировку
        try:
            os.remove(_LOCK_FILE)
        except Exception:
            pass
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_lock():
    try:
        os.remove(_LOCK_FILE)
    except Exception:
        pass


# ── Вспомогательные ───────────────────────────────────────────────────────────

def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return socket.gethostbyname(socket.gethostname())


# ── Локальный SQLite-буфер ────────────────────────────────────────────────────

class LocalBuffer:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init()

    def _init(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    severity   TEXT,
                    details    TEXT,
                    ts         TEXT,
                    sent       INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    def store(self, event_type: str, severity, details: dict):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO events (event_type, severity, details, ts) VALUES (?,?,?,?)",
                    (
                        event_type,
                        severity,
                        json.dumps(details, ensure_ascii=False),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()

    def get_unsent(self, limit: int = 50) -> list:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                return conn.execute(
                    "SELECT id, event_type, severity, details FROM events WHERE sent=0 ORDER BY id LIMIT ?",
                    (limit,),
                ).fetchall()

    def mark_sent(self, ids: list):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany("UPDATE events SET sent=1 WHERE id=?", [(i,) for i in ids])
                conn.commit()


# ── Кеш сканирования ──────────────────────────────────────────────────────────

class ScanCache:
    """Хранит mtime+size уже отсканированных файлов.
    При повторном запуске агента пропускает файлы которые не изменились."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_cache (
                    path  TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    size  INTEGER NOT NULL
                )
            """)
            conn.commit()

    def is_cached(self, path: str) -> bool:
        """True если файл не изменился с последнего сканирования."""
        try:
            st = os.stat(path)
        except OSError:
            return False
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT mtime, size FROM scan_cache WHERE path=?", (path,)
                ).fetchone()
        return (row is not None
                and abs(row[0] - st.st_mtime) < 0.01
                and row[1] == st.st_size)

    def mark(self, path: str):
        """Записать текущий mtime+size файла — пометить как отсканированный."""
        try:
            st = os.stat(path)
        except OSError:
            return
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO scan_cache (path, mtime, size) VALUES (?,?,?)",
                    (path, st.st_mtime, st.st_size),
                )
                conn.commit()

    def invalidate(self, path: str):
        """Убрать файл из кеша — будет пересканирован при следующем старте."""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM scan_cache WHERE path=?", (path,))
                conn.commit()

    def clear(self):
        """Очистить весь кеш — принудит пересканирование всех файлов."""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM scan_cache")
                conn.commit()
        logger.info("Кеш сканирования очищен (force rescan)")


# ── Агент ─────────────────────────────────────────────────────────────────────

class Agent:
    def __init__(self):
        self.scanner = PIIScanner()
        self.usb_monitor = USBMonitor()
        self.buffer = LocalBuffer(BUFFER_DB)
        self.scan_cache = ScanCache(_SCAN_CACHE_DB)
        self.running = True
        self.observers: list = []
        self._watched_paths: set = set()
        self.removable_drives: set = set()
        self._registered = False
        self.pii_files: set = set()
        self.dir_watcher: DirectoryWatcher | None = None
        self._flush_now = threading.Event()

    # ── Регистрация ───────────────────────────────────────────────────────────

    def register(self) -> bool:
        payload = {
            "agent_id": AGENT_ID,
            "hostname": socket.gethostname(),
            "ip_address": _get_local_ip(),
            "os_info": f"{platform.system()} {platform.version()}",
        }
        try:
            r = requests.post(
                f"{SERVER_BASE}/agents/register", json=payload, timeout=5
            )
            if r.status_code == 200:
                self._registered = True
                logger.info(f"Агент зарегистрирован: {AGENT_ID}")
                return True
            logger.warning(f"Регистрация: HTTP {r.status_code} — {r.text[:200]}")
        except Exception as e:
            logger.warning(f"Регистрация не удалась: {e}")
        return False

    # ── Отправка событий ──────────────────────────────────────────────────────

    def send_event(self, event_type: str, severity, details: dict):
        payload = {
            "agent_id": AGENT_ID,
            "user": CURRENT_USER,
            "event_type": event_type,
            "severity": severity,
            "details": details,
        }
        try:
            r = requests.post(f"{SERVER_BASE}/events", json=payload, timeout=5)
            if r.status_code == 200:
                logger.info(f"Событие → сервер: {event_type} [{severity}]")
                return
            logger.warning(f"Сервер {r.status_code} для {event_type}: {r.text[:200]}")
            self.buffer.store(event_type, severity, details)
        except Exception as e:
            self.buffer.store(event_type, severity, details)
            logger.warning(f"Буферизовано (сервер недоступен): {event_type} — {e}")

    # ── Фоновый сброс буфера ──────────────────────────────────────────────────

    def _flush_buffer(self):
        while self.running:
            self._flush_now.wait(timeout=30)
            self._flush_now.clear()
            rows = self.buffer.get_unsent()
            if not rows:
                continue
            # Перед отправкой буфера переподключаемся если нужно
            if not self._registered:
                self.register()
                if not self._registered:
                    continue
            sent_ids = []
            for row_id, event_type, severity, details_json in rows:
                try:
                    details = json.loads(details_json) if details_json else {}
                    payload = {
                        "agent_id": AGENT_ID,
                        "user": CURRENT_USER,
                        "event_type": event_type,
                        "severity": severity,
                        "details": details,
                    }
                    r = requests.post(
                        f"{SERVER_BASE}/events", json=payload, timeout=5
                    )
                    if r.status_code == 200:
                        sent_ids.append(row_id)
                    else:
                        break
                except Exception:
                    break
            if sent_ids:
                self.buffer.mark_sent(sent_ids)
                logger.info(f"Сброшено из буфера: {len(sent_ids)} событий")

    # ── Heartbeat — автопереподключение ───────────────────────────────────────

    def _heartbeat(self):
        """Каждые 30 сек отправляет heartbeat на сервер (обновляет last_seen)."""
        while self.running:
            time.sleep(30)
            try:
                was_registered = self._registered
                self.register()  # обновляет last_seen на сервере каждые 30 сек
                if not was_registered and self._registered:
                    logger.info("Сервер снова доступен, переподключились.")
                    self._flush_now.set()
            except Exception:
                if self._registered:
                    logger.warning("Сервер недоступен, жду восстановления...")
                self._registered = False

    # ── Первичное сканирование ────────────────────────────────────────────────

    def _scan_path(self, root_path: str, shallow: bool = False) -> int:
        """Scan root_path, return count of newly found PII files.
        shallow=True → top-level files only, skip all subdirectories."""
        found = 0
        norm_root = os.path.normpath(root_path)

        for root, dirs, files in os.walk(root_path, topdown=True):
            norm_here = os.path.normpath(root)

            # Shallow mode: only process top-level, never recurse
            if shallow and norm_here != norm_root:
                dirs.clear()
                continue

            for fname in files:
                path = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()
                if ext not in PROTECTED_EXTS:
                    continue

                # Already a quarantine stub — file was moved, nothing to do
                if is_quarantine_stub(path):
                    logger.debug(f"Quarantine stub, skip: {fname}")
                    continue

                # Файл уже сканировался и не изменился — пропускаем
                if self.scan_cache.is_cached(path):
                    logger.debug(f"Кеш (пропуск): {fname}")
                    continue

                try:
                    is_pii, confidence, pii_types = self.scanner.scan_file(path)
                    # Записываем в кеш после сканирования (не важно нашли ПД или нет)
                    self.scan_cache.mark(path)

                    # Для изображений порог выше — OCR даёт много ложных срабатываний
                    ext_lower = os.path.splitext(fname)[1].lower()
                    min_conf = (_MIN_CONF_IMAGE if ext_lower in _IMAGE_EXTS_AGENT
                                else _MIN_CONF_DEFAULT)

                    if is_pii and confidence >= min_conf:
                        found += 1
                        self.pii_files.add(path)
                        severity = _pii_severity(pii_types, confidence)
                        self.send_event(
                            "pii_detected",
                            severity,
                            {
                                "file": path,
                                "file_name": fname,
                                "confidence": confidence,
                                "pii_types": ",".join(pii_types),
                                "blocked": False,
                            },
                        )
                        logger.info(f"ПД найдены, ждём команды с дашборда: {fname}")
                except Exception as e:
                    logger.debug(f"Сканирование {path}: {e}")

        return found

    def initial_scan(self):
        logger.info("Запуск первичного сканирования...")
        found = 0
        downloads_norm = os.path.normpath(os.path.expanduser("~/Downloads"))

        for root_path in WATCH_PATHS:
            if not os.path.exists(root_path):
                continue
            norm = os.path.normpath(root_path)
            # Downloads: top-level only (no subdirectories)
            shallow = norm.lower() == downloads_norm.lower()
            label = f"(поверхностно)" if shallow else "(рекурсивно)"
            logger.info(f"Сканирую {root_path} {label}")
            found += self._scan_path(root_path, shallow=shallow)

        logger.info(f"Первичное сканирование завершено. ПД-файлов: {found}")

    # ── Callback от DirectoryWatcher ─────────────────────────────────────────

    def on_usb_pii_event(self, path: str):
        """Called when a PII file appears on a USB drive — block it."""
        path_lower = path.lower()
        drive = next(
            (d for d in self.removable_drives if path_lower.startswith(d.lower())), ""
        )
        details = {
            "file": path, "file_name": os.path.basename(path), "drive": drive,
        }
        try:
            os.remove(path)
            details["blocked"] = True
            logger.warning(f"Файл удалён с USB: {path}")
        except Exception as e:
            details["blocked"] = False
            logger.error(f"Не удалось удалить с USB {path}: {e}")
        self.send_event("usb_pii_copy_blocked", "HIGH", details)
        handle_high_event(path, "usb_copy")

    # ── Запуск наблюдателя директорий (ReadDirectoryChangesW) ────────────────

    def _notify_quarantine(self, original_path: str, qpath: str, pii_types, confidence: float):
        """Register quarantined file on server."""
        try:
            payload = {
                "agent_id":        AGENT_ID,
                "original_path":   original_path,
                "quarantine_path": qpath,
                "filename":        os.path.basename(original_path),
                "pii_types":       ",".join(pii_types) if isinstance(pii_types, list) else str(pii_types),
                "confidence":      confidence,
            }
            r = requests.post(f"{SERVER_BASE}/quarantine", json=payload, timeout=10)
            if r.status_code == 200:
                logger.info(f"Quarantine registered on server: {os.path.basename(original_path)}")
            else:
                logger.warning(f"Quarantine register: HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"Quarantine register error: {e}")

    def _take_and_upload_screenshot(self, event_type: str) -> str:
        """Capture screen, save locally, upload to server. Returns filename."""
        try:
            from PIL import ImageGrab
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename  = f"screenshot_{timestamp}.png"

            img = ImageGrab.grab()

            # Сохраняем в защищённую директорию карантина (admin-only ACL)
            os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
            img.save(os.path.join(SCREENSHOTS_DIR, filename))

            import base64, io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)

            payload = {
                "agent_id":   AGENT_ID,
                "event_type": event_type,
                "filename":   filename,
                "image_b64":  base64.b64encode(buf.getvalue()).decode(),
            }
            r = requests.post(f"{SERVER_BASE}/screenshots", json=payload, timeout=15)
            if r.status_code == 200:
                logger.info(f"Screenshot uploaded: {filename}")
            return filename
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return ""

    def _on_watcher_event(self, path: str, event_type: str, severity: str, extra: dict | None = None):
        """Callback from DirectoryWatcher."""
        if extra is None:
            extra = {}
        # Файл изменился — убираем из кеша, чтобы следующий запуск его пересканировал
        self.scan_cache.invalidate(path)
        scr_filename = ""
        if severity == "HIGH":
            scr_filename = self._take_and_upload_screenshot(event_type)
        details = {
            "file":      path,
            "file_name": os.path.basename(path),
            **extra,
            **({"screenshot": scr_filename} if scr_filename else {}),
        }
        self.send_event(event_type, severity, details)

        # Register quarantined file on server when watcher does the quarantine
        if event_type == "file_quarantined" and extra.get("quarantine_path"):
            self._notify_quarantine(
                path,
                extra["quarantine_path"],
                extra.get("pii_types", ""),
                float(extra.get("confidence", 0.0)),
            )

        if severity == "HIGH":
            handle_high_event(path, event_type)

    def watch_files(self):
        self.dir_watcher = DirectoryWatcher(
            paths=WATCH_PATHS,
            scanner=self.scanner,
            callback=self._on_watcher_event,
            quarantine_on_detect=False,
        )
        # Pre-populate with files found during initial_scan
        for p in self.pii_files:
            self.dir_watcher.add_known_pii(p)
        self.dir_watcher.start()

    # ── Мониторинг процессов ──────────────────────────────────────────────────

    def monitor_processes(self):
        while self.running:
            try:
                for proc in scan_processes():
                    self.send_event("suspicious_process", "HIGH", proc)
            except Exception as e:
                logger.error(f"monitor_processes: {e}")
            time.sleep(5)

    # ── Мониторинг USB ────────────────────────────────────────────────────────

    def _scan_usb_drive(self, drive: str):
        """Сканирует существующие файлы на флешке при вставке и блокирует ПД."""
        logger.info(f"Сканирование содержимого USB при вставке: {drive}")
        _skip = {"system volume information", "recycled", "$recycle.bin", ".trash-1000"}
        try:
            for root, dirs, files in os.walk(drive, topdown=True):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in _skip]
                for fname in files:
                    path = os.path.join(root, fname)
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in PROTECTED_EXTS:
                        continue
                    try:
                        is_pii, confidence, pii_types = self.scanner.scan_file(path)
                        if is_pii and confidence >= _MIN_CONF_DEFAULT:
                            logger.warning(f"ПД на USB (существующий файл): {path}")
                            self.on_usb_pii_event(path)
                    except Exception as e:
                        logger.debug(f"USB scan {path}: {e}")
        except Exception as e:
            logger.error(f"_scan_usb_drive {drive}: {e}")

    def monitor_usb(self):
        while self.running:
            try:
                inserted, removed = self.usb_monitor.poll()
                for drive in inserted:
                    norm = os.path.normpath(drive)
                    self.removable_drives.add(norm)
                    self.send_event(
                        "usb_inserted",
                        "LOW",
                        {"drive": norm, "drive_label": drive},
                    )
                    if self.dir_watcher:
                        self.dir_watcher.add_usb_path(norm, self.on_usb_pii_event)
                    # Сканируем файлы которые уже были на флешке
                    threading.Thread(target=self._scan_usb_drive, args=(norm,), daemon=True).start()
                    logger.info(f"USB подключён: {norm}")
                for drive in removed:
                    norm = os.path.normpath(drive)
                    self.removable_drives.discard(norm)
                    logger.info(f"USB отключён: {norm}")
            except Exception as e:
                logger.error(f"monitor_usb: {e}")
            time.sleep(2)

    # ── Мониторинг буфера обмена ──────────────────────────────────────────────

    def _monitor_clipboard(self):
        try:
            logger.info("Clipboard-монитор запущен.")
            monitor_clipboard()
            logger.warning("Clipboard-монитор завершился штатно (неожиданно).")
        except Exception as e:
            logger.error(f"monitor_clipboard завершился с ошибкой: {e}", exc_info=True)

    # ── Polling команды ресканирования от сервера ─────────────────────────────

    def _poll_rescan_commands(self):
        """Каждые 10 сек проверяет команду полного ресканирования от дашборда."""
        while self.running:
            try:
                r = requests.get(
                    f"{SERVER_BASE}/pending-rescan",
                    params={"agent_id": AGENT_ID}, timeout=5,
                )
                if r.status_code == 200 and r.json().get("rescan"):
                    logger.info("Получена команда ресканирования с дашборда")
                    threading.Thread(target=self._do_rescan, daemon=True).start()
            except Exception as e:
                logger.debug(f"pending_rescan poll: {e}")
            time.sleep(10)

    def _do_rescan(self):
        """Очищает кеш и запускает полное сканирование всех папок наблюдения."""
        logger.info("=== Принудительное ресканирование (команда с дашборда) ===")
        self.scan_cache.clear()
        found = 0
        downloads_norm = os.path.normpath(os.path.expanduser("~/Downloads"))
        for root_path in WATCH_PATHS:
            if not os.path.exists(root_path):
                continue
            norm = os.path.normpath(root_path)
            shallow = norm.lower() == downloads_norm.lower()
            label = "(поверхностно)" if shallow else "(рекурсивно)"
            logger.info(f"Ресканирую {root_path} {label}")
            found += self._scan_path(root_path, shallow=shallow)
        logger.info(f"=== Ресканирование завершено: {found} ПД-файлов ===")
        try:
            requests.post(
                f"{SERVER_BASE}/rescan-done",
                json={"agent_id": AGENT_ID, "found": found},
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"rescan-done error: {e}")

    # ── Polling команд карантина от сервера ───────────────────────────────────

    def _poll_quarantine_commands(self):
        """Каждые 5 сек выполняет команды карантина и восстановления от сервера."""
        while self.running:
            self._run_pending_quarantines()
            self._run_pending_restores()
            time.sleep(5)

    def _upload_quarantine_to_server(self, quarantine_path: str, file_id: int) -> bool:
        """Upload quarantined file to server then delete local copy.
        After this the file lives ONLY on the admin's server — student admins can't access it.
        """
        import base64 as _b64
        try:
            size = os.path.getsize(quarantine_path)
            if size > 50 * 1024 * 1024:
                logger.warning(f"Quarantine file too large for server upload ({size} bytes): {quarantine_path}")
                return False
            with open(quarantine_path, "rb") as f:
                content = f.read()
            r = requests.post(
                f"{SERVER_BASE}/quarantine-upload/{file_id}",
                json={"file_content_b64": _b64.b64encode(content).decode()},
                timeout=120,
            )
            if r.status_code == 200:
                try:
                    os.remove(quarantine_path)
                    logger.info(f"Карантинный файл загружен на сервер и удалён локально: {quarantine_path}")
                except Exception as e:
                    logger.warning(f"Не удалось удалить локальную копию карантина: {e}")
                return True
            logger.warning(f"Загрузка карантина на сервер: HTTP {r.status_code}")
            return False
        except Exception as e:
            logger.warning(f"_upload_quarantine_to_server: {e}")
            return False

    def _download_quarantine_from_server(self, file_id: int, original_path: str) -> str:
        """Download quarantine file from server for restore. Returns temp file path or ''."""
        import base64 as _b64
        import tempfile
        try:
            r = requests.get(
                f"{SERVER_BASE}/quarantine-download/{file_id}",
                params={"agent_id": AGENT_ID},
                timeout=120,
            )
            if r.status_code != 200:
                logger.warning(f"Скачивание карантина {file_id}: HTTP {r.status_code}")
                return ""
            data = r.json()
            content = _b64.b64decode(data["file_content_b64"])
            ext = (os.path.splitext(original_path)[1]
                   or os.path.splitext(data.get("filename", ""))[1])
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            tmp.write(content)
            tmp.close()
            logger.info(f"Карантинный файл скачан с сервера: {data.get('filename')} ({len(content)} bytes)")
            return tmp.name
        except Exception as e:
            logger.warning(f"_download_quarantine_from_server: {e}")
            return ""

    def _run_pending_quarantines(self):
        try:
            r = requests.get(
                f"{SERVER_BASE}/pd-files/pending-quarantine",
                params={"agent_id": AGENT_ID}, timeout=5,
            )
            if r.status_code != 200:
                return
            for item in r.json():
                fid, path = item["id"], item["file_path"]
                ok, qpath = quarantine_file(path)
                if not ok and not os.path.isfile(path):
                    ok = True
                    logger.warning(f"Файл для карантина не найден (уже перемещён?): {path}")
                try:
                    done_r = requests.post(
                        f"{SERVER_BASE}/pd-files/{fid}/quarantine-done",
                        json={"quarantine_path": qpath, "success": ok},
                        timeout=5,
                    )
                    if ok and qpath and done_r.status_code == 200:
                        qf_id = done_r.json().get("quarantine_file_id")
                        if qf_id:
                            # Upload to server in background, then delete local copy
                            threading.Thread(
                                target=self._upload_quarantine_to_server,
                                args=(qpath, qf_id),
                                daemon=True,
                            ).start()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"pending_quarantines: {e}")

    def _run_pending_restores(self):
        try:
            r = requests.get(
                f"{SERVER_BASE}/quarantine/pending-restore",
                params={"agent_id": AGENT_ID}, timeout=5,
            )
            if r.status_code != 200:
                return
            for item in r.json():
                fid   = item["id"]
                qpath = item["quarantine_path"]
                opath = item["original_path"]

                tmp_file = None
                if not qpath:
                    # File stored on server — download to temp location first
                    tmp_file = self._download_quarantine_from_server(fid, opath)
                    if not tmp_file:
                        logger.warning(f"Не удалось получить карантинный файл {fid} с сервера")
                        try:
                            requests.post(
                                f"{SERVER_BASE}/quarantine/{fid}/restore-done",
                                json={"success": False}, timeout=5,
                            )
                        except Exception:
                            pass
                        continue
                    qpath = tmp_file

                from quarantine import restore_file
                if self.dir_watcher:
                    self.dir_watcher.suppress_restore_path(opath)
                ok = restore_file(qpath, opath)

                # Clean up temp download
                if tmp_file and os.path.isfile(tmp_file):
                    try:
                        os.remove(tmp_file)
                    except Exception:
                        pass

                if ok:
                    logger.info(f"Файл восстановлен: {opath}")
                else:
                    logger.warning(f"Не удалось восстановить файл (qpath={qpath!r})")
                try:
                    requests.post(
                        f"{SERVER_BASE}/quarantine/{fid}/restore-done",
                        json={"success": ok}, timeout=5,
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"pending_restores: {e}")

    # ── Polling ручного сканирования от сервера ───────────────────────────────

    # Папки которые пропускаем при поиске по всем дискам
    _SKIP_DIRS = {
        "windows", "program files", "program files (x86)",
        "programdata", "recovery", "$recycle.bin", "system volume information",
        "appdata", "application data",
        ".venv", "venv", "node_modules", "__pycache__", ".git",
        "pycharmprojects", "onedrive",
    }

    def _find_files_by_name(self, name: str) -> list[str]:
        """Ищет файлы по имени (частичное совпадение, без учёта регистра) на дисках C и D."""
        found = []
        name_lower = name.lower()
        for drive in ("C:\\", "D:\\"):
            if not os.path.isdir(drive):
                continue
            for root, dirs, files in os.walk(drive, topdown=True):
                # Пропускаем системные/служебные папки
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in self._SKIP_DIRS
                    and not d.startswith(".")
                ]
                for fname in files:
                    if name_lower in fname.lower():
                        found.append(os.path.join(root, fname))
        return found

    def _poll_scan_requests(self):
        """Каждые 5 сек выполняет ручные запросы сканирования с дашборда."""
        while self.running:
            try:
                r = requests.get(
                    f"{SERVER_BASE}/pending-scans",
                    params={"agent_id": AGENT_ID}, timeout=5,
                )
                if r.status_code == 200:
                    for item in r.json():
                        name = item["name"]
                        threading.Thread(
                            target=self._do_manual_scan_by_name, args=(name,), daemon=True
                        ).start()
            except Exception as e:
                logger.debug(f"pending_scans poll: {e}")
            time.sleep(5)

    def _do_manual_scan_by_name(self, name: str):
        logger.info(f"Поиск файла по имени: '{name}'")
        found = self._find_files_by_name(name)
        logger.info(f"Найдено файлов: {len(found)}")

        if not found:
            # Сообщаем серверу что файл не найден
            try:
                requests.post(f"{SERVER_BASE}/scan-file-done", json={
                    "agent_id": AGENT_ID,
                    "file_path": name,
                    "is_pii": False,
                    "pii_types": "",
                    "confidence": 0.0,
                    "found_count": 0,
                }, timeout=10)
            except Exception:
                pass
            return

        for path in found:
            self._do_manual_scan(path, found_count=len(found))

    def _do_manual_scan(self, path: str, found_count: int = 1):
        logger.info(f"Ручное сканирование: {path}")
        try:
            is_pii, confidence, pii_types = self.scanner.scan_file(path)
        except Exception as e:
            logger.warning(f"Ошибка сканирования {path}: {e}")
            is_pii, confidence, pii_types = False, 0.0, []

        payload = {
            "agent_id": AGENT_ID,
            "file_path": path,
            "is_pii": is_pii,
            "pii_types": ",".join(pii_types),
            "confidence": confidence,
            "found_count": found_count,
        }
        try:
            requests.post(f"{SERVER_BASE}/scan-file-done", json=payload, timeout=10)
        except Exception as e:
            logger.warning(f"scan-file-done error: {e}")

        if is_pii and confidence >= 0.15:
            fname = os.path.basename(path)
            severity = _pii_severity(pii_types, confidence)
            self.send_event("pii_detected", severity, {
                "file": path,
                "file_name": fname,
                "confidence": confidence,
                "pii_types": ",".join(pii_types),
                "blocked": False,
            })
            logger.info(f"Ручной скан — ПД найдены: {fname} ({','.join(pii_types)})")
        else:
            logger.info(f"Ручной скан — ПД не найдены: {path}")

    # ── Запуск ────────────────────────────────────────────────────────────────

    def run(self):
        setup_quarantine_dir()

        # Пытаемся зарегистрироваться, но не блокируемся
        if not self.register():
            logger.warning(
                "Сервер недоступен при старте — работаем в автономном режиме, "
                "события будут буферизованы."
            )
        else:
            self._flush_now.set()

        self.initial_scan()
        self.watch_files()

        threads = [
            threading.Thread(target=self.monitor_processes, daemon=True),
            threading.Thread(target=self.monitor_usb, daemon=True),
            threading.Thread(target=self._flush_buffer, daemon=True),
            threading.Thread(target=self._heartbeat, daemon=True),
            threading.Thread(target=self._poll_quarantine_commands, daemon=True),
            threading.Thread(target=self._poll_scan_requests, daemon=True),
            threading.Thread(target=self._poll_rescan_commands, daemon=True),
        ]
        if MONITOR_CLIPBOARD:
            threads.append(threading.Thread(target=self._monitor_clipboard, daemon=True))
        else:
            logger.info("Clipboard-монитор отключён (monitor_clipboard=false в config.ini)")
        for t in threads:
            t.start()

        logger.info("Агент полностью запущен.")
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            if self.dir_watcher:
                self.dir_watcher.stop()
            logger.info("Агент остановлен.")


_UPDATABLE_FILES = [
    "agent.py", "pii_scanner.py", "quarantine.py",
    "response_handler.py", "usb_monitor.py", "process_monitor.py",
]


def _local_version() -> str:
    h = hashlib.sha256()
    for fname in sorted(_UPDATABLE_FILES):
        p = os.path.join(_AGENT_DIR, fname)
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()[:16]


def _check_and_update():
    """При старте сравниваем версию с сервером. Если отличается — скачиваем и перезапускаемся."""
    try:
        r = requests.get(f"{SERVER_BASE}/agent-version", timeout=5)
        if r.status_code != 200:
            return
        server_ver = r.json().get("version", "")
        local_ver = _local_version()
        if server_ver == local_ver:
            logger.info(f"Версия агента актуальна: {local_ver}")
            return
        logger.info(f"Обновление: {local_ver} → {server_ver}. Загружаю файлы...")
        for fname in _UPDATABLE_FILES:
            fr = requests.get(f"{SERVER_BASE}/agent-update/{fname}", timeout=30)
            if fr.status_code == 200:
                dest = os.path.join(_AGENT_DIR, fname)
                with open(dest, "wb") as f:
                    f.write(fr.content)
                logger.info(f"  Обновлён: {fname}")
            else:
                logger.warning(f"  Не удалось скачать: {fname} (HTTP {fr.status_code})")
        logger.info("Обновление применено. Выхожу (код 99) — лаунчер перезапустит агент.")
        sys.exit(99)
    except Exception as e:
        logger.warning(f"Автообновление недоступно (сервер не отвечает?): {e}")


if __name__ == "__main__":
    _check_and_update()
    if not _acquire_lock():
        logger.error(
            f"Агент уже запущен (PID из {_LOCK_FILE}). "
            "Остановите предыдущий экземпляр и попробуйте снова."
        )
        sys.exit(1)
    try:
        Agent().run()
    finally:
        _release_lock()
