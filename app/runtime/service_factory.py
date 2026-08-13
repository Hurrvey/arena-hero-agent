"""Production RuntimeManager factory and SQLite post-submit persistence adapter."""

from __future__ import annotations

import os
from threading import RLock

from adaptive_strategy import AdaptiveCoordinator, load_dotenv
from app.errors import AppError
from app.observability.redaction import PublicIdMapper
from app.storage import RuntimeStore, StrategyRepository
from app.strategy.planner_adapter import plan_turn

from .agent_runtime import AgentRuntime
from .client import sdk_client_factory
from .models import RuntimeBatch
from .runtime_manager import RuntimeManager
from .serialization import json_value, serialize_turn


class RuntimeServicesFactory:
    def __init__(
        self, *, settings, runtime_store: RuntimeStore, strategies: StrategyRepository, broadcaster
    ) -> None:
        self.settings = settings
        self.runtime_store = runtime_store
        self.strategies = strategies
        self.broadcaster = broadcaster
        self._lock = RLock()
        self._session_id: str | None = None
        self._mapper: PublicIdMapper | None = None
        self._coordinator = None

    @property
    def session_id(self) -> str | None:
        with self._lock:
            return self._session_id

    def build(self) -> AgentRuntime:
        load_dotenv(self.settings.dotenv_path)
        api_key = os.environ.get("ARENA_HERO_API_KEY")
        if not api_key:
            raise AppError(
                "ARENA_HERO_KEY_MISSING",
                "Arena Hero API key is not configured in the local .env file",
                503,
            )
        session = self.runtime_store.create_session(account_hash="configured")
        with self._lock:
            self._session_id = session.session_id
            self._mapper = PublicIdMapper(session.session_id)
        coordinator = AdaptiveCoordinator.from_env(self.settings.dotenv_path)
        self._coordinator = coordinator
        return AgentRuntime(
            api_key=api_key,
            client_factory=sdk_client_factory,
            planner=plan_turn,
            profile_provider=lambda: self.strategies.current().profile,
            persistence=self.persist,
            adaptive_observer=self.observe_adaptive,
            lock_directory=self.settings.lock_directory,
        )

    def persist(self, batch: RuntimeBatch) -> None:
        with self._lock:
            session_id = self._session_id
            mapper = self._mapper
        if session_id is None or mapper is None:
            return
        if batch.kind == "RECEIPT":
            event = self.runtime_store.append_service_event(
                session_id=session_id,
                tick=batch.tick,
                event_type="plan.received",
                payload={"source": batch.source or "UNKNOWN"},
            )
            self.broadcaster.publish_committed(_service_event(event))
            return
        if batch.turn is None:
            return
        raw_state, public_state = serialize_turn(batch.turn, mapper)
        if batch.result is None:
            events = self.runtime_store.save_turn_batch(
                session_id=session_id,
                tick=int(batch.tick or 0),
                raw_snapshot=raw_state,
                public_snapshot=public_state,
                raw_plan={},
                public_plan={},
                explanation={},
                resolution_events=(),
                service_events=(("turn.observed", {"paused": True}),),
                plan_status="DRAFT",
            )
        else:
            public_result = batch.result.public_mapping()
            raw_plan = json_value(batch.result.plan)
            events = self.runtime_store.save_turn_batch(
                session_id=session_id,
                tick=int(batch.tick or 0),
                raw_snapshot=raw_state,
                public_snapshot=public_state,
                raw_plan=raw_plan if isinstance(raw_plan, dict) else {"plan": raw_plan},
                public_plan=public_result["plan"],
                explanation={"actions": public_result["explanation"]},
                resolution_events=(),
                service_events=(("plan.accepted", {"source": batch.source or "AGENT"}),),
                strategy_revision=self.strategies.current().revision,
                plan_status="ACCEPTED",
            )
        for event in events:
            self.broadcaster.publish_committed(_service_event(event))

    def observe_adaptive(self, turn, receipt, result) -> None:
        coordinator = self._coordinator
        if coordinator is None:
            return
        observer = getattr(coordinator, "observe_snapshot_with_diagnostics", None)
        if callable(observer):
            observer(turn, receipt, self.strategies.current().profile, result.diagnostics.economy)
        else:
            coordinator.observe(turn, receipt)


def build_runtime_manager(*, settings, runtime_store, strategies, broadcaster):
    factory = RuntimeServicesFactory(
        settings=settings,
        runtime_store=runtime_store,
        strategies=strategies,
        broadcaster=broadcaster,
    )
    return RuntimeManager(factory.build), factory


def _service_event(event) -> dict[str, object]:
    return {
        "type": "event",
        "seq": event.seq,
        "sessionId": event.session_id,
        "tick": event.tick,
        "eventType": event.event_type,
        "payload": event.payload,
        "createdAt": event.created_at,
    }
