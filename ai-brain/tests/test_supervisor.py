import asyncio
import logging
from pathlib import Path

import httpx
import pytest
import respx

from ai_brain import metrics
from ai_brain.config import load_settings
from ai_brain.ledger import Ledger, Limits
from ai_brain.llm import ProviderChain
from ai_brain.metrics import MetricsWriter
from ai_brain.supervisor import build

SEED = Path(__file__).resolve().parents[1] / "seed"


def env(tmp_path: Path, **extra) -> dict[str, str]:
    base = {
        "MEMORY_ROOT": str(tmp_path / "memory"),
        "SEED_ROOT": str(SEED),
        "LLM_CHAIN": "fake:a,fake:b",
        "VM_URL": "http://vm.test",
        "INFLUX_TOKEN": "tok",
    }
    base.update(extra)
    return base


def fake_chain(settings, ledger) -> ProviderChain:
    return ProviderChain([], ledger)


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
