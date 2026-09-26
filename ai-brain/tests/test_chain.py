import asyncio
import dataclasses
import pathlib
import sys
import time
import types

import pytest

from ai_brain.config import load_settings
from ai_brain.ledger import Ledger, Limits
from ai_brain.llm import (
    ChainExhausted,
    Message,
    ProviderChain,
    ProviderError,
    Reply,
    ToolCall,
    ToolSpec,
    Usage,
    limits_from_settings,
)
from ai_brain.llm.fake import FakeProvider

T0 = 1_757_000_000.0

MSGS = [Message(role="user", content="hi")]


def reply(text="ok", model="m", prompt=10, completion=5):
    return Reply(
        text=text,
        tool_calls=(),
        usage=Usage(prompt_tokens=prompt, completion_tokens=completion),
        model=model,
    )


def make_ledger(tmp_path, keys, rpm=10, tpm=100_000, rpd=100, t0=T0):
    state = {"t": t0}
    limits = {key: Limits(rpm=rpm, tpm=tpm, rpd=rpd) for key in keys}
    ledger = Ledger(limits, tmp_path / "ledger.json", clock=lambda: state["t"])
    return ledger, state


async def test_first_provider_answers_and_is_recorded(tmp_path):
    ledger, _ = make_ledger(tmp_path, ["a", "b"])
    a = FakeProvider("a", [reply("from-a", prompt=100, completion=20)])
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([a, b], ledger)

    out = await chain.complete(MSGS, [], 512, "brain")

    assert out.text == "from-a"
    assert b.calls == []
    assert a.calls == [(MSGS, [])]
    assert ledger.snapshot()["buckets"]["a"]["tokens_day"] == 120
    assert ledger.snapshot()["buckets"]["a"]["requests_day"] == 1


async def test_the_answered_log_line_names_the_calling_agent(tmp_path, caplog):
    ledger, _ = make_ledger(tmp_path, ["a"])
    a = FakeProvider("a", [reply("from-a", prompt=100, completion=20)])
    chain = ProviderChain([a], ledger)

    with caplog.at_level("INFO", logger="ai_brain.llm"):
        await chain.complete(MSGS, [], 512, "brain", agent="energy")

    assert "energy: a answered (in=100 out=20 tokens)" in caplog.text


async def test_the_answered_log_line_has_no_agent_prefix_when_unset(tmp_path, caplog):
    ledger, _ = make_ledger(tmp_path, ["a"])
    a = FakeProvider("a", [reply("from-a", prompt=100, completion=20)])
    chain = ProviderChain([a], ledger)

    with caplog.at_level("INFO", logger="ai_brain.llm"):
        await chain.complete(MSGS, [], 512, "brain")

    assert "a answered (in=100 out=20 tokens)" in caplog.text
    assert ": a answered" not in caplog.text


async def test_not_found_disables_key_and_falls_through(tmp_path):
    ledger, _ = make_ledger(tmp_path, ["a", "b"])
    a = FakeProvider("a", [ProviderError("gone", kind="not_found")])
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([a, b], ledger)

    out = await chain.complete(MSGS, [], 512, "brain")

    assert out.text == "from-b"
    decision = ledger.can_spend("a", "brain")
    assert not decision.allowed
    assert decision.reason == "disabled"


async def test_rate_limited_records_429_and_falls_through(tmp_path):
    ledger, state = make_ledger(tmp_path, ["a", "b"])
    a = FakeProvider("a", [ProviderError("slow down", kind="rate_limited", retry_after_s=30)])
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([a, b], ledger)

    out = await chain.complete(MSGS, [], 512, "brain")

    assert out.text == "from-b"
    decision = ledger.can_spend("a", "brain")
    assert not decision.allowed
    assert decision.reason == "cooldown"
    # Compare the offset, not the epoch value: pytest.approx on a ~1.7e9
    # timestamp has a relative tolerance of ~1700s and hides real drift.
    assert decision.retry_at - state["t"] == pytest.approx(30)


async def test_server_error_is_retried_once_on_same_provider(tmp_path):
    ledger, _ = make_ledger(tmp_path, ["a", "b"])
    a = FakeProvider("a", [ProviderError("boom", kind="server"), reply("from-a-retry")])
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([a, b], ledger)

    out = await chain.complete(MSGS, [], 512, "brain")

    assert out.text == "from-a-retry"
    assert len(a.calls) == 2
    assert b.calls == []


