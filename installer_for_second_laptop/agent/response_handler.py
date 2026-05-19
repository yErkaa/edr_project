import ctypes
import os
import socket
import subprocess
import time

import requests

SERVER_BASE = os.environ.get("EDR_SERVER", "http://127.0.0.1:8088")
AGENT_ID = os.environ.get("AGENT_ID", socket.gethostname())
CURRENT_USER = os.environ.get("USERNAME", "unknown")

_MESSENGER_PROCESSES = {"WhatsApp.exe", "Telegram.exe", "OUTLOOK.EXE", "thunderbird.exe"}

_MAIL_DOMAINS = [
    "smtp.gmail.com", "imap.gmail.com",
    "smtp.mail.ru",   "imap.mail.ru",
    "smtp.yandex.ru", "imap.yandex.ru",
    "smtp.yahoo.com",
]

# ── Проверка прав администратора ──────────────────────────────────────────────

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

if not is_admin():
    print("ВНИМАНИЕ: агент запущен без прав администратора. Блокировка почты недоступна.")

# ── pywin32 (опционально) ─────────────────────────────────────────────────────
try:
    import win32security
    import ntsecuritycon as con
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

# ── win10toast (опционально) ──────────────────────────────────────────────────
try:
    from win10toast import ToastNotifier
    _toaster = ToastNotifier()
    _TOAST_AVAILABLE = True
except ImportError:
    _toaster = None
    _TOAST_AVAILABLE = False

# ── Auth-токен (устанавливается агентом при старте) ───────────────────────────
_auth_token: str | None = None


def set_auth_token(token: str):
    global _auth_token
    _auth_token = token


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
    """Показывает уведомление: win10toast → MessageBox как fallback."""
    if _TOAST_AVAILABLE and _toaster:
        try:
            _toaster.show_toast(title, message, duration=8, threaded=True)
            return
        except Exception:
            pass
    try:
        ctypes.windll.user32.MessageBoxW(
            0, message, title,
            0x00000030 | 0x00001000,  # MB_ICONWARNING | MB_SYSTEMMODAL
        )
    except Exception as e:
        print(f"[WARN] Уведомление не показано: {e}")


# ── Проверка привилегий ───────────────────────────────────────────────────────

def check_privilege() -> bool:
    """Проверяет, имеет ли текущий пользователь привилегию на разблокировку."""
    if not _auth_token:
        return False
    try:
        resp = requests.get(
            f"{SERVER_BASE}/privileges",
            params={"username": CURRENT_USER},
            headers={"Authorization": f"Bearer {_auth_token}"},
            timeout=3,
        )
        if resp.status_code == 200:
            for priv in resp.json():
                agent_ok = priv.get("agent_id") in (None, "", AGENT_ID)
                if agent_ok and (priv.get("can_unlock_files") or priv.get("can_view_pii")):
                    return True
    except Exception:
        pass
    return False


def check_user_privilege(username: str, agent_id: str, token: str) -> bool:
    """Проверяет привилегию для конкретного пользователя (используется из агента)."""
    try:
        resp = requests.get(
            f"{SERVER_BASE}/privileges",
            params={"username": username},
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.status_code == 200:
            for priv in resp.json():
                if (priv.get("agent_id") in (None, "", agent_id)
                        and priv.get("can_unlock_files")):
                    return True
    except Exception as e:
        print(f"[ERROR] Проверка привилегий: {e}")
    return False


# ── HIGH-событие ──────────────────────────────────────────────────────────────

def handle_high_event(file_path: str, reason: str):
    """Блокирует файл и уведомляет пользователя при HIGH-событии."""
    blocked = block_file(file_path)
    fname = os.path.basename(file_path)
    msg = (
        f"Обнаружена попытка утечки персональных данных!\n\n"
        f"Файл: {fname}\n"
        f"Причина: {reason}\n\n"
        f"Доступ {'заблокирован' if blocked else 'НЕ заблокирован (нет прав)'}.\n"
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

            if check_privilege():
                # Пользователь имеет привилегию — только логируем как MEDIUM
                print(f"[INFO] Clipboard с ПД разрешён (привилегия): {process_name}")
                _send_event("clipboard_pii", "MEDIUM", {
                    "process": process_name,
                    "pii_types": ",".join(pii_types),
                    "note": "Разрешено — есть привилегия",
                })
                continue

            # Нет привилегии — блокируем
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


# ── 2. Блокировка почтовых серверов ──────────────────────────────────────────

def block_mail_servers():
    """Блокирует исходящие соединения к почтовым серверам через Windows Firewall."""
    if not is_admin():
        print("[ERROR] block_mail_servers: требуются права администратора")
        return

    print("[INFO] Блокировка почтовых серверов через Windows Firewall...")
    for domain in _MAIL_DOMAINS:
        rule_name = f"EDR_BLOCK_{domain}"
        cmd = [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name}",
            "dir=out",
            "action=block",
            f"remotehost={domain}",
            "enable=yes",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  [OK] Заблокирован: {domain}")
        else:
            print(f"  [WARN] Не удалось заблокировать {domain}: {result.stderr.strip()}")


# ── 3. Снятие блокировки почтовых серверов ───────────────────────────────────

def unblock_mail_servers():
    """Удаляет все правила EDR_BLOCK_* из Windows Firewall."""
    if not is_admin():
        print("[ERROR] unblock_mail_servers: требуются права администратора")
        return

    print("[INFO] Снятие блокировки почтовых серверов...")
    for domain in _MAIL_DOMAINS:
        rule_name = f"EDR_BLOCK_{domain}"
        cmd = [
            "netsh", "advfirewall", "firewall", "delete", "rule",
            f"name={rule_name}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  [OK] Удалено правило: {rule_name}")
        else:
            print(f"  [INFO] Правило не найдено: {rule_name}")


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
