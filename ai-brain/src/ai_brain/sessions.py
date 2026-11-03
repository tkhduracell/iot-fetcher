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
  a tool's untrimmed output reaches disk. ``append`` does it, so a new event or
  field cannot forget to. The exceptions are the opaque blobs a provider signs
  (``OPAQUE_FIELDS``): they are ciphertext rather than text, and altering one
  turns the next call into a 400. A turn whose text redaction did change is
  flagged ``redacted``, because its signature no longer matches it.
* **Bounded on three axes**: ``MAX_EVENT_TEXT_CHARS`` per string,
  ``MAX_SESSION_BYTES`` per cycle, and age plus total size per agent
  (``prune``). A tool that forgets to bound itself must not be able to fill the
  volume the journal and the facts live on.

Nothing here serves a reader over HTTP. The log holds full tool results, the
API has no authentication, and its trace keeps carrying previews only.

``recover`` is the other direction: an unfinished log back into the state a
cycle needs to carry on. It is pure, so what it decides can be tested without
running one. What it must never do is run anything: a call with no recorded
result gets a synthetic result saying so, and the model decides what to do.
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
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ai_brain.llm import Message, ToolCall
from ai_brain.redact import MASK, SECRET_NAME, masked_under_name, redact

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

# How far past MAX_EVENT_TEXT_CHARS a long string is still read. More than any
# secret is long, so whatever the cut splits is dropped rather than kept half
# masked, and a tool that forgot to bound itself costs no more than one at the cap.
_CUT_MARGIN = 4096

# Provider-signed blobs, not text. See the module docstring. Only ever skipped in
# a provider's own blocks: a tool argument called ``data`` is just an argument.
OPAQUE_FIELDS = frozenset({"thought_signature", "signature", "data"})

# How much of a value decides whether ``redact`` masks it under a field's name:
# only its start can pair up with the name, and the rest gets redacted anyway.
_FIELD_PROBE_CHARS = 64

_ID_DATE = "%Y%m%d-%H%M%S"

# How a cycle ended without being finished: a ``no_budget`` stop, a restart's
# ``cancelled``, a mid-cycle PAUSE. Anything else that reached a ``cycle_end``
# is over: ``timeout`` and ``error`` have already archived their inbox, and
# carrying on from them is how one bad reply becomes a cycle that never ends.
RESUMABLE_STATUSES = frozenset({"no_budget", "cancelled", "paused"})

ABANDONED = "abandoned"

# Stands in for ``Message.model`` on a turn that may not be echoed as its model
# wrote it. Being nobody's model, it makes ``is_foreign_turn`` true for every
# provider, which replays the turn as plain text.
UNSIGNED_MODEL = "(replayed)"

# A fence a tool put around what it returned, as ``tools.wrap_external`` writes
# it. Text inside a fence cannot forge one: ``wrap_external`` defuses the tag.
# A tool's ``ok()`` JSON-encodes its payload, so in a logged result the quotes
# arrive escaped.
_EXTERNAL_FENCE = re.compile(r'<external source=\\?"([a-z0-9_.-]+)\\?">')

# Their fences mark another agent's words, not text from outside this system, so
# they are not what a cycle's record of outside reads is about.
_AGENT_MEMORY_READS = frozenset({"read_expert", "expert_overview"})

_NOT_RUN = (
    "not run: the cycle was interrupted before this call started. "
    "Call it again if you still need it."
)
_READ_INTERRUPTED = (
    "no result: the cycle was interrupted while this read was running. "
    "It changed nothing, so call it again if you still need it."
)
_OUTCOME_UNKNOWN = (
    "outcome unknown: the process stopped while this call was running, so it may "
    "or may not have taken effect. Check before retrying."
)


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


def _redacted_and_bounded(text: str) -> str:
    """Cuts before redacting, since ``redact`` is linear in what it is given and
    runs on the event loop every agent shares."""
    scanned = text[: MAX_EVENT_TEXT_CHARS + _CUT_MARGIN]
    masked = redact(scanned)
    if len(scanned) < len(text):
        masked = masked[:-_CUT_MARGIN]
    elif len(masked) <= MAX_EVENT_TEXT_CHARS:
        return masked
    kept = masked[:MAX_EVENT_TEXT_CHARS]
    return f"{kept}…[session log cut: {len(text) - len(kept)} more chars]"


