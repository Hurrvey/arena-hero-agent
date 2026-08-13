"""Cross-process, account-derived single-writer lock for CLI and Web Runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Self


class AccountLockHeld(RuntimeError):
    pass


class AccountLock:
    def __init__(
        self,
        account_hash: str,
        directory: str | Path,
        *,
        runtime_id: str,
    ) -> None:
        self.account_hash = account_hash
        self.directory = Path(directory).resolve()
        self.runtime_id = runtime_id
        self.path = self.directory / f"account-{account_hash}.lock"
        self._file = None
        self._mutex = RLock()

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        directory: str | Path,
        *,
        runtime_id: str,
    ) -> AccountLock:
        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        return cls(digest, directory, runtime_id=runtime_id)

    def acquire(self) -> None:
        with self._mutex:
            if self._file is not None:
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
            handle = os.fdopen(descriptor, "r+b", buffering=0)
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            try:
                _lock_byte(handle)
            except OSError as exc:
                handle.close()
                raise AccountLockHeld("Arena Hero account already has an active writer") from exc
            metadata = json.dumps(
                {"runtimeId": self.runtime_id, "pid": os.getpid()},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            os.lseek(descriptor, 1, os.SEEK_SET)
            os.ftruncate(descriptor, 1)
            os.write(descriptor, metadata)
            os.fsync(descriptor)
            self._file = handle

    def release(self) -> None:
        with self._mutex:
            if self._file is None:
                return
            try:
                _unlock_byte(self._file)
            finally:
                self._file.close()
                self._file = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


if os.name == "nt":
    import msvcrt

    def _lock_byte(handle) -> None:
        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_byte(handle) -> None:
        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_byte(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_byte(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
