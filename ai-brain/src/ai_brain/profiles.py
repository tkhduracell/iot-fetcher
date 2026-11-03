"""Everything the harness knows about one model, in one lookup.

Per-model settings used to be asked for separately wherever each was needed:
the chain read NUM_CTX_BY_MODEL and THINK_BY_MODEL when it was built, the loop
walked CYCLE_MAX_ROUNDS_BY_MODEL and CYCLE_MAX_PROMPT_TOKENS_BY_MODEL each round,
and the ledger's limits had their own table. ``ModelProfiles.for_key`` is now the
one place that says what a key gets. Every environment variable still works and
means what it did; only how the answer is looked up has moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ai_brain.config import CYCLE_MAX_ROUNDS_HARD_CEILING, match_by_model

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ai_brain.config import Settings

# The native /api/chat endpoint honours options.num_ctx per request; the
# OpenAI-compatible /v1/chat/completions endpoint does not, which is why
# llm/ollama.py talks to /api/chat rather than that one. Ollama's own server
# default is 4096 unless overridden (here, or via OLLAMA_CONTEXT_LENGTH on the
# server), which is far smaller than a cycle's persona+memory+tool-result
# prompt -- the server silently truncates from the *front* of the prompt to
# fit, dropping exactly the persona/system context a truncated prompt most
# needs. Observed prompts from a real cycle run ~9.7k tokens, so 16384 is the
# default rather than a smaller number: it clears that with headroom on a
# Raspberry Pi 5 (8GB), at the cost of more RAM per loaded model. See
# ai-brain/README.md's Configuration section for the tradeoff.
DEFAULT_NUM_CTX = 16384

# The lan: host is a desktop machine discovered on the network, not the rpi5
# running the rest of this stack, so it gets a larger default than the
# constrained in-compose ollama service.
LAN_DEFAULT_NUM_CTX = 32768

# Which providers ask their server for a window at all; the rest (gemini:,
# anthropic:) have the API's own, far above what a cycle's budget allows.
_NUM_CTX_KIND_DEFAULTS = {"lan": LAN_DEFAULT_NUM_CTX, "ollama": DEFAULT_NUM_CTX}

# gemini-3.8-flash's free-tier RPD is nowhere near the RPM/TPM/RPD every other
# metered key gets from settings -- Google's own docs no longer publish the
# number, so this is measured, not guessed: on 2026-09-22 it answered 12
# calls, then every call for the rest of the day was a 429. One below that
# observed ceiling, so the ledger's own rpd check blocks the key before a
# cycle ever has to spend a real request discovering it is gone (see
# EXHAUSTED_AFTER_429 in ledger.py -- without this, three separate loops each
# burn one real 429 finding this out the hard way, every single day). A key
# not listed here keeps the env-configured tier, unchanged.
KEY_RPD_OVERRIDES: dict[str, int] = {
    "gemini:gemini-3.8-flash": 11,
}

_DEFAULT_MAX_ROUNDS = 16
_DEFAULT_MAX_PROMPT_TOKENS = 400_000


@dataclass(frozen=True)
class ModelProfile:
    key: str
    # The prompt window this harness asks for, in tokens. None for a provider
    # whose window is the API's own.
    num_ctx: int | None = None
    # A THINK_BY_MODEL level, as written. None means the model's own default.
    think: str | None = None
    # Never above CYCLE_MAX_ROUNDS_HARD_CEILING.
    max_rounds: int = _DEFAULT_MAX_ROUNDS
    # 0 means no budget for this model.
    max_prompt_tokens: int = _DEFAULT_MAX_PROMPT_TOKENS
    # A measured requests-per-day ceiling, where one exists. None means the
    # env-configured tier applies.
    rpd: int | None = None


class ModelProfiles:
    """The per-model tables, answering for one key at a time.

    First pattern to match wins, as it does for every ``*_BY_MODEL`` variable.
    A key that matches nothing gets the flat setting, never the hard ceiling:
    the ceiling only clamps a match that is itself too generous.
    """

    def __init__(
        self,
        *,
        num_ctx_by_model: list[tuple[str, int]] | None = None,
        think_by_model: list[tuple[str, str]] | None = None,
        max_rounds: int = _DEFAULT_MAX_ROUNDS,
        max_rounds_by_model: list[tuple[str, int]] | None = None,
        max_prompt_tokens: int = _DEFAULT_MAX_PROMPT_TOKENS,
        max_prompt_tokens_by_model: list[tuple[str, int]] | None = None,
    ) -> None:
        self.num_ctx_by_model = list(num_ctx_by_model or [])
        self.think_by_model = list(think_by_model or [])
        self.max_rounds = max_rounds
        self.max_rounds_by_model = list(max_rounds_by_model or [])
        self.max_prompt_tokens = max_prompt_tokens
        self.max_prompt_tokens_by_model = list(max_prompt_tokens_by_model or [])

    @classmethod
    def from_settings(cls, settings: Settings) -> ModelProfiles:
        return cls(
            num_ctx_by_model=settings.num_ctx_by_model,
            think_by_model=settings.think_by_model,
            max_rounds=settings.max_rounds,
            max_rounds_by_model=settings.max_rounds_by_model,
            max_prompt_tokens=settings.max_prompt_tokens,
            max_prompt_tokens_by_model=settings.max_prompt_tokens_by_model,
        )

    def for_key(self, key: str) -> ModelProfile:
        kind_default = _NUM_CTX_KIND_DEFAULTS.get(key.partition(":")[0])
        return ModelProfile(
            key=key,
            num_ctx=(
                match_by_model(self.num_ctx_by_model, key, kind_default)
                if kind_default is not None
                else None
            ),
            think=match_by_model(self.think_by_model, key, None),
            max_rounds=min(
                match_by_model(self.max_rounds_by_model, key, self.max_rounds),
                CYCLE_MAX_ROUNDS_HARD_CEILING,
            ),
            max_prompt_tokens=match_by_model(
                self.max_prompt_tokens_by_model, key, self.max_prompt_tokens
            ),
            rpd=KEY_RPD_OVERRIDES.get(key),
        )
