import anthropic
import httpx2
import pytest
from anthropic.types import Message as SdkMessage

from ai_brain.config import load_settings
from ai_brain.ledger import Ledger, Limits, Pool
from ai_brain.llm import (
    ANTHROPIC_POOL,
    Message,
    ProviderChain,
    ProviderError,
    Reply,
    ToolCall,
    ToolSpec,
    Usage,
    limits_from_settings,
)
from ai_brain.llm.claude import (
    MIN_MAX_TOKENS,
    AnthropicProvider,
    build_request,
    classify,
    effort_for,
    price_usd,
)
from ai_brain.llm.fake import FakeProvider

MODEL = "claude-opus-5-5"
TOOL = ToolSpec("read_fact", "Read a fact", {"type": "object", "properties": {}})


def sdk_message(content, stop_reason="end_turn", usage=None, **extra):
    return SdkMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage
            or {
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_read_input_tokens": 9000,
                "cache_creation_input_tokens": 500,
            },
            **extra,
        }
    )


class FakeMessages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, *outcomes):
        self.messages = FakeMessages(outcomes)


def status_error(cls, status, message, error_type):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(
        status,
        request=request,
        json={"type": "error", "error": {"type": error_type, "message": message}},
        headers={"retry-after": "7"} if status == 429 else {},
    )
    return cls(
        message, response=response, body={"type": "error", "error": {"type": error_type}}
    )


def provider(*outcomes, effort=None):
    client = FakeClient(*outcomes)
    return AnthropicProvider(MODEL, "sk-test", client=client, effort=effort), client


# ---------- request building ----------


def test_system_tools_cache_and_thinking_shape():
    kwargs = build_request(
        [Message("system", "be calm"), Message("user", "hi")], [TOOL], 8000, MODEL, "high"
    )
    assert kwargs["system"] == "be calm"
    assert kwargs["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert kwargs["tools"] == [
        {"name": "read_fact", "description": "Read a fact", "input_schema": TOOL.parameters}
    ]
    assert kwargs["cache_control"] == {"type": "ephemeral"}
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"] == {"effort": "high"}
    # The loop's 8000 leaves no room once thinking eats into it.
    assert kwargs["max_tokens"] == MIN_MAX_TOKENS
    # Forced tool choice 400s on these models; never sent.
    assert "tool_choice" not in kwargs


def test_stable_system_half_gets_its_own_cache_breakpoint():
    """The loop's first system message survives between cycles; a breakpoint
    after it lets a cycle within the TTL read tools + that head from cache."""
    kwargs = build_request(
        [Message("system", "constitution"), Message("system", "journal"), Message("user", "hi")],
        [TOOL],
        8000,
        MODEL,
    )
    assert kwargs["system"] == [
        {"type": "text", "text": "constitution", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "journal"},
    ]
    # The moving top-level breakpoint is still there for the rounds in a cycle.
    assert kwargs["cache_control"] == {"type": "ephemeral"}


def test_no_effort_sends_no_output_config():
    kwargs = build_request([Message("user", "hi")], [], 8000, MODEL, None)
    assert "output_config" not in kwargs
    assert "tools" not in kwargs


def test_own_turn_is_echoed_verbatim_with_thinking_blocks():
    blocks = (
        {"type": "thinking", "thinking": "summary", "signature": "sig"},
        {"type": "tool_use", "id": "toolu_1", "name": "read_fact", "input": {}},
    )
    msgs = [
        Message("user", "go"),
        Message(
            "assistant",
            "",
            tool_calls=(ToolCall("toolu_1", "read_fact", {}),),
            model=MODEL,
            provider_blocks=blocks,
        ),
        Message("tool", "fact body", tool_call_id="toolu_1", name="read_fact"),
        Message("user", "wrap up now"),
    ]
    turns = build_request(msgs, [TOOL], 8000, MODEL)["messages"]
    assert turns[1] == {"role": "assistant", "content": list(blocks)}
    # Tool result first, then the nudge in the same user turn.
    assert turns[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "fact body"},
            {"type": "text", "text": "wrap up now"},
        ],
    }
    assert len(turns) == 3


