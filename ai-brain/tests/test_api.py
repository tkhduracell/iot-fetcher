"""The read-only introspection API.

Everything here drives a real aiohttp application through
``aiohttp.test_utils``, so the routing, the status codes and the JSON encoding
are the ones production serves -- no handler is called directly.
"""

import asyncio
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


async def test_status_reports_no_lan_host_without_a_lan_entry(client):
    assert (await get_json(client, "/api/status"))["lan_host"] == {"enabled": False}


async def test_status_reports_the_lan_host_when_one_is_found(tmp_path):
    import ipaddress

    from ai_brain.discovery import OllamaFinder, OllamaHost

    settings = load_settings(env(tmp_path))
    system = build(settings, chain_factory=fake_chain, clock=lambda: NOW)
    finder = OllamaFinder("qwen3-coder:30b", [ipaddress.ip_network("192.168.68.0/24")])
    finder._host = OllamaHost("http://192.168.68.9:11434", "qwen3-coder:30b", NOW)
    system.chain.lan_finders = [finder]

    app = build_app(system, started_at=STARTED_AT, clock=lambda: NOW)
    async with TestClient(TestServer(app)) as test_client:
        lan = (await get_json(test_client, "/api/status"))["lan_host"]

    assert lan == {
        "enabled": True,
        "hosts": [
            {
                "model": "qwen3-coder:30b",
                "host": "http://192.168.68.9:11434",
                "found_at": NOW,
                "subnets": ["192.168.68.0/24"],
            }
        ],
    }


async def test_status_reports_every_lan_host_when_several_are_configured(tmp_path):
    import ipaddress

    from ai_brain.discovery import OllamaFinder, OllamaHost

    settings = load_settings(env(tmp_path))
    system = build(settings, chain_factory=fake_chain, clock=lambda: NOW)
    net = [ipaddress.ip_network("192.168.68.0/24")]
    found = OllamaFinder("qwen3.8:27b-mlx", net)
    found._host = OllamaHost("http://192.168.68.9:11434", "qwen3.8:27b-mlx", NOW)
    unmatched = OllamaFinder("qwen3-coder:30b", net)
    system.chain.lan_finders = [found, unmatched]

    app = build_app(system, started_at=STARTED_AT, clock=lambda: NOW)
    async with TestClient(TestServer(app)) as test_client:
        lan = (await get_json(test_client, "/api/status"))["lan_host"]

    assert lan["enabled"] is True
    assert [h["model"] for h in lan["hosts"]] == ["qwen3.8:27b-mlx", "qwen3-coder:30b"]
    assert lan["hosts"][0]["host"] == "http://192.168.68.9:11434"
    assert lan["hosts"][1]["host"] is None


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
    secrets = (
        BOT_TOKEN,
        APP_TOKEN,
        GEMINI_KEY,
        INFLUX,
        "brave-super-secret",
        "ha-super-secret",
    )
    for secret in secrets:
        assert secret not in text
    assert "super-secret" not in text


async def test_status_shows_a_pause(client, system):
    system.settings.memory_root.joinpath("PAUSE").write_text("stop", encoding="utf-8")
    assert (await get_json(client, "/api/status"))["paused"] is True


async def test_status_counts_proposals(client, system, tmp_path):
    outbox = system.memories["brain"].outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    for name, status in (
        ("20260906T100000-a", "pending"),
        ("20260906T110000-b", "executed"),
    ):
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

    assert (await get_json(client, "/api/status"))["proposals"] == {
        "pending": 1,
        "total": 2,
    }


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


async def test_agents_lists_the_emoji_from_the_real_seed_persona(client):
    # `system` seeds from the real ai-brain/seed/, whose brain.md now carries
    # this frontmatter -- see seed/personas/brain.md.
    agents = (await get_json(client, "/api/agents"))["agents"]
    assert agents[0]["emoji"] == "🧠"


async def test_agents_lists_no_emoji_when_the_persona_has_no_frontmatter(client, system):
    # Overwrite the seeded persona with one that predates this field.
    system.memories["brain"].persona_path.write_text("I am the brain.", encoding="utf-8")

    agents = (await get_json(client, "/api/agents"))["agents"]
    assert agents[0]["emoji"] == ""


