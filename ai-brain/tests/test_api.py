"""The read-only introspection API.

Everything here drives a real aiohttp application through
``aiohttp.test_utils``, so the routing, the status codes and the JSON encoding
are the ones production serves -- no handler is called directly.
"""

import json
import socket

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from conftest import env, fake_chain

from ai_brain.api import build_app, start_api, stop_api
from ai_brain.config import load_settings
from ai_brain.loop import CycleResult, CycleTrace, RoundTrace
from ai_brain.supervisor import build

STARTED_AT = 1_000_000.0
NOW = STARTED_AT + 3600.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


BOT_TOKEN = "xoxb-super-secret-bot"
APP_TOKEN = "xapp-super-secret-app"
GEMINI_KEY = "gemini-super-secret-key"
INFLUX = "influx-super-secret"


@pytest.fixture
def system(tmp_path):
    settings = load_settings(env(tmp_path, EXPERTS="energy"))
    return build(settings, chain_factory=fake_chain, clock=lambda: NOW)


@pytest.fixture
async def client(system):
    app = build_app(system, started_at=STARTED_AT, clock=lambda: NOW)
    async with TestClient(TestServer(app)) as test_client:
        yield test_client


async def get_json(client, path: str, expect: int = 200) -> dict:
    response = await client.get(path)
    assert response.status == expect, await response.text()
    assert response.headers["Cache-Control"] == "no-store"
    return await response.json()


# -- healthz -----------------------------------------------------------


async def test_healthz_reports_uptime_and_the_loops(client):
    body = await get_json(client, "/healthz")
    assert body["ok"] is True
    assert body["uptime_s"] == 3600
    assert sorted(body["loops"]) == ["brain", "energy"]


# -- status ------------------------------------------------------------


async def test_status_reports_the_supervisor_at_a_glance(client, system):
    body = await get_json(client, "/api/status")

    assert body["uptime_s"] == 3600
    assert body["now"] == NOW
    assert body["paused"] is False
    assert body["pause_file"].endswith("PAUSE")
    assert body["slack"] == {
        "configured": False,
        "connected": False,
        "queued": 0,
        "sessions": 0,
    }
    assert body["proposals"] == {"pending": 0, "total": 0}
    assert body["ledger"]["day"] == system.ledger.day
    assert [key["key"] for key in body["ledger"]["keys"]] == ["fake:a", "fake:b"]
    first = body["ledger"]["keys"][0]
    assert first["requests_day"] == 0
    assert first["requests_remaining"] == 1.0
    assert first["tokens_remaining"] == 1.0
    # The denominators the bar needs: rpd as-is, and the ledger's synthetic
    # daily token budget rather than tpm.
    limits = system.ledger.limits("fake:a")
    assert first["requests_limit"] == limits.rpd
    assert first["tokens_limit"] == limits.daily_tokens
    assert first["tokens_limit"] == limits.tpm * 60 * 24 // 10
    assert first["consecutive_429"] == 0
    assert first["blocked_until"] is None
    assert first["disabled_until"] is None
    assert first["recent_requests"] == 0


async def test_status_settings_are_an_allowlist_not_the_whole_dataclass(client):
    settings = (await get_json(client, "/api/status"))["settings"]
    assert set(settings) == {
        "llm_chain",
        "experts",
        "brain_heartbeat_s",
        "expert_heartbeat_s",
        "call_timeout_s",
        "dry_run",
        "rpm",
        "tpm",
        "rpd",
        "memory_root",
    }
    assert settings["llm_chain"] == ["fake:a", "fake:b"]
    assert settings["experts"] == ["energy"]
    assert settings["dry_run"] is False


async def test_status_never_leaks_a_token(tmp_path):
    """The one thing a no-auth endpoint on the LAN must never do."""
    settings = load_settings(
        env(
            tmp_path,
            LLM_CHAIN="gemini:flash",
            GEMINI_API_KEY=GEMINI_KEY,
            INFLUX_TOKEN=INFLUX,
            SLACK_BOT_TOKEN=BOT_TOKEN,
            SLACK_APP_TOKEN=APP_TOKEN,
            SLACK_USER_ID="U-filip",
            BRAVE_API_KEY="brave-super-secret",
            HA_TOKEN="ha-super-secret",
        )
    )
    system = build(settings, chain_factory=fake_chain, clock=lambda: NOW)
    app = build_app(system, started_at=STARTED_AT, clock=lambda: NOW)

    async with TestClient(TestServer(app)) as test_client:
        response = await test_client.get("/api/status")
        text = await response.text()

    assert response.status == 200
    secrets = (BOT_TOKEN, APP_TOKEN, GEMINI_KEY, INFLUX, "brave-super-secret", "ha-super-secret")
    for secret in secrets:
        assert secret not in text
    assert "super-secret" not in text


