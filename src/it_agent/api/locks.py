"""进程内任务锁：同一任务同一时刻只有一个后台执行者。"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class TaskLockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, task_id: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(task_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[task_id] = lock
            return lock

    def try_acquire(self, task_id: str) -> bool:
        return self._lock(task_id).acquire(blocking=False)

    def release(self, task_id: str) -> None:
        lock = self._lock(task_id)
        if lock.locked():
            lock.release()

    def is_busy(self, task_id: str) -> bool:
        return self._lock(task_id).locked()

    @contextmanager
    def hold(self, task_id: str) -> Iterator[bool]:
        acquired = self.try_acquire(task_id)
        try:
            yield acquired
        finally:
            if acquired:
                self.release(task_id)
