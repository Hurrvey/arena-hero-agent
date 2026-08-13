import json
import os

import pytest

from app.runtime.account_lock import AccountLock, AccountLockHeld


def test_two_lock_objects_cannot_hold_the_same_account_hash(tmp_path) -> None:
    first = AccountLock.from_api_key("same-key", tmp_path, runtime_id="one")
    second = AccountLock.from_api_key("same-key", tmp_path, runtime_id="two")

    first.acquire()
    try:
        with pytest.raises(AccountLockHeld):
            second.acquire()
    finally:
        first.release()


def test_releasing_lock_allows_takeover(tmp_path) -> None:
    first = AccountLock.from_api_key("same-key", tmp_path, runtime_id="one")
    second = AccountLock.from_api_key("same-key", tmp_path, runtime_id="two")

    first.acquire()
    first.release()
    second.acquire()
    second.release()


def test_lock_metadata_contains_runtime_and_pid_but_not_api_key(tmp_path) -> None:
    lock = AccountLock.from_api_key("do-not-persist", tmp_path, runtime_id="runtime-7")

    lock.acquire()
    lock.release()
    metadata = json.loads(lock.path.read_bytes()[1:].decode("utf-8"))

    assert metadata["runtimeId"] == "runtime-7"
    assert metadata["pid"] == os.getpid()
    assert "do-not-persist" not in lock.path.read_text(encoding="utf-8")


def test_cli_and_runtime_use_the_same_lock_derivation(tmp_path) -> None:
    cli = AccountLock.from_api_key("shared", tmp_path, runtime_id="cli")
    runtime = AccountLock.from_api_key("shared", tmp_path, runtime_id="web")

    assert cli.account_hash == runtime.account_hash
    assert cli.path == runtime.path
