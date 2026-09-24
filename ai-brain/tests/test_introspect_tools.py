"""Tests for the brain-only introspection tools (tools/introspect.py).

Follows the pattern of test_memory_tools.py / test_usage_tool.py: a
ToolRegistry with just register_introspect, a ToolContext built from the
brain_dir/expert_dir fixtures in conftest.py, and json.loads(dispatch(...)).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from ai_brain.config import load_settings
from ai_brain.ledger import Ledger, Limits
from ai_brain.llm import ToolCall
from ai_brain.repo import Commit, RepoSnapshot, RepoState
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.tools.introspect import register_introspect

ENV = {"MEMORY_ROOT": "/tmp/ai-brain-introspect-test", "VM_URL": "http://vm.test"}


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_introspect(reg)
    return reg


@dataclass
class FakeLoop:
    """The slice of AgentLoop system_status/expert_overview actually read."""

    priority: str = "expert"
    cycle_counts: dict[str, int] = field(default_factory=dict)
    last_cycle_at: float = 0.0
    trace: object = None
    last_cycle: object = None


@dataclass
class FakeCycleResult:
    status: str = "ok"
    rounds: int = 3
    model: str = "fake:1"


@dataclass
class FakeTrace:
    in_progress: bool = False


class FakeProposal:
    def __init__(self, topic: str, status: str, created: str, kind: str = "sonos_say"):
        self.topic = topic
        self.status = status
        self.created = created
        self.kind = kind
        self.id = f"{created}-{kind}"
        self.result = ""


class FakeApprovals:
    def __init__(self, proposals=None):
        self._proposals = proposals or []

    def all(self):
        return self._proposals


@pytest.fixture
def make_ctx(brain_dir, expert_dir, tmp_path):
    memories = {"brain": brain_dir, "energy": expert_dir}

    def _make(loop: str = "brain", extras: dict | None = None) -> ToolContext:
        return ToolContext(
            loop=loop,
            memory=memories[loop],
            memories=memories,
            settings=load_settings(ENV),
            wake=lambda _loop: None,
            extras=extras or {},
        )

    return _make


async def call(registry, ctx, tool, **args):
    return json.loads(await registry.dispatch(ctx, ToolCall(id="1", name=tool, args=args)))


# -- allowlist -----------------------------------------------------------


def test_every_introspect_tool_is_brain_only(registry):
    brain = {s.name for s in registry.specs_for("brain")}
    energy = {s.name for s in registry.specs_for("energy")}
    new_tools = {
        "system_status",
        "expert_overview",
        "read_expert",
        "review_expert",
        "code_overview",
        "code_list",
        "code_read",
        "code_grep",
        "code_log",
    }
    assert new_tools <= brain
    assert new_tools.isdisjoint(energy)


# -- system_status ---------------------------------------------------------


async def test_system_status_reports_cycles_usefulness_and_ledger(registry, make_ctx, tmp_path):
    loops = {
        "brain": FakeLoop(
            priority="brain",
            cycle_counts={"ok": 3, "max_rounds": 1},
            last_cycle_at=100.0,
            trace=FakeTrace(in_progress=True),
            last_cycle=FakeCycleResult(status="ok", rounds=4, model="fake:1"),
        ),
        "energy": FakeLoop(cycle_counts={"ok": 1}),
    }
    ledger = Ledger({"fake:1": Limits(rpm=10, tpm=1000, rpd=100)}, tmp_path / "ledger.json")
    approvals = FakeApprovals(
        [
            FakeProposal("pool", "rejected", "2026-09-06T10:00:00Z"),
            FakeProposal("pool", "rejected", "2026-09-06T11:00:00Z"),
        ]
    )
    ctx = make_ctx(extras={"loops": loops, "approvals": approvals, "ledger": ledger})

    out = await call(registry, ctx, "system_status")

    assert out["ok"] is True
    names = [c["name"] for c in out["cycles"]]
    assert names == ["brain", "energy"]
    brain_row = out["cycles"][0]
    assert brain_row["running"] is True
    assert brain_row["last_cycle"] == {"status": "ok", "rounds": 4, "model": "fake:1"}
    assert out["usefulness"]["loops"][0]["real"] == 3
    assert out["usefulness"]["loops"][0]["repeat"] == 1
    assert out["proposal_loops"][0]["topic"] == "pool"
    assert out["proposal_loops"][0]["laps"] == 2
    assert out["ledger"]["day"] == ledger.day
    assert any("ai_brain_cycle_total" in m for m in out["metrics"])


async def test_system_status_without_runtime_state_is_an_error(registry, make_ctx):
    out = await call(registry, make_ctx(), "system_status")
    assert "error" in out


# -- expert_overview / read_expert -----------------------------------------


async def test_expert_overview_lists_a_row_per_expert(registry, make_ctx, expert_dir):
    expert_dir.write_fact("pool", "Pool status", "cold")
    ctx = make_ctx(extras={"loops": {"energy": FakeLoop()}})

    out = await call(registry, ctx, "expert_overview")

    assert out["experts"] == [
        {
            "name": "energy",
            "facts": 1,
            "open_gaps": 0,
            "unread_notes": 0,
            "last_review_verdict": None,
        }
    ]


async def test_expert_overview_for_one_expert_covers_persona_facts_gaps_and_review(
    registry, make_ctx, expert_dir, brain_dir
):
    expert_dir.write_fact("pool", "Pool status", "cold for weeks")
    expert_dir.open_gap("Why is the pool cold?", "blocks heating plan")
    expert_dir.drop_note("brain", "check the pump")
    brain_dir.append_review("energy", {"ts": 1.0, "verdict": "stale", "findings": "old numbers"})

    ctx = make_ctx(extras={"loops": {"energy": FakeLoop(last_cycle=FakeCycleResult())}})
    out = await call(registry, ctx, "expert_overview", name="energy")

    assert out["name"] == "energy"
    assert out["facts"][0]["name"] == "pool"
    assert out["open_gaps"] == ["Why is the pool cold?"]
    assert out["unread_notes"] == 1
    assert out["last_review"] == {"ts": 1.0, "verdict": "stale", "findings": "old numbers"}
    assert out["last_cycle"] == {"status": "ok", "rounds": 3}
    assert "journal_tail" in out


async def test_expert_overview_unknown_name_lists_the_known_ones(registry, make_ctx):
    out = await call(registry, make_ctx(), "expert_overview", name="mars")
    assert "unknown expert" in out["error"]
    assert "energy" in out["error"]


async def test_expert_overview_refuses_the_brain_itself(registry, make_ctx):
    out = await call(registry, make_ctx(), "expert_overview", name="brain")
    assert "unknown expert" in out["error"]


async def test_read_expert_persona(registry, make_ctx, expert_dir):
    out = await call(registry, make_ctx(), "read_expert", name="energy", what="persona")
    assert out["persona"] == expert_dir.persona_text()


async def test_read_expert_journal(registry, make_ctx, expert_dir):
    expert_dir.append_journal("checked the meter")
    out = await call(registry, make_ctx(), "read_expert", name="energy", what="journal")
    assert "checked the meter" in out["journal"]


async def test_read_expert_gaps(registry, make_ctx, expert_dir):
    expert_dir.open_gap("Tariff after March?", "pricing")
    out = await call(registry, make_ctx(), "read_expert", name="energy", what="gaps")
    assert out["gaps"] == ["Tariff after March?"]


async def test_read_expert_fact(registry, make_ctx, expert_dir):
    expert_dir.write_fact("pool", "Pool status", "cold")
    out = await call(registry, make_ctx(), "read_expert", name="energy", what="fact", fact="pool")
    assert out == {"ok": True, "name": "pool", "title": "Pool status", "body": "cold"}


async def test_read_expert_unknown_fact_lists_valid_ones(registry, make_ctx, expert_dir):
    expert_dir.write_fact("pool", "Pool status", "cold")
    out = await call(
        registry, make_ctx(), "read_expert", name="energy", what="fact", fact="nope"
    )
    assert "unknown fact" in out["error"]
    assert "pool" in out["error"]


async def test_read_expert_unknown_expert_is_an_error(registry, make_ctx):
    out = await call(registry, make_ctx(), "read_expert", name="mars", what="persona")
    assert "unknown expert" in out["error"]


async def test_read_expert_cannot_target_the_brain(registry, make_ctx):
    out = await call(registry, make_ctx(), "read_expert", name="brain", what="persona")
    assert "unknown expert" in out["error"]


# -- review_expert -----------------------------------------------------


async def test_review_expert_good_writes_no_note(registry, make_ctx, brain_dir, expert_dir):
    woken = []
    ctx = make_ctx()
    ctx.wake = woken.append

    out = await call(registry, ctx, "review_expert", name="energy", verdict="good", findings="looks fine")

    assert out["reviewed"] == "energy"
    assert brain_dir.recent_reviews("energy", 1)[0]["verdict"] == "good"
    assert expert_dir.unread_notes() == []
    assert woken == []


async def test_review_expert_wrong_notes_the_expert_and_wakes_it(
    registry, make_ctx, brain_dir, expert_dir
):
    woken = []
    ctx = make_ctx()
    ctx.wake = woken.append

    out = await call(
        registry, ctx, "review_expert", name="energy", verdict="wrong", findings="heater fact is stale"
    )

    assert out["verdict"] == "wrong"
    notes = expert_dir.unread_notes()
    assert len(notes) == 1
    assert notes[0].sender == "brain"
    assert "Brain review: wrong" in notes[0].body
    assert "heater fact is stale" in notes[0].body
    assert "fix or delete" in notes[0].body.lower()
    assert woken == ["energy"]


async def test_review_expert_findings_are_redacted(registry, make_ctx, brain_dir):
    ctx = make_ctx()
    await call(
        registry,
        ctx,
        "review_expert",
        name="energy",
        verdict="stale",
        findings="token=supersecretvalue123",
    )
    stored = brain_dir.recent_reviews("energy", 1)[0]
    assert "supersecretvalue123" not in stored["findings"]


async def test_review_expert_note_findings_are_also_redacted(registry, make_ctx, expert_dir):
    ctx = make_ctx()
    await call(
        registry,
        ctx,
        "review_expert",
        name="energy",
        verdict="wrong",
        findings="token=supersecretvalue123",
    )
    note = expert_dir.unread_notes()[0]
    assert "supersecretvalue123" not in note.body


async def test_review_expert_prunes_to_50(registry, make_ctx, brain_dir):
    ctx = make_ctx()
    for i in range(55):
        await call(
            registry, ctx, "review_expert", name="energy", verdict="good", findings=f"pass {i}"
        )
    all_reviews = brain_dir.recent_reviews("energy", n=100)
    assert len(all_reviews) == 50
    assert all_reviews[0]["findings"] == "pass 54"


async def test_review_expert_unknown_verdict_is_an_error(registry, make_ctx):
    out = await call(registry, make_ctx(), "review_expert", name="energy", verdict="meh", findings="x")
    assert "error" in out


async def test_review_expert_unknown_expert_is_an_error(registry, make_ctx):
    out = await call(registry, make_ctx(), "review_expert", name="mars", verdict="good", findings="x")
    assert "unknown expert" in out["error"]


# -- code_* ---------------------------------------------------------------


@pytest.fixture
def fake_repo(tmp_path):
    """A RepoSnapshot whose ``state`` points at a small tree on disk, no
    network -- refresh() is exercised in test_repo.py instead."""
    root = tmp_path / "snapshot"
    (root / "pool-pump-planner").mkdir(parents=True)
    (root / "pool-pump-planner" / "vm.go").write_text(
        "package main\n\nfunc fetchWaterTempAt(t time.Time) float64 {\n\treturn 0\n}\n",
        encoding="utf-8",
    )
    (root / "pool-pump-planner" / "README.md").write_text(
        "# pool-pump-planner\n\nPlans the pool pump schedule from Tibber prices.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# iot-fetcher\n\n## Components\n\n## Development\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Repository Information\n\n# Build Instructions\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text(
        "services:\n"
        "  ai-brain:\n"
        "    image: example/ai-brain:latest\n"
        "    depends_on:\n"
        "      - database-auth\n"
        "    environment:\n"
        "      - INFLUX_TOKEN=supersecret\n"
        "      - VM_URL=http://vm:8427\n"
        "    ports:\n"
        "      - \"8091:8091\"\n"
        "    volumes:\n"
        "      - ai-brain-memory:/memory\n",
        encoding="utf-8",
    )
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "build.yml").write_text(
        "name: build\n" "on:\n" "  push:\n" "    paths:\n" "      - 'ai-brain/**'\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("SECRET=do-not-read-me\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("noise", encoding="utf-8")

    repo = RepoSnapshot(tmp_path / "memory", "tkhduracell/iot-fetcher", "main", http=None)
    repo._state = RepoState(sha="deadbeef1234", fetched_at=1_700_000_000.0, root=root)
    repo._commits = [
        Commit(sha="deadbeef1234", date="2026-09-20T10:00:00Z", subject="fix(pool): use vm.go"),
        Commit(sha="cafefeed5678", date="2026-09-19T10:00:00Z", subject="feat(brain): add tool"),
    ]
    return repo


async def test_code_read_a_known_file(registry, make_ctx, fake_repo):
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_read", path="pool-pump-planner/vm.go")
    assert "fetchWaterTempAt" in out["body"]
    assert out["sha"] == "deadbeef1234"


async def test_code_read_a_line_range(registry, make_ctx, fake_repo):
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(
        registry, ctx, "code_read", path="pool-pump-planner/vm.go", start=1, end=1
    )
    assert out["body"] == "package main"


async def test_code_read_missing_env_file_is_not_found(registry, make_ctx, fake_repo):
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_read", path=".env")
    assert "error" in out
    assert "do-not-read-me" not in json.dumps(out)


async def test_code_grep_finds_the_function(registry, make_ctx, fake_repo):
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_grep", pattern="fetchWaterTempAt")
    hits = out["hits"]
    assert any(h["path"] == "pool-pump-planner/vm.go" for h in hits)


async def test_code_grep_caps_hits(registry, make_ctx, tmp_path):
    root = tmp_path / "manyhits"
    root.mkdir()
    for i in range(150):
        (root / f"f{i}.py").write_text("needle\n", encoding="utf-8")
    repo = RepoSnapshot(tmp_path / "memory", "x/y", "main", http=None)
    repo._state = RepoState(sha="s", fetched_at=0.0, root=root)
    ctx = make_ctx(extras={"repo": repo})

    out = await call(registry, ctx, "code_grep", pattern="needle")

    assert out["truncated"] is True
    assert len(out["hits"]) <= 100


async def test_code_list_root(registry, make_ctx, fake_repo):
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_list")
    assert "pool-pump-planner/" in out["entries"]
    assert not any("node_modules" in e for e in out["entries"])


async def test_code_list_traversal_is_refused(registry, make_ctx, fake_repo):
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_list", path="../../etc")
    assert "error" in out


async def test_code_read_traversal_is_refused(registry, make_ctx, fake_repo):
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_read", path="../../../etc/passwd")
    assert "error" in out


async def test_code_read_absolute_path_is_refused(registry, make_ctx, fake_repo):
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_read", path="/etc/passwd")
    assert "error" in out


async def test_code_read_symlink_escape_is_refused(registry, make_ctx, fake_repo, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("should never be read\n", encoding="utf-8")
    link = fake_repo.state.root / "escape.txt"
    link.symlink_to(outside)

    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_read", path="escape.txt")

    assert "error" in out


async def test_code_read_disallowed_extension_is_refused(registry, make_ctx, fake_repo):
    binary = fake_repo.state.root / "image.png"
    binary.write_bytes(b"\x89PNG\r\n")
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_read", path="image.png")
    assert "error" in out


async def test_code_log_lists_recent_commits(registry, make_ctx, fake_repo):
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_log", n=1)
    assert out["commits"] == [
        {"sha": "deadbeef1234", "date": "2026-09-20T10:00:00Z", "subject": "fix(pool): use vm.go"}
    ]


async def test_code_overview_covers_components_services_and_ci(registry, make_ctx, fake_repo):
    ctx = make_ctx(extras={"repo": fake_repo})
    out = await call(registry, ctx, "code_overview")

    names = [c["name"] for c in out["components"]]
    assert "pool-pump-planner" in names
    pool = next(c for c in out["components"] if c["name"] == "pool-pump-planner")
    assert "Tibber" in pool["readme_summary"]

    ai_brain_service = next(s for s in out["services"] if s["name"] == "ai-brain")
    assert ai_brain_service["depends_on"] == ["database-auth"]
    assert "INFLUX_TOKEN" in ai_brain_service["env_var_names"]
    assert "VM_URL" in ai_brain_service["env_var_names"]
    # Never the secret value, only its name.
    assert "supersecret" not in json.dumps(out)

    assert "Repository Information" in out["claude_md_headings"]
    assert any(w["file"] == "build.yml" for w in out["ci_workflows"])
    build = next(w for w in out["ci_workflows"] if w["file"] == "build.yml")
    assert build["triggers_on_paths"] == ["ai-brain/**"]


async def test_code_overview_without_a_snapshot_reports_unavailable(registry, make_ctx, tmp_path):
    repo = RepoSnapshot(tmp_path / "memory", "x/y", "main", http=None)
    ctx = make_ctx(extras={"repo": repo})
    out = await call(registry, ctx, "code_overview")
    assert "unavailable" in out["error"]


async def test_code_tools_without_a_repo_extra_report_unavailable(registry, make_ctx):
    out = await call(registry, make_ctx(), "code_read", path="README.md")
    assert "unavailable" in out["error"]