def clean(value: Any, *, opaque: bool = False) -> Any:
    """``value`` with every string in it redacted and bounded, whatever its shape.

    A model can put anything in a tool argument, so nothing here assumes a
    type. Walks values rather than redacting a serialised blob: ``redact`` on
    raw JSON rewrites a ``"key": "..."`` pair into something that no longer
    parses. The field name still counts, though, since a tool argument called
    ``token`` is a secret by name whatever its value looks like, and so is
    everything under it. ``opaque`` is for a provider's own blocks, whose signed
    fields must come through untouched.

    Nesting too deep to walk is the one thing it cannot handle, and an event
    should still land without that one field.
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
        # A field called ``key`` is not in SECRET_NAME, since ``key=on`` is a
        # setting, but ``redact`` would mask a longer value under that name.
        masked = _redacted_and_bounded(value)
        if field and masked_under_name(field, masked[:_FIELD_PROBE_CHARS]):
            return MASK
        return masked
    if isinstance(value, dict):
        # Opaque reaches a block's own strings and no further: what is nested
        # in one, such as a tool_use's ``input``, is the model's and not signed.
        return {
            redact(str(key)): _clean(
                item,
                str(key),
                secret or bool(SECRET_NAME.search(str(key))),
                opaque and isinstance(item, str),
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

    def __init__(
        self, path: Path, cycle_id: str, clock: Callable[[], float], seq: int = 0
    ) -> None:
        self.path = path
        self.cycle_id = cycle_id
        self.clock = clock
        self.seq = seq
        # Events lost to a failed write. Non-zero means the log has a hole.
        self.lost = 0
        self._fd: int | None = None
        self._bytes = 0
        self._torn = False
        if seq:
            self._reopen()

    def _reopen(self) -> None:
        """Pick up after an earlier process: its size, and a line it left open."""
        try:
            self._bytes = self.path.stat().st_size
            with self.path.open("rb") as fh:
                fh.seek(-1, os.SEEK_END)
                self._torn = fh.read(1) != b"\n"
        except OSError:
            pass

    def append(self, event: str, **fields: Any) -> int | None:
        """Write one event, redacted. Returns its ``seq``, or None if it did not land."""
        return self.append_cleaned(event, **{name: clean(v) for name, v in fields.items()})

    def append_cleaned(self, event: str, **fields: Any) -> int | None:
        """``append`` for a provider's turn, whose caller has run ``clean`` with
        ``opaque`` on what needs it. Nothing else should be calling this."""
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
            if self.lost == 1:
                log.warning("[session] %s: cannot log %s: %s", self.cycle_id, event, exc)
                self._mark_incomplete()
            return None
        return seq

    def _mark_incomplete(self) -> None:
        """Say in the filename that this record has holes. See the module docstring."""
        target = self.path.with_name(cycle_id_of(self.path) + HOLES_SUFFIX)
        try:
            os.replace(self.path, target)
        except OSError:
            # Nothing on disk yet, or a disk too far gone even for this. If later
            # events land after all, their seq gap is the remaining signal.
            return
        self.path = target

    def record_mutation(self, op: str, name: str, old: str) -> None:
        """A memory write and the body it replaced. See ``memory.MEMORY_SINK``."""
        self.append("memory", op=op, name=name, old=old)

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
        retention_days: int,
        max_mb: int,
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

    def reopen(self, path: Path, cycle_id: str, seq: int) -> SessionLog:
        """The log an unfinished cycle left, ready to be appended to."""
        return SessionLog(path, cycle_id, self.clock, seq=seq)

    def live_logs(self) -> list[Path]:
        """Uncompressed logs, oldest first: anything an earlier cycle left open.

        One with holes too. It will not be resumed, but it may say which of Filip's
        notes were answered, and a fresh cycle that cannot tell would answer again.
        """
        if self.directory is None or not self.directory.exists():
            return []
        found = [*self.directory.glob(f"*{LOG_SUFFIX}"), *self.directory.glob(f"*{HOLES_SUFFIX}")]
        return sorted(found, key=lambda path: path.name)

    def housekeep(self, keep: str = "") -> None:
        """Compress finished logs and prune old ones, keeping cycle ``keep`` live.

        Scans the directory rather than acting on one path, so a log a crash
        left uncompressed is picked up by the next cycle to end.
        """
        for path in self.live_logs():
            if cycle_id_of(path) != keep:
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
        cap = self.max_mb * 1024 * 1024
        removed = 0
        total = 0
        for path in reversed(session_files(self.directory)):
            try:
                total += path.stat().st_size
                if _cycle_day(path.name) < cutoff or (cap and total > cap):
                    path.unlink()
                    removed += 1
            except OSError:
                log.warning("[session] could not prune %s", path, exc_info=True)
        return removed


class _NullSessionLog(SessionLog):
    """What a disabled store hands out: writes nothing."""

    def append(self, event: str, **fields: Any) -> int | None:
        return None

    append_cleaned = append


def compress(path: Path) -> Path | None:
    """Replace ``x.jsonl`` with ``x.jsonl.gz``. On any failure the plain log stays.

    Through a temp file and with an fsync, so the worst a power cut can do is
    leave both copies and never leave neither.
    """
    target = path.with_name(cycle_id_of(path) + GZ_SUFFIX)
    tmp = target.with_name(target.name + ".tmp")
    try:
        with path.open("rb") as src, tmp.open("wb") as raw:
            os.fchmod(raw.fileno(), 0o600)
            # mtime=0: the same log compresses to the same bytes.
            # Level 6: a third of level 9's CPU on JSONL for about 4% more bytes.
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as gz:
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
    return [event for _, event in _iter_events(path)]


def _iter_events(path: Path) -> Iterator[tuple[int, dict]]:
    """``read_events`` as it comes, with each event's size on disk."""
    opener: Callable[..., Any] = gzip.open if path.name.endswith(GZ_SUFFIX) else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield len(line), event
    except (OSError, EOFError, zlib.error):
        log.warning("[session] %s is damaged; using what was readable", path)


