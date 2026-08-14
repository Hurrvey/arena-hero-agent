"""Production RuntimeManager factory and SQLite post-submit persistence adapter."""

from __future__ import annotations

import os
from threading import RLock

from adaptive_strategy import DisabledAdaptiveCoordinator, SkillBundleError, load_dotenv
from app.adaptive import SqliteAdaptiveCoordinator
from app.api.event_schema import service_event_envelope
from app.errors import AppError
from app.observability.redaction import PublicIdMapper
from app.storage import AdaptiveRepository, MetricsRepository, RuntimeStore, StrategyRepository
from app.strategy.planner_adapter import plan_turn

from .agent_runtime import AgentRuntime
from .account_lock import account_scope_from_api_key
from .exploration import ExplorationRuntime
from .client import sdk_client_factory
from .models import RuntimeBatch
from .runtime_manager import RuntimeManager
from .serialization import (
    json_value,
    serialize_public_explanation,
    serialize_public_plan,
    serialize_resolution_events,
    serialize_resolution_service_payload,
    serialize_turn,
)


class RuntimeServicesFactory:
    def __init__(
        self,
        *,
        settings,
        runtime_store: RuntimeStore,
        strategies: StrategyRepository,
        metrics: MetricsRepository,
        adaptive: AdaptiveRepository,
        broadcaster,
        exploration=None,
    ) -> None:
        self.settings = settings
        self.runtime_store = runtime_store
        self.strategies = strategies
        self.metrics = metrics
        self.adaptive = adaptive
        self.broadcaster = broadcaster
        self.exploration = exploration
        self._lock = RLock()
        self._session_id: str | None = None
        self._account_scope: str | None = None
        self._mapper: PublicIdMapper | None = None
        self._coordinator = None
        self._exploration_runtime: ExplorationRuntime | None = None

    @property
    def session_id(self) -> str | None:
        with self._lock:
            return self._session_id

    @property
    def account_scope(self) -> str | None:
        with self._lock:
            return self._account_scope

    def build(self) -> AgentRuntime:
        load_dotenv(self.settings.dotenv_path)
        api_key = os.environ.get("ARENA_HERO_API_KEY")
        if not api_key:
            raise AppError(
                "ARENA_HERO_KEY_MISSING",
                "Arena Hero API key is not configured in the local .env file",
                503,
            )
        account_scope = account_scope_from_api_key(api_key)
        session = self.runtime_store.create_session(account_hash=account_scope)
        with self._lock:
            self._session_id = session.session_id
            self._account_scope = account_scope
            self._mapper = PublicIdMapper(session.session_id)
        try:
            coordinator = SqliteAdaptiveCoordinator.from_env(
                repository=self.adaptive,
                strategies=self.strategies,
                env_path=self.settings.dotenv_path,
            )
        except (OSError, ValueError, SkillBundleError):
            coordinator = DisabledAdaptiveCoordinator()
        self._coordinator = coordinator
        exploration_runtime = (
            ExplorationRuntime(self.exploration, account_scope)
            if self.exploration is not None
            else None
        )
        self._exploration_runtime = exploration_runtime
        return AgentRuntime(
            api_key=api_key,
            client_factory=sdk_client_factory,
            planner=plan_turn,
            profile_provider=self.profile_for_tick,
            persistence=self.persist,
            adaptive_observer=self.observe_adaptive,
            lock_directory=self.settings.lock_directory,
            exploration=exploration_runtime,
        )

    def profile_for_tick(self, tick: int | None = None):
        if tick is not None:
            self.strategies.activate_pending(tick=tick)
        return self.strategies.current().profile

    def persist(self, batch: RuntimeBatch) -> None:
        with self._lock:
            session_id = self._session_id
            mapper = self._mapper
        if session_id is None or mapper is None:
            return
        if batch.kind == "RECEIPT":
            receipt = batch.receipt
            public_receipt = {
                "accepted": bool(getattr(receipt, "accepted", True)),
                "source": batch.source or "UNKNOWN",
            }
            received_at = getattr(receipt, "received_at", None)
            if received_at is not None:
                public_receipt["receivedAt"] = str(received_at)
            raw_received_plan = json_value(getattr(receipt, "plan", None))
            public_received_plan = (
                serialize_public_plan(raw_received_plan, mapper)
                if isinstance(raw_received_plan, dict)
                else None
            )
            event = self.runtime_store.save_receipt(
                session_id=session_id,
                tick=int(batch.tick or 0),
                receipt=public_receipt,
                raw_plan=raw_received_plan if isinstance(raw_received_plan, dict) else None,
                public_plan=public_received_plan,
            )
            self.broadcaster.publish_committed(service_event_envelope(event))
            return
        if batch.turn is None:
            return
        raw_state, public_state = serialize_turn(batch.turn, mapper)
        if batch.exploration is not None and self._exploration_runtime is not None:
            exploration_revision = self._exploration_runtime.persist(batch.exploration)
            public_state["visibility"] = {
                "tick": int(batch.exploration.tick),
                "currentCells": [
                    list(cell) for cell in sorted(batch.exploration.current_cells)
                ],
                "explorationRevision": int(exploration_revision),
            }
        resolution_events = serialize_resolution_events(batch.turn, mapper)
        previous_tick = max(0, int(batch.tick or 0) - 1)
        state_service = ("state.snapshot", {"paused": batch.result is None})
        resolution_service = (
            (
                (
                    "resolution.results",
                    serialize_resolution_service_payload(resolution_events),
                ),
            )
            if resolution_events
            else ()
        )
        if batch.result is None:
            events = self.runtime_store.save_turn_batch(
                session_id=session_id,
                tick=int(batch.tick or 0),
                raw_snapshot=raw_state,
                public_snapshot=public_state,
                raw_plan={},
                public_plan={},
                explanation={},
                resolution_events=resolution_events,
                service_events=(state_service, *resolution_service),
                plan_status="DRAFT",
                resolve_plan_tick=previous_tick,
            )
        else:
            raw_plan = json_value(batch.result.plan)
            raw_plan_mapping = raw_plan if isinstance(raw_plan, dict) else {"plan": raw_plan}
            public_state["defenseLevel"] = str(
                batch.result.diagnostics.defense.get("level", "CLEAR")
            ).upper()
            events = self.runtime_store.save_turn_batch(
                session_id=session_id,
                tick=int(batch.tick or 0),
                raw_snapshot=raw_state,
                public_snapshot=public_state,
                raw_plan=raw_plan_mapping,
                public_plan=serialize_public_plan(raw_plan_mapping, mapper),
                explanation=serialize_public_explanation(batch.result.explanation, mapper),
                resolution_events=resolution_events,
                service_events=(
                    state_service,
                    ("plan.accepted", {"source": batch.source or "AGENT"}),
                    *resolution_service,
                ),
                strategy_revision=self.strategies.current().revision,
                plan_status="ACCEPTED",
                resolve_plan_tick=previous_tick,
            )
        for event in events:
            self.broadcaster.publish_committed(service_event_envelope(event))
        self.metrics.save(
            session_id,
            int(batch.tick or 0),
            _metric_values(public_state),
        )

    def observe_adaptive(self, turn, receipt, result) -> None:
        coordinator = self._coordinator
        if coordinator is None:
            return
        observer = getattr(coordinator, "observe_snapshot_with_diagnostics", None)
        if callable(observer):
            diagnostics = {
                **dict(result.diagnostics.economy),
                "defense_level": result.diagnostics.defense.get("level", "CLEAR"),
                "incoming_core_damage": result.diagnostics.defense.get("incoming_damage", 0),
            }
            observer(turn, receipt, self.strategies.current().profile, diagnostics)
        else:
            coordinator.observe(turn, receipt)

    def close_adaptive(self) -> None:
        coordinator = self._coordinator
        self._coordinator = None
        if coordinator is not None:
            coordinator.close()


def build_runtime_manager(
    *,
    settings,
    runtime_store,
    strategies,
    metrics,
    adaptive,
    broadcaster,
    exploration=None,
):
    factory = RuntimeServicesFactory(
        settings=settings,
        runtime_store=runtime_store,
        strategies=strategies,
        metrics=metrics,
        adaptive=adaptive,
        broadcaster=broadcaster,
        exploration=exploration,
    )
    return RuntimeManager(factory.build), factory


def _metric_values(state: dict[str, object]) -> dict[str, float]:
    units = state.get("units")
    population = state.get("population")
    if population is None and isinstance(units, list):
        population = len(units)
    beacon = state.get("beacon")
    beacon_owned = 0.0
    if isinstance(beacon, dict):
        status = str(beacon.get("status", "")).upper()
        carrier = beacon.get("carrierId", beacon.get("carrier_id"))
        own_ids = {
            str(item.get("id"))
            for item in ([state.get("core")] + (units if isinstance(units, list) else []))
            if isinstance(item, dict) and item.get("id") is not None
        }
        beacon_owned = float(status == "CARRIED" and str(carrier) in own_ids)
    return {
        "resources": float(state.get("resources", 0) or 0),
        "population": float(population or 0),
        "beaconOwned": beacon_owned,
    }
