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

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

from ai_brain.introspection import proposal_loops, usefulness_rows
from ai_brain.ledger import Ledger
from ai_brain.llm import ToolSpec
from ai_brain.memory import MemoryDir
from ai_brain.redact import redact
from ai_brain.repo import RepoSnapshot
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

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
MAX_PATTERN_CHARS = 128
MAX_LINE_PREVIEW = 300

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
        ".json", ".md", ".toml", ".cfg", ".ini", ".txt", ".env.example",
        ".sql", ".html", ".css", ".dockerfile", ".mod", ".sum",
    }
)


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
            "proposal_loops": proposal_loops(approvals)["loops"],
            "ledger": ledger.usage(),
            "lan_hosts": _lan_summary(ctx.extras.get("chain")),
            "metrics": [
                "ai_brain_cycle_total (labels: loop, status)",
                "ai_brain_loop_last_cycle_seconds (labels: loop)",
                "ai_brain_ledger_remaining (labels: model, kind)",
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
    return path.suffix.lower() in TEXT_EXTENSIONS or path.name in (
        "Dockerfile",
        "Makefile",
        "CLAUDE.md",
    )


async def _code_list(ctx: ToolContext, args: dict) -> str:
    repo = _repo(ctx)
    if repo is None or repo.state is None:
        return _unavailable()
    relative = str(args.get("path") or "")
    resolved = _resolve_in_snapshot(repo.state.root, relative)
    if resolved is None or not resolved.absolute.exists():
        return err(f"code_list: no such path: {relative}")
    if resolved.absolute.is_file():
        return ok({"path": resolved.relative, "entries": [resolved.relative]})

    entries = []
    for child in sorted(resolved.absolute.iterdir()):
        if child.is_dir() and _skip_dir(child.name):
            continue
        if child.is_file() and _skip_file(child.name):
            continue
        suffix = "/" if child.is_dir() else ""
        rel = f"{resolved.relative}/{child.name}" if resolved.relative else child.name
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
    if not _is_text_file(resolved.absolute) or _skip_file(resolved.absolute.name):
        return err(f"code_read: not a readable text file: {relative}")

    try:
        size = resolved.absolute.stat().st_size
    except OSError:
        return err(f"code_read: no such file: {relative}")
    if size > MAX_FILE_BYTES:
        return err(f"code_read: {relative} is {size} bytes, over the {MAX_FILE_BYTES} cap")

    text = resolved.absolute.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, int(args.get("start") or 1))
    end = int(args.get("end") or len(lines))
    end = min(end, len(lines))
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
            "body": "\n".join(selected),
            "sha": repo.state.sha,
        }
    )


async def _code_grep(ctx: ToolContext, args: dict) -> str:
    repo = _repo(ctx)
    if repo is None or repo.state is None:
        return _unavailable()
    pattern = str(args["pattern"])
    if len(pattern) > MAX_PATTERN_CHARS:
        return err(f"code_grep: pattern too long (max {MAX_PATTERN_CHARS})")
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
    truncated = False
    for file_path in _walk_text_files(resolved.absolute):
        try:
            size = file_path.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            continue
        rel = str(file_path.relative_to(snapshot_root))
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if matcher.search(line):
                hits.append({"path": rel, "line": lineno, "text": line[:MAX_LINE_PREVIEW]})
                if len(hits) >= MAX_GREP_HITS:
                    truncated = True
                    break
        if truncated:
            break
    return ok({"hits": hits, "truncated": truncated, "sha": repo.state.sha})


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


def _env_var_names(value) -> list[str]:
    """Every env var *name* referenced by a compose value -- never the value
    itself. ``value`` can be a ``KEY=literal`` string (docker-compose's list
    form) or a ``${VAR}``/``${VAR:-default}`` reference inside one; both cases
    are just names to a model asking "what does this service need configured"
    -- the literal on the right of ``=`` is exactly the secret this tool must
    never repeat.
    """
    names = []
    if isinstance(value, str):
        # List form "KEY=value" or "KEY=${OTHER}": the name is always the
        # part before the first '=', when there is one.
        key = value.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            names.append(key)
        names.extend(_ENV_VAR_RE.findall(value))
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
    if not body:
        return []
    base_indent = min(_indent(line) for line in body)
    out: list[str] = []
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
                    out.append(value.strip("[]").strip())
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
            out.append(stripped[2:].strip().strip("\"'"))
        elif ":" in stripped:
            # Mapping form (depends_on as a dict of conditions, environment
            # as key: value pairs): the key is what a name list wants, the
            # env line is handled by the caller instead.
            out.append(stripped)
    return out


def _compose_services(text: str) -> list[dict]:
    """The shape code_overview promises for each service: image/build,
    depends_on, ports, networks, volumes and env var NAMES ONLY."""
    out = []
    for name, body in _top_level_blocks(text, "services"):
        env_lines = _list_field(body, "environment")
        env_names: set[str] = set()
        for entry in env_lines:
            env_names.update(_env_var_names(entry.split(":", 1)[0] if ":" in entry and "=" not in entry else entry))

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


def _ci_workflows(root: Path) -> list[dict]:
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.exists():
        return []
    out = []
    for path in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        push_blocks = _top_level_blocks(text, "on")
        push_body = next((body for name, body in push_blocks if name == "push"), None)
        paths = _list_field(push_body, "paths") if push_body else []
        out.append({"file": path.name, "triggers_on_paths": paths})
    return out


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
        summary = ""
        if readme.exists():
            try:
                summary = _first_paragraph(readme.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                summary = ""
        components.append({"name": child.name, "readme_summary": summary})

    compose_path = root / "docker-compose.yml"
    services = []
    if compose_path.exists():
        try:
            services = _compose_services(compose_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            services = []

    root_claude = root / "CLAUDE.md"
    root_readme = root / "README.md"
    result = {
        "sha": repo.state.sha,
        "fetched_at": repo.state.fetched_at,
        "components": components,
        "services": services,
        "claude_md_headings": (
            _headings(root_claude.read_text(encoding="utf-8", errors="replace"))
            if root_claude.exists()
            else []
        ),
        "readme_headings": (
            _headings(root_readme.read_text(encoding="utf-8", errors="replace"))
            if root_readme.exists()
            else []
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
                    "range ('start'/'end', 1-based, inclusive). Repo-relative path, e.g. "
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
                    "Returns matching lines with file and line number, capped at 100 hits."
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
