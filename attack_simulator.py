import os
import shutil
import time

BASE_DIR = r"C:\Users\Public\Documents\Students"
SOURCE_FILE = os.path.join(BASE_DIR, "students_data.txt")
BACKUP_DIR = os.path.join(BASE_DIR, "backup")

os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

with open(SOURCE_FILE, "w", encoding="utf-8") as f:
    f.write("IIN: 123456789012\nPhone: +77771234567\nEmail: student@example.com\n")

print(f"[INFO] Source file created: {SOURCE_FILE}")

for i in range(10):
    dst = os.path.join(BACKUP_DIR, f"students_data_copy_{i}.txt")
    shutil.copy(SOURCE_FILE, dst)
    print(f"[INFO] Copied to: {dst}")
    time.sleep(0.5)