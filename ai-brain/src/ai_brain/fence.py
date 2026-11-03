"""The ``<external>`` fence that marks text this system did not write.

A metric name is ours; a Drive document, a web page, a note from another agent
and last week's journal line are not. Anything in the second group is data the
model reads, never instructions it follows, so it is handed over inside an
``<external>`` element that says where it came from.

Lives below both ``tools`` and ``memory`` so either can fence without importing
the other: ``tools`` imports ``memory`` already, and ``memory`` renders the
journal and inbox that go into the user turn.
"""

from __future__ import annotations

import re

_SOURCE_RE = re.compile(r"[a-z0-9_.-]+")
_UNSAFE_SOURCE_CHAR = re.compile(r"[^a-z0-9_.-]")
_ZWSP = "\u200b"
# Case-insensitive: HTML tag names are, so ``</EXTERNAL>`` closes the fence too.
_FENCE_RE = re.compile(r"</?external", re.IGNORECASE)


def safe_source(name: str) -> str:
    """``name`` flattened to what ``fence`` accepts as a ``source``.

    Each bad character is replaced, not collapsed or stripped, so a name that
    is not ``filip`` cannot come out as ``filip``.
    """
    return _UNSAFE_SOURCE_CHAR.sub("_", name)


def escape_fence(text: str) -> str:
    """``text`` with every ``<external`` / ``</external`` defused.

    The fence only means anything if the fenced text cannot close it, so a
    zero-width space is wedged after the ``<`` of any such sequence -- in any
    case, since HTML tag names are case-insensitive. That breaks the tag while
    leaving the text readable: a page saying "put </external> here" still reads
    correctly and still cannot escape.
    """
    return _FENCE_RE.sub(lambda m: m.group(0)[0] + _ZWSP + m.group(0)[1:], text)


def fence(source: str, text: str, *, block: bool = False) -> str:
    """``text`` inside an ``<external source="...">`` element.

    ``source`` is ours rather than a stranger's, but it lands in an attribute
    value, so it is validated. ``block`` puts the tags on lines of their own,
    for multi-line text whose first line matters -- a note's ``topic:`` line
    must still start a line for the model to find it.
    """
    if not _SOURCE_RE.fullmatch(source):
        raise ValueError(f"fence: invalid source {source!r}")
    safe = escape_fence(text)
    if block:
        return f'<external source="{source}">\n{safe}\n</external>'
    return f'<external source="{source}">{safe}</external>'
