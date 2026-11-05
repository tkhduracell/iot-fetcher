"""The memory MCP: tools over the same MemoryDir the loops use."""

import httpx
import pytest
from conftest import env, fake_chain

from ai_brain.config import load_settings
from mcp.server.mcpserver.exceptions import ToolError

from ai_brain.mcp_server import bearer_guard, build_mcp
from ai_brain.memory import MEMORY_SINK
from ai_brain.supervisor import build

NOW = 1_000_000.0


@pytest.fixture
def system(tmp_path):
    settings = load_settings(env(tmp_path, EXPERTS="energy"))
    return build(settings, chain_factory=fake_chain, clock=lambda: NOW)


async def call(mcp, tool, **args):
    result = await mcp.call_tool(tool, args)
    structured = result.structured_content
    if structured is not None:
        return structured.get("result", structured)
    return result.content[0].text


async def tool_names(mcp) -> set[str]:
    return {t.name for t in await mcp.list_tools()}


async def test_without_a_token_only_read_tools_exist(system):
    names = await tool_names(build_mcp(system, ""))
    assert {"list_agents", "read_fact", "read_constitution", "history"} <= names
    assert not names & {"write_fact", "delete_fact", "write_persona", "write_constitution"}


async def test_with_a_token_write_tools_exist(system):
    names = await tool_names(build_mcp(system, "t"))
    assert {"write_fact", "delete_fact", "rename_fact", "write_persona", "send_note"} <= names


async def test_fact_write_delete_keeps_revisions(system):
    mcp = build_mcp(system, "t")
    await call(mcp, "write_fact", agent="energy", name="pool", title="Pool", body="v1")
    await call(mcp, "write_fact", agent="energy", name="pool", title="Pool", body="v2")
    assert (await call(mcp, "read_fact", agent="energy", name="pool"))["body"] == "v2"

    await call(mcp, "delete_fact", agent="energy", name="pool")
    assert system.memories["energy"].read_fact("pool") is None
    bodies = [r.body for r in system.memories["energy"].fact_history("pool")]
    assert bodies[:2] == ["v2", "v1"]


async def test_restore_is_a_write_tool(system):
    assert "restore_fact" not in await tool_names(build_mcp(system, ""))
    assert "restore_fact" in await tool_names(build_mcp(system, "t"))


async def test_history_shows_an_agents_own_edit_and_restore_undoes_it(system):
    """The whole path: a loop's overwrite, seen and undone from outside."""
    mem = system.memories["energy"]
    mem.write_fact("pool", "Pool", "the good version")
    token = MEMORY_SINK.set(lambda *_: None)
    try:
        mem.write_fact("pool", "Pool", "what the agent replaced it with")
    finally:
        MEMORY_SINK.reset(token)

    mcp = build_mcp(system, "t")
    revisions = await call(mcp, "history", agent="energy", kind="fact", name="pool")
    assert revisions[0]["body"] == "the good version"
    assert revisions[0]["by"] == "agent"

    await call(mcp, "restore_fact", agent="energy", name="pool", revision=revisions[0]["revision"])

    assert mem.read_fact("pool").body == "the good version"
    assert mem.fact_history("pool")[0].by == "outside"


async def test_history_names_deleted_facts_and_restore_brings_one_back(system):
    mem = system.memories["energy"]
    mem.write_fact("pool", "Pool heat", "the only version")
    mem.delete_fact("pool")

    mcp = build_mcp(system, "t")
    deleted = await call(mcp, "history", agent="energy", kind="deleted")
    assert [(d["name"], d["title"]) for d in deleted] == [("pool", "Pool heat")]
    assert "body" not in deleted[0]

    await call(mcp, "restore_fact", agent="energy", name="pool")

    assert mem.read_fact("pool").title == "Pool heat"
    assert await call(mcp, "history", agent="energy", kind="deleted") == []


async def test_restore_without_a_revision_is_an_error(system):
    with pytest.raises(ToolError, match="no such revision"):
        await build_mcp(system, "t").call_tool(
            "restore_fact", {"agent": "energy", "name": "never-existed"}
        )


async def test_rename_fact_moves_body_and_title(system):
    mcp = build_mcp(system, "t")
    await call(mcp, "write_fact", agent="brain", name="old", title="T", body="b")
    await call(mcp, "rename_fact", agent="brain", name="old", new_name="new")
    mem = system.memories["brain"]
    assert mem.read_fact("old") is None
    fact = mem.read_fact("new")
    assert (fact.title, fact.body) == ("T", "b")


async def test_rename_fact_keeps_the_tag(system):
    mem = system.memories["brain"]
    mem.write_fact("old", "T", "b", carried=("web",))
    mcp = build_mcp(system, "t")
    await call(mcp, "rename_fact", agent="brain", name="old", new_name="new")
    assert mem.read_fact("new").provenance == ("web",)


async def test_read_facts_shows_the_tag(system):
    mem = system.memories["brain"]
    mem.write_fact("tagged", "T", "b", carried=("web",))
    facts = await call(build_mcp(system, "t"), "read_facts", agent="brain")
    tagged = next(f for f in facts if f["name"] == "tagged")
    assert tagged["written_after_reading"] == ["web"]


async def test_unknown_agent_is_an_error(system):
    with pytest.raises(ToolError, match="unknown agent"):
        await build_mcp(system, "").call_tool("read_journal", {"agent": "nobody"})


async def test_write_persona_works_for_experts_and_keeps_history(system):
    mcp = build_mcp(system, "t")
    before = await call(mcp, "read_persona", agent="energy")
    assert before.startswith("---\nemoji:")  # the whole file, frontmatter included
    await call(mcp, "write_persona", agent="energy", body="---\nemoji: x\n---\nnew soul")
    assert system.memories["energy"].persona_text() == "new soul"
    assert system.memories["energy"].identity_history()[0].body == before


async def test_write_persona_on_the_brain_stays_direct_and_outranks_a_pending_proposal(system):
    """Filip's own edit needs no approval, and a proposal filed before it is not
    applied over it afterwards."""
    brain = system.memories["brain"]
    approvals = system.approvals

    async def _post(topic, text, blocks=None):
        return "ts-1"

    approvals.on_message = _post
    proposed = "I am the brain, and I would like to ask fewer questions of everyone. " * 4
    await approvals.propose("rewrite_identity", {"body": proposed}, "r", "identity")
    mcp = build_mcp(system, "t")

    await call(mcp, "write_persona", agent="brain", body="---\nemoji: 🧠\n---\nFilip's own words")
    assert brain.persona_text() == "Filip's own words"

    done = await approvals.on_reaction("ts-1", "white_check_mark")
    assert done.status == "failed"
    assert brain.persona_text() == "Filip's own words"


async def test_write_constitution_reaches_running_loops(system):
    mcp = build_mcp(system, "t")
    await call(mcp, "write_constitution", body="be kind")
    assert (system.settings.memory_root / "constitution.md").read_text() == "be kind"
    assert all(loop.constitution == "be kind" for loop in system.loops.values())
    assert await call(mcp, "read_constitution") == "be kind"
    assert (await call(mcp, "history", agent="brain", kind="constitution"))[0]["body"]


async def test_bearer_guard_rejects_missing_or_wrong_token():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    transport = httpx.ASGITransport(app=bearer_guard(app, "secret"))
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
        assert (await client.post("/mcp")).status_code == 401
        bad = await client.post("/mcp", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401
        good = await client.post("/mcp", headers={"Authorization": "Bearer secret"})
        assert good.status_code == 200
