import json

import pytest

from ai_brain.config import load_settings
from ai_brain.ledger import Ledger, Limits
from ai_brain.llm import ToolCall
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.tools.usage import register_usage_tools

ENV = {
    "MEMORY_ROOT": "/tmp/ai-brain-usage-test",
    "VM_URL": "http://vm.test",
    "INFLUX_TOKEN": "tok",
}


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_usage_tools(reg)
    return reg


def make_ctx(brain_dir, ledger=None) -> ToolContext:
    return ToolContext(
        loop="brain",
        memory=brain_dir,
        memories={"brain": brain_dir},
        settings=load_settings(ENV),
        wake=lambda _loop: None,
        extras={"ledger": ledger} if ledger is not None else {},
    )


async def call(registry, ctx, tool, **args):
    return json.loads(await registry.dispatch(ctx, ToolCall(id="1", name=tool, args=args)))


async def test_usage_status_reports_ledger_snapshot(registry, brain_dir):
    limits = {"gemini:a": Limits(rpm=2, tpm=1000, rpd=10)}
    ledger = Ledger(limits, brain_dir.root / "_ledger.json")
    ledger.record("gemini:a", 100, 50)

    result = await call(registry, make_ctx(brain_dir, ledger), "usage_status")

    assert result["ok"] is True
    assert result["keys"] == ledger.usage()["keys"]
    assert result["day"] == ledger.usage()["day"]


async def test_usage_status_without_a_ledger_is_an_error(registry, brain_dir):
    result = await call(registry, make_ctx(brain_dir), "usage_status")

    assert "error" in result
