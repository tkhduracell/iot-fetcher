"""The append-only record of one think cycle, written while the cycle runs.

The journal is one line per cycle and the trace dies with the process, so
everything a cycle actually did -- what it was told, what it answered, what each
tool returned before the loop cut it down -- used to exist only in a local
variable. This keeps it outside the harness, one JSON object per line, in
``<memory root>/sessions/<cycle-id>.jsonl``.

What shapes this file:

* **It may never break a cycle.** ``append`` swallows every failure. A cycle on
  a full disk thinks exactly as it did before; it just leaves a hole in its
  record. Every event carries a ``seq``, and a failed write still spends its
  number, so a hole with something after it shows. A hole at the end does not,
  since what would show it failed to land as well. So the first failure also
  renames the file to ``.jsonl.holes``, which needs no disk space: whatever reads
  this log later can tell it is not the whole story.
* **One unbuffered ``os.write`` per event.** There is no userspace buffer for a
  SIGKILL to lose, so a killed process loses at most the line being written and
  a reader drops a torn one. It does not ``fsync`` per event: that survives a
  power cut this design does not need to survive, at the price of a journal
  commit on an SD card for every tool call. The kernel's writeback coalesces
  them instead. The one fsync is on the finished ``.gz``, once per cycle, so a
  rename can never publish a file whose data never landed.
* **Everything passes through ``redact`` first**, since this is the first place
  a tool's untrimmed output reaches disk. The exceptions are the opaque blobs a
  provider signs (``OPAQUE_FIELDS``): they are ciphertext rather than text, and
  altering one turns the next call into a 400. A turn whose text redaction did
  change is flagged ``redacted``, because its signature no longer matches it.
* **Bounded on three axes**: ``MAX_EVENT_TEXT_CHARS`` per string,
  ``MAX_SESSION_BYTES`` per cycle, and age plus total size per agent
  (``prune``). A tool that forgets to bound itself must not be able to fill the
  volume the journal and the facts live on.

Nothing here serves a reader over HTTP. The log holds full tool results, the
API has no authentication, and its trace keeps carrying previews only.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import secrets
import shutil
import zlib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ai_brain.redact import MASK, redact

log = logging.getLogger(__name__)

SESSIONS_DIRNAME = "sessions"
LOG_SUFFIX = ".jsonl"
HOLES_SUFFIX = ".jsonl.holes"
GZ_SUFFIX = ".jsonl.gz"
SCHEMA_VERSION = 1

# Far above what the conversation is allowed to hold (the loop's own
# MAX_TOOL_RESULT_CHARS is 16k): the point of the log is the result the
# conversation could not keep. code_read's own cap is 256 KB, so this only ever
# bites a tool that forgot to bound itself.
MAX_EVENT_TEXT_CHARS = 512_000

# A cycle that gets here is already pathological, and its record stops rather
# than letting it take the rest of the volume down with it.
MAX_SESSION_BYTES = 64 * 1024 * 1024

# Below this a finished log is compressed right there on the event loop: a few
# milliseconds, against a thread hop that lets every other task run first and so
# moves when a cycle's own bookkeeping lands. Above it, a cycle's log is big enough
# that gzip on a Pi would hold up the loops sharing this event loop.
INLINE_HOUSEKEEP_BYTES = 1024 * 1024

# Provider-signed blobs, not text. See the module docstring. Only ever skipped in
# a provider's own blocks: a tool argument called ``data`` is just an argument.
OPAQUE_FIELDS = frozenset({"thought_signature", "signature", "data"})

# A field named this is a secret whatever its value looks like, however short, and
# whatever it is nested in. ``redact`` only masks a value of 8 characters or more,
# because in free text a short one is usually a setting. Not ``key``: an ordinary
# short ``key=on`` is left to ``redact``, which knows to let it through.
_SECRET_FIELD = re.compile(r"api[_-]?key|token|secret|passw(or)?d|credential", re.IGNORECASE)

_ID_DATE = "%Y%m%d-%H%M%S"


def new_cycle_id(at: float) -> str:
    """A name that sorts by time and that ``memory.safe_name`` accepts.

    Lowercase only, which is why the separator is a dash rather than ISO's
    ``T``/``Z``. The random suffix is what makes two cycles started inside one
    second -- a frozen test clock, or a loop that keeps dying on boot -- two
    different files.
    """
    stamp = datetime.fromtimestamp(at, UTC).strftime(_ID_DATE)
    return f"{stamp}-{secrets.token_hex(2)}"


def cycle_id_of(path: Path) -> str:
    """The cycle a session file belongs to, whatever state it is in."""
    return path.name.split(".", 1)[0]


def _cycle_day(name: str) -> datetime | None:
    """When a session file's cycle started, from its name; None if not one."""
    try:
        return datetime.strptime(name[:15], _ID_DATE).replace(tzinfo=UTC)
    except ValueError:
        return None


