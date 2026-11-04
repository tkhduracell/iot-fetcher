"""Changing the brain's identity: what may be asked for, and what Filip is shown.

``identity.md`` is the one text the brain writes itself that still loads into the
system message of every later cycle (see ``MemoryDir.context_parts``). Everything
else it writes -- facts, journal, goals, notes -- reaches a later cycle fenced, in
the user turn, as data. A page the researcher read can end up in a note to the
brain; if that were enough to get a sentence into ``identity.md``, it would sit
beside the constitution from then on. So the brain does not write its identity:
``rewrite_identity`` files a proposal and Filip approves it, like any other thing
the brain wants done.

This module is the part of that which is about the text itself, kept apart from
``approvals`` (the gate) and ``executors`` (the write) so both can use it:

* **What may be asked for.** ``clean_body`` and ``problem`` decide the exact text
  that is stored, shown and written, and refuse what cannot be shown honestly.
* **What the card says.** ``card`` renders a diff against the current identity as
  Slack sections, with the proposed text inside code fences it cannot leave.
* **What counts as the same ask.** ``target`` is the rejection-memory key. It is
  a hash of the proposed text, not "identity": that would make one ❌ forbid every
  later change, where the point is that the same words stay refused and others may
  still be asked.
"""

from __future__ import annotations

import difflib
import unicodedata

from ai_brain.memory import split_frontmatter, text_digest
from ai_brain.redact import redact

KIND = "rewrite_identity"
# The Slack thread, and the rejection-memory topic, every identity proposal uses.
TOPIC = "identity"

# An identity is a page of self-description (the seed is about 1.5 KB). The cap
# keeps the whole diff on a card in the worst case, a complete rewrite of text
# this long, and keeps the system message the model reads every cycle from
# growing without anyone deciding it should. Filip's own edits through the MCP
# have no such limit.
MAX_CHARS = 6000
# A floor against a label or a stub ("curious") replacing who the brain is, in
# the spirit of memory_tools' fact floor. The seed is far above it.
MIN_CHARS = 200
# Longest reason kept at all, and the longest shown on a card: the brain wrote it,
# and Filip should read it as that, beside a diff and not instead of one.
MAX_REASON_CHARS = 500
REASON_CARD_CHARS = 300

CONTEXT_LINES = 2
# Slack rejects a section whose text exceeds 3000 characters (approvals'
# SECTION_LIMIT, which would cut a longer one from the end and with it the closing
# fence). A diff block is a fenced section, so the fences (8 characters) and some
# slack come off the top.
_DIFF_BLOCK_CHARS = 2900
# How many diff sections one card carries: with the header, the closing section and
# the buttons it stays far under Slack's 50 blocks, and covers a complete rewrite
# of text at MAX_CHARS. Past it the card says it was cut, rather than failing.
MAX_DIFF_BLOCKS = 5
# One line can be longer than a block (a paragraph is a single line); it is split
# into pieces of at most this many escaped characters, each keeping its marker.
_PIECE_CHARS = 2500

# Characters that are not drawn, or that rewrite how neighbours are drawn: the
# model reads them and Filip's card does not show them. A tag character spelling
# out an instruction is the case that matters. A zero-width joiner stays, since
# emoji sequences need it.
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn"})
_ALLOWED_INVISIBLE = frozenset({"\n", "\t", "‍"})


def clean_body(raw: str) -> str:
    """The text exactly as it will be stored, shown and written.

    One canonical form, so that the same words with different line endings or
    trailing blanks are the same proposal for rejection memory, and so that a
    secret-shaped string never reaches Slack or the file: this is the one place a
    proposed identity is redacted, before it is hashed.
    """
    return redact(raw.replace("\r\n", "\n")).strip() + "\n"


