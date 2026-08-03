"""跨进程文件锁；进程退出时由操作系统自动释放，不依赖删除锁文件。"""

import os
from pathlib import Path


class ProcessLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._handle = None
        self._locked = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        self._handle.seek(0, os.SEEK_END)
        if self._handle.tell() == 0:
            self._handle.write(b"0")
            self._handle.flush()
        self._handle.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._locked = True
            return True
        except (OSError, IOError):
            self._handle.close()
            self._handle = None
            return False

    def release(self):
        if not self._handle:
            return
        if self._locked:
            self._handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._locked = False
        self._handle.close()
        self._handle = None

    def __enter__(self):
        if not self.acquire():
            raise BlockingIOError(f"无法获取进程锁: {self.path}")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.release()