async def test_agents_reports_the_last_cycle_and_the_next_wake(client, system):
    loop = system.loops["brain"]
    loop.last_cycle = CycleResult(
        status="ok", model="fake:1", rounds=3, next_wake_s=1800
    )
    loop.last_cycle_at = NOW - 60
    loop.cycle_counts = {"ok": 4, "error": 1}
    loop.trace = CycleTrace(started_at=NOW - 60)

    brain = (await get_json(client, "/api/agents"))["agents"][0]

    assert brain["last_cycle"] == {
        "status": "ok",
        "model": "fake:1",
        "rounds": 3,
        "cap": 16,
        "next_wake_s": 1800,
    }
    assert brain["last_cycle_at"] == NOW - 60
    assert brain["next_wake_at"] == NOW - 60 + 1800
    assert brain["cycle_counts"] == {"ok": 4, "error": 1}
    assert brain["in_progress"] is True


async def test_agents_counts_facts_and_unread_notes(client, system):
    brain_memory = system.memories["brain"]
    brain_memory.write_fact("pool", "Pool status", "cold")
    brain_memory.write_fact("spa", "Spa status", "warm")
    brain_memory.drop_note("filip", "hello")

    brain = (await get_json(client, "/api/agents"))["agents"][0]
    assert brain["facts"] == 2
    assert brain["unread_notes"] == 1


# -- one agent ---------------------------------------------------------


async def test_agent_detail_adds_identity_goals_facts_and_inbox(client, system):
    memory = system.memories["brain"]
    memory.rewrite_identity("I am the brain")
    memory.rewrite_goals("keep the house warm")
    memory.write_fact("pool", "Pool status", "cold")
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


async def test_agent_detail_carries_fact_stats_and_open_gaps(client, system):
    memory = system.memories["brain"]
    memory.write_fact("pool", "Pool status", "cold")
    memory.write_fact("pool", "Pool status", "colder")
    memory.open_gap("Why does the pump stop at 03:00?", "blocks the heating plan")
    answered = memory.open_gap("Is the hall sensor dead?", "")
    assert memory.close_gap(answered.id, "it is dead")

    body = await get_json(client, "/api/agents/brain")

    assert [stat["name"] for stat in body["fact_stats"]] == ["pool"]
    stat = body["fact_stats"][0]
    assert stat["title"] == "Pool status"
    assert stat["writes"] == 2
    assert stat["first_written_at"] <= stat["written_at"]

    # Closed gaps are answered questions; only the open one is a known unknown.
    assert [gap["question"] for gap in body["gaps"]] == [
        "Why does the pump stop at 03:00?"
    ]
    gap = body["gaps"][0]
    assert gap["why"] == "blocks the heating plan"
    assert gap["id"]
    assert gap["opened_at"] > 0
    # The detail body never carries a closed gap, so it never carries the two
    # fields that would only ever describe one.
    assert "closed_at" not in gap
    assert "answer" not in gap


async def test_agent_detail_has_empty_history_before_any_rewrite(client):
    body = await get_json(client, "/api/agents/brain")
    assert body["fact_stats"] == []
    assert body["gaps"] == []
    assert body["identity_history"] == []
    assert body["goals_history"] == []


async def test_agent_detail_shows_identity_and_goals_drift_newest_first(client, system):
    memory = system.memories["brain"]
    memory.rewrite_identity("first identity")
    memory.rewrite_identity("second identity")
    memory.rewrite_identity("third identity")
    memory.rewrite_goals("first goals")
    memory.rewrite_goals("second goals")

    body = await get_json(client, "/api/agents/brain")

    # Newest first, and the seeded persona the first rewrite replaced is the
    # oldest revision of all -- nothing is dropped just because it was seeded.
    assert [rev["body"] for rev in body["identity_history"]][:2] == [
        "second identity",
        "first identity",
    ]
    assert len(body["identity_history"]) == 3
    assert [rev["body"] for rev in body["goals_history"]] == ["first goals"]
    assert body["identity"] == "third identity"
    assert all(rev["at"] > 0 for rev in body["identity_history"])
    assert all(rev["truncated"] is False for rev in body["identity_history"])


async def test_agent_detail_caps_a_huge_revision_body(client, system):
    memory = system.memories["brain"]
    memory.rewrite_identity("x" * 5000)
    memory.rewrite_identity("short")

    revision = (await get_json(client, "/api/agents/brain"))["identity_history"][0]

    assert len(revision["body"]) == 2000
    assert revision["truncated"] is True
    # The live document is never truncated -- only the history excerpt is.
    assert (await get_json(client, "/api/agents/brain"))["identity"] == "short"


