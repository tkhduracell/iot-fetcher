import asyncio
import dataclasses
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
import respx
from conftest import SEED, env, fake_chain

from ai_brain import metrics, supervisor
from ai_brain.config import load_settings
from ai_brain.ledger import Ledger, Limits
from ai_brain.metrics import MetricsWriter
from ai_brain.supervisor import build

# -- build ------------------------------------------------------------


def test_build_keeps_known_experts_and_warns_about_unknown(tmp_path, caplog):
    settings = load_settings(env(tmp_path, EXPERTS="energy,bogus"))
    with caplog.at_level(logging.WARNING):
        system = build(settings, chain_factory=fake_chain)

    assert sorted(system.loops) == ["brain", "energy"]
    assert "bogus" in caplog.text
    assert system.loops["brain"].priority == "brain"
    assert system.loops["energy"].priority == "expert"
    assert system.constitution.strip()


def test_build_seeds_constitution_on_first_boot(tmp_path):
    settings = load_settings(env(tmp_path))
    system = build(settings, chain_factory=fake_chain)

    copied = settings.memory_root / "constitution.md"
    assert copied.exists()
    assert copied.read_text(encoding="utf-8") == (SEED / "constitution.md").read_text(
        encoding="utf-8"
    )
    assert system.constitution == copied.read_text(encoding="utf-8")


def test_build_prefers_the_existing_constitution_over_the_seed(tmp_path):
    settings = load_settings(env(tmp_path))
    settings.memory_root.mkdir(parents=True, exist_ok=True)
    (settings.memory_root / "constitution.md").write_text("mine", encoding="utf-8")

    system = build(settings, chain_factory=fake_chain)

    assert system.constitution == "mine"


def test_build_exits_when_no_constitution_anywhere(tmp_path, caplog):
    settings = load_settings(env(tmp_path, SEED_ROOT=str(tmp_path / "no-seed")))
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        build(settings, chain_factory=fake_chain)
    assert excinfo.value.code == 2


def test_build_exits_on_an_empty_chain(tmp_path, caplog):
    """A zero-provider chain builds fine and then fails silently forever.

    Every cycle raises ChainExhausted, the journal fills with [no_budget], and
    the ledger has no buckets so ai_brain_ledger_remaining emits no series at
    all -- a blank Grafana panel, which is indistinguishable from a healthy
    one. That has to be a startup error, not a runtime condition.
    """
    settings = load_settings(env(tmp_path, LLM_CHAIN="x"))
    settings = dataclasses.replace(settings, llm_chain=[])
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        build(settings, chain_factory=fake_chain)
    assert excinfo.value.code == 2
    assert "LLM_CHAIN" in caplog.text


def test_build_exits_when_a_gemini_entry_has_no_key(tmp_path, caplog):
    settings = load_settings(env(tmp_path, LLM_CHAIN="gemini:flash", GEMINI_API_KEY=""))
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        build(settings, chain_factory=fake_chain)
    assert excinfo.value.code == 2
    assert "GEMINI_API_KEY" in caplog.text


def test_build_allows_a_gemini_entry_with_a_key(tmp_path):
    settings = load_settings(env(tmp_path, LLM_CHAIN="gemini:flash", GEMINI_API_KEY="k"))
    system = build(settings, chain_factory=fake_chain)
    assert sorted(system.loops) == ["brain"]


def test_build_passes_the_call_timeout_to_every_loop(tmp_path):
    settings = load_settings(env(tmp_path, EXPERTS="energy", CALL_TIMEOUT_S="17"))
    system = build(settings, chain_factory=fake_chain)
    assert [loop.call_timeout_s for loop in system.loops.values()] == [17, 17]


# -- daily budget reset -----------------------------------------------


def test_day_roll_watcher_notes_and_wakes_the_brain_at_pacific_midnight(tmp_path):
    """Crossing Pacific midnight must reach the brain's inbox, once."""
    # 23:59 Pacific on 2026-09-06, then two minutes later.
    before = datetime(2026, 9, 7, 6, 59, tzinfo=UTC).timestamp()
    after = datetime(2026, 9, 7, 7, 1, tzinfo=UTC).timestamp()
    state = {"t": before}

    settings = load_settings(env(tmp_path))
    system = build(settings, chain_factory=fake_chain, clock=lambda: state["t"])
    watch = supervisor.day_roll_watcher(system)
    brain = system.memories["brain"]

    asyncio.run(watch())
    assert brain.unread_notes() == []
    assert not system.loops["brain"].wake.is_set()

    state["t"] = after
    asyncio.run(watch())

    notes = brain.unread_notes()
    assert [(n.sender, n.body) for n in notes] == [("ledger", "new day, budget restored")]
    assert system.loops["brain"].wake.is_set()

    # Still the same day now: no second note.
    asyncio.run(watch())
    assert len(brain.unread_notes()) == 1


