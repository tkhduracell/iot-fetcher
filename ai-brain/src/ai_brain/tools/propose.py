"""The brain's only way to ask for something to happen in the physical house.

It does not act. It files a request that a human approves in Slack, and comes
straight back with ``status: pending`` so the model knows the speaker has not
spoken yet and should not say it has. The outcome arrives later as an inbox
note, on a future cycle.

Experts do not get this tool: the registry's allowlist refuses them, so an
expert that wants the house to do something has to send the brain a note and
let the brain decide whether it is worth a human's attention.
"""

from __future__ import annotations

import re

from ai_brain.approvals import KINDS, PENDING
from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

BRAIN_ONLY = frozenset({"brain"})

# How many of the most recent terminal (executed/rejected/failed/expired)
# proposals list_proposals shows alongside every pending one. Unbounded would
# mean an outbox that has been running for weeks turns one call into the
# whole history; this is enough to notice "I already asked about this
# yesterday" without that.
RECENT_TERMINAL_LIMIT = 20

# On a weak local fallback model, ``propose`` sometimes gets called with the
# model's own prompt text as the item -- "Start the think cycle", "Pick up an
# open thread. Read your recent journal…" -- rather than anything Filip
# should see in Slack. 17 of 23 real proposals were this junk. The guard below
# catches it before a human ever sees the message, and the error names the
# reason so the model can learn what "an actual request" looks like instead of
# just trying again with the same words.

_WORD_RE = re.compile(r"[a-z0-9]+")

# Words that name the *mechanism* of thinking rather than a subject a human
# would recognise -- the giveaway that the model proposed its own scaffolding
# instead of a request. "chat" is deliberately not included here: it is
# Filip's own reserved topic name, already refused by approvals.propose with
# a more specific reason ("is reserved; pick a topic naming this proposal"),
# and that error must not be shadowed by this more generic one.
#
# This must NOT be a "does any word appear" check: "dishwasher-cycle",
# "washing-machine-cycle", "wake-up-light", "thread-network" (Thread/Matter is
# a real smart-home protocol) and "sunrise-wake" are all real subjects that
# happen to contain a meta word next to a real one. What actually
# distinguishes junk is that the *whole* topic is built only from meta words
# plus filler -- "think-cycle-2026-09-24" has nothing else in it, an actual
# subject always does.
_META_TOPIC_WORDS = frozenset(
    {
        "think",
        "thinking",
        "cycle",
        "wake",
        "unfinished",
        "thought",
        "thoughts",
        "thread",
        "greeting",
        "greetings",
        "next",
        "pick",
        "up",
        "to",
    }
)

# Digits (a date/timestamp suffix like "-2026-09-24") do not make a topic any
# less meta -- they are filler, not a subject -- so they are stripped before
# checking whether every remaining word is a meta word.
_DIGIT_RE = re.compile(r"^[0-9]+$")

# A title/body must share at least this fraction of its *content* words with
# a known prompt sentence to count as a restatement of it rather than a
# coincidence. Stopwords are excluded from both sides before this is computed
# (see _content_words) -- otherwise a short real item like "Water the plants
# in the living room" shares "the"/"in" with half of CYCLE_INSTRUCTIONS and
# nothing else, which is not what "restates your own instructions" means.
_ANGLE_OVERLAP_THRESHOLD = 0.6

# However high the overlap fraction, a couple of shared stopword-free words is
# still coincidence, not a restatement -- "check it" vs. "check it later"
# would otherwise be 100% overlap on one real word. Requiring a handful of
# shared content words is what actually distinguishes "this is the model's
# own prompt back at it" from "these two sentences both happen to be short".
MIN_SHARED_CONTENT_WORDS = 3

# Real to-dos and things Filip actually says are often this short ("Buy
# milk", "Dinner ready", "Good morning") -- the echo and meta-topic checks
# are what catch scaffolding text, not a floor on ordinary human brevity.
MIN_ITEM_WORDS = 2