async def test_server_error_twice_moves_to_next_provider(tmp_path):
    ledger, _ = make_ledger(tmp_path, ["a", "b"])
    a = FakeProvider(
        "a", [ProviderError("boom", kind="server"), ProviderError("boom", kind="timeout")]
    )
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([a, b], ledger)

    out = await chain.complete(MSGS, [], 512, "brain")

    assert out.text == "from-b"
    assert len(a.calls) == 2


async def test_server_errors_are_retried_exactly_once_not_more(tmp_path):
    # "retry once" is the budget: a third scripted reply must stay untouched,
    # otherwise a flapping provider would starve the rest of the chain.
    ledger, _ = make_ledger(tmp_path, ["a", "b"])
    a = FakeProvider(
        "a",
        [
            ProviderError("boom", kind="server"),
            ProviderError("boom", kind="server"),
            reply("third-attempt"),
        ],
    )
    b = FakeProvider("b", [reply("from-b")])

    out = await ProviderChain([a, b], ledger).complete(MSGS, [], 512, "brain")

    assert out.text == "from-b"
    assert len(a.calls) == 2
    assert len(a.script) == 1


async def test_bad_request_raises_immediately(tmp_path):
    ledger, _ = make_ledger(tmp_path, ["a", "b"])
    a = FakeProvider("a", [ProviderError("schema is wrong", kind="bad_request")])
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([a, b], ledger)

    with pytest.raises(ProviderError) as exc:
        await chain.complete(MSGS, [], 512, "brain")

    assert exc.value.kind == "bad_request"
    assert b.calls == []


async def test_all_exhausted_raises_with_earliest_retry_at(tmp_path):
    ledger, state = make_ledger(tmp_path, ["a", "b"])
    a = FakeProvider("a", [ProviderError("slow", kind="rate_limited", retry_after_s=300)])
    b = FakeProvider("b", [ProviderError("slow", kind="rate_limited", retry_after_s=45)])
    chain = ProviderChain([a, b], ledger)

    with pytest.raises(ChainExhausted) as exc:
        await chain.complete(MSGS, [], 512, "brain")

    assert exc.value.retry_at - state["t"] == pytest.approx(45)


async def test_denied_key_is_skipped_and_its_retry_at_is_reported(tmp_path):
    # rpd=1 so one recorded request parks "a" until the next Pacific midnight.
    ledger, _ = make_ledger(tmp_path, ["a", "b"], rpd=1)
    ledger.record("a", 5, 5)
    a = FakeProvider("a", [reply("never")])
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([a, b], ledger)

    out = await chain.complete(MSGS, [], 512, "brain")

    assert out.text == "from-b"
    assert a.calls == []


async def test_expert_priority_skips_a_bucket_below_the_floor(tmp_path):
    # rpd=10: 7 recorded requests leaves 30% < the 40% expert floor, so an
    # expert must skip "a" while the brain may still use it.
    ledger, _ = make_ledger(tmp_path, ["a", "b"], rpd=10)
    for _ in range(7):
        ledger.record("a", 1, 1)

    a = FakeProvider("a", [reply("from-a")])
    b = FakeProvider("b", [reply("from-b")])
    out = await ProviderChain([a, b], ledger).complete(MSGS, [], 512, "expert")
    assert out.text == "from-b"
    assert a.calls == []

    a2 = FakeProvider("a", [reply("from-a")])
    b2 = FakeProvider("b", [reply("from-b")])
    out2 = await ProviderChain([a2, b2], ledger).complete(MSGS, [], 512, "brain")
    assert out2.text == "from-a"


async def test_exhausted_falls_back_to_ledger_next_available(tmp_path):
    # Server errors record nothing in the ledger, so the chain learns no
    # retry_at of its own and must ask the ledger when it gives up. The key is
    # still spendable, so the ledger answers "now".
    ledger, state = make_ledger(tmp_path, ["a"])
    a = FakeProvider(
        "a", [ProviderError("boom", kind="server"), ProviderError("boom", kind="server")]
    )
    chain = ProviderChain([a], ledger)

    with pytest.raises(ChainExhausted) as exc:
        await chain.complete(MSGS, [], 512, "brain")

    assert exc.value.retry_at == state["t"]


