"""What outside text the running cycle has read so far.

A fence tells a model not to obey a page. It does nothing about the page having
shaped what the model goes on to write, and once that is a fact or a note, it
reads back later as this system's own words. So a cycle remembers where outside
text reached it, and whatever it writes from then on says so.

Advisory, and only that. Nothing here blocks anything: the tag is for whoever
reads the fact, the note or the approval card to weigh. It says the writer had
read this, not that the text it wrote came from it.

A ContextVar for the same reason as ``memory.MEMORY_SINK``: the MCP server and
Slack handlers write through the same MemoryDir from their own tasks, where this
is unset, so a person's edit is never tagged with what a cycle happened to read.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar

READ_SOURCES: ContextVar[set[str] | None] = ContextVar("READ_SOURCES", default=None)

# What a fact's tag is called wherever it is handed out.
WRITTEN_AFTER_READING = "written_after_reading"


def note_read(sources: Iterable[str]) -> None:
    """The running cycle has read text from these sources."""
    current = READ_SOURCES.get()
    if current is not None:
        current.update(sources)


def carry_tag(result: dict, provenance: Iterable[str]) -> dict:
    """A tool's ``result`` for a fact with this tag on it. What the fact was written
    after reading now reaches this cycle by way of it. The tag goes first: the loop
    cuts a long result from the end, and a long fact must not lose it that way."""
    tag = list(provenance)
    if not tag:
        return result
    note_read(tag)
    return {WRITTEN_AFTER_READING: tag, **result}


def read_so_far() -> tuple[str, ...]:
    """Where the outside text this cycle has read came from. Empty outside a cycle."""
    current = READ_SOURCES.get()
    return tuple(sorted(current)) if current else ()