# Ordinary English function words: excluded before computing token overlap
# for the echo check, so two unrelated sentences that both happen to use
# "the"/"a"/"to" do not register as one restating the other. Deliberately
# small and closed-class rather than a general stopword list off the shelf --
# this only needs to cover the words that show up in both prompt sentences
# and everyday requests.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "in",
        "on",
        "of",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "your",
        "you",
        "this",
        "that",
        "for",
        "with",
        "at",
        "as",
        "so",
        "not",
    }
)


def _words(text: str) -> frozenset[str]:
    return frozenset(_WORD_RE.findall(text.lower()))


def _content_words(text: str) -> frozenset[str]:
    return _words(text) - _STOPWORDS


def _prompt_sentences() -> tuple[str, ...]:
    """Every sentence the model is ever shown as an instruction, not a request.

    Imported lazily (inside the function, not at module scope) to avoid a
    circular import at process start: ``ai_brain.loop`` imports
    ``ai_brain.tools`` (this package's ``__init__``), so importing
    ``ai_brain.loop`` back at ``propose.py``'s own module scope would run
    while that package init is still mid-import.
    """
    from ai_brain.loop import BRAIN_ANGLES, CYCLE_INSTRUCTIONS, EXPERT_ANGLES

    sentences = list(BRAIN_ANGLES) + list(EXPERT_ANGLES)
    sentences.extend(s.strip() for s in CYCLE_INSTRUCTIONS.split(".") if s.strip())
    return tuple(sentences)


def _echoes_a_prompt(text: str) -> str | None:
    """The prompt sentence ``text`` most looks like a restatement of, if any."""
    text_words = _content_words(text)
    for sentence in _prompt_sentences():
        sentence_words = _content_words(sentence)
        overlap = len(text_words & sentence_words)
        if overlap < MIN_SHARED_CONTENT_WORDS:
            continue
        smaller = min(len(text_words), len(sentence_words))
        if smaller and overlap / smaller >= _ANGLE_OVERLAP_THRESHOLD:
            return sentence
    return None


# Only the payload keys that carry prose a human would actually read in
# Slack -- "item" for ha_todo_add, "text" for sonos_say. Deliberately not
# every key KINDS lists for a kind: ha_service's "service" and "entity_id"
# (e.g. "light.turn_off", "light.kitchen") and docker_restart's "container"
# are short identifiers, not sentences a model would restate its own prompt
# into, and checking them against MIN_ITEM_WORDS would reject every ordinary
# entity id for being "too short".
_PROSE_FIELDS = frozenset({"item", "text"})


def junk_proposal_reason(kind: str, payload: dict, topic: str) -> str | None:
    """None if this proposal looks like a real request; else why it is not.

    Pure and side-effect free so it can be unit tested directly, without a
    running approvals system. ``_propose`` is the only caller in production.
    """
    text_fields = [
        str(payload[key])
        for key in KINDS.get(kind, ())
        if key in _PROSE_FIELDS and isinstance(payload.get(key), str)
    ]
    topic_norm = topic.strip().lower()
    topic_words = _words(topic)
    # Filler that carries no subject of its own: a date/timestamp suffix, or
    # one of the tiny connector words a meta phrase like "thread to pick up"
    # is built from. Once these are removed, whatever is left is either
    # nothing (the whole topic was meta + filler) or an actual subject.
    meaningful_words = {w for w in topic_words if not _DIGIT_RE.match(w)}

    if meaningful_words and meaningful_words <= _META_TOPIC_WORDS:
        return f"topic {topic!r} names the thinking process, not a subject -- pick what this is actually about"

    for field_text in text_fields:
        stripped = field_text.strip()
        if len(_words(stripped)) < MIN_ITEM_WORDS:
            return f"too short to be a real request: {field_text!r}"
        if stripped.strip().lower() == topic_norm:
            return "the item is just the topic repeated -- say what you actually want done"
        echoed = _echoes_a_prompt(stripped)
        if echoed is not None:
            return (
                f"this restates your own instructions ({echoed!r}) rather than asking for "
                "something -- propose the actual request that came out of following it"
            )
    return None


