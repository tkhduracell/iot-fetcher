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

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

MAX_FACTS = 40
MAX_JOURNAL_FILES = 30


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


@dataclass
class Note:
    path: Path
    sender: str
    body: str
    created: datetime


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
        if self.is_brain and not self.goals_path.exists():
            _atomic_write(self.goals_path, "")

    # -- persona / goals ----------------------------------------------

    def persona_text(self) -> str:
        return self._read(self.persona_path)

    def goals_text(self) -> str:
        if not self.is_brain:
            return ""
        return self._read(self.goals_path)

    def rewrite_identity(self, body: str) -> None:
        self._require_brain("rewrite_identity")
        _atomic_write(self.persona_path, body)

    def rewrite_goals(self, body: str) -> None:
        self._require_brain("rewrite_goals")
        _atomic_write(self.goals_path, body)

    # -- journal -------------------------------------------------------

    def append_journal(self, line: str) -> None:
        now = self.clock()
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        path = self.journal_dir / f"{now:%Y-%m-%d}.md"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{now:%H:%M}  {line}\n")

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

    def write_fact(self, name: str, body: str) -> None:
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.facts_dir / f"{safe_name(name)}.md", body)

    def read_fact(self, name: str) -> str | None:
        path = self.facts_dir / f"{safe_name(name)}.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

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
        return True

    def list_facts(self) -> list[str]:
        if not self.facts_dir.exists():
            return []
        return sorted(p.stem for p in self.facts_dir.glob("*.md"))

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

    def read_context(self, constitution: str) -> str:
        parts = [f"# Constitution\n{constitution}"]
        parts.append(f"# {'Identity' if self.is_brain else 'Persona'}\n{self.persona_text()}")
        if self.is_brain:
            parts.append(f"# Goals\n{self.goals_text()}")
        parts.append(f"# Journal (last 2 days)\n{self.journal_text(2)}")
        facts = "\n".join(f"- {n}" for n in self.list_facts())
        parts.append(f"# Facts available (use read_fact)\n{facts}")
        inbox = "\n\n".join(
            f"## from: {n.sender} ({_iso(n.created)})\n{n.body}" for n in self.unread_notes()
        )
        parts.append(f"# Inbox\n{inbox}")
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


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