async def test_an_expert_has_no_goals(client, system):
    body = await get_json(client, "/api/agents/energy")
    assert body["is_brain"] is False
    assert body["goals"] == ""


async def test_an_expert_with_no_review_yet_has_a_null_last_review(client):
    body = await get_json(client, "/api/agents/energy")
    assert body["last_review"] is None


async def test_an_expert_detail_carries_the_brains_last_review(client, system):
    system.memories["brain"].append_review(
        "energy", {"ts": 1.0, "verdict": "stale", "findings": "old tariff numbers"}
    )
    body = await get_json(client, "/api/agents/energy")
    assert body["last_review"] == {"ts": 1.0, "verdict": "stale", "findings": "old tariff numbers"}


async def test_the_brain_itself_has_no_last_review_field(client):
    body = await get_json(client, "/api/agents/brain")
    assert "last_review" not in body


async def test_an_unknown_agent_is_a_404(client):
    body = await get_json(client, "/api/agents/nope", expect=404)
    assert body == {"error": "unknown agent: nope"}


async def test_an_unknown_agent_name_cannot_break_the_json_body(client):
    """The name is URL input, so it is encoded, never interpolated."""
    response = await client.get("/api/agents/he%22llo")
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
    assert await get_json(client, "/api/agents/brain/trace") == {
        "agent": "brain",
        "trace": None,
    }


