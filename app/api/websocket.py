"""Committed event replay and bounded live WebSocket fanout."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from threading import RLock

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from .event_schema import service_event_envelope

router = APIRouter()


@dataclass(frozen=True, slots=True)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, object]]


@dataclass(slots=True)
class CommittedEventBroadcaster:
    queue_size: int = 256
    _clients: set[_Subscriber] = field(default_factory=set)
    _lock: RLock = field(default_factory=RLock)

    def subscribe(self) -> _Subscriber:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(self.queue_size)
        subscriber = _Subscriber(asyncio.get_running_loop(), queue)
        with self._lock:
            self._clients.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: _Subscriber) -> None:
        with self._lock:
            self._clients.discard(subscriber)

    def _offer(self, subscriber: _Subscriber, event: dict[str, object]) -> None:
        try:
            subscriber.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.unsubscribe(subscriber)
            try:
                while True:
                    subscriber.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            subscriber.queue.put_nowait({"type": "_eventGap", "maxSeq": int(event.get("seq", 0))})

    def publish_committed(self, event: dict[str, object]) -> int:
        with self._lock:
            clients = tuple(self._clients)
        scheduled = 0
        for subscriber in clients:
            try:
                subscriber.loop.call_soon_threadsafe(self._offer, subscriber, event)
                scheduled += 1
            except RuntimeError:
                self.unsubscribe(subscriber)
        return scheduled

    def publish_uncommitted(self, _event: dict[str, object]) -> bool:
        return False


@router.websocket("/ws/v1/live")
async def live(websocket: WebSocket, after_seq: int = Query(default=0, alias="afterSeq", ge=0)):
    await websocket.accept()
    services = websocket.app.state.services
    store = services.runtime_store
    configured_limit = services.settings.websocket_replay_limit
    subscriber = services.broadcaster.subscribe()
    try:
        max_seq = store.latest_event_seq()
        await websocket.send_json({"type": "hello", "schemaVersion": 1, "maxSeq": max_seq})
        if max_seq - after_seq > configured_limit:
            await websocket.send_json(
                {"type": "eventGap", "afterSeq": after_seq, "maxSeq": max_seq}
            )
            await websocket.close(code=4009)
            return
        replay = store.events_after(after_seq, limit=configured_limit)
        last_sent = after_seq
        for event in replay.events:
            await websocket.send_json(service_event_envelope(event))
            last_sent = event.seq

        while True:
            event_task = asyncio.create_task(subscriber.queue.get())
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
            event = event_task.result()
            if event.get("type") == "_eventGap":
                await websocket.send_json(
                    {
                        "type": "eventGap",
                        "afterSeq": last_sent,
                        "maxSeq": int(event.get("maxSeq", last_sent)),
                    }
                )
                await websocket.close(code=4009)
                return
            seq = int(event.get("seq", 0))
            if seq <= last_sent:
                continue
            await websocket.send_json(event)
            last_sent = seq
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        return
    finally:
        services.broadcaster.unsubscribe(subscriber)
