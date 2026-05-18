import sys, os
sys.path.insert(0, r'C:\Users\dinar\PycharmProjects\edr\agent')
from file_watcher import PIIFileHandler
from pii_scanner import PIIScanner
import time, shutil
from watchdog.observers import Observer

scanner = PIIScanner()

def on_event(path, event_type, severity, is_pii, confidence, pii_types):
    name = os.path.basename(path)
    print(f"EVENT: {event_type} [{severity}] file={name} confidence={confidence:.2f} is_pii={is_pii} types={pii_types}")

handler = PIIFileHandler(scanner=scanner, callback=on_event)
observer = Observer()

watch_path = r'C:\Users\dinar\Desktop'
observer.schedule(handler, watch_path, recursive=False)
observer.start()
print(f'Watching: {watch_path}')
print('Moving PII file in 3 seconds...')
time.sleep(3)

src = r'C:\Users\dinar\Documents\pd_test_watchdog.txt'
dst = r'C:\Users\dinar\Desktop\pd_test_watchdog.txt'
if not os.path.exists(src):
    # re-create if missing
    with open(src, 'w', encoding='utf-8') as f:
        f.write('ИИН 030514312345 Иванов Иван Петрович +77051234567')

shutil.move(src, dst)
print(f'Moved: {os.path.basename(src)} -> Desktop')

time.sleep(10)
observer.stop()
observer.join()
print('Done.')
