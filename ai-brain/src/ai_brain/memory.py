"""Markdown-on-disk memory for the brain and expert agents.

Layout under ``root``::

    identity.md | persona.md
    goals.md            (brain only)
    journal/YYYY-MM-DD.md
    facts/<name>.md
    inbox/<ts>-<sender>-<n>.md
    inbox/done/
    outbox/             (brain only, with outbox/slack/)

All writes go through a temp file plus ``os.replace`` so a reader never sees a
half-written file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_brain.redact import redact

SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

MAX_FACTS = 40
MAX_JOURNAL_FILES = 30
MAX_REVISIONS = 20
MAX_CONTEXT_GAPS = 10
MAX_TITLE_TOKENS = 20

# The fact sidecar lives beside the facts but is not one: ``list_facts`` globs
# ``*.md``, so a ``.json`` name can never be mistaken for a fact.
FACT_META_NAME = "_meta.json"


def _fit_title(title: str) -> str:
    """``title`` cut to ``MAX_TITLE_TOKENS`` words, ellipsis included.

    Truncated rather than rejected: a title one word over the limit is a
    trivial mistake, and losing the whole ``write_fact`` call over it would
    make the limit more disruptive than the thing it protects the UI from.
    """
    words = title.split()
    if len(words) <= MAX_TITLE_TOKENS:
        return title
    return " ".join(words[:MAX_TITLE_TOKENS]) + "…"


def _fallback_title(body: str) -> str:
    """A title for a fact written before ``title`` existed.

    The first ``MAX_TITLE_TOKENS`` words of the body, so an old fact still
    gets a readable label in a list view instead of a blank one -- exactly
    what a writer would have put in ``title`` had the field existed when they
    wrote it. Computed on read, never stored, so it tracks the body if the
    body is ever read again before a title is finally set.
    """
    words = body.split()
    title = " ".join(words[:MAX_TITLE_TOKENS])
    if len(words) > MAX_TITLE_TOKENS:
        title += "…"
    return title


_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_FRONTMATTER_LINE = re.compile(r"^([a-z][a-z0-9_]*):\s*(.*)$")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """A persona file's leading ``---\\nkey: value\\n---`` block, and the rest.

    A persona is fed straight into the model's own system prompt (see
    ``read_context``), so whatever lives in the frontmatter must never reach
    it -- this is the one place that boundary is drawn, and every reader of a
    persona (the prompt, the API) goes through it rather than reading the
    file directly. Deliberately not YAML: one ``key: value`` per line, no
    nesting, no lists -- everything this needs and nothing a persona file
    could accidentally trigger a parser edge case with.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line_match = _FRONTMATTER_LINE.match(line.strip())
        if line_match:
            meta[line_match.group(1)] = line_match.group(2).strip()
    return meta, text[match.end() :]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def safe_name(name: str) -> str:
    """Validate a memory item name. Tools take names, never paths."""
    if not isinstance(name, str) or not SAFE_NAME.match(name):
        raise ValueError(f"unsafe memory name: {name!r}")
    return name