async def _propose(ctx: ToolContext, args: dict) -> str:
    approvals = ctx.extras.get("approvals")
    if approvals is None:
        return err("approvals are not configured in this process")

    payload = args["payload"]
    if not isinstance(payload, dict):
        return err(f"payload must be an object, got {type(payload).__name__}")

    junk_reason = junk_proposal_reason(str(args["kind"]), payload, str(args["topic"]))
    if junk_reason is not None:
        return err(f"refused: {junk_reason}")

    try:
        proposal = await approvals.propose(
            str(args["kind"]),
            payload,
            str(args["reason"]),
            str(args["topic"]),
        )
    except ValueError as exc:
        return err(str(exc))
    except RuntimeError as exc:
        # Slack was unreachable, so there is no message for a human to react
        # to and nothing was left pending. Saying so plainly is what lets the
        # model try again on a later cycle instead of assuming it asked.
        return err(f"{exc}; nothing was proposed, try again on a later cycle")
    return ok({"id": proposal.id, "status": proposal.status})


async def _list_proposals(ctx: ToolContext, args: dict) -> str:
    approvals = ctx.extras.get("approvals")
    if approvals is None:
        return err("approvals are not configured in this process")

    all_proposals = approvals.all()
    pending = [p for p in all_proposals if p.status == PENDING]
    terminal = [p for p in all_proposals if p.status != PENDING][-RECENT_TERMINAL_LIMIT:]

    def _brief(p) -> dict:
        return {
            "id": p.id,
            "kind": p.kind,
            "payload": p.payload,
            "reason": p.reason,
            "status": p.status,
            "created": p.created,
        }

    return ok(
        {
            "pending": [_brief(p) for p in pending],
            "recent": [_brief(p) for p in terminal],
        }
    )


def register_propose_tool(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="propose",
                description=(
                    "Ask a human to approve one physical action. Nothing happens until they "
                    "react in Slack, so this returns 'pending' -- never report the action as "
                    "done. The outcome arrives as an inbox note on a later cycle. "
                    "Check list_proposals first if the thing you are about to ask for sounds "
                    "like something you may have already asked -- a pending proposal is still "
                    "waiting on a human, and asking again just duplicates the Slack message; a "
                    "recently rejected one probably should not be re-asked either. "
                    "Kinds: sonos_say (payload {\"text\": ...}, speaks aloud, refused between "
                    "22:00 and 07:00), ha_todo_add (payload {\"item\": ...}), ha_service "
                    "(payload {\"service\": \"light.turn_off\", \"entity_id\": \"light.kitchen\", "
                    "optional \"data\": {\"brightness_pct\": 40}}) for lights, switches, scenes, "
                    "scripts, covers, fans, climate and media players -- look the entity up with "
                    "ha_context first, and expect anything outside that allowlist to be refused -- "
                    "and docker_restart (payload {\"container\": \"iot-fetcher\"}) for a container "
                    "that is crash-looping or stuck, found with docker_ps first."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": sorted(KINDS)},
                        "payload": {"type": "object"},
                        "reason": {
                            "type": "string",
                            "description": "Why you are asking, in one line, for the human.",
                        },
                        "topic": {
                            "type": "string",
                            "description": (
                                "Short subject naming this proposal, e.g. 'roborock' or "
                                "'pool-pump'. Never 'chat' -- that thread is reserved for "
                                "Filip's own open conversation with you and is repointed "
                                "every time he starts a new one."
                            ),
                        },
                    },
                    "required": ["kind", "payload", "reason", "topic"],
                },
            ),
            fn=_propose,
            loops=BRAIN_ONLY,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="list_proposals",
                description=(
                    "See every proposal you currently have pending, plus your most recent "
                    "resolved ones (executed, rejected, failed, blocked, expired). Call this "
                    "before propose when what you are about to ask for might be something "
                    "you already asked -- reworded or not, the point is the physical thing "
                    "being requested, not the exact wording. Asking again while the same "
                    "request is still pending just posts a second, redundant Slack message a "
                    "human has to react to."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            fn=_list_proposals,
            loops=BRAIN_ONLY,
        )
    )