def test_build_wires_memories_registry_and_context(tmp_path):
    settings = load_settings(env(tmp_path, EXPERTS="energy"))
    system = build(settings, chain_factory=fake_chain)

    assert sorted(system.memories) == ["brain", "energy"]
    assert system.memories["brain"].is_brain
    assert not system.memories["energy"].is_brain
    # ensure() ran and the persona was seeded
    assert (settings.memory_root / "brain" / "journal").is_dir()
    assert (settings.memory_root / "experts" / "energy" / "persona.md").exists()

    names = {spec.name for spec in system.registry.specs_for("brain")}
    assert {"end_cycle", "write_fact", "propose", "vm_query", "slack_post"} <= names

    ctx = system.loops["energy"].ctx
    assert ctx.loop == "energy"
    assert ctx.extras["approvals"] is system.approvals
    assert ctx.extras["http"] is system.http
    assert system.loops["brain"].pause_file == settings.memory_root / "PAUSE"
    assert system.loops["brain"].heartbeat_s == settings.brain_heartbeat_s
    assert system.loops["energy"].heartbeat_s == settings.expert_heartbeat_s


def test_build_wake_rings_the_named_loop_and_ignores_strangers(tmp_path):
    settings = load_settings(env(tmp_path, EXPERTS="energy"))
    system = build(settings, chain_factory=fake_chain)
    ctx = system.loops["brain"].ctx

    # Tools and Slack ring the same bell.
    assert ctx.wake is system.wake

    ctx.wake("energy")
    assert system.loops["energy"].wake.is_set()
    ctx.wake("nobody")  # must not raise


def test_build_ledger_covers_the_chain_keys(tmp_path):
    settings = load_settings(env(tmp_path))
    system = build(settings, chain_factory=fake_chain)
    assert system.ledger.keys() == ["fake:a", "fake:b"]
    assert (settings.memory_root / "_ledger.json").parent.is_dir()


# -- metrics.render ---------------------------------------------------


class FakeLoop:
    def __init__(self, counts, last_cycle_at=0.0):
        self.cycle_counts = dict(counts)
        self.last_cycle_at = last_cycle_at


def test_render_emits_the_three_families(tmp_path):
    ledger = Ledger({"gemini:2.5-flash": Limits(rpm=10, tpm=1000, rpd=100)}, tmp_path / "l.json")
    loops = {
        "brain": FakeLoop({"ok": 3, "error": 1}, last_cycle_at=940.0),
        "energy": FakeLoop({}),
    }

    lines = metrics.render(loops, ledger, now=1000.0)

    assert "ai_brain_cycle_total,loop=brain,status=ok value=3i" in lines
    assert "ai_brain_cycle_total,loop=brain,status=error value=1i" in lines
    assert "ai_brain_loop_last_cycle_seconds,loop=brain value=60.0" in lines
    # never ran: no age line at all
    assert not any(
        line.startswith("ai_brain_loop_last_cycle_seconds,loop=energy") for line in lines
    )
    assert "ai_brain_ledger_remaining,model=gemini:2.5-flash,kind=requests value=1.0" in lines
    assert "ai_brain_ledger_remaining,model=gemini:2.5-flash,kind=tokens value=1.0" in lines


def test_render_escapes_tag_values(tmp_path):
    ledger = Ledger({"we ird,k=y": Limits(rpm=1, tpm=1, rpd=1)}, tmp_path / "l.json")
    lines = metrics.render({"a b": FakeLoop({"o,k": 1})}, ledger, now=1.0)

    assert "ai_brain_cycle_total,loop=a\\ b,status=o\\,k value=1i" in lines
    assert any("model=we\\ ird\\,k\\=y" in line for line in lines)


# -- MetricsWriter ----------------------------------------------------


@respx.mock
async def test_writer_posts_line_protocol_with_bearer():
    route = respx.post("http://vm.test/api/v2/write").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        await MetricsWriter("http://vm.test", "tok", http).write(["a value=1i", "b value=2"])

    request = route.calls[0].request
    assert request.content == b"a value=1i\nb value=2"
    assert request.headers["Authorization"] == "Bearer tok"


@respx.mock
async def test_writer_swallows_server_errors(caplog):
    respx.post("http://vm.test/api/v2/write").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as http:
        with caplog.at_level(logging.WARNING):
            await MetricsWriter("http://vm.test", "tok", http).write(["a value=1i"])
    assert "metrics" in caplog.text.lower()


@respx.mock
async def test_writer_skips_empty_lines():
    route = respx.post("http://vm.test/api/v2/write")
    async with httpx.AsyncClient() as http:
        await MetricsWriter("http://vm.test", "tok", http).write([])
    assert not route.called


