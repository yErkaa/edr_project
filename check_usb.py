import ctypes
import os
import sys

sys.path.insert(0, r"C:\edr_agent\agent")

# ── USB диагностика ──────────────────────────────────────────────────────────
print("=== Диски (2=флешка, 3=HDD) ===")
GetDriveTypeW = ctypes.windll.kernel32.GetDriveTypeW
bitmask = ctypes.windll.kernel32.GetLogicalDrives()
for i, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    if bitmask & (1 << i):
        drive = f"{letter}:\\"
        dtype = GetDriveTypeW(drive)
        label = {2: "ФЛЕШКА <<<", 3: "HDD/SSD", 4: "Сетевой", 5: "CD"}.get(dtype, f"тип {dtype}")
        print(f"  {drive}  {label}")

# ── Сканер ПД на Desktop ──────────────────────────────────────────────────────
print("\n=== Сканирование файлов на Desktop ===")
desktop = r"C:\Users\User\Desktop"
try:
    from pii_scanner import PIIScanner
    scanner = PIIScanner()
    for fname in os.listdir(desktop):
        fpath = os.path.join(desktop, fname)
        if not os.path.isfile(fpath):
            continue
        ext = os.path.splitext(fname)[1].lower()
        try:
            is_pii, conf, types = scanner.scan_file(fpath)
            status = f"ПД={is_pii}  conf={conf:.2f}  типы={types}"
        except Exception as e:
            status = f"ОШИБКА: {e}"
        print(f"  [{ext}] {fname}")
        print(f"       {status}")
except ImportError as e:
    print(f"Не удалось импортировать pii_scanner: {e}")
    print("Запусти этот скрипт через venv агента:")
    print(r"  C:\edr_agent\edr_agent_for_laptop2\.venv\Scripts\python.exe C:\edr_agent\check_usb.py")
