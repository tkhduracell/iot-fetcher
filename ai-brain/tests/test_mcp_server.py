"""The memory MCP: tools over the same MemoryDir the loops use."""

import httpx
import pytest
from conftest import env, fake_chain

from ai_brain.config import load_settings
from mcp.server.mcpserver.exceptions import ToolError

from ai_brain.mcp_server import bearer_guard, build_mcp
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


async def test_rename_fact_moves_body_and_title(system):
    mcp = build_mcp(system, "t")
    await call(mcp, "write_fact", agent="brain", name="old", title="T", body="b")
    await call(mcp, "rename_fact", agent="brain", name="old", new_name="new")
    mem = system.memories["brain"]
    assert mem.read_fact("old") is None
    fact = mem.read_fact("new")
    assert (fact.title, fact.body) == ("T", "b")


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