async def test_status_shows_a_pause(client, system):
    system.settings.memory_root.joinpath("PAUSE").write_text("stop", encoding="utf-8")
    assert (await get_json(client, "/api/status"))["paused"] is True


async def test_status_counts_proposals(client, system, tmp_path):
    outbox = system.memories["brain"].outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    for name, status in (("20260906T100000-a", "pending"), ("20260906T110000-b", "executed")):
        (outbox / f"{name}.json").write_text(
            json.dumps(
                {
                    "id": name,
                    "kind": "sonos_say",
                    "payload": {"text": "hi"},
                    "reason": "why",
                    "topic": "#home",
                    "created": "2026-09-06T10:00:00Z",
                    "status": status,
                }
            ),
            encoding="utf-8",
        )

    assert (await get_json(client, "/api/status"))["proposals"] == {"pending": 1, "total": 2}


# -- agents ------------------------------------------------------------


def _write_journal(memory, days: dict[str, str]) -> None:
    memory.journal_dir.mkdir(parents=True, exist_ok=True)
    for day, text in days.items():
        (memory.journal_dir / f"{day}.md").write_text(text, encoding="utf-8")


async def test_agents_lists_the_brain_first(client):
    agents = (await get_json(client, "/api/agents"))["agents"]
    assert [a["name"] for a in agents] == ["brain", "energy"]
    brain = agents[0]
    assert brain["priority"] == "brain"
    assert brain["heartbeat_s"] == 1800
    assert brain["last_cycle"] is None
    assert brain["last_cycle_at"] is None
    assert brain["next_wake_at"] is None
    assert brain["cycle_counts"] == {}
    assert brain["in_progress"] is False
    assert brain["needs_compaction"] is False
    assert brain["facts"] == 0
    assert brain["unread_notes"] == 0


async def test_agents_reports_the_last_cycle_and_the_next_wake(client, system):
    loop = system.loops["brain"]
    loop.last_cycle = CycleResult(status="ok", model="fake:1", rounds=3, next_wake_s=1800)
    loop.last_cycle_at = NOW - 60
    loop.cycle_counts = {"ok": 4, "error": 1}
    loop.trace = CycleTrace(started_at=NOW - 60)

    brain = (await get_json(client, "/api/agents"))["agents"][0]

    assert brain["last_cycle"] == {
        "status": "ok",
        "model": "fake:1",
        "rounds": 3,
        "next_wake_s": 1800,
    }
    assert brain["last_cycle_at"] == NOW - 60
    assert brain["next_wake_at"] == NOW - 60 + 1800
    assert brain["cycle_counts"] == {"ok": 4, "error": 1}
    assert brain["in_progress"] is True


async def test_agents_counts_facts_and_unread_notes(client, system):
    brain_memory = system.memories["brain"]
    brain_memory.write_fact("pool", "cold")
    brain_memory.write_fact("spa", "warm")
    brain_memory.drop_note("filip", "hello")

    brain = (await get_json(client, "/api/agents"))["agents"][0]
    assert brain["facts"] == 2
    assert brain["unread_notes"] == 1


# -- one agent ---------------------------------------------------------


async def test_agent_detail_adds_identity_goals_facts_and_inbox(client, system):
    memory = system.memories["brain"]
    memory.rewrite_identity("I am the brain")
    memory.rewrite_goals("keep the house warm")
    memory.write_fact("pool", "cold")
    memory.drop_note("filip", "check the pool")
    _write_journal(memory, {"2026-09-06": "10:00  [ok] fine\n"})

    body = await get_json(client, "/api/agents/brain")

    assert body["name"] == "brain"
    assert body["is_brain"] is True
    assert body["identity"] == "I am the brain"
    assert body["goals"] == "keep the house warm"
    assert body["fact_names"] == ["pool"]
    assert body["journal_days"] == ["2026-09-06"]
    assert body["trace"] is None
    assert len(body["notes"]) == 1
    note = body["notes"][0]
    assert note["sender"] == "filip"
    assert note["body"] == "check the pool"
    assert "/" not in note["file"]
    assert note["file"].endswith(".md")


async def test_an_expert_has_no_goals(client, system):
    body = await get_json(client, "/api/agents/energy")
    assert body["is_brain"] is False
    assert body["goals"] == ""


async def test_an_unknown_agent_is_a_404(client):
    body = await get_json(client, "/api/agents/nope", expect=404)
    assert body == {"error": "unknown agent: nope"}