@dataclass
class Recovered:
    """What an unfinished log says about the cycle that wrote it."""

    path: Path
    cycle_id: str
    messages: list[Message] = field(default_factory=list)
    # Inbox files the conversation has already rendered, and the event each one
    # was rendered by: a post answers only what had been shown before it.
    note_seq: dict[str, int] = field(default_factory=dict)
    started_at: float = 0.0
    # The last thing the cycle actually did, not counting earlier resumes that
    # got nowhere: what "how long has this been stale" is measured from.
    last_active_at: float = 0.0
    last_seq: int = 0
    # A gap in ``seq``, or nothing to carry on from at all.
    has_hole: bool = False
    has_opening: bool = False
    ended_status: str | None = None
    rounds: int = 0
    model: str = ""
    # The key that answered last, which is what a rounds cap is looked up by.
    chain_key: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    budget_tokens: int = 0
    effective_cap: int | None = None
    budget_stopped: bool = False
    # The cap above was set by the quota left at the time, not by a setting.
    quota_stopped: bool = False
    floor_cap: int | None = None
    # The budget a token-budget stop was judged against, for its summary.
    budget_hit: int | None = None
    nudge_sent: bool = False
    # Resumes that never got as far as a ``cycle_end``: the process died again.
    # A resume that stopped on ``no_budget`` or a restart proved nothing about
    # the cycle, so it does not count -- the age cap bounds those instead.
    deaths: int = 0
    # The model finished in the log, but the process died before ``cycle_end``.
    # Asking it anything else would be paying for a cycle that is over.
    end_cycle: tuple[int, str] | None = None
    finished: bool = False
    # Each slack_post that may have reached Filip, as (seq, topic).
    answers: list[tuple[int, str]] = field(default_factory=list)
    # The last time end_cycle was refused over an owed reply; 0 for never.
    refused_seq: int = 0
    # Synthetic results for calls with none on record, in call order; the loop
    # logs these, so a second resume reads them as ordinary results.
    pending_results: list[dict] = field(default_factory=list)
    replies: list[dict] = field(default_factory=list)
    # Where the outside text this cycle has read came from.
    external_sources: set[str] = field(default_factory=set)

    @property
    def resumable(self) -> bool:
        return self.ended_status is None or self.ended_status in RESUMABLE_STATUSES

    @property
    def over(self) -> bool:
        """The model has said its last word, whatever became of the bookkeeping."""
        return self.finished or self.end_cycle is not None


