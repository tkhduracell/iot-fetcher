"""The brain's identity changes only when Filip approves it.

``rewrite_identity`` is a tool that files a proposal; ✅ writes through
``MemoryDir.rewrite_identity``. These tests drive the real tool, the real
``Approvals`` and the real ``Executors`` over a real ``MemoryDir``, with only
Slack faked, so what they pin is the file on disk: what it holds after each
outcome, and what Filip is shown before he picks one.
"""

import asyncio
import json
from datetime import timedelta

import pytest
from sessions_helpers import build_loop, build_registry, call, end, reply

from ai_brain import identity
from ai_brain.approvals import (
    APPROVE_ACTION,
    EXPIRE_AFTER,
    REJECT_ACTION,
    SECTION_LIMIT,
    Approvals,
    proposal_target,
    rejected_groups,
    rejection_memory_section,
    resolved_text,
)
from ai_brain.config import load_settings
from ai_brain.executors import Executors
from ai_brain.llm import ToolCall
from ai_brain.loop import BRAIN_INSTRUCTIONS
from ai_brain.memory import MemoryDir
from ai_brain.provenance import READ_SOURCES
from ai_brain.sessions import read_events, session_files
from ai_brain.tools import READ_ONLY_TOOLS, ToolContext, ToolRegistry
from ai_brain.tools.memory_tools import register_memory_tools
from ai_brain.tools.propose import register_propose_tool

OLD = (
    "I am ai-brain, on a Raspberry Pi in Filip's house in Malmo. I run rarely, on the "
    "strongest model, so I spend each cycle thinking rather than measuring.\n"
    "\n"
    "I look for contradictions between what the experts believe, and I correct them "
    "through reviews rather than editing their memory.\n"
    "I ask Filip when only he can know the answer.\n"
)
NEW = (
    "I am ai-brain, on a Raspberry Pi in Filip's house in Malmo. I run rarely, on the "
    "strongest model, so I spend each cycle thinking rather than measuring.\n"
    "\n"
    "I look for contradictions between what the experts believe, and I correct them "
    "through reviews rather than editing their memory.\n"
    "I ask Filip when only he can know the answer, and I keep the question short.\n"
    "I distrust a number I have only seen once.\n"
)
OTHER = NEW.replace("distrust a number", "distrust a reading")
FRONT = "---\nemoji: 🧠\n---\n"


class FakeSlack:
    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []
        self.blocks: list[list[dict]] = []

    async def __call__(self, topic, text, blocks=None):
        self.posts.append((topic, text))
        self.blocks.append(blocks)
        return f"ts-{len(self.posts)}"


@pytest.fixture
def slack():
    return FakeSlack()


@pytest.fixture(autouse=True)
def seeded(brain_dir):
    """The identity as the seed ships it: frontmatter, then the body."""
    brain_dir.persona_path.write_text(FRONT + OLD, encoding="utf-8")


def make_approvals(brain_dir, clock, slack, **env):
    executors = Executors(load_settings(env), None, clock, brain_dir)
    return Approvals(brain_dir, executors, clock, on_message=slack)


@pytest.fixture
def approvals(brain_dir, clock, slack):
    return make_approvals(brain_dir, clock, slack)


def make_registry():
    registry = ToolRegistry()
    register_memory_tools(registry)
    register_propose_tool(registry)
    return registry


def make_ctx(brain_dir, approvals):
    return ToolContext(
        loop="brain",
        memory=brain_dir,
        memories={"brain": brain_dir},
        settings=load_settings({}),
        wake=lambda name: None,
        extras={"approvals": approvals},
    )


async def rewrite(brain_dir, approvals, body=NEW, reason="sharper questions", **extra):
    registry = make_registry()
    out = await registry.dispatch(
        make_ctx(brain_dir, approvals),
        ToolCall(id="1", name="rewrite_identity", args={"body": body, "reason": reason, **extra}),
    )
    return json.loads(out)


def file_text(brain_dir) -> str:
    return brain_dir.persona_path.read_text(encoding="utf-8")


def the_proposal(approvals):
    [proposal] = approvals.all()
    return proposal


def notes(brain_dir) -> list[str]:
    return [n.body for n in brain_dir.unread_notes() if n.sender == "approvals"]


# -- the tool files a proposal and writes nothing ---------------------------