async def test_an_unknown_agent_name_cannot_break_the_json_body(client):
    """The name is URL input, so it is encoded, never interpolated."""
    response = await client.get('/api/agents/he%22llo')
    assert response.status == 404
    # Parses at all -- an f-string body would have emitted invalid JSON here.
    assert await response.json() == {"error": 'unknown agent: he"llo'}


# -- journal -----------------------------------------------------------


async def test_journal_returns_the_newest_days_first(client, system):
    _write_journal(
        system.memories["brain"],
        {
            "2026-09-04": "10:00  oldest\n",
            "2026-09-05": "10:00  middle\n",
            "2026-09-06": "10:00  newest\n10:30  also newest\n",
        },
    )

    body = await get_json(client, "/api/agents/brain/journal?days=2")

    assert body["agent"] == "brain"
    assert body["days"] == 2
    assert [entry["date"] for entry in body["entries"]] == ["2026-09-06", "2026-09-05"]
    assert body["entries"][0]["lines"] == ["10:00  newest", "10:30  also newest"]


async def test_journal_defaults_to_three_days(client, system):
    _write_journal(
        system.memories["brain"],
        {f"2026-09-0{i}": f"10:00  day {i}\n" for i in range(1, 7)},
    )
    body = await get_json(client, "/api/agents/brain/journal")
    assert body["days"] == 3
    assert [entry["date"] for entry in body["entries"]] == [
        "2026-09-06",
        "2026-09-05",
        "2026-09-04",
    ]


@pytest.mark.parametrize(("asked", "clamped"), [("0", 1), ("-5", 1), ("999", 30)])
async def test_journal_days_is_clamped(client, asked, clamped):
    body = await get_json(client, f"/api/agents/brain/journal?days={asked}")
    assert body["days"] == clamped


async def test_journal_days_must_be_an_integer(client):
    body = await get_json(client, "/api/agents/brain/journal?days=lots", expect=400)
    assert "days" in body["error"]


async def test_journal_of_an_unknown_agent_is_a_404(client):
    await get_json(client, "/api/agents/nope/journal", expect=404)


async def test_journal_skips_a_file_pruned_between_listing_and_reading(
    client, system, monkeypatch
):
    """Compaction can delete a day after the glob and before the read."""
    memory = system.memories["brain"]
    _write_journal(
        memory,
        {
            "2026-09-05": "10:00  survives\n",
            "2026-09-06": "10:00  about to vanish\n",
        },
    )

    real_days = memory.journal_days

    def vanishing_days():
        days = real_days()
        # Delete the newest file the moment the handler asks for the list --
        # exactly the window a concurrent prune runs in.
        (memory.journal_dir / "2026-09-06.md").unlink()
        return days

    monkeypatch.setattr(memory, "journal_days", vanishing_days)

    body = await get_json(client, "/api/agents/brain/journal?days=2")

    assert [entry["date"] for entry in body["entries"]] == ["2026-09-05"]
    assert body["entries"][0]["lines"] == ["10:00  survives"]


# -- trace -------------------------------------------------------------


async def test_trace_is_null_before_the_first_cycle(client):
    assert await get_json(client, "/api/agents/brain/trace") == {"agent": "brain", "trace": None}


async def test_trace_renders_the_live_cycle(client, system):
    system.loops["brain"].trace = CycleTrace(
        started_at=NOW - 10,
        model="fake:1",
        rounds=[
            RoundTrace(
                at=NOW - 9,
                text="looking",
                tool_calls=[{"name": "list_facts", "args": {}}],
                tool_results=[{"name": "list_facts", "result_preview": "[]"}],
            )
        ],
    )

    trace = (await get_json(client, "/api/agents/brain/trace"))["trace"]

    assert trace["started_at"] == NOW - 10
    assert trace["finished_at"] is None
    assert trace["in_progress"] is True
    assert trace["status"] is None
    assert trace["rounds"][0]["text"] == "looking"
    assert trace["rounds"][0]["tool_calls"] == [{"name": "list_facts", "args": {}}]
    assert trace["rounds"][0]["tool_results"] == [{"name": "list_facts", "result_preview": "[]"}]


# -- facts -------------------------------------------------------------


async def test_a_fact_is_returned_by_name(client, system):
    system.memories["brain"].write_fact("pool", "the pool is cold")
    body = await get_json(client, "/api/agents/brain/facts/pool")
    assert body == {"agent": "brain", "name": "pool", "body": "the pool is cold"}


async def test_a_missing_fact_is_a_404(client):
    body = await get_json(client, "/api/agents/brain/facts/nothing", expect=404)
    assert "nothing" in body["error"]


