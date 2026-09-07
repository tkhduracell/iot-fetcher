from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_brain.llm import ProviderChain
from ai_brain.memory import MemoryDir

SEED = Path(__file__).resolve().parents[1] / "seed"


def env(tmp_path: Path, **extra) -> dict[str, str]:
    """A minimal environment that builds a real System without a network."""
    base = {
        "MEMORY_ROOT": str(tmp_path / "memory"),
        "SEED_ROOT": str(SEED),
        "LLM_CHAIN": "fake:a,fake:b",
        "VM_URL": "http://vm.test",
        "INFLUX_TOKEN": "tok",
    }
    base.update(extra)
    return base


def fake_chain(settings, ledger) -> ProviderChain:
    return ProviderChain([], ledger)


@pytest.fixture
def clock():
    state = {"now": datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)}

    def _now():
        return state["now"]

    _now.state = state
    return _now


@pytest.fixture
def brain_dir(tmp_path: Path, clock) -> MemoryDir:
    m = MemoryDir(tmp_path / "brain", "brain", is_brain=True, clock=clock)
    m.ensure()
    return m


@pytest.fixture
def expert_dir(tmp_path: Path, clock) -> MemoryDir:
    m = MemoryDir(tmp_path / "experts" / "energy", "energy", is_brain=False, clock=clock)
    m.ensure()
    return m