def _bounded(text: str) -> str:
    if len(text) <= MAX_EVENT_TEXT_CHARS:
        return text
    dropped = len(text) - MAX_EVENT_TEXT_CHARS
    return text[:MAX_EVENT_TEXT_CHARS] + f"…[session log cut: {dropped} more chars]"


def clean(value: Any, *, opaque: bool = False) -> Any:
    """``value`` with every string in it redacted and bounded, whatever its shape.

    A model can put anything in a tool argument, so nothing here assumes a
    type. Walks values rather than redacting a serialised blob: ``redact`` on
    raw JSON rewrites a ``"key": "..."`` pair into something that no longer
    parses. The field name still counts, though, since a tool argument called
    ``token`` is a secret by name whatever its value looks like, and so is
    everything under it. ``opaque`` is for a provider's own blocks, whose signed
    fields must come through untouched.

    It runs at the call site, outside ``append``'s own guard, so it has to keep
    the same promise. Nesting too deep to walk is the one thing it cannot handle.
    """
    try:
        return _clean(value, "", False, opaque)
    except RecursionError:
        return "[not logged: nested too deeply]"


def _clean(value: Any, field: str, secret: bool, opaque: bool) -> Any:
    if isinstance(value, str):
        if opaque and field in OPAQUE_FIELDS:
            return value
        if secret:
            return MASK
        masked = redact(value)
        if field and redact(f"{field}={masked}") != f"{field}={masked}":
            return MASK
        return _bounded(masked)
    if isinstance(value, dict):
        return {
            redact(str(key)): _clean(
                item, str(key), secret or bool(_SECRET_FIELD.search(str(key))), opaque
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_clean(item, field, secret, opaque) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clean(str(value), field, secret, opaque)


class SessionLog:
    """One cycle's log file. Every method is safe to call on any failure.

    The file is opened on the first event, so a loop that never gets as far as
    an event leaves nothing behind.
    """

    def __init__(self, path: Path, cycle_id: str, clock: Callable[[], float]) -> None:
        self.path = path
        self.cycle_id = cycle_id
        self.clock = clock
        self.seq = 0
        # Events lost to a failed write. Non-zero means the log has a hole.
        self.lost = 0
        self._fd: int | None = None
        self._bytes = 0
        self._torn = False
        self._warned = False

    def append(self, event: str, **fields: Any) -> int | None:
        """Write one event. Returns its ``seq``, or None if it did not land."""
        self.seq += 1
        seq = self.seq
        try:
            record = {"seq": seq, "at": self.clock(), "type": event, **fields}
            # backslashreplace for a lone surrogate, which a model can emit and
            # utf-8 cannot encode. It comes out as a \\ud83d escape, which is what
            # JSON would have written for it anyway, so it reads back the same.
            text = json.dumps(record, ensure_ascii=False, default=str)
            line = text.encode("utf-8", "backslashreplace") + b"\n"
            if self._bytes + len(line) > MAX_SESSION_BYTES:
                raise OSError(f"session over {MAX_SESSION_BYTES} bytes")
            if self._fd is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # 0600: the one file on the volume holding untrimmed tool output.
                self._fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            if self._torn:
                # Terminate a line a short write left open, or the next event
                # would be glued onto it and lost along with it.
                os.write(self._fd, b"\n")
                self._torn = False
            written = os.write(self._fd, line)
            self._bytes += written
            if written != len(line):
                self._torn = True
                raise OSError(f"short write: {written} of {len(line)} bytes")
        except Exception as exc:  # the log must never take a cycle down with it
            self.lost += 1
            if not self._warned:
                self._warned = True
                log.warning("[session] %s: cannot log %s: %s", self.cycle_id, event, exc)
                self._mark_incomplete()
            return None
        return seq

    def _mark_incomplete(self) -> None:
        """Say in the filename that this record has holes. See the module docstring."""
        target = self.path.with_name(self.path.name.removesuffix(LOG_SUFFIX) + HOLES_SUFFIX)
        try:
            os.replace(self.path, target)
        except OSError:
            # Nothing on disk yet, or a disk too far gone even for this. If later
            # events land after all, their seq gap is the remaining signal.
            return
        self.path = target

    @property
    def size(self) -> int:
        return self._bytes

    def record_mutation(self, op: str, name: str, old: str) -> None:
        """A memory write and the body it replaced. See ``memory.MEMORY_SINK``."""
        self.append("memory", op=op, name=name, old=clean(old))

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


class SessionStore:
    """One agent's session directory: naming, compressing and pruning.

    ``retention_days`` of 0 switches logging off altogether, and ``open`` then
    hands out a log that writes nowhere so the loop needs no second code path.
    """

    def __init__(
        self,
        directory: Path | None,
        clock: Callable[[], float],
        retention_days: int = 30,
        max_mb: int = 200,
    ) -> None:
        self.directory = directory if retention_days > 0 else None
        self.clock = clock
        self.retention_days = retention_days
        self.max_mb = max_mb

    def open(self) -> SessionLog:
        cycle_id = new_cycle_id(self.clock())
        if self.directory is None:
            return _NullSessionLog(Path(os.devnull), cycle_id, self.clock)
        return SessionLog(self.directory / f"{cycle_id}{LOG_SUFFIX}", cycle_id, self.clock)

    def housekeep(self, keep: str = "") -> None:
        """Compress finished logs and prune old ones, keeping cycle ``keep`` live.

        Scans the directory rather than acting on one path, so a log a crash
        left uncompressed is picked up by the next cycle to end.
        """
        if self.directory is None or not self.directory.exists():
            return
        for path in sorted(self.directory.glob(f"*{LOG_SUFFIX}")):
            if path.name != f"{keep}{LOG_SUFFIX}":
                compress(path)
        self.prune()

    def prune(self) -> int:
        """Delete sessions past the retention age, then the oldest past the size cap.

        By the date in the name, not by mtime, so restoring the volume from a
        backup cannot make a year of old cycles look new. A name that is not a
        cycle id is left alone. Oldest first against ``max_mb``: the recent
        record is the one anyone is likely to want. 0 means no size cap.
        """
        if self.directory is None or not self.directory.exists():
            return 0
        cutoff = datetime.fromtimestamp(self.clock(), UTC) - timedelta(days=self.retention_days)
        removed = 0
        kept: list[tuple[Path, int]] = []
        for path in sorted(self.directory.iterdir()):
            day = _cycle_day(path.name)
            if day is None:
                continue
            try:
                if day < cutoff:
                    path.unlink()
                    removed += 1
                else:
                    kept.append((path, path.stat().st_size))
            except OSError:
                log.warning("[session] could not prune %s", path, exc_info=True)
        if self.max_mb > 0:
            over = sum(size for _, size in kept) - self.max_mb * 1024 * 1024
            for path, size in kept:
                if over <= 0:
                    break
                try:
                    path.unlink()
                    over -= size
                    removed += 1
                except OSError:
                    log.warning("[session] could not prune %s", path, exc_info=True)
        return removed


class _NullSessionLog(SessionLog):
    """What a disabled store hands out: counts, writes nothing."""

    def append(self, event: str, **fields: Any) -> int | None:
        self.seq += 1
        return None

    def record_mutation(self, op: str, name: str, old: str) -> None:
        return


def compress(path: Path) -> Path | None:
    """Replace ``x.jsonl`` with ``x.jsonl.gz``. On any failure the plain log stays.

    Through a temp file and with an fsync, so the worst a power cut can do is
    leave both copies and never leave neither.
    """
    target = path.with_name(path.name[: -len(LOG_SUFFIX)] + GZ_SUFFIX)
    tmp = target.with_name(target.name + ".tmp")
    try:
        with path.open("rb") as src, tmp.open("wb") as raw:
            os.fchmod(raw.fileno(), 0o600)
            # mtime=0: the same log compresses to the same bytes.
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
                shutil.copyfileobj(src, gz)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, target)
        path.unlink()
        return target
    except OSError:
        log.warning("[session] could not compress %s", path, exc_info=True)
        tmp.unlink(missing_ok=True)
        return None


def read_events(path: Path) -> list[dict]:
    """Every event in one session file, plain or gzipped.

    A torn last line is what a SIGKILL leaves, and a truncated gzip what a
    power cut can, so neither raises: a reader gets whatever is intact. A gap
    in the ``seq`` numbers is how it can tell the record is incomplete.
    """
    opener: Callable[..., Any] = gzip.open if path.name.endswith(GZ_SUFFIX) else open
    events: list[dict] = []
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except (OSError, EOFError, zlib.error):
        log.warning("[session] %s is damaged; using what was readable", path)
    return events


def session_files(directory: Path) -> Iterable[Path]:
    """Every session in ``directory``, oldest first."""
    if not directory.exists():
        return []
    return sorted(p for p in directory.iterdir() if _cycle_day(p.name) is not None)
