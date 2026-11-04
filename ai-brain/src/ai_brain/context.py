"""What a model is shown of the conversation, decided by the harness.

Storage and context are separate jobs. The session log keeps everything; this
decides what fits in front of one model on one call, and it only ever returns a
new list. The loop's own ``messages`` stay whole, so a fallback to a model with
a larger window gets the larger view back, and nothing is lost to a window that
happened to be small on one round.

It used to live inside the Ollama provider, which had two problems. A provider
sees one call, so it could only drop a result with no way back to it. And the
loop could not know about it, so nothing recorded that it had happened.

This has to run per provider, not per round, because the chain picks who answers
only once it is walking the list. See ``ProviderChain.complete``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import replace

from ai_brain.llm import Message, ToolSpec
from ai_brain.profiles import ModelProfile

log = logging.getLogger(__name__)

# Rough tokens-per-character, used only to decide whether to trim. The server
# does the real tokenization. English averages ~4 chars/token; rounding down
# over-counts tokens, so a trim comes before the server's own truncation would.
CHARS_PER_TOKEN = 4

# Chat-template framing (role markers and the like) that no message's text
# contains. Fixed and conservative, since this is a trim decision and not a bill.
TEMPLATE_OVERHEAD_TOKENS = 512

# What a turn the policy cut down starts with, so the provider's guard can leave it be.
STUB_PREFIX = "[trimmed"


def read_back(seq: int) -> str:
    """The call that fetches one logged event back."""
    return f"read_cycle_events(from={seq}, to={seq})"


def estimate_tokens(messages: Iterable[Message]) -> int:
    """A cheap estimate of everything these turns put on the wire.

    Thinking and tool-call arguments count too: on a thinking model the echoed
    reasoning can outweigh the text it led to.
    """
    chars = 0
    for message in messages:
        chars += len(message.content) + len(message.thinking)
        for call in message.tool_calls:
            chars += len(call.name) + len(json.dumps(call.args, ensure_ascii=False, default=str))
    return chars // CHARS_PER_TOKEN


def estimate_tool_tokens(tools: Iterable[ToolSpec]) -> int:
    """What the tool list costs. Tens of descriptions go out on every call, and a
    budget that ignores them can leave a small window overflowing after all."""
    chars = sum(
        len(t.name) + len(t.description) + len(json.dumps(t.parameters, default=str)) for t in tools
    )
    return chars // CHARS_PER_TOKEN


def _stub(message: Message) -> str:
    size = len(message.content)
    if message.log_seq:
        return f"{STUB_PREFIX} from context: {size} chars; {read_back(message.log_seq)} has it]"
    return f"{STUB_PREFIX} from context: {size} chars]"


def fit_context(
    messages: list[Message], profile: ModelProfile, reserve_tokens: int = 0
) -> list[Message]:
    """``messages`` as this model should see them, trimmed only if they must be.

    ``reserve_tokens`` is what the window has to keep free: the reply, the tool
    list. A model with no window of ours to respect gets the list back untouched,
    the same object, as does a prompt that fits.

    What goes, in order, and why:

    1. Reasoning echoed on earlier turns, oldest first. It is the most expensive
       text in the conversation and the least needed verbatim once acted on.
    2. Tool results, oldest first. Bulky, already digested by the model, and the
       stub says how to get one back.

    Never the system turns, which is what the server's own truncation would have
    eaten first, and never the last turn. If that is still too much, the provider's
    guard has the last word.
    """
    if profile.num_ctx is None:
        return messages
    budget = max(profile.num_ctx - reserve_tokens - TEMPLATE_OVERHEAD_TOKENS, 1)
    estimated = estimate_tokens(messages)
    if estimated <= budget:
        return messages

    fitted = list(messages)
    last = len(fitted) - 1
    for index in range(last):
        if estimated <= budget:
            return fitted
        turn = fitted[index]
        if turn.role == "assistant" and turn.thinking:
            estimated -= len(turn.thinking) // CHARS_PER_TOKEN
            fitted[index] = replace(turn, thinking="")
    for index in range(last):
        if estimated <= budget:
            return fitted
        turn = fitted[index]
        if turn.role == "tool":
            stub = _stub(turn)
            if len(stub) < len(turn.content):
                estimated -= (len(turn.content) - len(stub)) // CHARS_PER_TOKEN
                fitted[index] = replace(turn, content=stub)
    if estimated > budget:
        log.warning(
            "%s: prompt ~%d tokens still over its ~%d budget after trimming every "
            "tool result; what is left is the opening turns",
            profile.key,
            estimated,
            budget,
        )
    return fitted
