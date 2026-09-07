import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from ai_brain.config import load_settings
from ai_brain.executors import Executors, QuietHours, in_quiet_hours

ENV = {
    "SONOS_URL": "http://sonos:5005",
    "SONOS_ROOM": "Kitchen",
    "HA_URL": "http://ha:8123",
    "HA_TOKEN": "ha-token",
    "HA_TODO_LIST": "todo.shopping_list",
}

# 10:00 Stockholm (CEST, UTC+2) -- well outside quiet hours.
AWAKE = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)


def stockholm(hour: int, minute: int = 0) -> datetime:
    """A UTC instant that is ``hour:minute`` in Stockholm during CEST (UTC+2)."""
    return datetime(2026, 9, 6, hour, minute, tzinfo=timezone.utc) - timedelta(hours=2)


@pytest.fixture
def settings():
    return load_settings(ENV)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def make_executors(settings, http, now=AWAKE):
    return Executors(settings, http, lambda: now)


# --- quiet hours ----------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "minute", "quiet"),
    [
        (21, 59, False),
        (22, 0, True),
        (23, 30, True),
        (6, 59, True),
        (7, 0, False),
        (12, 0, False),
    ],
)
def test_quiet_hours_boundaries(hour, minute, quiet):
    assert in_quiet_hours(stockholm(hour, minute)) is quiet


def test_quiet_hours_converts_to_the_local_zone():
    # 21:00 UTC is 23:00 in Stockholm during CEST: quiet locally, not in UTC.
    assert in_quiet_hours(datetime(2026, 9, 6, 21, 0, tzinfo=timezone.utc)) is True


def test_quiet_hours_honours_an_explicit_zone():
    # The same instant is 21:00 in London, which is not quiet.
    assert in_quiet_hours(datetime(2026, 9, 6, 21, 0, tzinfo=timezone.utc), tz="UTC") is False


# --- sonos_say ------------------------------------------------------------


@respx.mock
async def test_sonos_say_calls_the_http_api(settings, http):
    route = respx.get("http://sonos:5005/Kitchen/say/hello%20there").mock(
        return_value=httpx.Response(200, text="queued")
    )
    assert await make_executors(settings, http).sonos_say("hello there") == "queued"
    assert route.called


@respx.mock
async def test_sonos_say_quotes_the_room_and_text(settings, http):
    env = dict(ENV, SONOS_ROOM="Living Room")
    route = respx.get(
        "http://sonos:5005/Living%20Room/say/caf%C3%A9%20%2B%20bulle"
    ).mock(return_value=httpx.Response(200, text="ok"))
    await make_executors(load_settings(env), http).sonos_say("café + bulle")
    assert route.called


@respx.mock
async def test_sonos_say_raises_on_a_non_2xx(settings, http):
    respx.get(url__startswith="http://sonos:5005/").mock(return_value=httpx.Response(503))
    with pytest.raises(RuntimeError, match="503"):
        await make_executors(settings, http).sonos_say("hi")


@respx.mock
async def test_sonos_say_refuses_during_quiet_hours(settings, http):
    with pytest.raises(QuietHours):
        await make_executors(settings, http, now=stockholm(23, 0)).sonos_say("hi")
    assert not respx.calls


@respx.mock
async def test_sonos_say_dry_run_never_calls_http(settings, http, caplog):
    dry = load_settings(dict(ENV, DRY_RUN="1"))
    with caplog.at_level("INFO"):
        assert await make_executors(dry, http).sonos_say("hi") == "dry-run"
    assert not respx.calls
    assert "[dry-run] sonos_say" in caplog.text


@respx.mock
async def test_sonos_say_checks_quiet_hours_before_dry_run(settings, http):
    dry = load_settings(dict(ENV, DRY_RUN="1"))
    with pytest.raises(QuietHours):
        await make_executors(dry, http, now=stockholm(3, 0)).sonos_say("hi")


# --- ha_todo_add ----------------------------------------------------------


@respx.mock
async def test_ha_todo_add_posts_the_right_json(settings, http):
    route = respx.post("http://ha:8123/api/services/todo/add_item").mock(
        return_value=httpx.Response(200, json=[])
    )
    assert await make_executors(settings, http).ha_todo_add("milk") == "added: milk"
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer ha-token"
    assert json.loads(request.content) == {
        "entity_id": "todo.shopping_list",
        "item": "milk",
    }


@respx.mock
async def test_ha_todo_add_works_during_quiet_hours(settings, http):
    respx.post("http://ha:8123/api/services/todo/add_item").mock(
        return_value=httpx.Response(200, json=[])
    )
    out = await make_executors(settings, http, now=stockholm(3, 0)).ha_todo_add("milk")
    assert out == "added: milk"


@respx.mock
async def test_ha_todo_add_raises_on_a_non_2xx(settings, http):
    respx.post("http://ha:8123/api/services/todo/add_item").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(RuntimeError, match="401"):
        await make_executors(settings, http).ha_todo_add("milk")


@respx.mock
async def test_ha_todo_add_dry_run_never_calls_http(settings, http, caplog):
    dry = load_settings(dict(ENV, DRY_RUN="1"))
    with caplog.at_level("INFO"):
        assert await make_executors(dry, http).ha_todo_add("milk") == "dry-run"
    assert not respx.calls
    assert "[dry-run] ha_todo_add" in caplog.text


# --- run ------------------------------------------------------------------


@respx.mock
async def test_run_dispatches_by_kind(settings, http):
    respx.get(url__startswith="http://sonos:5005/").mock(
        return_value=httpx.Response(200, text="queued")
    )
    respx.post("http://ha:8123/api/services/todo/add_item").mock(
        return_value=httpx.Response(200, json=[])
    )
    ex = make_executors(settings, http)
    assert await ex.run("sonos_say", {"text": "hi"}) == "queued"
    assert await ex.run("ha_todo_add", {"item": "milk"}) == "added: milk"


async def test_run_rejects_an_unknown_kind(settings, http):
    with pytest.raises(ValueError, match="unknown kind"):
        await make_executors(settings, http).run("launch_missiles", {})


async def test_run_rejects_a_payload_missing_its_key(settings, http):
    with pytest.raises(ValueError, match="text"):
        await make_executors(settings, http).run("sonos_say", {"nope": "hi"})
