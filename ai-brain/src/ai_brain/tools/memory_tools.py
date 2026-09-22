"""Tools that let an agent read and write its own memory.

These are the only tools every loop gets: the journal it thinks in, the facts
it keeps, the notes it sends to its peers, and the ``end_cycle`` call that says
"I am done, wake me in N minutes". Rewriting goals or identity is the brain's
alone -- an expert that asks for it gets a policy refusal from the registry.
"""

from __future__ import annotations

import re

from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

BRAIN_ONLY = frozenset({"brain"})

_NO_ARGS: dict = {"type": "object", "properties": {}}

# ``safe_name`` treats "-", "_" and "." as ordinary characters, so
# "tibber-bridge-baseline" and "tibber_bridge_baseline" are two different
# files -- nothing normalises them together. This is the same split, used
# only to *compare* names for write_fact's duplicate warning; it never
# touches what actually gets written to disk.
_NAME_TOKENS = re.compile(r"[-_.]+")

# A new name sharing this fraction or more of its tokens with an existing one
# is flagged. High enough that "goals" (one token, shared by every persona's
# housekeeping fact) does not flag against unrelated single-token names, low
# enough to catch "tibber-bridge-baseline" against "tibber_bridge_status"
# (2 of 3 tokens shared each way).
_SIMILAR_THRESHOLD = 0.5


def _tokens(name: str) -> frozenset[str]:
    return frozenset(t for t in _NAME_TOKENS.split(name.lower()) if t)


def _similar_fact_names(name: str, existing: list[str]) -> list[str]:
    """Existing fact names that look like they are about the same thing as
    ``name``, so a new write can be shown them instead of silently adding a
    third spelling of a fact already on disk. Excludes an exact match --
    that is an ordinary overwrite, not a duplicate.

    A single-token name (``goals``, ``status``) is skipped rather than
    compared: a lone generic word shared with an unrelated longer name (e.g.
    ``deploy_status``) would hit the threshold every time on a coincidence,
    not a real duplicate, and a one-word name is short enough that Filip or
    the agent will notice a real clash without this hint's help.
    """
    new_tokens = _tokens(name)
    if len(new_tokens) < 2:
        return []
    found = []
    for other in existing:
        if other == name:
            continue
        other_tokens = _tokens(other)
        if len(other_tokens) < 2:
            continue
        overlap = len(new_tokens & other_tokens)
        if overlap == 0:
            continue
        smaller = min(len(new_tokens), len(other_tokens))
        if overlap / smaller >= _SIMILAR_THRESHOLD:
            found.append(other)
    return sorted(found)


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


async def _append_journal(ctx: ToolContext, args: dict) -> str:
    ctx.memory.append_journal(str(args["line"]))
    return ok({"appended": True})


async def _write_fact(ctx: ToolContext, args: dict) -> str:
    name = str(args["name"])
    # Read before writing: once written, name is its own exact match and
    # would never show up as a "similar" existing name against itself.
    similar = _similar_fact_names(name, ctx.memory.list_facts())
    ctx.memory.write_fact(name, str(args["title"]), str(args["body"]))
    result: dict = {"written": name}
    if similar:
        result["similar_existing_facts"] = similar
        result["hint"] = (
            "these look like they may be about the same thing -- read them, and if so "
            "delete_fact the old name(s) so this is not a third copy of the same fact"
        )
    return ok(result)


async def _read_fact(ctx: ToolContext, args: dict) -> str:
    fact = ctx.memory.read_fact(str(args["name"]))
    if fact is None:
        return ok(None)
    return ok({"name": fact.name, "title": fact.title, "body": fact.body})


async def _delete_fact(ctx: ToolContext, args: dict) -> str:
    name = str(args["name"])
    if not ctx.memory.delete_fact(name):
        return err(f"no such fact: {name}")
    return ok({"deleted": name})


async def _list_facts(ctx: ToolContext, args: dict) -> str:
    return ok(ctx.memory.list_facts())


async def _send_note(ctx: ToolContext, args: dict) -> str:
    to = str(args["to"])
    if to == ctx.loop:
        return err("cannot send a note to yourself; use append_journal instead")
    target = ctx.memories.get(to)
    if target is None:
        known = ", ".join(sorted(ctx.memories))
        return err(f"unknown loop: {to} (known: {known})")
    target.drop_note(ctx.loop, str(args["body"]))
    ctx.wake(to)
    return ok({"sent_to": to})


async def _end_cycle(ctx: ToolContext, args: dict) -> str:
    minutes = int(args["next_wake_minutes"])
    summary = str(args["summary"])
    ctx.extras["end_cycle"] = (minutes, summary)
    return ok({"next_wake_minutes": minutes})


async def _note_gap(ctx: ToolContext, args: dict) -> str:
    gap = ctx.memory.open_gap(str(args["question"]), str(args.get("why") or ""))
    return ok({"gap": gap.id, "question": gap.question})


async def _close_gap(ctx: ToolContext, args: dict) -> str:
    gap_id = str(args["id"])
    try:
        closed = ctx.memory.close_gap(gap_id, str(args["answer"]))
    except ValueError:
        # An id that is not a name at all (a path, free text, the question
        # itself) raises rather than returning False. Same shape of mistake as
        # a missing gap from the model's side, so answer it the same way
        # instead of letting the traceback surface as a tool crash.
        return err(f"not a gap id: {gap_id} (ids are the slugs note_gap returned)")
    if not closed:
        return err(f"no such gap: {gap_id}")
    return ok({"closed": gap_id})