def test_foreign_turn_is_replayed_as_text():
    msgs = [
        Message("user", "go"),
        Message(
            "assistant",
            "checking",
            tool_calls=(ToolCall("call_1", "read_fact", {"title": "x"}),),
            model="gemini-3.8-flash",
            thought_signature="gsig",
        ),
        Message("tool", "body", tool_call_id="call_1", name="read_fact"),
    ]
    turns = build_request(msgs, [TOOL], 8000, MODEL)["messages"]
    assert turns[1]["role"] == "assistant"
    assert turns[1]["content"] == [
        {"type": "text", "text": 'checking\nI called read_fact({"title": "x"})'}
    ]
    # No tool_result pointing at a tool_use the API never saw.
    assert turns[2]["content"] == [{"type": "text", "text": "Result of read_fact: body"}]


def test_another_claude_models_turn_is_foreign_too():
    msgs = [
        Message("user", "go"),
        Message(
            "assistant",
            "",
            tool_calls=(ToolCall("toolu_9", "read_fact", {}),),
            model="claude-haiku-5-5",
            provider_blocks=({"type": "tool_use", "id": "toolu_9", "name": "read_fact", "input": {}},),
        ),
    ]
    turns = build_request(msgs, [TOOL], 8000, MODEL)["messages"]
    assert turns[1]["content"][0]["type"] == "text"


def test_empty_tool_result_is_not_sent_empty():
    msgs = [
        Message("user", "go"),
        Message(
            "assistant",
            "",
            tool_calls=(ToolCall("toolu_1", "read_fact", {}),),
            model=MODEL,
            provider_blocks=({"type": "tool_use", "id": "toolu_1", "name": "read_fact", "input": {}},),
        ),
        Message("tool", "", tool_call_id="toolu_1", name="read_fact"),
    ]
    turns = build_request(msgs, [TOOL], 8000, MODEL)["messages"]
    assert turns[2]["content"][0]["content"] == "(empty result)"


# ---------- replies ----------


async def test_reply_maps_text_thinking_calls_usage_and_cost():
    p, client = provider(
        sdk_message(
            [
                {"type": "thinking", "thinking": "hmm", "signature": "sig"},
                {"type": "text", "text": "reading"},
                {"type": "tool_use", "id": "toolu_1", "name": "read_fact", "input": {"t": 1}},
            ],
            stop_reason="tool_use",
        )
    )
    reply = await p.complete([Message("user", "go")], [TOOL], 8000)
    assert reply.text == "reading"
    assert reply.thinking == "hmm"
    assert reply.tool_calls == (ToolCall("toolu_1", "read_fact", {"t": 1}),)
    assert reply.key == "anthropic:claude-opus-5-5"
    assert reply.model == MODEL
    assert [b["type"] for b in reply.provider_blocks] == ["thinking", "text", "tool_use"]
    assert reply.usage.prompt_tokens == 1000 + 9000 + 500
    assert reply.usage.cached_tokens == 9000
    assert reply.usage.cache_write_tokens == 500
    expected = (1000 * 4 + 500 * 4 * 1.25 + 9000 * 0.20 + 200 * 20) / 1e6
    assert reply.usage.cost_usd == pytest.approx(expected)
    assert client.messages.calls[0]["model"] == MODEL


async def test_cut_off_tool_call_is_dropped():
    p, _ = provider(
        sdk_message(
            [
                {"type": "text", "text": "partial"},
                {"type": "tool_use", "id": "toolu_1", "name": "read_fact", "input": {}},
            ],
            stop_reason="max_tokens",
        )
    )
    reply = await p.complete([Message("user", "go")], [TOOL], 8000)
    assert reply.tool_calls == ()
    assert [b["type"] for b in reply.provider_blocks] == ["text"]


async def test_refusal_raises_refused_with_usage():
    p, _ = provider(sdk_message([], stop_reason="refusal"))
    with pytest.raises(ProviderError) as info:
        await p.complete([Message("user", "go")], [], 8000)
    assert info.value.kind == "refused"
    assert info.value.usage is not None and info.value.usage.cost_usd > 0


# ---------- errors ----------


