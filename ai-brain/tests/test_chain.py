import dataclasses
import sys
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
        def __init__(self, model, api_key):
            self.key = f"gemini:{model}"
            self.model = model
            self.api_key = api_key

    stub = types.ModuleType("ai_brain.llm.gemini")
    stub.GeminiProvider = StubGemini
    monkeypatch.setitem(sys.modules, "ai_brain.llm.gemini", stub)

    settings = load_settings({"LLM_CHAIN": "gemini:flash,gemini:pro,fake:x", "GEMINI_API_KEY": "k"})
    ledger, _ = make_ledger(tmp_path, ["gemini:flash", "gemini:pro", "fake:x"])
    chain = ProviderChain.from_settings(settings, ledger)

    assert [p.key for p in chain.providers] == ["gemini:flash", "gemini:pro", "fake:x"]
    assert chain.providers[0].api_key == "k"
    assert isinstance(chain.providers[2], FakeProvider)


def test_from_settings_rejects_unknown_provider(tmp_path):
    settings = load_settings({"LLM_CHAIN": "ollama:llama3"})
    ledger, _ = make_ledger(tmp_path, ["ollama:llama3"])
    with pytest.raises(ValueError, match="provider 'ollama' not implemented"):
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