async def _rewrite_goals(ctx: ToolContext, args: dict) -> str:
    ctx.memory.rewrite_goals(str(args["body"]))
    return ok({"rewritten": "goals"})


async def _rewrite_identity(ctx: ToolContext, args: dict) -> str:
    ctx.memory.rewrite_identity(str(args["body"]))
    return ok({"rewritten": "identity"})


def register_memory_tools(registry: ToolRegistry) -> None:
    for spec, fn, loops in (
        (
            ToolSpec(
                name="append_journal",
                description=(
                    "Append one line to today's journal. Use it to record what you did, "
                    "saw or decided this cycle so your future self can read it back."
                ),
                parameters=_schema({"line": {"type": "string"}}, ["line"]),
            ),
            _append_journal,
            None,
        ),
        (
            ToolSpec(
                name="write_fact",
                description=(
                    "Store or replace a durable fact under a short lowercase name "
                    "(letters, digits, '.', '-', '_'). Use it for things that stay true "
                    "across cycles, not for one-off observations. '-' and '_' are "
                    "different characters to this tool, so the same fact under two "
                    "spellings of its name is two facts, not one update -- if the result "
                    "lists similar_existing_facts, read them and delete_fact whichever "
                    "name you are not keeping. 'title' is a short human-readable label "
                    "(20 words or fewer, longer is cut) for list views -- put the detail "
                    "in 'body' instead of stretching the title to fit it."
                ),
                parameters=_schema(
                    {
                        "name": {"type": "string"},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    ["name", "title", "body"],
                ),
            ),
            _write_fact,
            None,
        ),
        (
            ToolSpec(
                name="read_fact",
                description=(
                    "Read one stored fact by name: its title and full body. Returns null "
                    "when no such fact exists; call list_facts first if you are unsure of "
                    "the name."
                ),
                parameters=_schema({"name": {"type": "string"}}, ["name"]),
            ),
            _read_fact,
            None,
        ),
        (
            ToolSpec(
                name="delete_fact",
                description=(
                    "Delete one stored fact by name. Use it when consolidating: overwriting "
                    "a fact you no longer need still leaves it taking up a slot, so remove "
                    "it instead. Errors when no such fact exists."
                ),
                parameters=_schema({"name": {"type": "string"}}, ["name"]),
            ),
            _delete_fact,
            None,
        ),
        (
            ToolSpec(
                name="list_facts",
                description="List the names of every fact you have stored.",
                parameters=_NO_ARGS,
            ),
            _list_facts,
            None,
        ),
        (
            ToolSpec(
                name="note_gap",
                description=(
                    "Record a known unknown: something you tried to determine, that "
                    "matters for a goal, and could not. Say what would change if you "
                    "knew it. Open gaps are read back to you every cycle, so use it for "
                    "the question you keep needing an answer to -- not for every failed "
                    "tool call. Asking the same question twice is the same gap, not two."
                ),
                parameters=_schema(
                    {"question": {"type": "string"}, "why": {"type": "string"}},
                    ["question", "why"],
                ),
            ),
            _note_gap,
            None,
        ),
        (
            ToolSpec(
                name="close_gap",
                description=(
                    "Answer a gap you noted earlier and stop it being read back to you. "
                    "Pass the id note_gap returned and what you now know. Close it when "
                    "you have the answer or the question stopped mattering; if the answer "
                    "is durable, write_fact it too. Errors when no such gap exists."
                ),
                parameters=_schema(
                    {"id": {"type": "string"}, "answer": {"type": "string"}},
                    ["id", "answer"],
                ),
            ),
            _close_gap,
            None,
        ),
        (
            ToolSpec(
                name="send_note",
                description=(
                    "Send a note to another agent's inbox and wake it. Use it to delegate "
                    "work or report something the other agent owns; you cannot note yourself."
                ),
                parameters=_schema(
                    {"to": {"type": "string"}, "body": {"type": "string"}},
                    ["to", "body"],
                ),
            ),
            _send_note,
            None,
        ),
        (
            ToolSpec(
                name="end_cycle",
                description=(
                    "Finish this cycle: say how many minutes until you want waking again "
                    "and summarise what you did. Call it exactly once, as your last action."
                ),
                parameters=_schema(
                    {"next_wake_minutes": {"type": "integer"}, "summary": {"type": "string"}},
                    ["next_wake_minutes", "summary"],
                ),
            ),
            _end_cycle,
            None,
        ),
        (
            ToolSpec(
                name="rewrite_goals",
                description=(
                    "Replace the whole goals document with new text. Brain only. Rewrite "
                    "rather than patch: pass the complete document you want to keep."
                ),
                parameters=_schema({"body": {"type": "string"}}, ["body"]),
            ),
            _rewrite_goals,
            BRAIN_ONLY,
        ),
        (
            ToolSpec(
                name="rewrite_identity",
                description=(
                    "Replace the whole identity document with new text. Brain only. Use it "
                    "sparingly, when how you work has genuinely changed."
                ),
                parameters=_schema({"body": {"type": "string"}}, ["body"]),
            ),
            _rewrite_identity,
            BRAIN_ONLY,
        ),
    ):
        registry.register(Tool(spec=spec, fn=fn, loops=loops))