@pytest.mark.parametrize(
    ("status", "error_type", "message", "kind"),
    [
        (400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API.", "exhausted"),
        (402, "billing_error", "payment required", "exhausted"),
        (429, "rate_limit_error", "slow down", "rate_limited"),
        (529, "overloaded_error", "overloaded", "server"),
        (500, "api_error", "oops", "server"),
        (404, "not_found_error", "no such model", "not_found"),
        (401, "authentication_error", "bad key", "not_found"),
        (400, "invalid_request_error", "messages: bad shape", "bad_request"),
    ],
)
def test_classify(status, error_type, message, kind):
    assert classify(status, error_type, message) == kind


async def test_credit_balance_400_becomes_exhausted():
    err = status_error(
        anthropic.BadRequestError,
        400,
        "Your credit balance is too low to access the Anthropic API.",
        "invalid_request_error",
    )
    p, _ = provider(err)
    with pytest.raises(ProviderError) as info:
        await p.complete([Message("user", "go")], [], 8000)
    assert info.value.kind == "exhausted"


async def test_429_carries_retry_after():
    p, _ = provider(status_error(anthropic.RateLimitError, 429, "slow", "rate_limit_error"))
    with pytest.raises(ProviderError) as info:
        await p.complete([Message("user", "go")], [], 8000)
    assert info.value.kind == "rate_limited"
    assert info.value.retry_after_s == 7.0


# ---------- pricing / config ----------


def test_haiku_long_prompt_is_priced_at_5x():
    short = Usage(prompt_tokens=50_000, completion_tokens=1000)
    long = Usage(prompt_tokens=150_000, completion_tokens=1000)
    assert price_usd("claude-haiku-5-5", short) == pytest.approx((50_000 * 0.1 + 1000 * 0.5) / 1e6)
    assert price_usd("claude-haiku-5-5", long) == pytest.approx(
        5 * (150_000 * 0.1 + 1000 * 0.5) / 1e6
    )


def test_unknown_model_is_priced_as_the_most_expensive():
    usage = Usage(prompt_tokens=1000, completion_tokens=1000)
    assert price_usd("claude-new-9", usage) == price_usd("claude-fable-5-1", usage)


def test_effort_mapping():
    assert effort_for(None) is None
    assert effort_for("true") is None
    assert effort_for("false") == "low"
    assert effort_for("xhigh") == "xhigh"


def test_from_settings_builds_anthropic_provider_with_effort(tmp_path):
    settings = load_settings(
        {
            "LLM_CHAIN": "anthropic:claude-opus-5-5,anthropic:claude-haiku-5-5",
            "ANTHROPIC_API_KEY": "sk-test",
            "BRAIN_MODELS": "anthropic:claude-opus-5-5",
            "EXPERT_MODELS": "anthropic:claude-haiku-5-5",
        }
    )
    limits = limits_from_settings(settings)
    assert limits["anthropic:claude-opus-5-5"].usd_pool == ANTHROPIC_POOL
    assert limits["anthropic:claude-opus-5-5"].rpm == 50
    assert limits["anthropic:claude-opus-5-5"].loops == frozenset({"brain"})
    ledger = Ledger(limits, tmp_path / "ledger.json")
    chain = ProviderChain.from_settings(settings, ledger)
    opus, haiku = chain.providers
    assert isinstance(opus, AnthropicProvider) and opus._effort == "medium"
    assert isinstance(haiku, AnthropicProvider) and haiku._effort is None
    assert opus.call_timeout_s == 300


# ---------- chain integration ----------


def pool_ledger(tmp_path, keys):
    limits = {
        key: Limits(rpm=50, tpm=10**8, rpd=10**6, usd_pool=ANTHROPIC_POOL)
        if key.startswith("anthropic:")
        else Limits(rpm=50, tpm=10**8, rpd=10**6)
        for key in keys
    }
    return Ledger(
        limits,
        tmp_path / "ledger.json",
        clock=lambda: 1_757_000_000.0,
        pools={ANTHROPIC_POOL: Pool(30.0, 1)},
    )


def backup():
    return FakeProvider(
        "gemini:x",
        script=[Reply(text="ok", tool_calls=(), usage=Usage(1, 1), model="x", key="gemini:x")],
    )


async def test_exhausted_parks_pool_and_falls_through(tmp_path):
    ledger = pool_ledger(tmp_path, ["anthropic:claude-opus-5-5", "gemini:x"])
    claude, _ = provider(
        status_error(
            anthropic.BadRequestError,
            400,
            "Your credit balance is too low to access the Anthropic API.",
            "invalid_request_error",
        )
    )
    chain = ProviderChain([claude, backup()], ledger)
    reply = await chain.complete([Message("user", "go")], [], 512, "brain")
    assert reply.key == "gemini:x"
    decision = ledger.can_spend("anthropic:claude-opus-5-5", "brain")
    assert not decision.allowed and decision.reason == "exhausted"


async def test_success_charges_the_pool(tmp_path):
    ledger = pool_ledger(tmp_path, ["anthropic:claude-opus-5-5"])
    claude, _ = provider(sdk_message([{"type": "text", "text": "done"}]))
    chain = ProviderChain([claude], ledger)
    reply = await chain.complete([Message("user", "go")], [], 512, "brain")
    [pool] = ledger.pools()
    assert pool["usd_day"] == pytest.approx(reply.usage.cost_usd, abs=1e-4)
    assert pool["usd_period"] == pytest.approx(reply.usage.cost_usd, abs=1e-4)


async def test_refusal_is_charged_and_falls_through(tmp_path):
    ledger = pool_ledger(tmp_path, ["anthropic:claude-opus-5-5", "gemini:x"])
    claude, _ = provider(sdk_message([], stop_reason="refusal"))
    reply = await ProviderChain([claude, backup()], ledger).complete(
        [Message("user", "go")], [], 512, "brain"
    )
    assert reply.key == "gemini:x"
    assert ledger.pools()[0]["usd_day"] > 0


# ---------- review fixes ----------


async def test_sdk_timeout_is_charged_at_worst_case(tmp_path):
    p, _ = provider(
        sdk_message([{"type": "text", "text": "first"}]),
        anthropic.APITimeoutError(httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
    )
    await p.complete([Message("user", "go")], [], 8000)
    with pytest.raises(ProviderError) as info:
        await p.complete([Message("user", "go")], [], 8000)
    assert info.value.kind == "timeout"
    # Last prompt (10500) uncached + a full 16k output on Opus.
    expected = (10_500 * 4 + MIN_MAX_TOKENS * 20) / 1e6
    assert info.value.usage.cost_usd == pytest.approx(expected)


async def test_chain_timeout_charges_the_pool(tmp_path):
    import asyncio

    class Hangs(AnthropicProvider):
        call_timeout_s = 0.01

        async def complete(self, messages, tools, max_tokens):
            await asyncio.sleep(1)

    ledger = pool_ledger(tmp_path, ["anthropic:claude-opus-5-5", "gemini:x"])
    hang = Hangs(MODEL, "sk-test", client=FakeClient())
    reply = await ProviderChain([hang, backup()], ledger).complete(
        [Message("user", "go")], [], 512, "brain"
    )
    assert reply.key == "gemini:x"
    # Two attempts, each charged as a full output budget.
    assert ledger.pools()[0]["usd_day"] == pytest.approx(
        2 * MIN_MAX_TOKENS * 20 / 1e6, abs=1e-4
    )


async def test_expert_spend_is_recorded_as_expert_share(tmp_path):
    ledger = pool_ledger(tmp_path, ["anthropic:claude-opus-5-5"])
    claude, _ = provider(sdk_message([{"type": "text", "text": "done"}]))
    await ProviderChain([claude], ledger).complete([Message("user", "go")], [], 512, "expert")
    [pool] = ledger.pools()
    assert pool["usd_day_expert"] == pytest.approx(pool["usd_day"])
    assert pool["usd_day_expert"] > 0


def test_cache_reads_count_a_tenth_against_the_prompt_budget():
    usage = Usage(prompt_tokens=100_000, completion_tokens=0, cached_tokens=90_000)
    assert usage.budget_tokens == 10_000 + 9_000
    assert Usage(prompt_tokens=5000, completion_tokens=0).budget_tokens == 5000


@pytest.mark.parametrize("raw", ["nan", "inf", "-1"])
def test_monthly_cap_must_be_finite_and_non_negative(raw):
    with pytest.raises(ValueError):
        load_settings({"CLAUDE_MONTHLY_USD": raw})
