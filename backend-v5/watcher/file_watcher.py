from __future__ import annotations
import asyncio
import threading
import time
from pathlib import Path
from typing import Callable, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from config import SUPPORTED_EXTENSIONS, WATCHER_DEBOUNCE_SECONDS

# 明确排除的路径片段 — watchdog 启动后扫描/重建等操作不应该触发
_SKIP_PATH_PARTS = {
    ".git", "target", "build", "__pycache__", "node_modules",
    "dist", ".idea", ".vscode", "db",
}

# 排除的文件后缀
_SKIP_SUFFIXES = {
    ".db", ".db-wal", ".db-shm", ".pyc", ".class",
    ".log", ".tmp", ".lock", ".idx", ".pack",
}


def _is_user_source_file(path: str) -> bool:
    """判断是否是用户手动修改的源码文件（排除工具生成/系统文件）。"""
    p = Path(path)
    # 后缀必须是支持的源码类型
    if p.suffix not in SUPPORTED_EXTENSIONS:
        return False
    if p.suffix in _SKIP_SUFFIXES:
        return False
    # 路径中不能包含排除目录
    parts_lower = {part.lower() for part in p.parts}
    if parts_lower & _SKIP_PATH_PARTS:
        return False
    return True


class _DebounceHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[list[str]], None], debounce: float):
        super().__init__()
        self._callback = callback
        self._debounce = debounce
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def on_modified(self, event: FileSystemEvent):
        self._handle(event)

    def on_created(self, event: FileSystemEvent):
        self._handle(event)

    def on_deleted(self, event: FileSystemEvent):
        self._handle(event)

    def _handle(self, event: FileSystemEvent):
        if event.is_directory:
            return
        path = str(event.src_path)
        if not _is_user_source_file(path):
            return
        with self._lock:
            self._pending[path] = time.time()
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self):
        with self._lock:
            paths = list(self._pending.keys())
            self._pending.clear()
        if paths:
            self._callback(paths)


class RepoWatcher:
    def __init__(self):
        self._observer: Observer | None = None
        self._watching_path: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_callback: Callable | None = None

    def start(
        self,
        repo_path: str,
        loop: asyncio.AbstractEventLoop,
        async_callback: Callable[[list[str]], Any],
    ):
        self.stop()
        self._loop = loop
        self._async_callback = async_callback
        self._watching_path = repo_path

        def sync_callback(paths: list[str]):
            if self._loop and self._async_callback:
                asyncio.run_coroutine_threadsafe(
                    self._async_callback(paths), self._loop
                )

        handler = _DebounceHandler(sync_callback, WATCHER_DEBOUNCE_SECONDS)
        self._observer = Observer()
        self._observer.schedule(handler, repo_path, recursive=True)
        self._observer.start()

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=3)
            self._observer = None
        self._watching_path = None

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    @property
    def watching_path(self) -> str | None:
        return self._watching_path


_watcher = RepoWatcher()


def get_watcher() -> RepoWatcher:
    return _watcher
