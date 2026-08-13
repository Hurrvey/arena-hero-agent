"""Ordered standard-library SQLite migrations."""

from __future__ import annotations

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS runtime_sessions (
            session_id TEXT PRIMARY KEY,
            account_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            last_tick INTEGER,
            error_code TEXT
        );

        CREATE TABLE IF NOT EXISTS turn_snapshots (
            session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id) ON DELETE CASCADE,
            tick INTEGER NOT NULL CHECK (tick >= 0),
            received_at TEXT NOT NULL,
            raw_payload_json TEXT NOT NULL,
            public_payload_json TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (session_id, tick)
        );

        CREATE TABLE IF NOT EXISTS plans (
            session_id TEXT NOT NULL,
            tick INTEGER NOT NULL,
            strategy_revision INTEGER,
            status TEXT NOT NULL,
            raw_plan_json TEXT NOT NULL,
            public_plan_json TEXT NOT NULL,
            explanation_json TEXT NOT NULL,
            receipt_json TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, tick),
            FOREIGN KEY (session_id, tick)
                REFERENCES turn_snapshots(session_id, tick) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS resolution_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id) ON DELETE CASCADE,
            plan_tick INTEGER NOT NULL,
            observed_tick INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            short_id TEXT,
            public_payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS service_events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id) ON DELETE CASCADE,
            tick INTEGER,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS strategy_profiles (
            revision INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            parent_revision INTEGER REFERENCES strategy_profiles(revision),
            profile_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            activated_tick INTEGER,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS one_active_strategy
            ON strategy_profiles(status) WHERE status = 'ACTIVE';
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_strategy
            ON strategy_profiles(status) WHERE status = 'PENDING';

        CREATE TABLE IF NOT EXISTS adaptive_cycles (
            cycle_id TEXT PRIMARY KEY,
            start_tick INTEGER NOT NULL,
            end_tick INTEGER NOT NULL,
            sample_count INTEGER NOT NULL,
            base_revision INTEGER NOT NULL REFERENCES strategy_profiles(revision),
            candidate_revision INTEGER REFERENCES strategy_profiles(revision),
            skill_fingerprint TEXT NOT NULL,
            raw_score REAL NOT NULL,
            normalized_score REAL NOT NULL,
            status TEXT NOT NULL,
            failure_code TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS adaptive_candidates (
            candidate_id TEXT PRIMARY KEY,
            cycle_id TEXT NOT NULL REFERENCES adaptive_cycles(cycle_id) ON DELETE CASCADE,
            base_revision INTEGER NOT NULL REFERENCES strategy_profiles(revision),
            profile_json TEXT NOT NULL,
            evaluator_report_json TEXT NOT NULL,
            response_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metric_points (
            session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id) ON DELETE CASCADE,
            tick INTEGER NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, tick, metric_name)
        );

        CREATE TABLE IF NOT EXISTS legacy_imports (
            source_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY (source_path, content_hash)
        );

        CREATE INDEX IF NOT EXISTS service_events_session_seq
            ON service_events(session_id, seq);
        CREATE INDEX IF NOT EXISTS resolution_events_session_tick
            ON resolution_events(session_id, observed_tick);
        CREATE INDEX IF NOT EXISTS metric_points_session_tick
            ON metric_points(session_id, tick);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS adaptive_observations (
            tick INTEGER PRIMARY KEY CHECK (tick >= 0),
            base_revision INTEGER NOT NULL REFERENCES strategy_profiles(revision),
            projection_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS adaptive_cycles_end_tick
            ON adaptive_cycles(end_tick);
        CREATE INDEX IF NOT EXISTS adaptive_candidates_cycle_status
            ON adaptive_candidates(cycle_id, status);
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS plan_receipts (
            session_id TEXT NOT NULL,
            tick INTEGER NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('AGENT', 'MANUAL')),
            status TEXT NOT NULL,
            receipt_json TEXT NOT NULL,
            raw_plan_json TEXT NOT NULL,
            public_plan_json TEXT NOT NULL,
            received_at TEXT NOT NULL,
            PRIMARY KEY (session_id, tick, source),
            FOREIGN KEY (session_id)
                REFERENCES runtime_sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS plan_receipts_received_at
            ON plan_receipts(received_at);
        """,
    ),
)