# -- supervision ------------------------------------------------------


class DyingLoop:
    """Raises the first time, then blocks -- so a restart is observable."""

    def __init__(self) -> None:
        self.calls = 0
        self.restarted = asyncio.Event()

    async def run_forever(self) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        self.restarted.set()
        await asyncio.Event().wait()


async def test_supervise_restarts_a_loop_that_dies(monkeypatch, caplog):
    from ai_brain import supervisor

    slept: list[float] = []

    async def record_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(supervisor, "asyncio", _AsyncioWithSleep(record_sleep))
    loop = DyingLoop()

    with caplog.at_level(logging.ERROR):
        task = asyncio.create_task(supervisor._supervise("brain", loop))
        await asyncio.wait_for(loop.restarted.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert loop.calls == 2
    assert slept == [supervisor.RESTART_DELAY_S]
    assert "brain" in caplog.text


class _AsyncioWithSleep:
    """The real asyncio module with only ``sleep`` swapped out."""

    def __init__(self, sleep):
        self.sleep = sleep

    def __getattr__(self, name):
        return getattr(asyncio, name)


# -- unknown provider prefixes ----------------------------------------


def test_build_exits_on_an_unknown_provider_prefix(tmp_path, caplog):
    """A typo'd prefix would otherwise raise ValueError on the first real chain build."""
    settings = load_settings(env(tmp_path, LLM_CHAIN="gemeni:flash", GEMINI_API_KEY="k"))
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        build(settings, chain_factory=fake_chain)

    assert excinfo.value.code == 2
    assert "gemeni" in caplog.text


def test_build_exits_on_a_chain_entry_with_no_prefix(tmp_path, caplog):
    settings = load_settings(env(tmp_path, LLM_CHAIN="gemini-2.5-flash", GEMINI_API_KEY="k"))
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        build(settings, chain_factory=fake_chain)

    assert excinfo.value.code == 2


def test_every_known_prefix_is_accepted(tmp_path):
    settings = load_settings(env(tmp_path, LLM_CHAIN="fake:a,gemini:b", GEMINI_API_KEY="k"))
    assert sorted(build(settings, chain_factory=fake_chain).loops) == ["brain"]


# -- startup recovery of executing proposals --------------------------


def test_build_recovers_a_proposal_left_executing(tmp_path):
    settings = load_settings(env(tmp_path))
    system = build(settings, chain_factory=fake_chain)
    outbox = system.memories["brain"].outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "p1.json").write_text(
        json.dumps(
            {
                "id": "p1",
                "kind": "sonos_say",
                "payload": {"text": "hi"},
                "reason": "why",
                "topic": "#home",
                "created": "2026-09-06T10:00:00Z",
                "status": "executing",
                "slack_ts": "1.1",
                "result": "",
            }
        )
    )

    rebuilt = build(load_settings(env(tmp_path)), chain_factory=fake_chain)

    stored = json.loads((outbox / "p1.json").read_text())
    assert stored["status"] == "failed"
    assert "restarted mid-execution" in stored["result"]
    assert rebuilt.approvals.pending() == []
    assert any(
        "p1 failed" in n.body for n in rebuilt.memories["brain"].unread_notes()
    )


# -- an expiry wakes the brain ----------------------------------------


async def test_expiring_a_proposal_wakes_the_brain(tmp_path):
    settings = load_settings(env(tmp_path))
    woken: list[str] = []
    system = dataclasses.replace(
        build(settings, chain_factory=fake_chain), wake=woken.append
    )

    async def expired_one() -> list[object]:
        return [object()]

    system.approvals.expire = expired_one
    await supervisor.expiry_watcher(system)()

    assert woken == ["brain"]


async def test_expiring_nothing_does_not_wake(tmp_path):
    settings = load_settings(env(tmp_path))
    woken: list[str] = []
    system = dataclasses.replace(
        build(settings, chain_factory=fake_chain), wake=woken.append
    )

    await supervisor.expiry_watcher(system)()

    assert woken == []


# -- metrics with no VM configured ------------------------------------


@respx.mock
async def test_writer_skips_and_warns_once_when_unconfigured(caplog):
    route = respx.post("http://vm.test/api/v2/write")
    async with httpx.AsyncClient() as http:
        writer = MetricsWriter("http://vm.test", "", http)
        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                await writer.write(["a value=1i"])

    assert not route.called
    assert caplog.text.count("metrics disabled") == 1


@respx.mock
async def test_writer_skips_when_the_vm_url_is_empty(caplog):
    async with httpx.AsyncClient() as http:
        with caplog.at_level(logging.WARNING):
            await MetricsWriter("", "tok", http).write(["a value=1i"])

    assert "metrics disabled" in caplog.text