async def test_exhausted_reports_no_retry_when_the_ledger_knows_of_none(tmp_path):
    # A 404-disabled key has a retry_at, but a chain with no usable key at all
    # must still produce a ChainExhausted rather than hanging on None.
    ledger, _ = make_ledger(tmp_path, ["a"])
    a = FakeProvider("a", [ProviderError("nope", kind="not_found")])

    with pytest.raises(ChainExhausted) as exc:
        await ProviderChain([a], ledger).complete(MSGS, [], 512, "brain")

    assert exc.value.retry_at == ledger.can_spend("a", "brain").retry_at


async def test_fake_provider_raises_when_script_is_exhausted():
    provider = FakeProvider("a", [])
    with pytest.raises(AssertionError, match="script exhausted"):
        await provider.complete(MSGS, [], 512)


async def test_fake_provider_records_calls():
    tools = [ToolSpec(name="t", description="d", parameters={"type": "object"})]
    provider = FakeProvider("a", [reply()])
    await provider.complete(MSGS, tools, 512)
    assert provider.calls == [(MSGS, tools)]


def test_from_settings_builds_keys_in_order(tmp_path, monkeypatch):
    # Task 4 supplies the real llm/gemini.py; from_settings imports it lazily,
    # so a stub module is enough to prove the parsing and ordering here.
    class StubGemini:
        def __init__(self, model, api_key, thinking_budget=-1):
            self.key = f"gemini:{model}"
            self.model = model
            self.api_key = api_key

    stub = types.ModuleType("ai_brain.llm.gemini")
    stub.GeminiProvider = StubGemini
    monkeypatch.setitem(sys.modules, "ai_brain.llm.gemini", stub)

    settings = load_settings(
        {"LLM_CHAIN": "gemini:flash,gemini:pro,fake:x", "GEMINI_API_KEY": "k"}
    )
    ledger, _ = make_ledger(tmp_path, ["gemini:flash", "gemini:pro", "fake:x"])
    chain = ProviderChain.from_settings(settings, ledger)

    assert [p.key for p in chain.providers] == ["gemini:flash", "gemini:pro", "fake:x"]
    assert chain.providers[0].api_key == "k"
    assert isinstance(chain.providers[2], FakeProvider)


def test_from_settings_rejects_unknown_provider(tmp_path):
    settings = load_settings({"LLM_CHAIN": "openai:gpt-4"})
    ledger, _ = make_ledger(tmp_path, ["openai:gpt-4"])
    with pytest.raises(ValueError, match="provider 'openai' not implemented"):
        ProviderChain.from_settings(settings, ledger)


def test_limits_from_settings_covers_every_chain_key():
    settings = load_settings(
        {"LLM_CHAIN": "gemini:flash,fake:x", "RPM": "3", "TPM": "500", "RPD": "40"}
    )
    limits = limits_from_settings(settings)
    assert limits == {
        "gemini:flash": Limits(rpm=3, tpm=500, rpd=40),
        "fake:x": Limits(rpm=3, tpm=500, rpd=40),
    }


def test_gemini_3_8_flash_gets_its_measured_rpd_not_the_configured_one():
    settings = load_settings(
        {
            "LLM_CHAIN": "gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite",
            "RPM": "8",
            "TPM": "200000",
            "RPD": "200",
        }
    )
    limits = limits_from_settings(settings)
    # rpm/tpm are untouched -- only the daily cap is overridden, and only for
    # the one key it was measured against.
    assert limits["gemini:gemini-3.8-flash"] == Limits(rpm=8, tpm=200000, rpd=11, loops=frozenset({"brain"}))
    assert limits["gemini:gemini-3.5-flash-lite"] == Limits(rpm=8, tpm=200000, rpd=200, loops=frozenset({"expert"}))


async def test_success_clears_a_previous_429_streak(tmp_path):
    ledger, _ = make_ledger(tmp_path, ["a"])
    ledger.record_429("a", 0.0)
    a = FakeProvider("a", [reply("from-a")])
    out = await ProviderChain([a], ledger).complete(MSGS, [], 512, "brain")
    assert out.text == "from-a"
    assert ledger.snapshot()["buckets"]["a"]["consecutive_429"] == 0