async def test_the_tool_files_a_proposal_and_returns_pending(brain_dir, approvals, slack):
    before = file_text(brain_dir)

    out = await rewrite(brain_dir, approvals)

    proposal = the_proposal(approvals)
    assert out["status"] == "pending approval"
    assert out["id"] == proposal.id
    assert out["identity_changed"] is False
    assert (proposal.kind, proposal.status, proposal.topic) == ("rewrite_identity", "pending", "identity")
    assert proposal.payload["body"] == NEW
    assert proposal.payload["base_hash"] == brain_dir.persona_digest()
    assert proposal.reason == "sharper questions"
    assert file_text(brain_dir) == before
    assert len(slack.posts) == 1


@pytest.mark.parametrize(
    "scenario",
    ["filed", "refused-short", "slack-down", "no-approvals", "repeat-of-current", "pending-already"],
)
async def test_the_tool_itself_never_changes_the_file(brain_dir, clock, slack, monkeypatch, scenario):
    """Whatever the tool is asked and however it ends, the file is untouched and
    ``MemoryDir.rewrite_identity`` is never called by it."""
    approvals = make_approvals(brain_dir, clock, slack)

    def _boom(self, body):
        raise AssertionError("the tool wrote the identity")

    monkeypatch.setattr(MemoryDir, "rewrite_identity", _boom)
    before = file_text(brain_dir)
    registry = make_registry()
    ctx = make_ctx(brain_dir, approvals)
    body = NEW
    if scenario == "refused-short":
        body = "curious"
    if scenario == "repeat-of-current":
        body = OLD
    if scenario == "slack-down":

        async def _down(*_a):
            raise ConnectionError("down")

        approvals.on_message = _down
    if scenario == "no-approvals":
        ctx.extras.clear()
    if scenario == "pending-already":
        await approvals.propose("rewrite_identity", {"body": OTHER}, "first", "identity")

    for _ in range(2):
        await registry.dispatch(
            ctx,
            ToolCall(id="1", name="rewrite_identity", args={"body": body, "reason": "why"}),
        )

    assert file_text(brain_dir) == before
    assert brain_dir.identity_history() == []


async def test_the_tool_stays_brain_only(brain_dir, expert_dir, approvals):
    registry = make_registry()
    assert "rewrite_identity" in {s.name for s in registry.specs_for("brain")}
    assert "rewrite_identity" not in {s.name for s in registry.specs_for("energy")}
    ctx = ToolContext(
        loop="energy",
        memory=expert_dir,
        memories={"brain": brain_dir, "energy": expert_dir},
        settings=load_settings({}),
        wake=lambda name: None,
        extras={"approvals": approvals},
    )
    out = json.loads(
        await registry.dispatch(
            ctx, ToolCall(id="1", name="rewrite_identity", args={"body": NEW, "reason": "x"})
        )
    )
    assert "policy" in out["error"]
    assert approvals.all() == []


async def test_propose_does_not_offer_the_kind_and_refuses_it(brain_dir, approvals):
    registry = make_registry()
    [spec] = [s for s in registry.specs_for("brain") if s.name == "propose"]
    assert "rewrite_identity" not in spec.parameters["properties"]["kind"]["enum"]

    out = json.loads(
        await registry.dispatch(
            make_ctx(brain_dir, approvals),
            ToolCall(
                id="1",
                name="propose",
                args={
                    "kind": "rewrite_identity",
                    "payload": {"body": NEW},
                    "reason": "r",
                    "topic": "identity",
                },
            ),
        )
    )

    assert "rewrite_identity tool" in out["error"]
    assert approvals.all() == []