def _sources_in(tool: str, result: str) -> set[str]:
    """Where outside text in one logged result came from: its fences, and the tag
    on a fact it read, which carries what that fact's writer had read."""
    found = set() if tool in _AGENT_MEMORY_READS else set(_EXTERNAL_FENCE.findall(result))
    if "written_after_reading" not in result:
        return found
    try:
        parsed = json.loads(result)
    except (TypeError, ValueError):
        return found
    tagged = parsed.get("written_after_reading") if isinstance(parsed, dict) else None
    if isinstance(tagged, list):
        found.update(str(source) for source in tagged)
    return found


def _result_failed(result: str) -> bool:
    try:
        parsed = json.loads(result)
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and "error" in parsed


def _rebuilt_turn(event: dict) -> Message:
    """A logged reply as the ``Message`` the loop appended when it landed.

    A turn redaction changed no longer matches its signature, and a signature
    that does not match is a 400 on every later call -- so it is replayed as
    text, the same way another model's turn is, rather than resent as signed.
    """
    calls = tuple(
        ToolCall(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            args=raw.get("args") if isinstance(raw.get("args"), dict) else {},
            thought_signature=str(raw.get("thought_signature") or ""),
        )
        for raw in event.get("tool_calls") or []
    )
    turn = Message(
        "assistant",
        str(event.get("text") or ""),
        tool_calls=calls,
        thought_signature=str(event.get("thought_signature") or ""),
        model=str(event.get("model") or ""),
        thinking=str(event.get("thinking") or ""),
        provider_blocks=tuple(event.get("provider_blocks") or ()),
    )
    return unsigned_turn(turn) if event.get("redacted") else turn


def unsigned_turn(message: Message) -> Message:
    """An assistant turn with everything bound to the model that wrote it removed."""
    if message.role != "assistant":
        return message
    return replace(
        message,
        tool_calls=tuple(replace(c, thought_signature="") for c in message.tool_calls),
        thought_signature="",
        thinking="",
        provider_blocks=(),
        model=UNSIGNED_MODEL if message.model else "",
    )


def _is_activity(event: dict) -> bool:
    """Whether an event is the cycle working, not a resume's own bookkeeping."""
    kind = event.get("type")
    if kind == "message":
        return event.get("kind") == "nudge"
    if kind == "tool_result":
        return not event.get("synthetic")
    return kind not in ("resume", "cycle_end")


