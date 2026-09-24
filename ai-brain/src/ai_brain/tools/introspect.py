"""Brain-only introspection: the brain's view of the experts, itself, and the
code the whole system runs.

Everything here is brain-only (``loops=BRAIN_ONLY``): experts stay scoped to
their own domain and reach the brain only through ``send_note``, never by
reading another loop's memory or the process's own runtime state -- the brain
is the one loop whose job is to reflect on the rest. See the module docstrings
of ``ai_brain.introspection`` and ``ai_brain.repo`` for the two things this
mostly wraps: the read-only ``System`` state (loops, approvals, the ledger)
and the read-only repo snapshot.

``review_expert`` is the one tool here that writes anything, and it writes in
exactly two places: the brain's own ``reviews/<name>.jsonl`` (via
``MemoryDir.append_review``) and, for a verdict that is not ``good``, one note
in the reviewed expert's inbox -- reusing ``send_note``'s own delivery path
(a ``MemoryDir.drop_note`` plus ``ctx.wake``) rather than a second one. It
never touches an expert's facts, journal or persona directly: the brain
corrects through review notes, the same way it corrects anything else about
an expert, and the expert decides what to do with the note on its own next
cycle.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ai_brain import sensitive
from ai_brain.introspection import proposal_loops, token_rows, usefulness_rows
from ai_brain.ledger import Ledger
from ai_brain.llm import ToolSpec
from ai_brain.memory import MemoryDir
from ai_brain.redact import redact
from ai_brain.regex_safety import check_pattern
from ai_brain.repo import RepoSnapshot
from ai_brain.sensitive import is_sensitive
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

log = logging.getLogger(__name__)

BRAIN_ONLY = frozenset({"brain"})

VERDICTS = ("good", "stale", "wrong", "repetitive", "off_goal")
REVIEW_SENDER = "brain"

PERSONA_PREVIEW_LINES = 10
JOURNAL_DAYS = 2

# code_* limits -- generous enough for one file or one grep pass, capped hard
# enough that a runaway read cannot swamp the conversation the way
# MAX_TOOL_RESULT_CHARS exists to catch as a last resort (loop.py).
MAX_FILE_BYTES = 256 * 1024
MAX_LIST_ENTRIES = 500
MAX_GREP_HITS = 100
MAX_LINE_PREVIEW = 300
# Wall-clock budget for one code_grep call's walk-and-match, enforced from
# outside the worker thread (see _code_grep) since a thread cannot be
# interrupted mid-regex-match. A large subtree at a few hundred files a
# second is comfortably inside this; a pathologically slow-but-not-rejected
# pattern (see regex_safety.py for the ones that ARE rejected outright) is
# what this actually bounds.
CODE_GREP_TIMEOUT_S = 10

# Directories skipped everywhere under the snapshot: dependency trees and lock
# files are large, generated, and never what "read the code" means.
SKIP_DIR_NAMES = frozenset({"node_modules", ".git", "__pycache__", ".venv", "venv"})
SKIP_FILE_GLOBS = ("*.lock", "package-lock.json", "*.lockb", "uv.lock", "go.sum")

# Extensions code_read/code_grep will open. Deliberately a list of source and
# doc shapes rather than "everything under MAX_FILE_BYTES" -- a binary asset
# that happens to be small is still not something reading line-by-line helps
# with, and grep over it would just be noise.
TEXT_EXTENSIONS = frozenset(
    {
        ".py", ".go", ".ts", ".tsx", ".js", ".jsx", ".sh", ".yml", ".yaml",
        ".json", ".md", ".toml", ".cfg", ".ini", ".txt",
        ".sql", ".html", ".css", ".dockerfile", ".mod", ".sum",
    }
)

# An env template's *whole* filename, since Path.suffix only ever returns the
# last dotted component -- ".env.example".suffix is ".example", not
# ".env.example", so this has to be a name check rather than folded into
# TEXT_EXTENSIONS above. These are exactly the names is_sensitive's own
# _ENV_ALLOW exempts from the denylist -- a template is safe to read by the
# same reasoning that makes it safe to list.
ENV_TEMPLATE_NAMES = frozenset({".env.example", ".env.template", ".env.sample"})


_NO_ARGS: dict = {"type": "object", "properties": {}}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


# ======================================================================
# system_status
# ======================================================================


def _lan_summary(chain) -> list[dict]:
    finders = getattr(chain, "lan_finders", []) if chain is not None else []
    hosts = []
    for finder in finders:
        host = finder.current()
        hosts.append(
            {
                "model": finder.model,
                "host": host.base_url if host else None,
                "found_at": host.found_at if host else None,
            }
        )
    return hosts


async def _system_status(ctx: ToolContext, _args: dict) -> str:
    loops = ctx.extras.get("loops")
    approvals = ctx.extras.get("approvals")
    ledger = ctx.extras.get("ledger")
    if not isinstance(loops, dict) or approvals is None or not isinstance(ledger, Ledger):
        return err("system_status: runtime state unavailable")

    cycles = []
    for name in ["brain"] + sorted(n for n in loops if n != "brain"):
        loop = loops[name]
        last = loop.last_cycle
        cycles.append(
            {
                "name": name,
                "priority": loop.priority,
                "running": loop.trace is not None and loop.trace.in_progress,
                "last_cycle": (
                    None
                    if last is None
                    else {"status": last.status, "rounds": last.rounds, "model": last.model}
                ),
                "last_cycle_at": loop.last_cycle_at or None,
            }
        )

    return ok(
        {
            "cycles": cycles,
            "usefulness": usefulness_rows(loops),
            "tokens": token_rows(loops),
            "proposal_loops": proposal_loops(approvals)["loops"],
            "ledger": ledger.usage(),
            "lan_hosts": _lan_summary(ctx.extras.get("chain")),
            "metrics": [
                "ai_brain_cycle_total (labels: loop, status)",
                "ai_brain_loop_last_cycle_seconds (labels: loop)",
                "ai_brain_ledger_remaining (labels: model, kind)",
                "ai_brain_loop_tokens_total (labels: loop, kind)",
            ],
        }
    )


# ======================================================================
# expert_overview / read_expert
# ======================================================================


def _expert_names(ctx: ToolContext) -> list[str]:
    return sorted(n for n in ctx.memories if n != "brain")


def _persona_preview(memory: MemoryDir) -> str:
    lines = memory.persona_text().splitlines()
    return "\n".join(lines[:PERSONA_PREVIEW_LINES])


def _last_review(ctx: ToolContext, name: str) -> dict | None:
    brain = ctx.memories.get("brain")
    if brain is None:
        return None
    reviews = brain.recent_reviews(name, n=1)
    return reviews[0] if reviews else None


def _one_expert_overview(ctx: ToolContext, memory: MemoryDir) -> dict:
    return {
        "name": memory.name,
        "persona_preview": _persona_preview(memory),
        "facts": [
            {
                "name": stat.name,
                "title": stat.title,
                "writes": stat.writes,
                "first_written_at": stat.first_written_at,
            }
            for stat in memory.fact_stats()
        ],
        "open_gaps": [g.question for g in memory.gaps()],
        "unread_notes": len(memory.unread_notes()),
        "last_review": _last_review(ctx, memory.name),
    }


async def _expert_overview(ctx: ToolContext, args: dict) -> str:
    name = args.get("name")
    if name:
        memory = ctx.memories.get(str(name))
        if memory is None or memory.is_brain:
            known = ", ".join(_expert_names(ctx))
            return err(f"unknown expert: {name} (known: {known})")
        detail = _one_expert_overview(ctx, memory)
        loops = ctx.extras.get("loops")
        loop = loops.get(memory.name) if isinstance(loops, dict) else None
        detail["last_cycle"] = (
            None
            if loop is None or loop.last_cycle is None
            else {"status": loop.last_cycle.status, "rounds": loop.last_cycle.rounds}
        )
        detail["journal_tail"] = memory.journal_text(JOURNAL_DAYS)
        return ok(detail)

    rows = []
    for expert_name in _expert_names(ctx):
        memory = ctx.memories[expert_name]
        review = _last_review(ctx, expert_name)
        rows.append(
            {
                "name": expert_name,
                "facts": len(memory.list_facts()),
                "open_gaps": len(memory.gaps()),
                "unread_notes": len(memory.unread_notes()),
                "last_review_verdict": review.get("verdict") if review else None,
            }
        )
    return ok({"experts": rows})


_READ_WHATS = ("persona", "journal", "fact", "gaps")


async def _read_expert(ctx: ToolContext, args: dict) -> str:
    name = str(args["name"])
    memory = ctx.memories.get(name)
    if memory is None or memory.is_brain:
        known = ", ".join(_expert_names(ctx))
        return err(f"unknown expert: {name} (known: {known})")

    what = str(args["what"])
    if what == "persona":
        return ok({"persona": memory.persona_text()})
    if what == "journal":
        days = int(args.get("days") or JOURNAL_DAYS)
        return ok({"journal": memory.journal_text(max(1, days))})
    if what == "gaps":
        return ok({"gaps": [q.question for q in memory.gaps()]})
    if what == "fact":
        fact_name = args.get("fact")
        if not fact_name:
            return err("read_expert: 'fact' is required when what='fact'")
        try:
            found = memory.read_fact(str(fact_name))
        except ValueError as exc:
            return err(str(exc))
        if found is None:
            known = ", ".join(memory.list_facts())
            return err(f"unknown fact: {fact_name} (known: {known})")
        return ok({"name": found.name, "title": found.title, "body": found.body})
    return err(f"read_expert: unknown what={what!r} (one of {', '.join(_READ_WHATS)})")


# ======================================================================
# review_expert
# ======================================================================


async def _review_expert(ctx: ToolContext, args: dict) -> str:
    name = str(args["name"])
    memory = ctx.memories.get(name)
    if memory is None or memory.is_brain:
        known = ", ".join(_expert_names(ctx))
        return err(f"unknown expert: {name} (known: {known})")

    verdict = str(args["verdict"])
    if verdict not in VERDICTS:
        return err(f"review_expert: verdict must be one of {', '.join(VERDICTS)}")
    # Same belt-and-braces reasoning as write_fact/append_journal: this is
    # free text the model composes, going into a file that persists
    # indefinitely (and, in this public repo's case, could reach git history
    # via a volume backup) -- redacted here rather than trusting the
    # tool-output fence alone.
    findings = redact(str(args["findings"]))

    brain = ctx.memory  # review_expert is brain-only, so ctx.memory is the brain's own
    now = brain.clock().timestamp()
    brain.append_review(name, {"ts": now, "verdict": verdict, "findings": findings})

    if verdict != "good":
        # Same delivery path send_note uses: drop the note in the target's
        # inbox and wake it, rather than a second copy of that logic here.
        body = f"Brain review: {verdict} -- {findings}\nFix or delete the affected facts."
        memory.drop_note(REVIEW_SENDER, body)
        ctx.wake(name)

    return ok({"reviewed": name, "verdict": verdict})


# ======================================================================
# code_* tools
# ======================================================================


@dataclass(frozen=True)
class _Resolved:
    """A repo-relative path, validated and mapped onto the live snapshot."""

    relative: str
    absolute: Path


def _repo(ctx: ToolContext) -> RepoSnapshot | None:
    repo = ctx.extras.get("repo")
    return repo if isinstance(repo, RepoSnapshot) else None


def _unavailable() -> str:
    return err("snapshot unavailable: no repo snapshot has been fetched yet")


def _resolve_in_snapshot(root: Path, relative: str) -> _Resolved | None:
    """``relative`` mapped onto ``root``, refusing anything that would land
    outside it -- traversal, an absolute path, or (via ``resolve()``) a
    symlink that points out of the snapshot.

    Returns ``None`` on any violation; callers turn that into a "not found"
    rather than naming the reason, so a probing model learns nothing about
    the host filesystem from the shape of the refusal.
    """
    relative = relative.strip().lstrip("/")
    if not relative or relative == ".":
        relative = ""
    if ".." in Path(relative).parts:
        return None
    root = root.resolve()
    candidate = (root / relative).resolve() if relative else root
    if candidate != root and root not in candidate.parents:
        return None
    return _Resolved(relative=relative, absolute=candidate)


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES


def _skip_file(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in SKIP_FILE_GLOBS)


def _is_text_file(path: Path) -> bool:
    name = path.name
    if name in ENV_TEMPLATE_NAMES:
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS or name in (
        "Dockerfile",
        "Makefile",
        "CLAUDE.md",
    )


def _sniff(path: Path) -> bytes | None:
    """A small ``.json`` file's bytes, for ``is_sensitive``'s content check
    -- ``None`` for anything else, so a caller never pays to read a file this
    check would ignore anyway."""
    if path.suffix.lower() != ".json":
        return None
    try:
        if path.stat().st_size > sensitive.MAX_SNIFF_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


async def _code_list(ctx: ToolContext, args: dict) -> str:
    repo = _repo(ctx)
    if repo is None or repo.state is None:
        return _unavailable()
    relative = str(args.get("path") or "")
    resolved = _resolve_in_snapshot(repo.state.root, relative)
    if resolved is None or not resolved.absolute.exists():
        return err(f"code_list: no such path: {relative}")
    if resolved.absolute.is_file():
        # A path that names a sensitive file directly (not just one found
        # while iterating a directory below) gets the same "no such path"
        # this tool already gives a genuinely missing one -- never a
        # distinct "denied".
        if is_sensitive(resolved.relative, _sniff(resolved.absolute)):
            return err(f"code_list: no such path: {relative}")
        return ok({"path": resolved.relative, "entries": [resolved.relative]})

    entries = []
    for child in sorted(resolved.absolute.iterdir()):
        if child.is_dir() and _skip_dir(child.name):
            continue
        if child.is_file() and _skip_file(child.name):
            continue
        rel = f"{resolved.relative}/{child.name}" if resolved.relative else child.name
        # Second gate on top of extraction: a sensitive file is hidden from
        # the listing entirely, not shown-but-unreadable. The snapshot
        # should never contain one (repo.py's own is_sensitive check at
        # extraction), but a listing is cheap insurance against a future
        # reader of the snapshot directory that does not go through repo.py.
        # A directory listing is one level deep, so sniffing each small
        # .json child's content here (same as code_read/code_grep) is cheap
        # enough to be worth catching a service-account key with an
        # innocuous name before it even shows up in the list.
        if child.is_file() and is_sensitive(rel, _sniff(child)):
            continue
        suffix = "/" if child.is_dir() else ""
        entries.append(rel + suffix)
    truncated = len(entries) > MAX_LIST_ENTRIES
    return ok(
        {
            "path": resolved.relative,
            "entries": entries[:MAX_LIST_ENTRIES],
            "truncated": truncated,
            "sha": repo.state.sha,
        }
    )


async def _code_read(ctx: ToolContext, args: dict) -> str:
    repo = _repo(ctx)
    if repo is None or repo.state is None:
        return _unavailable()
    relative = str(args["path"])
    resolved = _resolve_in_snapshot(repo.state.root, relative)
    if resolved is None or not resolved.absolute.is_file():
        return err(f"code_read: no such file: {relative}")
    # A sensitive file reads as "no such file", the same as a path that
    # genuinely does not exist -- never a distinct "denied", which would
    # itself confirm the file is there. Name-only here; the content-shaped
    # check (a service-account JSON with no sensitive-looking name) runs
    # below once the bytes are already in hand for a small .json file.
    if is_sensitive(resolved.relative):
        return err(f"code_read: no such file: {relative}")
    if not _is_text_file(resolved.absolute) or _skip_file(resolved.absolute.name):
        return err(f"code_read: not a readable text file: {relative}")

    try:
        size = resolved.absolute.stat().st_size
    except OSError:
        return err(f"code_read: no such file: {relative}")
    if size > MAX_FILE_BYTES:
        return err(f"code_read: {relative} is {size} bytes, over the {MAX_FILE_BYTES} cap")

    raw = resolved.absolute.read_bytes()
    if is_sensitive(resolved.relative, raw):
        return err(f"code_read: no such file: {relative}")
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, int(args.get("start") or 1))
    end = int(args.get("end") or 0)
    # end <= 0 (unset, explicit 0, or negative) means "to the end of the
    # file"; otherwise it is clamped to both the file length and start, so a
    # caller-supplied end below start (a mistake, or start moved past a
    # short file) can never turn into a negative-index slice -- Python would
    # otherwise read that from the back of the list instead of returning an
    # empty, well-formed range.
    end = len(lines) if end <= 0 else min(end, len(lines))
    end = max(end, start)
    if start > len(lines):
        selected = []
    else:
        selected = lines[start - 1 : end]
    return ok(
        {
            "path": resolved.relative,
            "start": start,
            "end": start + len(selected) - 1 if selected else start,
            "total_lines": len(lines),
            # This is our own source, so it skips wrap_external's fence --
            # the model already runs it, it is not data from a stranger.
            # redact() still runs, defence in depth: a secret committed by
            # mistake and missed by is_sensitive's name/content check above
            # must not be handed to the model verbatim just because the file
            # around it looked like ordinary code.
            "body": redact("\n".join(selected)),
            "sha": repo.state.sha,
        }
    )


def _grep_worker(
    root: Path, snapshot_root: Path, matcher: re.Pattern, hits: list[dict]
) -> bool:
    """The actual walk-and-match, run off the event loop by ``_code_grep``.

    Appends to ``hits`` (a plain list -- the GIL makes a single ``append()``
    atomic, so a caller reading it from another thread after a timeout sees
    a consistent, if incomplete, list rather than a half-written entry) and
    returns whether the *file-count* cap was hit. It does not itself know
    about a wall-clock deadline -- ``code_grep`` enforces that from the
    outside via ``asyncio.wait_for``, since a plain thread cannot be
    interrupted mid-regex-match from Python once it is running.
    """
    for file_path in _walk_text_files(root):
        try:
            size = file_path.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            continue
        rel = str(file_path.relative_to(snapshot_root))
        # Same gate as code_read/code_list: a sensitive file is invisible to
        # grep too -- never opened, never matched, never named in a hit.
        # Name-only first (cheap, catches most of the denylist before a byte
        # is read); the content-shaped check runs after the read, same as
        # code_read, so a service-account JSON with an innocuous name is
        # still caught before any of its lines can become a hit.
        if is_sensitive(rel):
            continue
        try:
            raw = file_path.read_bytes()
        except OSError:
            continue
        if is_sensitive(rel, raw):
            continue
        text = raw.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if matcher.search(line):
                # Same defence-in-depth reasoning as code_read's body.
                hits.append(
                    {"path": rel, "line": lineno, "text": redact(line[:MAX_LINE_PREVIEW])}
                )
                if len(hits) >= MAX_GREP_HITS:
                    return True
    return False


async def _code_grep(ctx: ToolContext, args: dict) -> str:
    repo = _repo(ctx)
    if repo is None or repo.state is None:
        return _unavailable()
    pattern = str(args["pattern"])
    # A too-long or catastrophically-backtracking pattern is refused
    # outright (see regex_safety.py) -- the timeout below is a second,
    # independent defence against an aggregate walk over many ordinary
    # files simply taking a while, not a substitute for this check.
    problem = check_pattern(pattern)
    if problem is not None:
        return err(f"code_grep: {problem}")
    try:
        matcher = re.compile(pattern)
    except re.error as exc:
        return err(f"code_grep: invalid regex {pattern!r}: {exc}")

    prefix = str(args.get("path") or "")
    resolved = _resolve_in_snapshot(repo.state.root, prefix)
    if resolved is None or not resolved.absolute.exists():
        return err(f"code_grep: no such path: {prefix}")

    # Relativised against the *resolved* root (what _resolve_in_snapshot
    # already computed), not repo.state.root itself: rglob's own entries come
    # back resolved too (a temp dir under a symlinked prefix, e.g. macOS's
    # /tmp -> /private/tmp, resolves differently than the unresolved root
    # would), and relative_to requires both sides to agree.
    snapshot_root = repo.state.root.resolve()
    hits: list[dict] = []

    # Off the event loop: a big subtree is many files' worth of I/O and
    # regex matching, and running that inline would stall every other loop's
    # cycle for as long as it takes. asyncio.to_thread hands the whole walk
    # to a worker thread; wait_for's timeout bounds how long this call waits
    # for it, not how long the thread itself runs -- Python cannot interrupt
    # a thread mid-regex-match, so a timeout here means "stop waiting and
    # report what's in `hits` so far", not "the thread stopped". `hits` is
    # read after the timeout regardless of which way the race went, which is
    # safe because list.append is atomic under the GIL.
    try:
        capped = await asyncio.wait_for(
            asyncio.to_thread(_grep_worker, resolved.absolute, snapshot_root, matcher, hits),
            timeout=CODE_GREP_TIMEOUT_S,
        )
        truncated = capped
    except TimeoutError:
        truncated = True
        log.warning("[introspect] code_grep timed out after %ss; returning partial hits", CODE_GREP_TIMEOUT_S)

    result = {"hits": hits[:MAX_GREP_HITS], "truncated": truncated, "sha": repo.state.sha}
    if truncated and len(hits) <= MAX_GREP_HITS:
        result["note"] = (
            f"search did not finish within {CODE_GREP_TIMEOUT_S}s; results may be incomplete"
        )
    return ok(result)


def _walk_text_files(root: Path):
    if root.is_file():
        if _is_text_file(root) and not _skip_file(root.name):
            yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if any(_skip_dir(part) for part in path.relative_to(root).parts[:-1]):
            continue
        if _skip_file(path.name) or not _is_text_file(path):
            continue
        yield path


async def _code_log(ctx: ToolContext, args: dict) -> str:
    repo = _repo(ctx)
    if repo is None or repo.state is None:
        return _unavailable()
    n = int(args.get("n") or 20)
    commits = repo.commits(max(1, n))
    return ok(
        {
            "commits": [
                {"sha": c.sha[:12], "date": c.date, "subject": c.subject} for c in commits
            ],
            "sha": repo.state.sha,
        }
    )


# -- code_overview -------------------------------------------------------

_ENV_VAR_RE = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)\}?")


def _first_paragraph(text: str) -> str:
    for block in text.split("\n\n"):
        stripped = block.strip()
        if stripped and not stripped.startswith("#"):
            return " ".join(stripped.split())
    return ""


def _headings(text: str, max_n: int = 20) -> list[str]:
    return [line.strip("# ").strip() for line in text.splitlines() if line.startswith("#")][
        :max_n
    ]


def _env_var_name_from_entry(entry) -> list[str]:
    """Every env var *name* referenced by one ``(is_mapping, text)`` pair
    from ``_list_field_entries`` -- never the value itself. ``text`` is
    either ``KEY=literal`` (docker-compose's list form) or ``KEY: literal``
    (mapping form), possibly with a ``${VAR}``/``${VAR:-default}`` reference
    inside; all of that is just names to a model asking "what does this
    service need configured" -- the literal to the right of the separator is
    exactly the secret this tool must never repeat.

    The split is unambiguous because the shape (``is_mapping``) is already
    known from where ``_list_field_entries`` found the entry, rather than
    guessed from whether ``text`` happens to contain ``=`` or ``:`` --
    either can legitimately appear in a value, in either form
    (``- VM_URL=http://vm:8427`` is list-form with a ':' in its value;
    ``INFLUX_TOKEN: a=b`` is mapping-form with a '=' in its value), so
    picking the separator by content alone can silently pick the wrong key
    or, worse, leak a value into the name.
    """
    if isinstance(entry, tuple):
        is_mapping, text = entry
    else:  # a plain string, for callers that never tagged their entries
        is_mapping, text = False, entry
    if not isinstance(text, str):
        return []
    # Mapping form "KEY: value" -- the name is always before the first ':'.
    # List form "KEY=value" -- the name is always before the first '='.
    key = text.split(":", 1)[0].strip() if is_mapping else text.split("=", 1)[0].strip()
    names = []
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        names.append(key)
    names.extend(_ENV_VAR_RE.findall(text))
    return names


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _strip_comment(line: str) -> str:
    """Drop a trailing ``# ...`` comment, but not a ``#`` inside quotes --
    good enough for compose/workflow YAML, which never quotes a literal
    '#' in the values this reads."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def _top_level_blocks(text: str, key: str) -> list[tuple[str, list[str]]]:
    """Every ``<name>:`` mapping directly under a top-level ``key:`` block, as
    ``(name, body_lines)`` -- ``body_lines`` still carries their own indent, so
    a nested scan can recurse with the same helpers.

    This is not a YAML parser. It is a line scanner for the one shape both
    docker-compose.yml and a GitHub Actions workflow actually use in this
    repo: 2-space indents, block mappings and block sequences, no flow style
    and no anchors. Reading it this way avoids adding a YAML dependency this
    repo does not otherwise need for one introspection tool -- and it is
    deliberately forgiving: a line this scanner cannot make sense of is
    skipped rather than raising, so a compose file with a construct this does
    not handle degrades to a shorter code_overview instead of an error.
    """
    lines = [_strip_comment(line).rstrip() for line in text.splitlines()]
    key_indent: int | None = None
    key_line: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == f"{key}:" and _indent(line) == 0:
            key_indent, key_line = 0, i
            break
    if key_line is None:
        return []

    # The block's own body: every following line indented deeper than the
    # key, up to the next line at or above that indent.
    body: list[str] = []
    for line in lines[key_line + 1 :]:
        if not line.strip():
            continue
        if _indent(line) <= key_indent:
            break
        body.append(line)
    if not body:
        return []

    base_indent = min(_indent(line) for line in body)
    blocks: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    for line in body:
        if _indent(line) == base_indent and line.strip().endswith(":"):
            if current_name is not None:
                blocks.append((current_name, current_lines))
            current_name = line.strip()[:-1].strip("\"'")
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        blocks.append((current_name, current_lines))
    return blocks


def _scalar_field(body: list[str], field: str) -> str | None:
    """``field: value`` directly under this block (one indent level), or
    None. Only the first matching line is used, which is all a compose
    service's single-valued fields (image, build when it is a plain string)
    ever have."""
    if not body:
        return None
    base_indent = min(_indent(line) for line in body)
    prefix = f"{field}:"
    for line in body:
        if _indent(line) != base_indent:
            continue
        stripped = line.strip()
        if stripped == prefix or stripped.startswith(prefix + " "):
            value = stripped[len(prefix) :].strip().strip("\"'")
            return value or None
    return None


def _list_field(body: list[str], field: str) -> list[str]:
    """Every ``- item`` under a block-sequence ``field:``, or every key under
    a block-mapping ``field:`` (e.g. ``depends_on:``/``environment:`` can be
    either shape in compose) -- flattened to strings either way, since this
    tool only ever renders them as a list."""
    return [entry for _is_mapping, entry in _list_field_entries(body, field)]


def _list_field_entries(body: list[str], field: str) -> list[tuple[bool, str]]:
    """Like ``_list_field``, but tagging each entry with which YAML shape it
    came from: ``(True, "KEY: value")`` for a block-mapping entry, ``(False,
    "KEY=value")`` for a block-sequence one (the ``- `` already stripped).

    ``environment:`` is the one field this distinction actually matters for
    -- a list-form value can itself contain ``:`` (``- VM_URL=http://vm:8427``)
    and a mapping-form value can contain ``=`` (``INFLUX_TOKEN: a=b``), so
    guessing the shape back out of the flattened string (as an earlier
    version of this module did, splitting on whichever separator seemed to
    fit) could mistake one for the other and leak a value past the
    names-only name split. Recording the shape here, at the one place that
    already knows it, is what makes that split unambiguous downstream.
    """
    if not body:
        return []
    base_indent = min(_indent(line) for line in body)
    out: list[tuple[bool, str]] = []
    in_field = False
    field_indent = None
    for line in body:
        indent = _indent(line)
        stripped = line.strip()
        if indent == base_indent:
            in_field = stripped == f"{field}:" or stripped.startswith(f"{field}:")
            if in_field and stripped != f"{field}:":
                # Flow-style single-line list/dict on the same line as the
                # key ("ports: [3000]") -- rare in this repo's files, so it is
                # left as one opaque string rather than parsed further.
                value = stripped[len(field) + 1 :].strip()
                if value:
                    out.append((False, value.strip("[]").strip()))
                in_field = False
            field_indent = None
            continue
        if not in_field:
            continue
        if field_indent is None:
            field_indent = indent
        if indent < field_indent:
            in_field = False
            continue
        if stripped.startswith("- "):
            out.append((False, stripped[2:].strip().strip("\"'")))
        elif ":" in stripped:
            # Mapping form (depends_on as a dict of conditions, environment
            # as key: value pairs).
            out.append((True, stripped))
    return out


def _compose_services(text: str) -> list[dict]:
    """The shape code_overview promises for each service: image/build,
    depends_on, ports, networks, volumes and env var NAMES ONLY."""
    out = []
    for name, body in _top_level_blocks(text, "services"):
        env_entries = _list_field_entries(body, "environment")
        env_names: set[str] = set()
        for entry in env_entries:
            env_names.update(_env_var_name_from_entry(entry))

        depends_raw = _list_field(body, "depends_on")
        depends_list = sorted({d.split(":", 1)[0].strip() for d in depends_raw})

        networks_raw = _list_field(body, "networks")
        networks_list = sorted({n.split(":", 1)[0].strip() for n in networks_raw})

        out.append(
            {
                "name": name,
                "image": _scalar_field(body, "image"),
                "build": _scalar_field(body, "build"),
                "depends_on": depends_list,
                "ports": _list_field(body, "ports"),
                "networks": networks_list,
                "volumes": _list_field(body, "volumes"),
                "env_var_names": sorted(env_names),
            }
        )
    out.sort(key=lambda s: s["name"])
    return out


def _read_if_safe(root: Path, path: Path) -> str | None:
    """``path``'s text, or ``None`` if it does not exist, cannot be read, or
    ``is_sensitive`` (name and, for a small ``.json``, content) refuses it.
    ``root`` is the snapshot root ``path`` is relative to.

    ``code_overview`` reads a fixed, small set of well-known filenames
    (READMEs, ``CLAUDE.md``, ``docker-compose.yml``, CI workflow files) --
    none of them should ever match the denylist, but this is the same
    defence-in-depth reasoning as ``code_read``/``code_grep``: a check that
    costs nothing here is worth having in case a repo one day names one of
    these unusually (a component literally called ``secret-store``, say).
    """
    try:
        relative = str(path.relative_to(root))
    except ValueError:
        relative = path.name
    if is_sensitive(relative, _sniff(path)):
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _ci_workflows(root: Path) -> list[dict]:
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.exists():
        return []
    out = []
    for path in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        rel = str(path.relative_to(root))
        if is_sensitive(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        push_blocks = _top_level_blocks(text, "on")
        push_body = next((body for name, body in push_blocks if name == "push"), None)
        paths = [redact(p) for p in (_list_field(push_body, "paths") if push_body else [])]
        out.append({"file": path.name, "triggers_on_paths": paths})
    return out


def _redact_compose_service(service: dict) -> dict:
    """``redact()`` over every free-text field ``_compose_services`` fills --
    ``image``/``build`` (a registry URL can carry a token query string),
    ``ports``/``volumes`` (host paths, which can embed a household detail or
    a mount that names a secret file). ``depends_on``/``networks``/
    ``env_var_names`` are compose *identifiers* the file itself defines, not
    free text pulled from a value, so they are left alone."""
    return {
        **service,
        "image": redact(service["image"]) if service["image"] else service["image"],
        "build": redact(service["build"]) if service["build"] else service["build"],
        "ports": [redact(p) for p in service["ports"]],
        "volumes": [redact(v) for v in service["volumes"]],
    }


async def _code_overview(ctx: ToolContext, _args: dict) -> str:
    repo = _repo(ctx)
    if repo is None or repo.state is None:
        return _unavailable()
    root = repo.state.root

    components = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or _skip_dir(child.name) or child.name.startswith("."):
            continue
        readme = child / "README.md"
        component_readme_text = _read_if_safe(root, readme) if readme.exists() else None
        summary = (
            redact(_first_paragraph(component_readme_text))
            if component_readme_text is not None
            else ""
        )
        components.append({"name": child.name, "readme_summary": summary})

    compose_path = root / "docker-compose.yml"
    services = []
    compose_text = _read_if_safe(root, compose_path) if compose_path.exists() else None
    if compose_text is not None:
        try:
            services = [_redact_compose_service(s) for s in _compose_services(compose_text)]
        except OSError:
            services = []

    root_claude = root / "CLAUDE.md"
    root_readme = root / "README.md"
    claude_text = _read_if_safe(root, root_claude) if root_claude.exists() else None
    readme_text = _read_if_safe(root, root_readme) if root_readme.exists() else None
    result = {
        "sha": repo.state.sha,
        "fetched_at": repo.state.fetched_at,
        "components": components,
        "services": services,
        "claude_md_headings": (
            [redact(h) for h in _headings(claude_text)] if claude_text is not None else []
        ),
        "readme_headings": (
            [redact(h) for h in _headings(readme_text)] if readme_text is not None else []
        ),
        "ci_workflows": _ci_workflows(root),
        # Deployed vs main: the snapshot tracks REPO_REF (main by default),
        # which can be ahead of whatever rpi5 actually runs -- code_log is
        # how to see how far.
        "note": "this snapshot is the repo's REPO_REF branch, which may be ahead of what rpi5 runs -- see code_log",
    }
    return ok(result)


# ======================================================================
# registration
# ======================================================================


def register_introspect(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="system_status",
                description=(
                    "Your own runtime state: every loop's recent cycle outcomes bucketed "
                    "by usefulness, whether it is running now, proposal loops (a topic asked "
                    "again and again), the quota ledger, the discovered LAN model host, and "
                    "the ai_brain_* metrics you can vm_query for a longer history. Use it for "
                    "'why am I / is the system behaving like this' questions."
                ),
                parameters=_NO_ARGS,
            ),
            fn=_system_status,
            loops=BRAIN_ONLY,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="expert_overview",
                description=(
                    "An expert's memory at a glance: persona preview, facts with write "
                    "counts, open gaps, unread inbox count, last cycle and your most recent "
                    "review of it. Omit 'name' for a one-line row per expert instead."
                ),
                parameters={"type": "object", "properties": {"name": {"type": "string"}}},
            ),
            fn=_expert_overview,
            loops=BRAIN_ONLY,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="read_expert",
                description=(
                    "Read through one expert's memory. 'what' is one of persona, journal, "
                    "fact, gaps. For 'fact', pass 'fact' with the name. For 'journal', 'days' "
                    "controls how far back (default 2). Read-only -- you cannot write into an "
                    "expert's memory; use review_expert to leave feedback instead."
                ),
                parameters=_schema(
                    {
                        "name": {"type": "string"},
                        "what": {"type": "string", "enum": list(_READ_WHATS)},
                        "fact": {"type": "string"},
                        "days": {"type": "integer"},
                    },
                    ["name", "what"],
                ),
            ),
            fn=_read_expert,
            loops=BRAIN_ONLY,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="review_expert",
                description=(
                    "Record your verdict on one expert's memory: good, stale, wrong, "
                    "repetitive or off_goal. Appends to your own review history for that "
                    "expert. Any verdict but 'good' also drops a note in the expert's inbox "
                    "('Brain review: ..., fix or delete the affected facts') and wakes it -- "
                    "this is the only way you correct an expert; you never edit its memory "
                    "directly."
                ),
                parameters=_schema(
                    {
                        "name": {"type": "string"},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "findings": {"type": "string"},
                    },
                    ["name", "verdict", "findings"],
                ),
            ),
            fn=_review_expert,
            loops=BRAIN_ONLY,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="code_overview",
                description=(
                    "How the whole iot-fetcher system fits together: every top-level "
                    "component with its README's first paragraph, docker-compose.yml's "
                    "services (image/build, depends_on, ports, networks, volumes and env var "
                    "NAMES ONLY -- never values), the root CLAUDE.md/README headings, and the "
                    "CI workflows with the paths each triggers on. Call this first, then drill "
                    "down with code_read/code_grep. Reflects REPO_REF (usually main), which "
                    "can be ahead of what rpi5 actually runs -- see code_log."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            fn=_code_overview,
            loops=BRAIN_ONLY,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="code_list",
                description=(
                    "List a directory in the iot-fetcher repo snapshot (git-tracked files "
                    "only). Omit 'path' for the repo root. Paths are repo-relative, e.g. "
                    "'pool-pump-planner'."
                ),
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            ),
            fn=_code_list,
            loops=BRAIN_ONLY,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="code_read",
                description=(
                    "Read a text file from the iot-fetcher repo snapshot, optionally a line "
                    "range ('start'/'end', 1-based, inclusive). Omit 'end' or pass 0 (or "
                    "negative) for 'to the end of the file'; an 'end' below 'start' is "
                    "clamped up to 'start' rather than erroring. Repo-relative path, e.g. "
                    "'pool-pump-planner/vm.go'. Text files only, max 256 KB."
                ),
                parameters=_schema(
                    {
                        "path": {"type": "string"},
                        "start": {"type": "integer"},
                        "end": {"type": "integer"},
                    },
                    ["path"],
                ),
            ),
            fn=_code_read,
            loops=BRAIN_ONLY,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="code_grep",
                description=(
                    "Search the repo snapshot for a regex, optionally under one path prefix. "
                    "Returns matching lines with file and line number, capped at 100 hits. A "
                    "search that does not finish within 10s returns whatever it found so far "
                    "with truncated=true -- narrow the pattern or path if that happens."
                ),
                parameters=_schema(
                    {"pattern": {"type": "string"}, "path": {"type": "string"}},
                    ["pattern"],
                ),
            ),
            fn=_code_grep,
            loops=BRAIN_ONLY,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="code_log",
                description=(
                    "The last N commits (sha, date, subject) of the repo snapshot, newest "
                    "first. Use it to check whether something was already fixed rather than "
                    "re-diagnosing it. Default 20."
                ),
                parameters={"type": "object", "properties": {"n": {"type": "integer"}}},
            ),
            fn=_code_log,
            loops=BRAIN_ONLY,
        )
    )
