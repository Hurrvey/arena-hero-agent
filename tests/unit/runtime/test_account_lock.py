import json
import os
from types import SimpleNamespace

import pytest

from adaptive_strategy import DisabledAdaptiveCoordinator
from app.runtime import service_factory
from app.runtime.account_lock import (
    AccountLock,
    AccountLockHeld,
    account_scope_from_api_key,
)


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


def test_account_scope_is_stable_nonsecret_sha256() -> None:
    scope = account_scope_from_api_key("private-key")

    assert len(scope) == 64
    assert scope == account_scope_from_api_key("private-key")
    assert scope != account_scope_from_api_key("other-key")
    assert "private-key" not in scope


@pytest.mark.parametrize("value", ["", None, 0, False])
def test_account_scope_rejects_missing_or_nonstring_keys(value) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        account_scope_from_api_key(value)


def test_runtime_session_uses_the_same_nonsecret_account_scope(
    tmp_path,
    monkeypatch,
) -> None:
    created_scopes = []

    class RuntimeStore:
        def create_session(self, *, account_hash):
            created_scopes.append(account_hash)
            return SimpleNamespace(session_id="session-1")

    monkeypatch.setenv("ARENA_HERO_API_KEY", "runtime-private-key")
    monkeypatch.setattr(
        service_factory,
        "SqliteAdaptiveCoordinator",
        SimpleNamespace(from_env=lambda **_kwargs: DisabledAdaptiveCoordinator()),
    )
    factory = service_factory.RuntimeServicesFactory(
        settings=SimpleNamespace(
            dotenv_path=tmp_path / "missing.env",
            lock_directory=tmp_path / "locks",
        ),
        runtime_store=RuntimeStore(),
        strategies=SimpleNamespace(),
        metrics=SimpleNamespace(),
        adaptive=SimpleNamespace(),
        broadcaster=SimpleNamespace(),
    )

    factory.build()

    expected = account_scope_from_api_key("runtime-private-key")
    assert created_scopes == [expected]
    assert factory.account_scope == expected
    assert "runtime-private-key" not in expected