def test_message_and_tool_call_are_frozen():
    call = ToolCall(id="1", name="t", args={})
    msg = Message(role="assistant", content="", tool_calls=(call,))
    assert msg.tool_calls[0].name == "t"
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.content = "nope"


# --- the shipped .env.example ---------------------------------------------

ENV_EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / ".env.example"


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    """Parse a dotenv file the way docker compose --env-file does."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


def test_env_example_chain_builds_a_real_chain(tmp_path, monkeypatch):
    """The documented first-boot path must not crash-loop the container.

    Every entry in the shipped LLM_CHAIN has to parse as ``provider:model``.
    A bare model id (no colon) reaches ``raise ValueError(provider ... not
    implemented)`` inside build(), before any restart logic exists, so the
    container exits immediately on the very path the README tells you to take.
    """

    class StubGemini:
        def __init__(self, model, api_key, thinking_budget=-1):
            self.key = f"gemini:{model}"
            self.model = model
            self.api_key = api_key

    class StubOllama:
        def __init__(self, model, base_url, num_ctx=16384):
            self.key = f"ollama:{model}"
            self.model = model
            self.base_url = base_url
            self.num_ctx = num_ctx

    gemini_stub = types.ModuleType("ai_brain.llm.gemini")
    gemini_stub.GeminiProvider = StubGemini
    monkeypatch.setitem(sys.modules, "ai_brain.llm.gemini", gemini_stub)

    ollama_stub = types.ModuleType("ai_brain.llm.ollama")
    ollama_stub.OllamaProvider = StubOllama
    monkeypatch.setitem(sys.modules, "ai_brain.llm.ollama", ollama_stub)

    env = parse_env_file(ENV_EXAMPLE)
    assert env["LLM_CHAIN"] == (
        "gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite,"
        "lan:qwen3-coder:30b,ollama:llama3.2:3b"
    )

    settings = load_settings({**env, "GEMINI_API_KEY": "k"})
    ledger, _ = make_ledger(tmp_path, settings.llm_chain)
    chain = ProviderChain.from_settings(settings, ledger)

    # The free tier first, local hardware behind it, in the file's order.
    assert [p.key for p in chain.providers] == [
        "gemini:gemini-3.8-flash",
        "gemini:gemini-3.5-flash-lite",
        "lan:qwen3-coder:30b",
        "ollama:llama3.2:3b",
    ]


def test_env_example_ships_every_expert():
    """The file must agree with the code default, which is all five loops."""
    assert load_settings(parse_env_file(ENV_EXAMPLE)).experts == [
        "energy",
        "health",
        "house-ops",
        "researcher",
        "infra",
    ]


def test_env_example_effort_knobs_match_the_defaults():
    s = load_settings(parse_env_file(ENV_EXAMPLE))
    assert (s.max_rounds, s.max_tokens, s.thinking_budget) == (16, 8000, -1)


def test_env_example_call_timeout_matches_the_default():
    env = parse_env_file(ENV_EXAMPLE)
    assert load_settings(env).call_timeout_s == 60


def test_env_example_http_port_matches_the_default():
    """The published port in docker-compose.local.yml must match what we bind."""
    env = parse_env_file(ENV_EXAMPLE)
    assert load_settings(env).http_port == 8091


# --- per-call timeout (the timeout lives in the chain, not around it) ------


class SlowProvider(FakeProvider):
    """A provider that never answers within the chain's per-call budget."""

    def __init__(self, key, delay=5.0):
        super().__init__(key, script=[])
        self.delay = delay
        self.attempts = 0

    async def complete(self, messages, tools, max_tokens):
        self.attempts += 1
        await asyncio.sleep(self.delay)
        raise AssertionError("unreachable")


