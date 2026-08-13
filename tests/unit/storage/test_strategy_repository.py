import pytest

from app.storage.database import Database
from app.storage.strategy_repository import RevisionConflict, StrategyRepository
from strategy_policy import StrategyProfile


def repository(tmp_path) -> StrategyRepository:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    result = StrategyRepository(database)
    result.ensure_initial(StrategyProfile.default())
    return result


def test_profile_update_creates_pending_revision_instead_of_mutating_active(tmp_path) -> None:
    repo = repository(tmp_path)
    current = repo.current()
    changed = current.profile.with_updates(worker_target=current.profile.worker_target - 1)

    pending = repo.create_revision(
        expected_revision=current.revision,
        profile=changed,
        source="MANUAL",
        reason="dashboard edit",
    )

    assert pending.status == "PENDING"
    assert pending.parent_revision == current.revision
    assert repo.current() == current


def test_expected_revision_conflict_preserves_newer_revision(tmp_path) -> None:
    repo = repository(tmp_path)
    current = repo.current()
    repo.create_revision(
        expected_revision=current.revision,
        profile=current.profile.with_updates(worker_target=22),
        source="MANUAL",
        reason="first",
    )

    with pytest.raises(RevisionConflict):
        repo.create_revision(
            expected_revision=current.revision - 1,
            profile=current.profile.with_updates(worker_target=21),
            source="MANUAL",
            reason="stale",
        )

    assert repo.current() == current


def test_pending_revision_activates_only_at_a_turn_boundary(tmp_path) -> None:
    repo = repository(tmp_path)
    current = repo.current()
    pending = repo.create_revision(
        expected_revision=current.revision,
        profile=current.profile.with_updates(worker_target=22),
        source="MANUAL",
        reason="next tick",
    )

    activated = repo.activate_pending(tick=7)

    assert activated is not None
    assert activated.revision == pending.revision
    assert activated.status == "ACTIVE"
    assert activated.activated_tick == 7
    assert repo.get(current.revision).status == "SUPERSEDED"


def test_rollback_creates_a_new_revision_with_rollback_source(tmp_path) -> None:
    repo = repository(tmp_path)
    original = repo.current()
    pending = repo.create_revision(
        expected_revision=original.revision,
        profile=original.profile.with_updates(worker_target=22),
        source="MANUAL",
        reason="change",
    )
    repo.activate_pending(tick=2)

    rollback = repo.rollback(
        expected_revision=pending.revision,
        target_revision=original.revision,
        reason="canary regression",
    )

    assert rollback.revision > pending.revision
    assert rollback.source == "ROLLBACK"
    assert rollback.status == "PENDING"
    assert rollback.profile == original.profile
