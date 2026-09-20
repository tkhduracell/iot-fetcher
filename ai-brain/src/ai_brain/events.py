"""A small in-process pub/sub so a loop's rounds can be watched live.

``loop.py`` knows nothing about HTTP or SSE; it only calls ``publish`` with a
plain dict, the same instinct that keeps the tool registry ignorant of Slack.
``api.py`` is the only reader -- it subscribes, forwards each event to an SSE
response, and unsubscribes when the connection drops.

Two properties matter because ``publish`` runs inline on the cycle's own path,
with no ``await`` between a round landing and the call:

* **Never blocks.** ``publish`` is synchronous and every queue write is
  ``put_nowait``. A subscriber that stops reading -- a backgrounded browser
  tab -- must not stall the loop that produced the event.
* **Never raises into the caller.** A broken subscriber is dropped from this
  event's delivery, logged, and never allowed to break the others or bubble
  into ``run_cycle``. The events themselves are a convenience; losing one
  changes nothing a reader could not also get from the next poll of
  ``/trace``, so failing loudly here would cost more than it protects.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager

log = logging.getLogger(__name__)

# Six loops each mid-round, each round a few hundred bytes of JSON, is a
# bursty but small event rate. This is a safety cap against a subscriber that
# never reads, not a sizing decision for the normal case.
DEFAULT_MAXSIZE = 200


class EventBus:
    def __init__(self, maxsize: int = DEFAULT_MAXSIZE) -> None:
        self._maxsize = maxsize
        self._subscribers: set[asyncio.Queue] = set()

    def publish(self, event: dict) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest, not the newest: a slow reader should lose
                # history, never miss what just happened. The dropped count
                # rides on the next event so a reader can tell its stream had
                # a gap and re-sync from a snapshot instead of trusting a
                # feed it silently fell behind on.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                event = {**event, "dropped": event.get("dropped", 0) + 1}
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    log.warning("[events] subscriber queue still full after drop, skipping")
            except Exception:
                log.warning("[events] publish to one subscriber failed", exc_info=True)

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def subscriber_count(self) -> int:
        return len(self._subscribers)
