import json

from app.adaptive.legacy_import import LegacyImporter
from app.storage.database import Database
from app.storage.strategy_repository import StrategyRepository
from strategy_policy import StrategyProfile


def importer(tmp_path, legacy_dir):
    database = Database(tmp_path / "agent.db")
    database.initialize()
    repository = StrategyRepository(database)
    repository.ensure_initial(StrategyProfile.default())
    return database, repository, LegacyImporter(database, repository, legacy_dir)


def test_valid_legacy_active_profile_is_imported_once(tmp_path) -> None:
    legacy = tmp_path / "adaptive"
    legacy.mkdir()
    profile = StrategyProfile.default().with_updates(worker_target=20)
    state = legacy / "state.json"
    state.write_text(json.dumps({"active_profile": profile.to_mapping()}), encoding="utf-8")
    _database, repo, migration = importer(tmp_path / "new", legacy)

    result = migration.run()

    assert result.imported == 1
    assert repo.pending().profile.worker_target == 20
    assert state.exists()


def test_reimport_is_idempotent_by_source_hash(tmp_path) -> None:
    legacy = tmp_path / "adaptive"
    legacy.mkdir()
    (legacy / "state.json").write_text(
        json.dumps({"active_profile": StrategyProfile.default().to_mapping()}),
        encoding="utf-8",
    )
    _database, _repo, migration = importer(tmp_path / "new", legacy)

    first = migration.run()
    second = migration.run()

    assert first.imported == 1
    assert second.imported == 0
    assert second.skipped == 1


def test_invalid_or_oversized_legacy_file_warns_without_mutation(tmp_path) -> None:
    legacy = tmp_path / "adaptive"
    legacy.mkdir()
    state = legacy / "state.json"
    state.write_bytes(b"{" + b"x" * 1_100_000)
    _database, repo, migration = importer(tmp_path / "new", legacy)
    before = repo.current()

    result = migration.run()

    assert result.imported == 0
    assert result.warnings == ("legacy state ignored: invalid or oversized",)
    assert repo.current() == before
    assert state.exists()


def test_import_never_deletes_or_renames_legacy_files(tmp_path) -> None:
    legacy = tmp_path / "adaptive"
    legacy.mkdir()
    state = legacy / "state.json"
    telemetry = legacy / "telemetry.jsonl"
    state.write_text(
        json.dumps({"active_profile": StrategyProfile.default().to_mapping()}),
        encoding="utf-8",
    )
    telemetry.write_text('{"tick": 1}\n', encoding="utf-8")
    before = {path.name: path.read_bytes() for path in legacy.iterdir()}
    _database, _repo, migration = importer(tmp_path / "new", legacy)

    migration.run()

    assert {path.name: path.read_bytes() for path in legacy.iterdir()} == before