async def test_a_timed_out_call_is_charged_and_the_chain_falls_through(tmp_path):
    """A cancelled call may already have been billed, so it must be recorded.

    The request reached Google; only our side gave up. Not recording it is the
    one path in the system that spends without accounting, and it makes
    ai_brain_ledger_remaining overstate the budget after every timeout.
    """
    ledger, _ = make_ledger(tmp_path, ["slow", "b"])
    slow = SlowProvider("slow")
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([slow, b], ledger, call_timeout_s=0.01)

    out = await chain.complete(MSGS, [], 512, "brain")

    assert out.text == "from-b"
    # Two attempts: timeout is retryable, so the same provider gets one more go.
    assert slow.attempts == 2
    spent = ledger.snapshot()["buckets"]["slow"]
    assert spent["requests_day"] == 2
    assert spent["tokens_day"] == 2 * 4000


async def test_a_slow_first_provider_does_not_starve_the_second(tmp_path):
    """Each provider gets its own timeout, so the fallback still has time."""
    ledger, _ = make_ledger(tmp_path, ["slow", "b"])
    slow = SlowProvider("slow", delay=0.2)
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([slow, b], ledger, call_timeout_s=0.01)

    started = time.monotonic()
    out = await chain.complete(MSGS, [], 512, "brain")

    assert out.text == "from-b"
    # Bounded by the per-call budget (twice), never by the provider's own delay.
    assert time.monotonic() - started < 0.2


async def test_from_settings_takes_the_call_timeout_from_settings(tmp_path, monkeypatch):
    class StubGemini:
        def __init__(self, model, api_key, thinking_budget=-1):
            self.key = f"gemini:{model}"

    stub = types.ModuleType("ai_brain.llm.gemini")
    stub.GeminiProvider = StubGemini
    monkeypatch.setitem(sys.modules, "ai_brain.llm.gemini", stub)

    settings = load_settings(
        {"LLM_CHAIN": "gemini:flash", "GEMINI_API_KEY": "k", "CALL_TIMEOUT_S": "12"}
    )
    ledger, _ = make_ledger(tmp_path, ["gemini:flash"])

    assert ProviderChain.from_settings(settings, ledger).call_timeout_s == 12


# --- the retry re-checks the budget it just spent -------------------------


async def test_a_drained_key_is_not_retried_after_the_first_attempt(tmp_path):
    """The first attempt can be the last of the budget; the retry must not ignore that."""
    ledger, _ = make_ledger(tmp_path, ["slow", "b"], rpd=1)
    slow = SlowProvider("slow")
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([slow, b], ledger, call_timeout_s=0.01)

    out = await chain.complete(MSGS, [], 512, "brain")

    assert out.text == "from-b"
    # The timeout charged the day's only request, so there is nothing to retry with.
    assert slow.attempts == 1
    assert ledger.snapshot()["buckets"]["slow"]["requests_day"] == 1


async def test_a_key_with_budget_left_is_still_retried(tmp_path):
    ledger, _ = make_ledger(tmp_path, ["slow", "b"], rpd=10)
    slow = SlowProvider("slow")
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([slow, b], ledger, call_timeout_s=0.01)

    await chain.complete(MSGS, [], 512, "brain")

    assert slow.attempts == 2


async def test_a_denied_retry_still_remembers_when_the_key_frees_up(tmp_path):
    """The chain must report a retry_at rather than an unexplained exhaustion."""
    ledger, _ = make_ledger(tmp_path, ["slow"], rpd=1)
    slow = SlowProvider("slow")
    chain = ProviderChain([slow], ledger, call_timeout_s=0.01)

    with pytest.raises(ChainExhausted) as caught:
        await chain.complete(MSGS, [], 512, "brain")

    assert caught.value.retry_at is not None
    assert slow.attempts == 1


# -- skip logging ---------------------------------------------------------


async def test_falling_through_to_the_next_provider_is_not_a_warning(tmp_path, caplog):
    """The expert floor sending an expert to the LAN is the design, not news."""
    ledger, _ = make_ledger(tmp_path, ["a", "b"], rpd=10)
    a = FakeProvider("a", [reply("from-a")] * 8)
    b = FakeProvider("b", [reply("from-b")])
    chain = ProviderChain([a, b], ledger)
    # Spend past the 40% expert floor, so "a" is brain-only from here.
    for _ in range(7):
        await chain.complete(MSGS, [], 512, "brain")

    with caplog.at_level("WARNING"):
        out = await chain.complete(MSGS, [], 512, "expert")

    assert out.text == "from-b"
    assert [r for r in caplog.records if "skipping" in r.message] == []