async def test_list_proposals_does_not_dump_the_text(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    registry = make_registry()

    out = json.loads(
        await registry.dispatch(
            make_ctx(brain_dir, approvals), ToolCall(id="1", name="list_proposals", args={})
        )
    )

    [pending] = out["pending"]
    assert pending["payload"] == {"body_hash": identity.digest(NEW), "chars": len(NEW.strip())}
    assert "distrust" not in json.dumps(out)


def test_it_is_not_a_read_only_tool():
    assert "rewrite_identity" not in READ_ONLY_TOOLS


# -- approve, reject, expire, dry run ---------------------------------------


async def test_approve_applies_it_with_the_revision_trail_and_the_emoji(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    proposal = the_proposal(approvals)

    done = await approvals.on_reaction(proposal.slack_ts, "white_check_mark")

    assert done.status == "executed"
    assert brain_dir.persona_text() == NEW.strip()
    assert brain_dir.persona_meta() == {"emoji": "🧠"}
    assert file_text(brain_dir) == FRONT + NEW
    # The file as it was is kept, frontmatter and all.
    assert [r.body for r in brain_dir.identity_history()] == [FRONT + OLD]
    assert any("executed" in n and proposal.id in n for n in notes(brain_dir))


async def test_approve_by_button_does_the_same(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    proposal = the_proposal(approvals)

    done = await approvals.on_button(proposal.slack_ts, APPROVE_ACTION)

    assert done.status == "executed"
    assert brain_dir.persona_text() == NEW.strip()


async def test_reject_leaves_the_file_alone_and_tells_the_brain(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    proposal = the_proposal(approvals)
    before = file_text(brain_dir)

    done = await approvals.on_reaction(proposal.slack_ts, "x")

    assert done.status == "rejected"
    assert file_text(brain_dir) == before
    assert brain_dir.identity_history() == []
    assert any("rejected" in n and proposal.id in n for n in notes(brain_dir))


async def test_reject_button_too(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    done = await approvals.on_button(the_proposal(approvals).slack_ts, REJECT_ACTION)
    assert done.status == "rejected"
    assert brain_dir.persona_text() == OLD.strip()


async def test_expiry_leaves_the_file_alone_and_tells_the_brain(brain_dir, approvals, clock):
    await rewrite(brain_dir, approvals)
    proposal = the_proposal(approvals)
    before = file_text(brain_dir)
    clock.state["now"] += EXPIRE_AFTER + timedelta(minutes=1)

    [expired] = await approvals.expire()

    assert expired.status == "expired"
    assert file_text(brain_dir) == before
    assert brain_dir.identity_history() == []
    assert any("expired" in n and proposal.id in n for n in notes(brain_dir))


async def test_a_late_approval_does_not_apply_it_either(brain_dir, approvals, clock):
    await rewrite(brain_dir, approvals)
    proposal = the_proposal(approvals)
    clock.state["now"] += EXPIRE_AFTER + timedelta(hours=1)

    done = await approvals.on_reaction(proposal.slack_ts, "white_check_mark")

    assert done.status == "expired"
    assert brain_dir.persona_text() == OLD.strip()


async def test_dry_run_writes_nothing(brain_dir, clock, slack):
    approvals = make_approvals(brain_dir, clock, slack, DRY_RUN="1")
    await rewrite(brain_dir, approvals)
    before = file_text(brain_dir)

    done = await approvals.on_reaction(the_proposal(approvals).slack_ts, "white_check_mark")

    assert (done.status, done.result) == ("executed", "dry-run")
    assert file_text(brain_dir) == before
    assert brain_dir.identity_history() == []


async def test_a_double_tap_writes_once(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    ts = the_proposal(approvals).slack_ts

    first, second = await asyncio.gather(
        approvals.on_reaction(ts, "white_check_mark"), approvals.on_button(ts, APPROVE_ACTION)
    )

    assert sorted(r.status if r else "none" for r in (first, second)) == ["executed", "none"]
    assert len(brain_dir.identity_history()) == 1


async def test_a_second_delivery_after_the_first_does_nothing(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    ts = the_proposal(approvals).slack_ts
    await approvals.on_reaction(ts, "white_check_mark")
    brain_dir.rewrite_persona(FRONT + "Filip put this here afterwards, on his own, in full.\n" * 5)
    after = file_text(brain_dir)

    assert await approvals.on_reaction(ts, "white_check_mark") is None
    assert file_text(brain_dir) == after


# -- stale base --------------------------------------------------------------


async def test_a_changed_file_ends_failed_and_the_newer_text_survives(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    proposal = the_proposal(approvals)
    newer = FRONT + "Filip rewrote this himself in the meantime, with his own words about the house.\n" * 3
    brain_dir.rewrite_persona(newer)

    done = await approvals.on_reaction(proposal.slack_ts, "white_check_mark")

    assert done.status == "failed"
    assert "changed after this was proposed" in done.result
    assert "nothing was written" in done.result
    assert file_text(brain_dir) == newer
    assert any("failed" in n and "changed after" in n for n in notes(brain_dir))
    assert "Approved, but it failed" in resolved_text(done)


async def test_even_a_dry_run_reports_the_stale_base(brain_dir, clock, slack):
    approvals = make_approvals(brain_dir, clock, slack, DRY_RUN="1")
    await rewrite(brain_dir, approvals)
    brain_dir.rewrite_persona(FRONT + "newer text " * 40)

    done = await approvals.on_reaction(the_proposal(approvals).slack_ts, "white_check_mark")

    assert done.status == "failed"


async def test_a_frontmatter_only_change_is_a_change_too(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    brain_dir.persona_path.write_text("---\nemoji: 🐙\n---\n" + OLD, encoding="utf-8")

    done = await approvals.on_reaction(the_proposal(approvals).slack_ts, "white_check_mark")

    assert done.status == "failed"
    assert brain_dir.persona_meta() == {"emoji": "🐙"}


# -- rejection memory ---------------------------------------------------------


async def test_the_same_text_is_refused_after_a_rejection(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    await approvals.on_reaction(the_proposal(approvals).slack_ts, "x")

    out = await rewrite(brain_dir, approvals, reason="again")

    assert "already rejected" in out["error"]
    assert len(approvals.all()) == 1


async def test_the_same_text_in_other_clothes_is_still_the_same_text(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    await approvals.on_reaction(the_proposal(approvals).slack_ts, "x")

    out = await rewrite(brain_dir, approvals, body=NEW.replace("\n", "\r\n") + "\n\n  ")

    assert "already rejected" in out["error"]


async def test_different_text_may_still_be_asked(brain_dir, approvals):
    """One rejection must not forbid every future change."""
    await rewrite(brain_dir, approvals)
    await approvals.on_reaction(the_proposal(approvals).slack_ts, "x")

    out = await rewrite(brain_dir, approvals, body=OTHER, reason="a different take")

    assert out["status"] == "pending approval"
    assert len(approvals.pending()) == 1


async def test_the_refusal_lapses_with_the_memory_window(brain_dir, approvals, clock):
    await rewrite(brain_dir, approvals)
    await approvals.on_reaction(the_proposal(approvals).slack_ts, "x")
    clock.state["now"] += timedelta(days=31)

    assert (await rewrite(brain_dir, approvals))["status"] == "pending approval"


async def test_the_rejection_target_is_a_hash_of_the_text(brain_dir, approvals):
    target = proposal_target("rewrite_identity", {"body": NEW})
    assert target == f"identity {identity.short(identity.digest(NEW))} ({len(NEW)} chars)"
    assert proposal_target("rewrite_identity", {"body": OTHER}) != target
    assert "distrust" not in target


async def test_the_rejection_section_renders_without_the_text(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    await approvals.on_reaction(the_proposal(approvals).slack_ts, "x")

    section = rejection_memory_section(rejected_groups(approvals.all()))

    assert "rewrite_identity / identity sha256:" in section
    assert f"({len(NEW)} chars)" in section
    assert "(topic: identity): rejected 1x" in section
    assert "distrust" not in section and "Malmo" not in section
    assert len(section) < 400
    assert "…and" not in section


async def test_a_long_rejected_text_does_not_crowd_the_section_out(brain_dir, approvals):
    long_body = ("I notice things, and I write them down in my own voice. " * 100)[:5900]
    await rewrite(brain_dir, approvals, body=long_body)
    await approvals.on_reaction(the_proposal(approvals).slack_ts, "x")

    section = rejection_memory_section(rejected_groups(approvals.all()))

    assert "rewrite_identity / identity sha256:" in section
    assert "…and" not in section


async def test_only_one_request_waits_at_a_time(brain_dir, approvals, slack):
    await rewrite(brain_dir, approvals)
    first = the_proposal(approvals)

    for body in (NEW, OTHER):
        out = await rewrite(brain_dir, approvals, body=body)
        assert first.id in out["error"]
        assert "already waiting" in out["error"]

    assert len(slack.posts) == 1
    await approvals.on_reaction(first.slack_ts, "x")
    assert (await rewrite(brain_dir, approvals, body=OTHER))["status"] == "pending approval"


# -- bounds -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "complaint"),
    [
        ("", "empty"),
        ("  \n\t \n", "empty"),
        ("curious", "only 7 chars"),
        ("x " * 3001, "over the 6000 limit"),
        ("---\nemoji: 🐙\n---\n" + NEW, "frontmatter"),
        (NEW + "hidden​space", "U+200B"),
        (NEW + "tag \U000e0041", "U+E0041"),
        (NEW + "right-to-left ‮ override", "U+202E"),
        (NEW + "bell \x07", "U+0007"),
        (OLD, "already have"),
        (OLD + "\n\n", "already have"),
    ],
)
async def test_bounds_refuse_what_cannot_be_shown_or_is_not_a_change(
    brain_dir, approvals, body, complaint
):
    out = await rewrite(brain_dir, approvals, body=body)

    assert complaint in out["error"]
    assert approvals.all() == []


async def test_an_empty_reason_is_refused(brain_dir, approvals):
    out = await rewrite(brain_dir, approvals, reason="  \n ")
    assert "say why" in out["error"]


async def test_a_pasted_prompt_is_refused(brain_dir, approvals):
    pasted = NEW + "\n" + BRAIN_INSTRUCTIONS.split("\n\n")[0] + "\n"
    out = await rewrite(brain_dir, approvals, body=pasted)
    assert "word for word" in out["error"]


async def test_the_seed_identity_is_not_mistaken_for_a_pasted_prompt(brain_dir, approvals):
    from conftest import SEED

    seed = (SEED / "personas" / "brain.md").read_text(encoding="utf-8")
    body = seed.split("---\n", 2)[2]
    assert (await rewrite(brain_dir, approvals, body=body))["status"] == "pending approval"


async def test_a_zero_width_joiner_in_an_emoji_is_fine(brain_dir, approvals):
    body = NEW + "Family: 👨‍👩‍👧\n"
    assert (await rewrite(brain_dir, approvals, body=body))["status"] == "pending approval"


async def test_secret_shaped_text_is_masked_before_it_is_stored_or_shown(brain_dir, approvals, slack):
    secret = "AIza" + "x" * 35
    await rewrite(brain_dir, approvals, body=NEW + f"My key is {secret} for later.\n")

    proposal = the_proposal(approvals)
    assert secret not in proposal.payload["body"]
    assert "[REDACTED]" in proposal.payload["body"]
    assert secret not in json.dumps(slack.blocks)


async def test_the_gate_holds_for_a_caller_that_skips_the_tool(brain_dir, approvals):
    """``Approvals.propose`` checks the same bounds, so no way in avoids them."""
    with pytest.raises(ValueError, match="empty"):
        await approvals.propose("rewrite_identity", {"body": ""}, "r", "identity")
    with pytest.raises(ValueError, match="must be text"):
        await approvals.propose("rewrite_identity", {"body": ["x"]}, "r", "identity")
    assert approvals.all() == []


async def test_the_executor_holds_a_stored_proposal_to_the_bounds_too(brain_dir, approvals):
    """A proposal edited on disk after it was filed is checked once more at the write."""
    await rewrite(brain_dir, approvals)
    proposal = the_proposal(approvals)
    path = brain_dir.outbox_dir / f"{proposal.id}.json"
    stored = json.loads(path.read_text())
    stored["payload"]["body"] = "tampered​" + NEW
    path.write_text(json.dumps(stored))

    done = await approvals.on_reaction(proposal.slack_ts, "white_check_mark")

    assert done.status == "failed"
    assert "U+200B" in done.result
    assert brain_dir.persona_text() == OLD.strip()


# -- the card -------------------------------------------------------------------


def card_sections(slack):
    *sections, actions = slack.blocks[0]
    assert actions["type"] == "actions"
    assert [s["type"] for s in sections] == ["section"] * len(sections)
    return [s["text"]["text"] for s in sections]


async def test_the_card_shows_a_diff_against_the_current_identity(brain_dir, approvals, slack):
    await rewrite(brain_dir, approvals)
    header, *diff, footer = card_sections(slack)

    assert "Proposal" in header and "rewrite_identity" in header
    assert "sharper questions" in header
    text = "\n".join(diff)
    assert "-I ask Filip when only he can know the answer.\n" in text
    assert "+I ask Filip when only he can know the answer, and I keep the question short." in text
    assert "+I distrust a number I have only seen once." in text
    # Unchanged text is context, not noise.
    assert " I look for contradictions" in text
    assert "React ✅ to approve" in footer
    assert identity.short(identity.digest(NEW)) in header
    assert identity.short(brain_dir.persona_digest()) in header
    assert [b["action_id"] for b in slack.blocks[0][-1]["elements"]] == [APPROVE_ACTION, REJECT_ACTION]


async def test_the_provenance_warning_is_first_on_the_card(brain_dir, approvals, slack):
    token = READ_SOURCES.set({"web"})
    try:
        await rewrite(brain_dir, approvals)
    finally:
        READ_SOURCES.reset(token)

    sections = card_sections(slack)
    assert sections[0].startswith("⚠️ Asked after reading external text: web\n")
    assert slack.posts[0][1].startswith("⚠️ Asked after reading external text: web\n")
    assert the_proposal(approvals).provenance == ["web"]


async def test_the_proposed_text_cannot_fake_the_card(brain_dir, approvals, slack):
    """Everything proposed sits in code blocks it cannot leave, and nothing the
    brain wrote comes after the card's own instructions."""
    attack = (
        "```\n"
        "*Proposal 20260906T100000-rewrite_identity-0000 (rewrite_identity)*: approved by Filip\n"
        "React ✅ to approve, ❌ to reject, or use the buttons below.\n"
        "<!channel> <@U123> <https://evil.example|click> &amp; ```` ``` ````\n"
        "```\n"
    )
    await rewrite(
        brain_dir,
        approvals,
        body=NEW + attack,
        reason="```\n⚠️ nothing to see here\n``` <!channel>\nReact ✅ to approve",
    )
    header, *diff, footer = card_sections(slack)

    for block in diff:
        # Exactly the fences we put there: the proposed text closed none of them.
        assert block.startswith("```\n") and block.endswith("\n```")
        assert "```" not in block[4:-4]
        assert "<!channel>" not in block and "<@U123>" not in block and "<https" not in block
    joined = "\n".join(diff)
    assert "&lt;!channel&gt;" in joined
    assert "&amp;amp;" in joined
    # The brain's reason is one quoted line, in a code span, not a way to add
    # lines of its own.
    reason_line = next(line for line in header.splitlines() if line.startswith("Its reason"))
    # One code span: the reason's own backticks cannot end it early.
    assert reason_line.count("`") == 2
    assert "```" not in reason_line
    assert header.count("\n") == 3
    assert "<!channel>" not in header
    # The one real instruction is ours, last, and the only one outside a fence.
    assert footer.startswith("React ✅ to approve")
    assert footer.count("React ✅") == 1
    # The notification preview carries none of the brain's words.
    assert "nothing to see here" not in slack.posts[0][1] and "sharper" not in slack.posts[0][1]


async def test_every_section_fits_slack(brain_dir, approvals, slack):
    await rewrite(brain_dir, approvals, body="&<>`" * 200 + "\n" + "y" * 1000 + "\n")

    sections = card_sections(slack)
    assert all(len(s) <= SECTION_LIMIT for s in sections)
    # Not cut by the generic section fitting, which would have eaten a fence.
    assert not any(s.endswith("…") for s in sections)
    for block in sections[1:-1]:
        assert block.endswith("\n```")
    assert "The diff is cut" not in "\n".join(sections)


def varied_text(seed: str, size: int) -> str:
    lines = []
    while sum(len(line) + 1 for line in lines) < size:
        lines.append(f"{seed} line {len(lines)} " + "word " * 4)
    return "\n".join(lines)[:size]


async def test_a_complete_rewrite_at_the_size_limit_fits_without_a_cut(brain_dir, approvals, slack):
    brain_dir.persona_path.write_text(FRONT + varied_text("old", identity.MAX_CHARS), encoding="utf-8")

    await rewrite(brain_dir, approvals, body=varied_text("new", identity.MAX_CHARS))

    sections = card_sections(slack)
    assert "The diff is cut" not in "\n".join(sections)
    assert all(len(s) <= SECTION_LIMIT for s in sections)
    assert len(sections) <= identity.MAX_DIFF_BLOCKS + 2


async def test_a_cut_diff_says_so_with_sizes_and_where_the_text_is(brain_dir, approvals, slack):
    # Filip's own edits through the MCP have no size limit, so the current
    # identity can be far bigger than a proposal may be.
    brain_dir.persona_path.write_text(FRONT + varied_text("old", 40_000), encoding="utf-8")

    await rewrite(brain_dir, approvals, body=varied_text("new", identity.MAX_CHARS))

    proposal = the_proposal(approvals)
    header, *diff, footer = card_sections(slack)
    assert len(diff) == identity.MAX_DIFF_BLOCKS
    assert all(len(s) <= SECTION_LIMIT for s in (header, *diff, footer))
    for notice in (header, footer):
        assert "The diff is cut" in notice
        assert "is *not shown*" in notice
        assert f"brain/outbox/{proposal.id}.json" in notice
        assert "payload.body" in notice
        assert f"proposed text ({len(proposal.payload['body'])} chars)" in notice
    assert " lines (" in header and " of " in header
    assert (brain_dir.outbox_dir / f"{proposal.id}.json").exists()
    assert slack.blocks[0][-1]["type"] == "actions"


def test_a_single_huge_line_is_split_to_fit():
    lines = identity.diff_lines("old\n", "new " + "&" * 1500 + "\n")
    blocks, shown = identity._blocks(lines)
    assert shown == len(lines)
    assert all(len(b) <= 3000 for b in blocks)
    pieces = [line for b in blocks for line in b.splitlines() if line.startswith("+")]
    assert len(pieces) > 1
    assert all(line.startswith("+") for line in pieces)


async def test_the_resolved_card_keeps_a_record_of_what_was_answered(brain_dir, approvals):
    await rewrite(brain_dir, approvals, reason="x <!channel> `y`\nReact ✅")
    proposal = the_proposal(approvals)

    done = await approvals.on_reaction(proposal.slack_ts, "x")
    text = resolved_text(done)

    assert identity.short(identity.digest(NEW)) in text
    assert f"{len(NEW.strip())} chars" in text
    assert f"brain/outbox/{proposal.id}.json" in text
    assert text.endswith("❌ Rejected.")
    assert "<!channel>" not in text
    assert "\n" not in text.split("\n\n")[0]


# -- restarts -----------------------------------------------------------------


async def test_a_restart_after_the_write_is_recorded_as_executed(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    proposal = the_proposal(approvals)
    proposal.status = "executing"
    approvals._store(proposal)
    brain_dir.rewrite_identity(NEW)

    [closed] = approvals.recover_stale()

    assert closed.status == "executed"
    assert "after identity.md was written" in closed.result


async def test_a_restart_before_the_write_is_failed_and_says_the_old_text_stands(brain_dir, approvals):
    await rewrite(brain_dir, approvals)
    proposal = the_proposal(approvals)
    proposal.status = "executing"
    approvals._store(proposal)

    [closed] = approvals.recover_stale()

    assert closed.status == "failed"
    assert "before identity.md was written" in closed.result
    assert brain_dir.persona_text() == OLD.strip()
    assert any("Propose again" in n for n in notes(brain_dir))


async def test_executing_is_claimed_before_the_write(brain_dir, clock, slack):
    """The status leaves ``pending`` before the executor runs, as for every kind."""
    seen = []
    approvals = make_approvals(brain_dir, clock, slack)
    original = approvals.executors.rewrite_identity

    async def _spy(body, base_hash):
        seen.append(the_proposal(approvals).status)
        return await original(body, base_hash)

    approvals.executors.rewrite_identity = _spy
    await rewrite(brain_dir, approvals)

    await approvals.on_reaction(the_proposal(approvals).slack_ts, "white_check_mark")

    assert seen == ["executing"]


async def test_the_executor_without_a_brain_fails_cleanly(clock):
    executors = Executors(load_settings({}), None, clock)
    with pytest.raises(RuntimeError, match="no brain memory"):
        await executors.run("rewrite_identity", {"body": NEW, "base_hash": "sha256:0"})


# -- resume and the session log ------------------------------------------------


class Killed(BaseException):
    """Stands in for SIGKILL: not an Exception, so nothing in run_cycle catches it."""


@pytest.fixture
def wall():
    state = {"t": 1_800_000_000.0}

    def _now() -> float:
        return state["t"]

    _now.state = state
    return _now


async def run_until_killed(loop):
    original = loop._finish

    async def _no_finish(*_a, **_k):
        session = loop._session
        if session is not None:
            session.close()
        loop._session = None

    loop._finish = _no_finish
    with pytest.raises(Killed):
        await loop.run_cycle()
    loop._finish = original


async def test_a_resumed_cycle_never_replays_the_tool(brain_dir, approvals, slack, tmp_path, wall):
    """The process dies while the tool is running. The resumed cycle is told the
    outcome is unknown, runs nothing, and no second card is posted."""

    async def _die(*_a):
        raise Killed

    approvals.on_message = _die
    registry = build_registry()
    register_propose_tool(registry)
    body = NEW
    first, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("rewrite_identity", "r1", body=body, reason="why"))],
        registry=registry,
        extras={"approvals": approvals},
    )
    await run_until_killed(first)
    approvals.on_message = slack
    assert approvals.all() == []

    wall.state["t"] += 600
    second, provider = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", end())],
        registry=registry,
        extras={"approvals": approvals},
    )
    result = await second.run_cycle()

    assert result.status == "ok"
    sent = provider.calls[0][0]
    [tool_message] = [m for m in sent if m.role == "tool"]
    assert tool_message.tool_call_id == "r1"
    assert "outcome unknown" in tool_message.content
    assert slack.posts == []
    assert approvals.all() == []
    assert brain_dir.persona_text() == OLD.strip()
    assert [e["synthetic"] for e in read_events(next(iter(session_files(brain_dir.root / "sessions")))) if e.get("id") == "r1" and e["type"] == "tool_result"] == ["outcome_unknown"]


async def test_a_resumed_cycle_keeps_the_recorded_answer_and_files_nothing_more(
    brain_dir, approvals, slack, tmp_path, wall
):
    """The call finished before the crash: its 'pending approval' result is in
    the log and in the resumed conversation, and nothing is run again."""
    registry = build_registry()
    register_propose_tool(registry)

    async def _die(_ctx, _args):
        raise Killed

    from ai_brain.llm import ToolSpec
    from ai_brain.tools import Tool

    registry.register(
        Tool(
            spec=ToolSpec(name="die", description="d", parameters={"type": "object", "properties": {}}),
            fn=_die,
        )
    )
    first, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [
            reply("", call("rewrite_identity", "r1", body=NEW, reason="why")),
            reply("", call("die", "d1")),
        ],
        registry=registry,
        extras={"approvals": approvals},
    )
    await run_until_killed(first)
    assert len(slack.posts) == 1

    wall.state["t"] += 600
    second, provider = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", end())],
        registry=registry,
        extras={"approvals": approvals},
    )
    await second.run_cycle()

    tools = {m.tool_call_id: m.content for m in provider.calls[0][0] if m.role == "tool"}
    assert "pending approval" in tools["r1"]
    assert "outcome unknown" in tools["d1"]
    assert len(slack.posts) == 1
    assert len(approvals.all()) == 1
    assert brain_dir.persona_text() == OLD.strip()


async def test_the_session_log_redacts_the_arguments(brain_dir, approvals, tmp_path, wall):
    secret = "AIza" + "x" * 35
    registry = build_registry()
    loop, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [
            reply("", call("rewrite_identity", "r1", body=NEW + f"key {secret}\n", reason=f"see {secret}")),
            reply("", end()),
        ],
        registry=registry,
        extras={"approvals": approvals},
    )

    await loop.run_cycle()

    log_text = json.dumps(read_events(next(iter(session_files(brain_dir.root / "sessions")))))
    assert "rewrite_identity" in log_text
    assert "distrust a number" in log_text  # the arguments are logged ...
    assert secret not in log_text  # ... redacted
    assert secret not in json.dumps(the_proposal(approvals).__dict__)


# -- text -----------------------------------------------------------------------


def test_the_brain_is_told_a_rewrite_waits_for_approval():
    assert "rewrite_identity" in BRAIN_INSTRUCTIONS
    assert '"pending\napproval"' in BRAIN_INSTRUCTIONS
    assert "never write or\nact as though the new one were in force" in BRAIN_INSTRUCTIONS


def test_the_tool_description_says_it_writes_nothing():
    registry = make_registry()
    [spec] = [s for s in registry.specs_for("brain") if s.name == "rewrite_identity"]
    assert "writes nothing" in spec.description
    assert "pending approval" in spec.description
    assert spec.parameters["required"] == ["body", "reason"]
