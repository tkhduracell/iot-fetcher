import asyncio

import pytest

from ai_brain.events import EventBus


async def test_two_subscribers_each_get_every_event():
    bus = EventBus()
    with bus.subscribe() as a, bus.subscribe() as b:
        bus.publish({"type": "x"})
        assert await a.get() == {"type": "x"}
        assert await b.get() == {"type": "x"}


async def test_an_unsubscribed_queue_stops_receiving():
    bus = EventBus()
    with bus.subscribe() as queue:
        bus.publish({"type": "seen"})
        assert await queue.get() == {"type": "seen"}
    bus.publish({"type": "unseen"})
    assert queue.empty()


async def test_subscriber_count_reflects_active_subscriptions():
    bus = EventBus()
    assert bus.subscriber_count() == 0
    with bus.subscribe():
        assert bus.subscriber_count() == 1
        with bus.subscribe():
            assert bus.subscriber_count() == 2
        assert bus.subscriber_count() == 1
    assert bus.subscriber_count() == 0


async def test_a_full_queue_drops_the_oldest_and_reports_the_count():
    bus = EventBus(maxsize=2)
    with bus.subscribe() as queue:
        bus.publish({"type": "a"})
        bus.publish({"type": "b"})
        bus.publish({"type": "c"})  # queue was full; "a" is dropped for this one
        first = await queue.get()
        second = await queue.get()
        assert first == {"type": "b"}
        assert second == {"type": "c", "dropped": 1}
        assert queue.empty()


async def test_a_broken_subscriber_does_not_stop_delivery_to_others():
    bus = EventBus()
    with bus.subscribe() as good:

        class ExplodingQueue:
            def put_nowait(self, _item):
                raise RuntimeError("boom")

        bus._subscribers.add(ExplodingQueue())
        bus.publish({"type": "x"})
        assert await good.get() == {"type": "x"}


async def test_publish_with_no_subscribers_does_nothing():
    bus = EventBus()
    bus.publish({"type": "x"})  # must not raise


async def test_publish_is_synchronous_and_returns_immediately():
    """``loop.py`` calls this with no ``await`` on its own cycle path -- a
    coroutine here would silently never run without one."""
    bus = EventBus()
    with bus.subscribe() as queue:
        result = bus.publish({"type": "x"})
        assert result is None
        assert not asyncio.iscoroutine(bus.publish)
        assert queue.qsize() == 1
