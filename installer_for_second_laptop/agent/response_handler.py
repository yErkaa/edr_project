import ctypes
import os
import socket
import subprocess
import threading
import time

import requests

SERVER_BASE = os.environ.get("EDR_SERVER", "http://127.0.0.1:8088")
AGENT_ID = os.environ.get("AGENT_ID", socket.gethostname())
CURRENT_USER = os.environ.get("USERNAME", "unknown")

_MESSENGER_PROCESSES = {"WhatsApp.exe", "Telegram.exe", "OUTLOOK.EXE", "thunderbird.exe"}

# ── Проверка прав администратора ──────────────────────────────────────────────

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

# ── pywin32 (опционально) ─────────────────────────────────────────────────────
try:
    import win32security
    import ntsecuritycon as con
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

# Уведомления через ctypes MessageBoxW в отдельном потоке (неблокирующие).


# ── Вспомогательные ──────────────────────────────────────────────────────────

def _send_event(event_type: str, severity: str, details: dict):
    try:
        requests.post(
            f"{SERVER_BASE}/events",
            json={
                "agent_id": AGENT_ID,
                "user": CURRENT_USER,
                "event_type": event_type,
                "severity": severity,
                "details": details,
            },
            timeout=5,
        )
    except Exception as e:
        print(f"[WARN] Не удалось отправить событие {event_type}: {e}")


# ── Блокировка файла ──────────────────────────────────────────────────────────

def block_file(path: str) -> bool:
    """Запрещает доступ к файлу через ACL (pywin32) или icacls как fallback."""
    if not os.path.exists(path):
        return False

    if _WIN32_AVAILABLE:
        try:
            sd = win32security.GetFileSecurity(
                path, win32security.DACL_SECURITY_INFORMATION
            )
            dacl = sd.GetSecurityDescriptorDacl() or win32security.ACL()
            everyone = win32security.CreateWellKnownSid(win32security.WinWorldSid, None)
            dacl.AddAccessDeniedAceEx(win32security.ACL_REVISION, 0, con.FILE_ALL_ACCESS, everyone)
            sd.SetSecurityDescriptorDacl(True, dacl, False)
            win32security.SetFileSecurity(path, win32security.DACL_SECURITY_INFORMATION, sd)
            print(f"[ACTION] Файл заблокирован (ACL): {path}")
            return True
        except Exception as e:
            print(f"[WARN] win32security: {e} — пробую icacls")

    try:
        subprocess.run(
            ["icacls", path, "/deny", "Everyone:(F)"],
            check=False, capture_output=True, text=True
        )
        print(f"[ACTION] Файл заблокирован (icacls): {path}")
        return True
    except Exception as e:
        print(f"[ERROR] Не удалось заблокировать {path}: {e}")
        return False


def unblock_file(path: str) -> bool:
    """Снимает запрет доступа к файлу."""
    if not os.path.exists(path):
        return False

    if _WIN32_AVAILABLE:
        try:
            sd = win32security.GetFileSecurity(
                path, win32security.DACL_SECURITY_INFORMATION
            )
            dacl = sd.GetSecurityDescriptorDacl()
            if dacl:
                everyone = win32security.CreateWellKnownSid(win32security.WinWorldSid, None)
                for i in range(dacl.GetAceCount() - 1, -1, -1):
                    ace = dacl.GetAce(i)
                    if (ace[0][0] == win32security.ACCESS_DENIED_ACE_TYPE
                            and ace[2] == everyone):
                        dacl.DeleteAce(i)
                sd.SetSecurityDescriptorDacl(True, dacl, False)
                win32security.SetFileSecurity(
                    path, win32security.DACL_SECURITY_INFORMATION, sd
                )
            print(f"[ACTION] Блокировка снята (ACL): {path}")
            return True
        except Exception as e:
            print(f"[WARN] win32security разблокировка: {e}")

    try:
        subprocess.run(
            ["icacls", path, "/remove:d", "Everyone"],
            check=False, capture_output=True, text=True
        )
        print(f"[ACTION] Блокировка снята (icacls): {path}")
        return True
    except Exception as e:
        print(f"[ERROR] Не удалось разблокировать {path}: {e}")
        return False


# ── Уведомления ───────────────────────────────────────────────────────────────

def show_notification(title: str, message: str):
    """Показывает уведомление через ctypes MessageBoxW в отдельном потоке (неблокирующий)."""
    def _show():
        try:
            ctypes.windll.user32.MessageBoxW(
                0, message, title,
                0x00000030 | 0x00001000,  # MB_ICONWARNING | MB_SYSTEMMODAL
            )
        except Exception as e:
            print(f"[WARN] Уведомление не показано: {e}")

    threading.Thread(target=_show, daemon=True).start()


# ── HIGH-событие ──────────────────────────────────────────────────────────────

def handle_high_event(file_path: str, reason: str):
    """Блокирует файл и уведомляет пользователя при HIGH-событии."""
    fname = os.path.basename(file_path)

    # Файл мог быть уже перемещён в карантин — не пытаемся блокировать несуществующий путь
    if os.path.exists(file_path):
        blocked = block_file(file_path)
    else:
        blocked = False  # файл в карантине, блокировка не нужна

    msg = (
        f"Обнаружена попытка утечки персональных данных!\n\n"
        f"Файл: {fname}\n"
        f"Причина: {reason}\n\n"
        f"Доступ {'заблокирован' if blocked else 'файл изолирован (карантин)'}.\n"
        f"Обратитесь к системному администратору."
    )
    show_notification("EDR — Угроза ПД учеников", msg)


# ── 1. Мониторинг буфера обмена ───────────────────────────────────────────────

def monitor_clipboard():
    """
    Запускается в отдельном потоке.
    Каждые 0.5 с проверяет буфер обмена на наличие ПД.
    Если ПД найдены И открыт мессенджер/почтовый клиент — блокирует передачу.
    """
    try:
        import pyperclip
        import psutil
        from pii_scanner import PIIScanner
    except ImportError as e:
        print(f"[ERROR] monitor_clipboard: зависимость не установлена — {e}")
        return

    scanner = PIIScanner()
    prev_text = ""

    while True:
        time.sleep(0.5)
        try:
            text = pyperclip.paste()
            if not text or text == prev_text:
                continue
            prev_text = text

            is_pii, confidence, pii_types = scanner.scan_text(text)
            if not is_pii:
                continue

            # Ищем запущенные мессенджеры / почтовые клиенты
            running: set[str] = set()
            for proc in psutil.process_iter(["name"]):
                try:
                    name = proc.info.get("name", "")
                    if name in _MESSENGER_PROCESSES:
                        running.add(name)
                except Exception:
                    pass

            if not running:
                continue

            process_name = next(iter(running))

            # Блокируем передачу ПД через мессенджер
            pyperclip.copy("")
            prev_text = ""
            show_notification(
                "EDR — Блокировка",
                "⛔ Заблокировано — передача персональных данных запрещена",
            )
            _send_event("clipboard_block", "HIGH", {
                "process": process_name,
                "pii_types": ",".join(pii_types),
            })
            print(f"[ACTION] Буфер обмена очищен: ПД перехвачены в {process_name}")

        except Exception as e:
            print(f"[ERROR] monitor_clipboard: {e}")
            time.sleep(2)


# ── Завершение процесса ───────────────────────────────────────────────────────

def kill_process(process_name: str):
    if not process_name:
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", process_name],
            check=False, capture_output=True, text=True
        )
        print(f"[ACTION] Процесс завершён: {process_name}")
    except Exception as e:
        print(f"[ERROR] Не удалось завершить {process_name}: {e}")
