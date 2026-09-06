"""Tools that let an agent read and write its own memory.

These are the only tools every loop gets: the journal it thinks in, the facts
it keeps, the notes it sends to its peers, and the ``end_cycle`` call that says
"I am done, wake me in N minutes". Rewriting goals or identity is the brain's
alone -- an expert that asks for it gets a policy refusal from the registry.
"""

from __future__ import annotations

from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

BRAIN_ONLY = frozenset({"brain"})

_NO_ARGS: dict = {"type": "object", "properties": {}}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


async def _append_journal(ctx: ToolContext, args: dict) -> str:
    ctx.memory.append_journal(str(args["line"]))
    return ok({"appended": True})


async def _write_fact(ctx: ToolContext, args: dict) -> str:
    ctx.memory.write_fact(str(args["name"]), str(args["body"]))
    return ok({"written": args["name"]})


async def _read_fact(ctx: ToolContext, args: dict) -> str:
    return ok(ctx.memory.read_fact(str(args["name"])))


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
                    "across cycles, not for one-off observations."
                ),
                parameters=_schema(
                    {"name": {"type": "string"}, "body": {"type": "string"}},
                    ["name", "body"],
                ),
            ),
            _write_fact,
            None,
        ),
        (
            ToolSpec(
                name="read_fact",
                description=(
                    "Read one stored fact by name. Returns null when no such fact exists; "
                    "call list_facts first if you are unsure of the name."
                ),
                parameters=_schema({"name": {"type": "string"}}, ["name"]),
            ),
            _read_fact,
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
