"""Committed event replay and bounded live WebSocket fanout."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

router = APIRouter()


@dataclass(slots=True)
class CommittedEventBroadcaster:
    queue_size: int = 256
    _clients: set[asyncio.Queue[dict[str, object]]] = field(default_factory=set)

    def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(self.queue_size)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        self._clients.discard(queue)

    def publish_committed(self, event: dict[str, object]) -> int:
        delivered = 0
        stale: list[asyncio.Queue[dict[str, object]]] = []
        for queue in tuple(self._clients):
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self.unsubscribe(queue)
        return delivered

    def publish_uncommitted(self, _event: dict[str, object]) -> bool:
        return False


def _public_event(event) -> dict[str, object]:
    return {
        "type": "event",
        "seq": event.seq,
        "sessionId": event.session_id,
        "tick": event.tick,
        "eventType": event.event_type,
        "payload": event.payload,
        "createdAt": event.created_at,
    }


@router.websocket("/ws/v1/live")
async def live(websocket: WebSocket, after_seq: int = Query(default=0, alias="afterSeq", ge=0)):
    await websocket.accept()
    services = websocket.app.state.services
    store = services.runtime_store
    configured_limit = services.settings.websocket_replay_limit
    first_page = store.events_after(0, limit=1000)
    max_seq = first_page.last_seq
    await websocket.send_json({"type": "hello", "schemaVersion": 1, "maxSeq": max_seq})
    if max_seq - after_seq > configured_limit:
        await websocket.send_json({"type": "eventGap", "afterSeq": after_seq, "maxSeq": max_seq})
        await websocket.close(code=4009)
        return
    replay = store.events_after(after_seq, limit=configured_limit)
    for event in replay.events:
        await websocket.send_json(_public_event(event))

    queue = services.broadcaster.subscribe()
    try:
        while True:
            event_task = asyncio.create_task(queue.get())
            disconnect_task = asyncio.create_task(websocket.receive())
            done, pending = await asyncio.wait(
                {event_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if disconnect_task in done:
                break
            await websocket.send_json(event_task.result())
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        return
    finally:
        services.broadcaster.unsubscribe(queue)
