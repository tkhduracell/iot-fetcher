"""A shared guard against a user-supplied regex that could hang the process.

Two tools take a raw pattern from the model: ``vm_metrics`` (one name string
per call) and ``code_grep`` (every line of every file in a subtree, which
makes the aggregate cost far larger even for an "ordinary" pattern). Both
share the same refusal: a pattern that nests one quantifier inside another --
``(a+)+``, ``(a*)*``, ``(a|a)+`` -- can make the backtracking engine take
exponential time on a string that nearly matches. There is no way to bound
that once it starts: CPython's ``re`` holds the GIL for the whole match, so a
thread and a timeout stop nothing and block the process right along with it.
The only real defence against *that* shape is to refuse the pattern outright,
which costs nothing an ordinary search actually needs.

This is deliberately not exhaustive -- a merely slow-but-not-catastrophic
pattern over a large tree still gets through, which is what ``code_grep``'s
own wall-clock timeout (``asyncio.to_thread`` plus ``asyncio.wait_for``) is
for: unlike a single catastrophic match, a big *aggregate* walk is bounded by
time practically, if not always instantly, and returning partial results
under a timeout is a reasonable answer to it in a way it is not for a genuine
exponential blowup on one line.
"""

from __future__ import annotations

import re

MAX_PATTERN_CHARS = 128

_NESTED_QUANTIFIER = re.compile(r"""
    \(                     # a group
    (?:\?[:=!P][^)]*|)     # optionally non-capturing / lookaround / named
    [^()]*                 # its body, with no nested group
    [*+?}]                 # ending in a quantifier
    \)                     # close it
    \s*[*+{]               # and quantify the group itself
""", re.VERBOSE)
_ALTERNATION_QUANTIFIER = re.compile(r"\([^()]*\|[^()]*\)\s*[*+{]")


def check_pattern(pattern: str, *, max_chars: int = MAX_PATTERN_CHARS) -> str | None:
    """``None`` if ``pattern`` is safe to compile and use; otherwise an error
    message a tool can return as-is."""
    if len(pattern) > max_chars:
        return f"pattern too long (max {max_chars})"
    if _NESTED_QUANTIFIER.search(pattern) or _ALTERNATION_QUANTIFIER.search(pattern):
        return (
            "pattern nests a quantifier inside a quantified group, which can take "
            "exponential time; use a simpler pattern"
        )
    return None
