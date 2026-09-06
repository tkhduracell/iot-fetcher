from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_brain.memory import MemoryDir


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
