import ctypes
import os
import sys

sys.path.insert(0, r"C:\edr_agent\agent")

from pii_scanner import PIIScanner, HIGH_PII_TYPES

scanner = PIIScanner()

# ── 1. Проверка уровней ПД ────────────────────────────────────────────────────
print("=== 1. Проверка уровней ПД ===")

tests = [
    ("ИИН",       "Иванов Иван\nИИН: 870611301159"),
    ("Карта",     "Карта: 4532 0151 1283 0366"),
    ("Паспорт",   "Документ: AB1234567"),
    ("Телефон+email", "Тел: +7 701 111 22 33\nmail@test.kz"),
    ("ФИО",       "Иванов Иван Иванович"),
]

def severity(pii_types, confidence):
    if any(t in HIGH_PII_TYPES for t in pii_types):
        return "HIGH"
    return "MEDIUM" if confidence > 0.50 else "LOW"

for name, text in tests:
    is_pii, conf, types = scanner.scan_text(text)
    sev = severity(types, conf) if is_pii else "—"
    print(f"  {name:<20} is_pii={is_pii}  conf={conf:.2f}  types={types}  → {sev}")

# ── 2. USB диски ──────────────────────────────────────────────────────────────
print("\n=== 2. Диски ===")
GetDriveTypeW = ctypes.windll.kernel32.GetDriveTypeW
bitmask = ctypes.windll.kernel32.GetLogicalDrives()
for i, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    if bitmask & (1 << i):
        drive = f"{letter}:\\"
        dtype = GetDriveTypeW(drive)
        label = {2: "ФЛЕШКА <<<", 3: "HDD/SSD", 4: "Сетевой", 5: "CD"}.get(dtype, f"тип {dtype}")
        print(f"  {drive}  {label}")

# ── 3. Тест PDF ───────────────────────────────────────────────────────────────
print("\n=== 3. Тест PDF ===")

# Ищем PDF файлы в Desktop, Documents, Downloads
pdf_found = []
for folder in [r"C:\Users\User\Desktop", r"C:\Users\User\Documents", r"C:\Users\User\Downloads"]:
    if not os.path.isdir(folder):
        continue
    for fname in os.listdir(folder):
        if fname.lower().endswith(".pdf"):
            pdf_found.append(os.path.join(folder, fname))

if not pdf_found:
    print("  PDF файлов не найдено в Desktop/Documents/Downloads.")
    print("  Создай тестовый PDF и положи на Desktop для проверки.")
    # Попробуем создать простой PDF через reportlab если установлен
    try:
        from reportlab.pdfgen import canvas as rl_canvas
        test_pdf = r"C:\Users\User\Desktop\test_pdf_pd.pdf"
        c = rl_canvas.Canvas(test_pdf)
        c.setFont("Helvetica", 12)
        c.drawString(50, 750, "Ivanov Ivan Ivanovich")
        c.drawString(50, 730, "Phone: +7 701 234 56 78")
        c.drawString(50, 710, "Email: ivan@test.kz")
        c.save()
        print(f"  Создан тестовый PDF: {test_pdf}")
        pdf_found.append(test_pdf)
    except ImportError:
        print("  (reportlab не установлен — создай PDF вручную)")
else:
    print(f"  Найдено PDF файлов: {len(pdf_found)}")

for pdf_path in pdf_found[:3]:
    fname = os.path.basename(pdf_path)
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            text = ""
            for page in pdf.pages[:3]:
                t = page.extract_text()
                if t:
                    text += t + "\n"
        print(f"\n  [{fname}]")
        print(f"    pdfplumber прочитал {len(text)} символов")
        if text:
            print(f"    Первые 150 символов: {repr(text[:150])}")
        is_pii, conf, types = scanner.scan_text(text)
        sev = severity(types, conf) if is_pii else "—"
        print(f"    Сканер: is_pii={is_pii}  conf={conf:.2f}  types={types}  → {sev}")
    except Exception as e:
        print(f"  [{fname}] ОШИБКА: {e}")