def recover(
    path: Path,
    events: list[dict],
    read_only: Callable[[str], bool],
    cap_result: Callable[[str, str], str],
) -> Recovered:
    """Rebuild a cycle from its log. Runs nothing and writes nothing.

    ``read_only`` is asked about a call that has no result; anything it does
    not vouch for is treated as having had an effect. ``cap_result`` is the
    loop's own cut, since the log holds what the conversation could not.
    """
    out = Recovered(path=path, cycle_id=cycle_id_of(path), has_hole=path.name.endswith(HOLES_SUFFIX))
    calls: list[ToolCall] = []
    started: set[str] = set()
    answered: set[str] = set()
    resume_at: int | None = None
    resume_open = False

    def fill_gaps(*, tail: bool = False) -> None:
        for tool_call in calls:
            if tool_call.id in answered:
                continue
            if tool_call.id not in started:
                text, kind = _NOT_RUN, "not_run"
            elif read_only(tool_call.name):
                text, kind = _READ_INTERRUPTED, "read_interrupted"
            else:
                text, kind = _OUTCOME_UNKNOWN, "outcome_unknown"
                if tool_call.name == "slack_post":
                    # May well have reached Filip. Counting it as answered
                    # risks a spinner the watchdog then turns red; leaving it
                    # owed makes end_cycle push the model to post again, and a
                    # harness that talks a model into repeating a side effect
                    # is the one thing resume must not do.
                    out.answers.append((out.last_seq, str(tool_call.args.get("topic", ""))))
            result = json.dumps({"error": text})
            answered.add(tool_call.id)
            out.messages.append(
                Message("tool", result, tool_call_id=tool_call.id, name=tool_call.name)
            )
            if tail:
                out.pending_results.append(
                    {
                        "round": out.rounds,
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "chars": len(result),
                        "result": result,
                        "synthetic": kind,
                    }
                )

    for event in events:
        seq = event.get("seq")
        if isinstance(seq, int):
            if seq != out.last_seq + 1:
                out.has_hole = True
            out.last_seq = seq
        kind = event.get("type")
        at = event.get("at")
        if isinstance(at, (int, float)) and _is_activity(event):
            out.last_active_at = float(at)
        if kind == "opening":
            out.has_opening = True
            out.started_at = float(at) if isinstance(at, (int, float)) else 0.0
            for name in event.get("notes") or []:
                out.note_seq[str(name)] = out.last_seq
            out.messages = [
                Message(m.get("role", "user"), str(m.get("content") or ""))
                for m in event.get("messages") or []
                if isinstance(m, dict)
            ]
        elif kind == "reply":
            fill_gaps()
            turn = _rebuilt_turn(event)
            out.messages.append(turn)
            calls, started, answered = list(turn.tool_calls), set(), set()
            out.rounds += 1
            out.model = str(event.get("model") or "")
            out.chain_key = str(event.get("chain_key") or "")
            out.prompt_tokens += int(event.get("prompt_tokens") or 0)
            out.completion_tokens += int(event.get("completion_tokens") or 0)
            out.budget_tokens += int(event.get("budget_tokens") or 0)
            caps = event.get("caps") or {}
            if isinstance(caps.get("effective"), int):
                out.effective_cap = caps["effective"]
            out.budget_stopped = bool(caps.get("budget_stopped"))
            out.quota_stopped = bool(caps.get("quota_stopped"))
            floor = caps.get("floor")
            out.floor_cap = floor if isinstance(floor, int) else None
            hit = caps.get("budget_hit")
            out.budget_hit = hit if isinstance(hit, int) else None
            out.finished = not turn.tool_calls
            out.replies.append(event)
            resume_at = None
        elif kind == "tool_call":
            started.add(str(event.get("id")))
        elif kind == "tool_result":
            name = str(event.get("name") or "")
            call_id = str(event.get("id"))
            result = str(event.get("result") or "")
            answered.add(call_id)
            out.messages.append(
                Message("tool", cap_result(name, result), tool_call_id=call_id, name=name)
            )
            if out.replies:
                out.replies[-1].setdefault("results", []).append({"name": name, "result": result})
            out.external_sources.update(_sources_in(name, result))
            if name not in ("slack_post", "end_cycle"):
                continue
            failed = _result_failed(result)
            match = next((c for c in calls if c.id == call_id), None)
            if name == "slack_post" and match is not None:
                # Not on a synthetic result: that decision is fill_gaps', and
                # a call found "not run" on an earlier resume reached nobody.
                synthetic = event.get("synthetic")
                if (not synthetic and not failed) or synthetic == "outcome_unknown":
                    out.answers.append((out.last_seq, str(match.args.get("topic", ""))))
            if name == "end_cycle" and match is not None:
                if failed:
                    if not event.get("synthetic"):
                        out.refused_seq = out.last_seq
                else:
                    try:
                        minutes = int(match.args.get("next_wake_minutes", 0))
                    except (TypeError, ValueError):
                        minutes = 0
                    out.end_cycle = (minutes, str(match.args.get("summary", "")))
        elif kind == "message":
            fill_gaps()
            content = str(event.get("content") or "")
            subtype = event.get("kind")
            if subtype == "nudge":
                out.nudge_sent = True
            if subtype == "resume":
                # A cycle that keeps finding no budget would otherwise stack
                # up one "you were interrupted" per retry, all but the last of
                # them already wrong about how long it has been.
                if resume_at is not None:
                    del out.messages[resume_at]
                resume_at = len(out.messages)
            for name in event.get("notes") or []:
                out.note_seq[str(name)] = out.last_seq
            out.messages.append(Message("user", content))
        elif kind == "resume":
            if resume_open:
                out.deaths += 1
            resume_open = True
            out.ended_status = None
        elif kind == "cycle_end":
            out.ended_status = str(event.get("status") or "")
            resume_open = False
            if int(event.get("lost_events") or 0):
                out.has_hole = True
    if resume_open:
        out.deaths += 1
    fill_gaps(tail=True)
    # The resume about to happen writes its own notice, with the right time.
    if resume_at is not None:
        del out.messages[resume_at]
    return out