async def test_a_real_refusal_warns_once_and_then_stays_quiet(tmp_path, caplog):
    state = {"t": T0}
    ledger = Ledger(
        {"a": Limits(rpm=10, tpm=100_000, rpd=1), "b": Limits(rpm=10, tpm=100_000, rpd=100)},
        tmp_path / "ledger.json",
        clock=lambda: state["t"],
    )
    a = FakeProvider("a", [reply("from-a")])
    b = FakeProvider("b", [reply("1"), reply("2"), reply("3")])
    chain = ProviderChain([a, b], ledger)
    await chain.complete(MSGS, [], 512, "brain")  # spends a's only call

    with caplog.at_level("WARNING"):
        await chain.complete(MSGS, [], 512, "brain")
        await chain.complete(MSGS, [], 512, "brain")

    warned = [r for r in caplog.records if r.levelname == "WARNING" and "skipping a" in r.message]
    assert len(warned) == 1
    assert "rpd" in warned[0].message


async def test_a_key_that_recovers_can_warn_again(tmp_path, caplog):
    state = {"t": T0}
    ledger = Ledger(
        {"a": Limits(rpm=10, tpm=100_000, rpd=1), "b": Limits(rpm=10, tpm=100_000, rpd=100)},
        tmp_path / "ledger.json",
        clock=lambda: state["t"],
    )
    a = FakeProvider("a", [reply("1"), reply("2")])
    b = FakeProvider("b", [reply("b1"), reply("b2")])
    chain = ProviderChain([a, b], ledger)
    await chain.complete(MSGS, [], 512, "brain")

    with caplog.at_level("WARNING"):
        await chain.complete(MSGS, [], 512, "brain")  # warns: rpd
        state["t"] += 60 * 60 * 24  # next day: the daily budget resets
        await chain.complete(MSGS, [], 512, "brain")  # a answers again
        await chain.complete(MSGS, [], 512, "brain")  # warns: rpd, freshly

    warned = [r for r in caplog.records if r.levelname == "WARNING" and "skipping a" in r.message]
    assert len(warned) == 2


CHAIN_38 = "gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite,lan:qwen3-coder:30b"


def test_default_reserves_gemini_3_8_for_the_brain_and_the_rest_for_experts():
    limits = limits_from_settings(load_settings({"LLM_CHAIN": CHAIN_38}))
    assert limits["gemini:gemini-3.8-flash"].loops == frozenset({"brain"})
    assert limits["gemini:gemini-3.5-flash-lite"].loops == frozenset({"expert"})
    assert limits["lan:qwen3-coder:30b"].loops == frozenset({"expert"})


def test_all_lifts_the_split():
    settings = load_settings({"LLM_CHAIN": CHAIN_38, "BRAIN_MODELS": "all"})
    assert all(lim.loops is None for lim in limits_from_settings(settings).values())


def test_explicit_expert_models_can_share_a_key_with_the_brain():
    settings = load_settings(
        {
            "LLM_CHAIN": CHAIN_38,
            "BRAIN_MODELS": "gemini:gemini-3.8-flash,lan:qwen3-coder:30b",
            "EXPERT_MODELS": "gemini:gemini-3.5-flash-lite,lan:qwen3-coder:30b",
        }
    )
    limits = limits_from_settings(settings)
    assert limits["lan:qwen3-coder:30b"].loops is None
    assert limits["gemini:gemini-3.8-flash"].loops == frozenset({"brain"})


def test_custom_chain_without_the_default_brain_model_keeps_sharing():
    settings = load_settings({"LLM_CHAIN": "gemini:flash,fake:x"})
    assert settings.brain_models is None and settings.expert_models is None


def test_ledger_refuses_a_reserved_key_to_the_other_loop(tmp_path):
    ledger = Ledger(
        {"k": Limits(rpm=5, tpm=100_000, rpd=20, loops=frozenset({"brain"}))},
        tmp_path / "l.json",
        clock=lambda: 1_757_000_000.0,
    )
    assert ledger.can_spend("k", "brain").allowed
    refused = ledger.can_spend("k", "expert")
    assert not refused.allowed and refused.reason == "reserved"