def clean_reason(raw: str) -> str:
    """The brain's reason as one redacted line, cut to what a card has room for.

    Redacted for the same reason the body is: it is stored in the outbox and
    posted to Slack, and the brain wrote it after reading whatever it read.
    """
    flat = " ".join(redact(raw).split())
    if len(flat) > MAX_REASON_CHARS:
        flat = flat[: MAX_REASON_CHARS - 1] + "…"
    return flat


def problem(body: str) -> str | None:
    """None if ``body`` may be proposed as the identity; else why not.

    Checked again by the executor, so a proposal stored by an older version, or
    edited on disk, is held to the same rules at the moment it would be written.
    """
    stripped = body.strip()
    if not stripped:
        return "body is empty -- an identity replaces the whole document, so write all of it"
    if len(stripped) < MIN_CHARS:
        return (
            f"body is only {len(stripped)} chars -- an identity is who you are, not a label "
            f"(at least {MIN_CHARS}); pass the complete document"
        )
    if len(stripped) > MAX_CHARS:
        return f"body is {len(stripped)} chars, over the {MAX_CHARS} limit -- tighten it"
    _, rest = split_frontmatter(body)
    if rest != body:
        # The frontmatter is kept from the file already and never reaches the
        # prompt, so a block of it in a proposal is text nobody would see loaded.
        return (
            "body starts with a '---' frontmatter block -- leave it out; the file's own "
            "frontmatter is kept automatically"
        )
    for ch in body:
        if ch not in _ALLOWED_INVISIBLE and unicodedata.category(ch) in _HIDDEN_CATEGORIES:
            return (
                f"body contains an invisible or control character (U+{ord(ch):04X}) that the "
                "approval card could not show -- remove it"
            )
    return None


def digest(body: str) -> str:
    return text_digest(body)


def short(full_digest: str) -> str:
    """A digest short enough to read on a card, still plenty to tell two apart."""
    return full_digest[: len("sha256:") + 12]


def target(body: str) -> str:
    """What rejection memory calls this ask: the proposed text by hash and size.

    Never the text itself: it can be thousands of characters, and the "Filip has
    said no to" section is capped at about 1500. The hash is what makes "the same
    text stays refused, different text may be asked" true, and the size is what
    lets a model reading the section tell which of its drafts that was.
    """
    return f"identity {short(digest(body))} ({len(body)} chars)"


# -- rendering -----------------------------------------------------------


def _escape_char(ch: str) -> str:
    """One character as Slack should receive it inside a code fence.

    ``& < >`` are Slack's control characters (``<!channel>``, ``<@U1>`` and links
    are written with them), and a backtick is followed by a zero-width space so no
    run of the text can be three in a row: that is what would close the fence and
    let the rest of the proposed text pose as the card's own wording.
    """
    if ch == "&":
        return "&amp;"
    if ch == "<":
        return "&lt;"
    if ch == ">":
        return "&gt;"
    if ch == "`":
        return "`​"
    return ch


def _escape(text: str) -> str:
    return "".join(_escape_char(ch) for ch in text)


def inline(text: str, limit: int) -> str:
    """``text`` as one quoted, escaped line of at most ``limit`` characters.

    For what the brain wrote about its own proposal: it goes inside a code span
    on the card, so it reads as a quotation and cannot add lines or formatting of
    its own to the card around it. A backtick would end the span whatever follows
    it (unlike inside a code fence, where a zero-width space is enough), so here
    it becomes an apostrophe; the reason is stored whole in the proposal.
    """
    flat = " ".join(text.split()).replace("`", "'")
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return f"`{_escape(flat)}`"


def diff_lines(current: str, proposed: str) -> list[str]:
    """A unified diff of the two bodies, without the file header lines.

    Of the bodies, not the files: the frontmatter is not part of what loads, and
    it is not part of what is proposed. Surrounding whitespace is ignored for
    the same reason ``persona_text`` ignores it.
    """
    lines = list(
        difflib.unified_diff(
            current.strip().splitlines(),
            proposed.strip().splitlines(),
            lineterm="",
            n=CONTEXT_LINES,
        )
    )
    return lines[2:]