def _atomic_write(path: Path, body: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


def slug_name(text: str) -> str:
    """Turn free text into a name ``safe_name`` accepts.

    Gaps are addressed by a slug of their question so that asking the same
    thing twice is the same gap. Every character class outside ``SAFE_NAME``
    collapses to ``-``; the trailing strip matters because truncating to the
    64-char limit can land mid-separator, which ``safe_name`` would reject.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:64].strip("-.")
    return safe_name(slug or "gap")


@dataclass
class Note:
    path: Path
    sender: str
    body: str
    created: datetime


@dataclass(frozen=True)
class FactStat:
    """One fact, with enough metadata for a reader to judge its freshness."""

    name: str
    title: str
    written_at: float
    first_written_at: float
    writes: int


@dataclass(frozen=True)
class Fact:
    """One fact's full content: the short label and the long body behind it."""

    name: str
    title: str
    body: str


@dataclass(frozen=True)
class Revision:
    """A previous identity/goals body, kept at the moment it was replaced."""

    at: float
    body: str


@dataclass(frozen=True)
class Gap:
    """A known unknown: something the agent could not determine and cares about."""

    id: str
    question: str
    why: str
    opened_at: float
    closed_at: float | None
    answer: str


class MemoryDir:
    def __init__(
        self,
        root: Path,
        name: str,
        is_brain: bool,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.root = Path(root)
        self.name = name
        self.is_brain = is_brain
        self.clock = clock

    # -- paths ---------------------------------------------------------

    @property
    def journal_dir(self) -> Path:
        return self.root / "journal"

    @property
    def facts_dir(self) -> Path:
        return self.root / "facts"

    @property
    def inbox_dir(self) -> Path:
        return self.root / "inbox"

    @property
    def done_dir(self) -> Path:
        return self.inbox_dir / "done"

    @property
    def outbox_dir(self) -> Path:
        return self.root / "outbox"

    @property
    def gaps_dir(self) -> Path:
        return self.root / "gaps"

    @property
    def history_dir(self) -> Path:
        return self.root / "history"

    @property
    def fact_meta_path(self) -> Path:
        return self.facts_dir / FACT_META_NAME

    @property
    def persona_path(self) -> Path:
        return self.root / ("identity.md" if self.is_brain else "persona.md")

    @property
    def goals_path(self) -> Path:
        return self.root / "goals.md"

    # -- setup ---------------------------------------------------------

    def ensure(self) -> None:
        for d in (self.journal_dir, self.facts_dir, self.done_dir):
            d.mkdir(parents=True, exist_ok=True)
        if self.is_brain:
            (self.outbox_dir / "slack").mkdir(parents=True, exist_ok=True)

    def seed_from(self, seed_root: Path) -> None:
        """Copy the starting persona in, but never overwrite what the agent wrote."""
        seed_root = Path(seed_root)
        src = seed_root / "personas" / ("brain.md" if self.is_brain else f"{self.name}.md")
        if not self.persona_path.exists() and src.exists():
            self.persona_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, self.persona_path)
        elif src.exists():
            # Already live from before frontmatter existed (or from a seed
            # that had none yet): backfilling only the frontmatter, never the
            # body, is what makes this safe to run unconditionally on every
            # boot -- a persona the agent later rewrote (rewrite_identity)
            # keeps its own words untouched, and gets the seed's current
            # metadata sitting in front of them.
            self._backfill_frontmatter(src)
        if self.is_brain and not self.goals_path.exists():
            _atomic_write(self.goals_path, "")

    def _backfill_frontmatter(self, seed_path: Path) -> None:
        seed_meta, _ = split_frontmatter(seed_path.read_text(encoding="utf-8"))
        if not seed_meta:
            return
        live = self._read(self.persona_path)
        live_meta, live_body = split_frontmatter(live)
        if live_meta == seed_meta:
            return
        front = "".join(f"{k}: {v}\n" for k, v in seed_meta.items())
        _atomic_write(self.persona_path, f"---\n{front}---\n{live_body}")

    # -- persona / goals ----------------------------------------------

    def persona_text(self) -> str:
        """The persona body only -- never the frontmatter. This is what goes
        into the model's own prompt (see ``read_context``), so the split
        happens here rather than trusting every caller to remember it."""
        _, body = split_frontmatter(self._read(self.persona_path))
        return body.strip()

    def persona_meta(self) -> dict[str, str]:
        """The persona file's frontmatter (e.g. ``{"emoji": "🧠"}``), never
        its body. Introspection only -- nothing in the loop reads this."""
        meta, _ = split_frontmatter(self._read(self.persona_path))
        return meta

    def goals_text(self) -> str:
        if not self.is_brain:
            return ""
        return self._read(self.goals_path)

    def rewrite_identity(self, body: str) -> None:
        self._require_brain("rewrite_identity")
        self._keep_revision("identity", self.persona_path, body)
        _atomic_write(self.persona_path, body)

    def rewrite_goals(self, body: str) -> None:
        self._require_brain("rewrite_goals")
        self._keep_revision("goals", self.goals_path, body)
        _atomic_write(self.goals_path, body)

    def identity_history(self, limit: int = 10) -> list[Revision]:
        return self._history("identity", limit)

    def goals_history(self, limit: int = 10) -> list[Revision]:
        return self._history("goals", limit)

    def _keep_revision(self, kind: str, path: Path, body: str) -> None:
        """Park the body that is about to be overwritten.

        Two guards, both about noise: an identical rewrite is not drift and
        would otherwise fill the history with duplicates, and an empty (or
        absent) previous body has nothing worth keeping -- ``seed_from``
        creates ``goals.md`` empty, so the very first real goals would
        otherwise record a blank revision.
        """
        previous = self._read(path)
        if not previous or previous == body:
            return
        now = self.clock()
        directory = self.history_dir / safe_name(kind)
        directory.mkdir(parents=True, exist_ok=True)
        # A frozen or coarse clock can hand out the same millisecond twice;
        # the filename is both the identity and the ordering of the revision,
        # so step past everything already there. Stepping past the *highest*
        # stamp, not just past collisions, is what keeps pruning honest: a
        # stamp freed by ``_prune_history`` would otherwise be reused and the
        # revision just written would be the next one pruned.
        stamp = max([int(now.timestamp() * 1000)] + [s + 1 for s in _stamps(directory)])
        _atomic_write(directory / f"{stamp}.md", previous)
        self._prune_history(directory)

    @staticmethod
    def _prune_history(directory: Path) -> None:
        for stamp in sorted(_stamps(directory), reverse=True)[MAX_REVISIONS:]:
            (directory / f"{stamp}.md").unlink()

    def _history(self, kind: str, limit: int) -> list[Revision]:
        directory = self.history_dir / safe_name(kind)
        if not directory.exists():
            return []
        # ``_stamps`` drops anything whose name is not a stamp: a stray file in
        # the history dir is not a revision, and a reader of the brain's own
        # drift must never blow up over one.
        stamps = sorted(_stamps(directory), reverse=True)[: max(limit, 0)]
        return [
            Revision(at=s / 1000, body=(directory / f"{s}.md").read_text(encoding="utf-8"))
            for s in stamps
        ]

    # -- journal -------------------------------------------------------

    def append_journal(self, line: str) -> None:
        now = self.clock()
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        path = self.journal_dir / f"{now:%Y-%m-%d}.md"
        with path.open("a", encoding="utf-8") as fh:
            # Belt and braces: tool output the model reads is already
            # redacted (see wrap_external), but the journal is free text the
            # model composes itself, so a secret it paraphrased or quoted
            # from elsewhere in the conversation is caught here too, on the
            # way to a file that persists indefinitely.
            fh.write(f"{now:%H:%M}  {redact(line)}\n")

    def journal_days(self) -> list[str]:
        """Every date the journal has a file for, oldest first.

        Filenames are dates, so this is the list of days a reader may ask for
        without guessing at gaps -- a brain that was down for a week has no
        file for those days at all.
        """
        if not self.journal_dir.exists():
            return []
        return sorted(p.stem for p in self.journal_dir.glob("*.md"))

    def journal_text(self, days: int = 2) -> str:
        now = self.clock()
        wanted = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
        chunks: list[str] = []
        for day in sorted(wanted):
            path = self.journal_dir / f"{day}.md"
            if path.exists():
                chunks.append(f"## {day}\n{path.read_text(encoding='utf-8')}")
        return "\n".join(chunks)

    # -- facts ---------------------------------------------------------

    def write_fact(self, name: str, title: str, body: str) -> None:
        # Same belt-and-braces reasoning as append_journal: a fact is the
        # most durable thing this system writes -- it survives compaction,
        # gets read back into every future cycle's prompt, and (this repo
        # being public) could end up in git history -- so it gets its own
        # redaction pass rather than relying solely on the tool-output fence.
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.facts_dir / f"{safe_name(name)}.md", redact(body))
        self._bump_fact_meta(name, _fit_title(redact(title)))

    def read_fact(self, name: str) -> Fact | None:
        path = self.facts_dir / f"{safe_name(name)}.md"
        if not path.exists():
            return None
        body = path.read_text(encoding="utf-8")
        meta = self._read_fact_meta().get(name)
        title = meta.get("title") if isinstance(meta, dict) else None
        return Fact(name=name, title=title or _fallback_title(body), body=body)

    def delete_fact(self, name: str) -> bool:
        """Remove one fact. Returns False when there was nothing to remove.

        Without this the fact count is a one-way ratchet: the compaction hint
        tells the agent to prune, ``write_fact`` can only overwrite, and an
        overwritten fact still counts -- so once past ``MAX_FACTS`` the hint
        could never be satisfied and never stopped firing.
        """
        path = self.facts_dir / f"{safe_name(name)}.md"
        if not path.exists():
            return False
        path.unlink()
        meta = self._read_fact_meta()
        if meta.pop(name, None) is not None:
            self._write_fact_meta(meta)
        return True

    def list_facts(self) -> list[str]:
        if not self.facts_dir.exists():
            return []
        return sorted(p.stem for p in self.facts_dir.glob("*.md"))

    def fact_stats(self) -> list[FactStat]:
        """Every fact with its title, age and how often it has been rewritten.

        The sidecar is advisory: the fact files are the truth, so anything
        missing or unreadable degrades to "written once, just now, titled from
        its own body" instead of failing. A reader asking what the brain
        knows must never get an exception because a metadata file was
        half-written or hand-edited.

        A title missing from the sidecar (a fact written before ``title``
        existed) reads the body to fall back to one -- the one extra file
        read here is the cost of a list view never showing a blank title.
        """
        meta = self._read_fact_meta()
        stats: list[FactStat] = []
        for name in self.list_facts():
            path = self.facts_dir / f"{name}.md"
            try:
                written_at = path.stat().st_mtime
            except OSError:
                # Deleted between the listing and the stat; it is simply gone.
                continue
            entry = meta.get(name)
            first, writes, title = written_at, 1, None
            if isinstance(entry, dict):
                try:
                    first = float(entry.get("first_written_at", written_at))
                    writes = max(int(entry.get("writes", 1)), 1)
                except (TypeError, ValueError):
                    first, writes = written_at, 1
                raw_title = entry.get("title")
                title = raw_title if isinstance(raw_title, str) and raw_title else None
            if title is None:
                try:
                    title = _fallback_title(path.read_text(encoding="utf-8"))
                except OSError:
                    title = name
            stats.append(
                FactStat(
                    name=name,
                    title=title,
                    written_at=written_at,
                    first_written_at=first,
                    writes=writes,
                )
            )
        stats.sort(key=lambda s: s.name)
        return stats

    def _read_fact_meta(self) -> dict:
        try:
            data = json.loads(self.fact_meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_fact_meta(self, meta: dict) -> None:
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.fact_meta_path, json.dumps(meta, indent=2, sort_keys=True))

    def _bump_fact_meta(self, name: str, title: str) -> None:
        meta = self._read_fact_meta()
        entry = meta.get(name)
        now = self.clock().timestamp()
        if isinstance(entry, dict):
            try:
                first = float(entry.get("first_written_at", now))
                writes = max(int(entry.get("writes", 0)), 0) + 1
            except (TypeError, ValueError):
                first, writes = now, 1
        else:
            first, writes = now, 1
        meta[name] = {"first_written_at": first, "writes": writes, "title": title}
        self._write_fact_meta(meta)

    # -- gaps ----------------------------------------------------------

    def open_gap(self, question: str, why: str = "") -> Gap:
        """Record a known unknown, keyed by a slug of the question.

        Idempotent on that slug: an agent that keeps failing to answer the
        same question must not grow one gap per cycle, so an already-open gap
        comes back untouched. A closed one reopens instead -- the question
        came back, and the old answer no longer holds.
        """
        gap_id = slug_name(question)
        path = self.gaps_dir / f"{gap_id}.json"
        existing = self._read_gap(path)
        if existing is not None and existing.closed_at is None:
            return existing
        gap = Gap(
            id=gap_id,
            question=question,
            why=why or (existing.why if existing else ""),
            # Reopening keeps the original opening time: the point of the
            # gap is how long this has been unknown, not when it last recurred.
            opened_at=existing.opened_at if existing else self.clock().timestamp(),
            closed_at=None,
            answer="",
        )
        self._write_gap(gap)
        return gap

    def close_gap(self, gap_id: str, answer: str) -> bool:
        """Answer a gap. False when there is no such gap to answer."""
        path = self.gaps_dir / f"{safe_name(gap_id)}.json"
        gap = self._read_gap(path)
        if gap is None:
            return False
        self._write_gap(
            Gap(
                id=gap.id,
                question=gap.question,
                why=gap.why,
                opened_at=gap.opened_at,
                closed_at=self.clock().timestamp(),
                answer=answer,
            )
        )
        return True

    def gaps(self, include_closed: bool = False) -> list[Gap]:
        if not self.gaps_dir.exists():
            return []
        found: list[Gap] = []
        for path in self.gaps_dir.glob("*.json"):
            gap = self._read_gap(path)
            # A corrupt gap file hides one known unknown; raising here would
            # hide every fact and note in the context alongside it.
            if gap is None:
                continue
            if include_closed or gap.closed_at is None:
                found.append(gap)
        found.sort(key=lambda g: (g.opened_at, g.id), reverse=True)
        return found

    def _write_gap(self, gap: Gap) -> None:
        self.gaps_dir.mkdir(parents=True, exist_ok=True)
        body = {
            "id": gap.id,
            "question": gap.question,
            "why": gap.why,
            "opened_at": gap.opened_at,
            "closed_at": gap.closed_at,
            "answer": gap.answer,
        }
        _atomic_write(self.gaps_dir / f"{gap.id}.json", json.dumps(body, indent=2))

    @staticmethod
    def _read_gap(path: Path) -> Gap | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("question"):
            return None
        try:
            opened_at = float(data.get("opened_at", 0.0))
            closed = data.get("closed_at")
            closed_at = None if closed is None else float(closed)
        except (TypeError, ValueError):
            return None
        return Gap(
            id=str(data.get("id") or path.stem),
            question=str(data["question"]),
            why=str(data.get("why") or ""),
            opened_at=opened_at,
            closed_at=closed_at,
            answer=str(data.get("answer") or ""),
        )

    # -- inbox ---------------------------------------------------------

    def drop_note(self, sender: str, body: str) -> Path:
        safe_name(sender)
        now = self.clock()
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%S")
        counter = 0
        while True:
            path = self.inbox_dir / f"{stamp}-{sender}-{counter}.md"
            if not path.exists():
                break
            counter += 1
        _atomic_write(path, f"from: {sender}\nat: {_iso(now)}\n\n{body}")
        return path

    def unread_notes(self) -> list[Note]:
        if not self.inbox_dir.exists():
            return []
        notes = []
        for path in sorted(self.inbox_dir.glob("*.md")):
            note = self._parse_note(path)
            if note is not None:
                notes.append(note)
        return notes

    def mark_done(self, notes: list[Note]) -> None:
        self.done_dir.mkdir(parents=True, exist_ok=True)
        for note in notes:
            if note.path.exists():
                os.replace(note.path, self.done_dir / note.path.name)

    def prune_journal(self, keep_days: int = 30) -> int:
        """Delete journal files older than ``keep_days``, by their filename date.

        The journal grows by one file a day forever, so without this the
        ``needs_compaction`` journal rule latches true on day 31 and stays
        true for the life of the volume. The filename is the date, which makes
        this independent of mtime -- a file rewritten by an editor is still as
        old as the day it describes. A name that is not a date is left alone.

        ``keep_days`` is a count of files kept, not an age: today plus the
        previous ``keep_days - 1`` days survive. That boundary is deliberate --
        keeping today *and* 30 days behind it would leave 31 files, one over
        ``MAX_JOURNAL_FILES``, so the prune would run and the compaction hint
        would still be latched on.
        """
        if not self.journal_dir.exists():
            return 0
        cutoff = (self.clock() - timedelta(days=keep_days - 1)).date()
        removed = 0
        for path in self.journal_dir.glob("*.md"):
            try:
                # A filename date carries no time or zone; .date() is the
                # whole point, and the comparison below is date-to-date.
                day = datetime.strptime(  # noqa: DTZ007 - a filename date has no zone
                    path.stem, "%Y-%m-%d"
                ).date()
            except ValueError:
                continue
            if day < cutoff:
                path.unlink()
                removed += 1
        return removed

    def purge_done(self, older_than_days: int = 30) -> int:
        if not self.done_dir.exists():
            return 0
        cutoff = self.clock() - timedelta(days=older_than_days)
        removed = 0
        for path in self.done_dir.glob("*.md"):
            note = self._parse_note(path)
            if note is not None and note.created < cutoff:
                path.unlink()
                removed += 1
        return removed

    # -- context -------------------------------------------------------

    def read_context(self, constitution: str, notes: list[Note] | None = None) -> str:
        """Render the whole system prompt.

        ``notes`` is the inbox the caller already read. A loop marks exactly
        that list done at the end of the cycle, so re-reading the directory
        here would render a note that then stays unread -- shown once for free,
        and again next cycle.
        """
        parts = [f"# Constitution\n{constitution}"]
        parts.append(f"# {'Identity' if self.is_brain else 'Persona'}\n{self.persona_text()}")
        if self.is_brain:
            parts.append(f"# Goals\n{self.goals_text()}")
        parts.append(f"# Journal (last 2 days)\n{self.journal_text(2)}")
        facts = "\n".join(f"- {n}" for n in self.list_facts())
        parts.append(f"# Facts available (use read_fact)\n{facts}")
        inbox = "\n\n".join(
            f"## from: {n.sender} ({_iso(n.created)})\n{n.body}"
            for n in (self.unread_notes() if notes is None else notes)
        )
        parts.append(f"# Inbox\n{inbox}")
        open_gaps = self.gaps()[:MAX_CONTEXT_GAPS]
        # Silence when there is nothing unknown: an empty heading is pure
        # prompt budget, and the cap is there because a brain that opens gaps
        # faster than it closes them would otherwise crowd out the journal.
        if open_gaps:
            luckor = "\n".join(
                f"- {g.question}" + (f" ({g.why})" if g.why else "") for g in open_gaps
            )
            parts.append(f"## Öppna luckor\n{luckor}")
        return "\n\n".join(parts)

    # -- housekeeping --------------------------------------------------

    def needs_compaction(self) -> bool:
        """Whether to append the compaction hint to this cycle's system prompt.

        Both rules must be *clearable*, or the hint latches on forever and
        costs prompt tokens on every round of every cycle from then on. The
        journal side clears because ``prune_journal`` runs in the loop's
        finally before the next cycle asks; the facts side clears because
        ``delete_fact`` exists.
        """
        journal_files = len(list(self.journal_dir.glob("*.md"))) if self.journal_dir.exists() else 0
        return len(self.list_facts()) > MAX_FACTS or journal_files > MAX_JOURNAL_FILES

    # -- internals -----------------------------------------------------

    def _require_brain(self, op: str) -> None:
        if not self.is_brain:
            raise ValueError(f"{op} is brain-only, not allowed for expert {self.name!r}")

    @staticmethod
    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _parse_note(self, path: Path) -> Note | None:
        text = path.read_text(encoding="utf-8")
        head, _, body = text.partition("\n\n")
        sender = ""
        created = self.clock()
        for line in head.splitlines():
            if line.startswith("from: "):
                sender = line[len("from: ") :].strip()
            elif line.startswith("at: "):
                try:
                    created = datetime.fromisoformat(line[len("at: ") :].strip())
                except ValueError:
                    pass
        if not sender:
            return None
        return Note(path=path, sender=sender, body=body, created=created)


def _stamps(directory: Path) -> list[int]:
    """Epoch-ms revision stamps in ``directory``, ignoring anything else in it."""
    stamps = []
    for path in directory.glob("*.md"):
        try:
            stamps.append(int(path.stem))
        except ValueError:
            continue
    return stamps


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