async def test_trace_renders_the_live_cycle(client, system):
    system.loops["brain"].trace = CycleTrace(
        started_at=NOW - 10,
        model="fake:1",
        rounds=[
            RoundTrace(
                at=NOW - 9,
                text="looking",
                tool_calls=[{"name": "list_facts", "args": {}}],
                tool_results=[
                    {
                        "name": "list_facts",
                        "result_preview": "[]",
                        "stats": {"chars": 2, "ok": True},
                    }
                ],
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
    # stats rides along with result_preview -- loop.py's _tool_result_stats
    # output, passed through as-is rather than picked apart field by field.
    assert trace["rounds"][0]["tool_results"] == [
        {"name": "list_facts", "result_preview": "[]", "stats": {"chars": 2, "ok": True}}
    ]


# -- feed ----------------------------------------------------------------


async def _read_sse_events(response, count: int, timeout: float = 2.0) -> list[dict]:
    """Read ``count`` ``data:`` lines off an SSE response, decoded from JSON.

    A real ``EventSource`` never stops reading, so the test has to be the one
    that stops -- ``asyncio.wait_for`` around the whole read, not per line, so
    a keepalive comment in between two events does not itself count as a
    timeout.
    """

    async def _read() -> list[dict]:
        events = []
        while len(events) < count:
            line = await response.content.readline()
            text = line.decode().strip()
            if text.startswith("data: "):
                events.append(json.loads(text.removeprefix("data: ")))
        return events

    return await asyncio.wait_for(_read(), timeout=timeout)


async def test_feed_streams_a_round_started_event(client, system):
    async with client.get("/api/feed") as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "text/event-stream"
        # Give the handler a moment to reach `subscribe()` before publishing,
        # since the connection and the subscription are not the same instant.
        await asyncio.sleep(0.05)
        system.events.publish({"type": "round_started", "loop": "brain", "at": NOW})
        [event] = await _read_sse_events(response, 1)
        assert event == {"type": "round_started", "loop": "brain", "at": NOW}


async def test_feed_streams_multiple_events_in_order(client, system):
    async with client.get("/api/feed") as response:
        await asyncio.sleep(0.05)
        system.events.publish({"type": "round_started", "loop": "energy", "at": NOW})
        system.events.publish({"type": "round_started", "loop": "health", "at": NOW})
        events = await _read_sse_events(response, 2)
        assert [e["loop"] for e in events] == ["energy", "health"]


async def test_feed_disconnect_unsubscribes(client, system):
    assert system.events.subscriber_count() == 0
    async with client.get("/api/feed") as response:
        await asyncio.sleep(0.05)
        assert response.status == 200
        assert system.events.subscriber_count() == 1
    # The client context manager above closes the connection on exit; the
    # server side only notices on its next write, so give it one to react to.
    system.events.publish({"type": "round_started", "loop": "brain", "at": NOW})
    await asyncio.sleep(0.05)
    assert system.events.subscriber_count() == 0


# -- facts -------------------------------------------------------------


async def test_a_fact_is_returned_by_name(client, system):
    system.memories["brain"].write_fact("pool", "Pool status", "the pool is cold")
    body = await get_json(client, "/api/agents/brain/facts/pool")
    assert body == {
        "agent": "brain",
        "name": "pool",
        "title": "Pool status",
        "body": "the pool is cold",
    }


async def test_a_missing_fact_is_a_404(client):
    body = await get_json(client, "/api/agents/brain/facts/nothing", expect=404)
    assert "nothing" in body["error"]


@pytest.mark.parametrize("name", ["Pool", "a" * 80, "no spaces"])
async def test_an_unsafe_fact_name_is_a_400(client, name):
    """safe_name is the gate, so a name it rejects never reaches the disk."""
    body = await get_json(client, f"/api/agents/brain/facts/{name}", expect=400)
    assert "unsafe memory name" in body["error"]


async def test_a_traversing_fact_name_cannot_read_outside_the_facts_dir(client, system):
    (system.settings.memory_root / "constitution.md").write_text(
        "secret", encoding="utf-8"
    )
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


# -- loops -------------------------------------------------------------


def _write_proposals(system, rows) -> None:
    """Drop proposal files straight into the outbox.

    ``Approvals.propose`` needs a live Slack to produce anything at all, and
    these endpoints only ever read what is on disk.
    """
    outbox = system.memories["brain"].outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    for row in rows:
        (outbox / f"{row['id']}.json").write_text(
            json.dumps(
                {
                    "kind": "sonos_say",
                    "payload": {"text": "hi"},
                    "reason": "why",
                    "created": "2026-09-06T10:00:00Z",
                    "result": "",
                    **row,
                }
            ),
            encoding="utf-8",
        )


async def test_loops_is_empty_without_proposals(client):
    assert await get_json(client, "/api/loops") == {"loops": []}


async def test_loops_group_by_topic_loudest_first(client, system):
    _write_proposals(
        system,
        [
            {
                "id": "20260906T100000-a",
                "topic": "pool",
                "kind": "ha_todo_add",
                "status": "rejected",
                "created": "2026-09-06T10:00:00Z",
            },
            {
                "id": "20260906T110000-b",
                "topic": "pool",
                "kind": "ha_todo_add",
                "status": "rejected",
                "created": "2026-09-06T11:00:00Z",
            },
            {
                "id": "20260906T120000-c",
                "topic": "pool",
                "kind": "sonos_say",
                "status": "pending",
                "created": "2026-09-06T12:00:00Z",
            },
            {
                "id": "20260906T130000-d",
                "topic": "heating",
                "kind": "sonos_say",
                "status": "executed",
                "created": "2026-09-06T13:00:00Z",
                "result": "spoke",
            },
        ],
    )

    loops = (await get_json(client, "/api/loops"))["loops"]

    assert [loop["topic"] for loop in loops] == ["pool", "heating"]
    pool = loops[0]
    assert pool["laps"] == 3
    assert pool["first_at"] == "2026-09-06T10:00:00Z"
    assert pool["last_at"] == "2026-09-06T12:00:00Z"
    assert (pool["pending"], pool["approved"], pool["rejected"]) == (1, 0, 2)
    assert pool["kinds"] == ["ha_todo_add", "sonos_say"]
    assert [p["id"] for p in pool["proposals"]] == [
        "20260906T100000-a",
        "20260906T110000-b",
        "20260906T120000-c",
    ]
    assert pool["proposals"][0] == {
        "id": "20260906T100000-a",
        "kind": "ha_todo_add",
        "created": "2026-09-06T10:00:00Z",
        "status": "rejected",
        "result": "",
    }
    # A topic proposed once is still a loop of one.
    assert loops[1] == {
        "topic": "heating",
        "laps": 1,
        "first_at": "2026-09-06T13:00:00Z",
        "last_at": "2026-09-06T13:00:00Z",
        "pending": 0,
        "approved": 1,
        "rejected": 0,
        "kinds": ["sonos_say"],
        "proposals": [
            {
                "id": "20260906T130000-d",
                "kind": "sonos_say",
                "created": "2026-09-06T13:00:00Z",
                "status": "executed",
                "result": "spoke",
            }
        ],
    }


async def test_loops_never_leak_the_payload_or_the_slack_ts(client, system):
    _write_proposals(
        system,
        [
            {
                "id": "20260906T100000-a",
                "topic": "pool",
                "status": "pending",
                "slack_ts": "1725_secret",
            }
        ],
    )

    loop = (await get_json(client, "/api/loops"))["loops"][0]

    assert set(loop["proposals"][0]) == {"id", "kind", "created", "status", "result"}
    assert "1725_secret" not in json.dumps(loop)


async def test_loops_counts_are_a_subset_of_the_laps(client, system):
    """``expired`` and ``failed`` are neither a checkmark nor a cross."""
    _write_proposals(
        system,
        [
            {"id": "20260906T100000-a", "topic": "pool", "status": "expired"},
            {"id": "20260906T110000-b", "topic": "pool", "status": "failed"},
        ],
    )

    loop = (await get_json(client, "/api/loops"))["loops"][0]

    assert loop["laps"] == 2
    assert (loop["pending"], loop["approved"], loop["rejected"]) == (0, 0, 0)
    assert [p["status"] for p in loop["proposals"]] == ["expired", "failed"]


async def test_loops_ties_are_ordered_by_topic(client, system):
    _write_proposals(
        system,
        [
            {"id": "20260906T100000-a", "topic": "zeta", "status": "pending"},
            {"id": "20260906T110000-b", "topic": "alpha", "status": "pending"},
        ],
    )

    loops = (await get_json(client, "/api/loops"))["loops"]
    assert [loop["topic"] for loop in loops] == ["alpha", "zeta"]


# -- usefulness --------------------------------------------------------


async def test_usefulness_lists_every_loop_at_zero_before_any_cycle(client):
    body = await get_json(client, "/api/usefulness")

    assert [row["name"] for row in body["loops"]] == ["brain", "energy"]
    assert body["loops"][0] == {
        "name": "brain",
        "total": 0,
        "nothing": 0,
        "note": 0,
        "real": 0,
        "repeat": 0,
    }
    assert body["totals"] == {
        "total": 0,
        "nothing": 0,
        "note": 0,
        "real": 0,
        "repeat": 0,
    }


async def test_usefulness_buckets_every_status_loop_py_records(client, system):
    system.loops["brain"].cycle_counts = {
        "ok": 5,
        "max_rounds": 2,
        "error": 1,
        "timeout": 1,
        "no_budget": 3,
        "paused": 1,
        "cancelled": 1,
    }
    system.loops["energy"].cycle_counts = {"ok": 2}

    body = await get_json(client, "/api/usefulness")

    brain = body["loops"][0]
    assert brain["real"] == 5
    assert brain["repeat"] == 2
    assert brain["note"] == 2
    assert brain["nothing"] == 5
    assert brain["total"] == 14
    assert body["totals"] == {
        "total": 16,
        "nothing": 5,
        "note": 2,
        "real": 7,
        "repeat": 2,
    }


async def test_tokens_reports_each_loops_share(client, system):
    system.loops["brain"].token_counts = {"prompt": 600, "completion": 0}
    system.loops["brain"].cycle_counts = {"ok": 2}
    system.loops["energy"].token_counts = {"prompt": 150, "completion": 50}

    body = await get_json(client, "/api/tokens")

    assert body["total"] == 800
    brain = body["loops"][0]
    assert brain["name"] == "brain"
    assert brain["share"] == 0.75
    assert brain["per_cycle"] == 300
    assert brain["heartbeat_s"] == 1800


async def test_usefulness_keeps_an_unknown_status_rather_than_dropping_it(
    client, system
):
    """The buckets must always sum to ``total``, whatever ``loop.py`` adds."""
    system.loops["brain"].cycle_counts = {"ok": 1, "brand_new_status": 4}

    brain = (await get_json(client, "/api/usefulness"))["loops"][0]

    assert brain["total"] == 5
    assert brain["nothing"] == 4
    assert brain["real"] == 1
    assert (
        brain["nothing"] + brain["note"] + brain["real"] + brain["repeat"]
        == brain["total"]
    )


# -- method and error handling -----------------------------------------


async def test_the_api_is_read_only(client):
    for path in (
        "/api/status",
        "/api/agents",
        "/api/proposals",
        "/api/loops",
        "/api/usefulness",
    ):
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