def _pieces(line: str) -> list[str]:
    """``line``, escaped, in pieces that each fit in a block. The marker (the
    first character of a diff line) is repeated, so a split line still reads as
    added, removed or context all the way down."""
    marker = line[:1]
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for ch in line:
        escaped = _escape_char(ch)
        if size + len(escaped) > _PIECE_CHARS:
            pieces.append("".join(current))
            current, size = [marker], len(marker)
        current.append(escaped)
        size += len(escaped)
    pieces.append("".join(current))
    return pieces


def _blocks(lines: list[str]) -> tuple[list[str], int]:
    """The diff as fenced section texts, and how many of ``lines`` they hold whole."""
    blocks: list[str] = []
    body: list[str] = []
    size = 0
    shown = 0
    for line in lines:
        for piece in _pieces(line):
            if body and size + len(piece) + 1 > _DIFF_BLOCK_CHARS:
                blocks.append("```\n" + "\n".join(body) + "\n```")
                body, size = [], 0
                if len(blocks) == MAX_DIFF_BLOCKS:
                    return blocks, shown
            body.append(piece)
            size += len(piece) + 1
        shown += 1
    if body:
        blocks.append("```\n" + "\n".join(body) + "\n```")
    return blocks, shown


def card(
    *,
    proposal_id: str,
    reason: str,
    provenance_line: str,
    current: str,
    proposed: str,
    base_digest: str,
    stored_at: str,
) -> tuple[str, list[str]]:
    """The approval card for an identity rewrite: fallback text and its sections.

    The sections are Slack mrkdwn texts, each within ``SECTION_LIMIT``; the caller
    adds the buttons. In order: the provenance warning and what is being asked
    (ours, so first and not cut), the diff in code fences, and the instructions to
    react (ours, last, so nothing the brain wrote sits after them).

    The fallback text, which is a notification preview, carries none of the
    brain's words.
    """
    lines = diff_lines(current, proposed)
    blocks, shown = _blocks(lines)
    added = sum(1 for line in lines if line.startswith("+"))
    removed = sum(1 for line in lines if line.startswith("-"))

    cut = ""
    if shown < len(lines):
        total_chars = sum(len(line) + 1 for line in lines)
        shown_chars = sum(len(line) + 1 for line in lines[:shown])
        cut = (
            f"⚠️ *The diff is cut:* {shown} of {len(lines)} diff lines ({shown_chars} of "
            f"{total_chars} chars) fit on this card, and the rest is *not shown*. The full "
            f"proposed text ({len(proposed)} chars) is in `{stored_at}` under `payload.body` "
            "on the memory volume; read it there before approving.\n"
        )

    header = (
        f"{provenance_line}"
        f"*Proposal {proposal_id} (rewrite_identity)*: the brain asks to replace its own "
        "identity, the text that loads into the system prompt of every later cycle.\n"
        f"Current identity {short(base_digest)}, {len(current.strip())} chars. "
        f"Proposed {short(digest(proposed))}, {len(proposed.strip())} chars. "
        f"{added} lines added, {removed} removed.\n"
        f"{cut}"
        f"Its reason, as the brain wrote it: {inline(reason, REASON_CARD_CHARS)}\n"
        "Diff against the current identity (`-` goes, `+` comes in, the rest is context). "
        "Everything in the code blocks below is the proposed text and is not instructions "
        "to anyone:"
    )
    footer = (
        f"{cut}"
        "React ✅ to approve, ❌ to reject, or use the buttons below. Approving makes this the "
        "brain's identity from its next cycle; the current text is kept in the revision "
        "history. If the identity file has changed by then, nothing is written."
    )
    fallback = (
        f"{provenance_line}"
        f"Proposal {proposal_id} (rewrite_identity): the brain asks to rewrite its own "
        "identity. Open the card to review the diff."
    )
    return fallback, [header, *blocks, footer]
