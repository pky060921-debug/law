import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import subprocess

class TestFolderHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.pdf'):
            print(f"새 파일 감지: {event.src_path}. 분석을 시작합니다...")
            # 아까 만든 analyzer.py 실행
            subprocess.run(["python3", "analyzer.py"])

if __name__ == "__main__":
    path = "./test"
    event_handler = TestFolderHandler()
    observer = Observer()
    observer.schedule(event_handler, path, recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