@pytest.mark.parametrize("name", ["Pool", "a" * 80, "no spaces"])
async def test_an_unsafe_fact_name_is_a_400(client, name):
    """safe_name is the gate, so a name it rejects never reaches the disk."""
    body = await get_json(client, f"/api/agents/brain/facts/{name}", expect=400)
    assert "unsafe memory name" in body["error"]


async def test_a_traversing_fact_name_cannot_read_outside_the_facts_dir(client, system):
    (system.settings.memory_root / "constitution.md").write_text("secret", encoding="utf-8")
    response = await client.get("/api/agents/brain/facts/..%2F..%2Fconstitution")
    assert response.status == 400
    assert "secret" not in await response.text()


# -- proposals ---------------------------------------------------------


async def test_proposals_are_newest_first_with_counts(client, system):
    outbox = system.memories["brain"].outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    for name, status in (
        ("20260906T100000-a", "executed"),
        ("20260906T110000-b", "pending"),
        ("20260906T120000-c", "pending"),
    ):
        (outbox / f"{name}.json").write_text(
            json.dumps(
                {
                    "id": name,
                    "kind": "sonos_say",
                    "payload": {"text": name},
                    "reason": "why",
                    "topic": "#home",
                    "created": "2026-09-06T10:00:00Z",
                    "status": status,
                }
            ),
            encoding="utf-8",
        )

    body = await get_json(client, "/api/proposals")

    assert [p["id"] for p in body["proposals"]] == [
        "20260906T120000-c",
        "20260906T110000-b",
        "20260906T100000-a",
    ]
    assert body["pending"] == 2
    assert body["total"] == 3


async def test_proposals_is_empty_when_nothing_was_ever_proposed(client):
    assert await get_json(client, "/api/proposals") == {
        "proposals": [],
        "pending": 0,
        "total": 0,
    }


# -- slack -------------------------------------------------------------


async def test_slack_sessions_is_a_200_when_slack_is_off(client):
    assert await get_json(client, "/api/slack/sessions") == {
        "configured": False,
        "queued": 0,
        "sessions": [],
    }


async def test_slack_sessions_lists_the_topic_threads(client, system):
    class FakeOut:
        def sessions(self):
            return {
                "pool": {"thread_ts": "1.1", "channel": "D1", "status": "active"},
                "spa": {"thread_ts": "2.2", "channel": "D1", "status": "processing"},
            }

        def queued_count(self):
            return 3

    system.slack_out = FakeOut()

    body = await get_json(client, "/api/slack/sessions")

    assert body["configured"] is True
    assert body["queued"] == 3
    assert body["sessions"] == [
        {"topic": "pool", "thread_ts": "1.1", "channel": "D1", "status": "active"},
        {"topic": "spa", "thread_ts": "2.2", "channel": "D1", "status": "processing"},
    ]


# -- method and error handling -----------------------------------------


async def test_the_api_is_read_only(client):
    for path in ("/api/status", "/api/agents", "/api/proposals"):
        response = await client.post(path, json={})
        assert response.status == 405, path


async def test_an_unexpected_failure_is_a_plain_500(client, system, monkeypatch):
    def _boom():
        raise RuntimeError("token=super-secret-value")

    monkeypatch.setattr(type(system.ledger), "keys", lambda _self: _boom())

    response = await client.get("/api/status")
    text = await response.text()

    assert response.status == 500
    assert json.loads(text) == {"error": "internal error"}
    assert "super-secret-value" not in text
    assert "Traceback" not in text


# -- start / stop ------------------------------------------------------


async def test_start_api_is_disabled_by_a_non_positive_port(system):
    assert await start_api(system, 0, STARTED_AT) is None
    assert await start_api(system, -1, STARTED_AT) is None


async def test_start_api_serves_healthz_on_a_real_socket(system):
    """A real bind, because that is the one thing TestServer never exercises."""
    port = _free_port()
    runner = await start_api(system, port, STARTED_AT)
    assert runner is not None
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}/healthz") as response,
        ):
            assert response.status == 200
            assert (await response.json())["ok"] is True
    finally:
        await stop_api(runner)


async def test_a_stopped_api_stops_answering(system):
    port = _free_port()
    runner = await start_api(system, port, STARTED_AT)
    await stop_api(runner)

    with pytest.raises(aiohttp.ClientError):
        async with aiohttp.ClientSession() as session:
            await session.get(f"http://127.0.0.1:{port}/healthz")


async def test_start_api_survives_a_port_it_cannot_bind(system, monkeypatch, caplog):
    async def _refuse(self):
        raise OSError("address already in use")

    monkeypatch.setattr(web.TCPSite, "start", _refuse)

    assert await start_api(system, 8091, STARTED_AT) is None
    assert "HTTP API" in caplog.text


async def test_stop_api_tolerates_a_runner_that_never_started():
    await stop_api(None)