# search_history's bounds. Scanning decompresses and parses every file it reads, on
# a Pi, for one tool call: past this much of that the search stops and says so.
MAX_SEARCH_DECOMPRESSED_CHARS = 64_000_000
SEARCH_SNIPPET_CHARS = 300
MAX_SEARCH_LIMIT = 20

# Their results are this log's own text coming back, and their arguments the words
# searched for. Searching either would make every search find the last one.
_NOT_SEARCHED_TOOLS = frozenset({"search_history", "read_cycle_events"})


def _searchable(event: dict) -> str:
    """An event's text a search should look at.

    Not the ``opening``: it is the constitution and the persona every time, and
    would match on every cycle regardless of what the cycle did.
    """
    kind = event.get("type")
    if kind == "reply":
        calls = json.dumps(
            [
                c.get("args")
                for c in event.get("tool_calls") or []
                if c.get("name") not in _NOT_SEARCHED_TOOLS
            ],
            ensure_ascii=False,
        )
        return "\n".join((str(event.get("text") or ""), str(event.get("thinking") or ""), calls))
    if kind == "tool_result" and event.get("name") not in _NOT_SEARCHED_TOOLS:
        return str(event.get("result") or "")
    if kind == "memory":
        return str(event.get("old") or "")
    if kind == "cycle_end":
        return str(event.get("summary") or "")
    return ""


def _snippet(text: str, at: int, length: int) -> str:
    half = SEARCH_SNIPPET_CHARS // 2
    start = max(0, at - half)
    end = min(len(text), at + length + half)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def search_sessions(
    directory: Path,
    query: str,
    *,
    now: float,
    days: int,
    limit: int,
    exclude: str = "",
) -> dict:
    """Plain case-insensitive substring search over one agent's past cycles.

    Newest first, one hit per event. Plain substring on purpose: a model's
    regex over tens of megabytes on a Pi is a way to stall a cycle for nothing
    a word would not find. Takes one directory and no agent name, which is what
    keeps a loop to its own history.
    """
    needle = query.lower()
    cutoff = datetime.fromtimestamp(now, UTC) - timedelta(days=days)
    hits: list[dict] = []
    scanned = 0
    chars = 0
    for path in reversed(session_files(directory)):
        day = _cycle_day(path.name)
        if day is None or day < cutoff:
            break
        cycle = cycle_id_of(path)
        if cycle == exclude:
            continue
        scanned += 1
        for size, event in _iter_events(path):
            chars += size
            if chars > MAX_SEARCH_DECOMPRESSED_CHARS:
                return {"hits": hits, "cycles_scanned": scanned, "truncated": True}
            text = _searchable(event)
            at = text.lower().find(needle)
            if at < 0:
                continue
            hit = {
                "cycle": cycle,
                "at": event.get("at"),
                "type": event.get("type"),
                "snippet": _snippet(text, at, len(query)),
            }
            if event.get("round") is not None:
                hit["round"] = event["round"]
            if event.get("type") == "tool_result":
                hit["tool"] = event.get("name")
            hits.append(hit)
            if len(hits) >= limit:
                return {"hits": hits, "cycles_scanned": scanned, "truncated": True}
    return {"hits": hits, "cycles_scanned": scanned, "truncated": False}


def session_files(directory: Path) -> list[Path]:
    """Every session in ``directory``, oldest first."""
    if not directory.exists():
        return []
    return sorted(p for p in directory.iterdir() if _cycle_day(p.name) is not None)
