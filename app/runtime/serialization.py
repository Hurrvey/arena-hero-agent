"""Runtime event classification and public batch helpers."""

from __future__ import annotations

from .models import RuntimeBatch


def is_turn(event: object) -> bool:
    return hasattr(event, "submit") and hasattr(event, "tick")


def is_receipt(event: object) -> bool:
    kind = str(getattr(event, "kind", "")).upper()
    return kind == "RECEIVED" or (
        hasattr(event, "source") and hasattr(event, "accepted") and not hasattr(event, "submit")
    )


def receipt_batch(event: object) -> RuntimeBatch:
    source = str(getattr(getattr(event, "source", None), "value", getattr(event, "source", "")))
    return RuntimeBatch(
        "RECEIPT",
        int(getattr(event, "tick", 0)),
        receipt=event,
        source=source,
    )
