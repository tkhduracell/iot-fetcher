"""What the harness knows about one model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    key: str
    # The prompt window this harness asks for, in tokens. None for a provider
    # whose window is the API's own, far above what a cycle's budget allows.
    num_ctx: int | None = None
