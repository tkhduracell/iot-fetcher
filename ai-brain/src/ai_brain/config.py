"""Environment-backed settings for ai-brain.

``load_settings`` takes an explicit mapping so tests never touch ``os.environ``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SEED_ROOT = Path(__file__).resolve().parents[2] / "seed"

# The chain the component is documented and tested against. It lives here rather
# than only in .env.example so an unset or blank LLM_CHAIN produces a working
# brain instead of a chain with zero providers -- which builds fine, raises
# ChainExhausted on every cycle, and emits no ledger series at all, so the
# misconfiguration is invisible in both the logs and Grafana.
# Best model first, and local hardware is what the free tier falls back to
# rather than the other way round: the flash models answer until the ledger
# says their quota is gone, and only then does the chain reach for a machine in
# the house -- ``lan:`` on whatever LAN box is awake and has it pulled (see
# ai_brain.discovery), then the small model on the rpi5 itself.
#
# The lan: model is a tool-calling one on purpose. A cycle is nothing but tool
# calls -- every round ends in end_cycle -- so a reasoning model that answers
# in prose burns its rounds and writes nothing, however well it reasons.
DEFAULT_LLM_CHAIN = (
    "gemini:gemini-3.8-flash,"
    "gemini:gemini-3.5-flash-lite,"
    "lan:qwen3-coder:30b,"
    "ollama:llama3.2:3b"
)

# Every expert the image ships a persona for. Brain-only was the rollout
# default while the loops were unproven; with them proven the useful default is
# the whole set, for a blank value as much as an absent one -- a deployment
# that copied .env.example during the rollout has a literal ``EXPERTS=`` line,
# and that line silently pinning it to brain-only is a default nobody chose.
DEFAULT_EXPERTS = "energy,health,house-ops,researcher,infra"

# The explicit opt-out, since blank no longer is one. Case-insensitive, and
# ``brain`` is accepted too: the brain is not an expert, so naming it is the
# same statement as naming none.
NO_EXPERTS = frozenset({"none", "brain"})

# The explicit opt-out for CYCLE_MAX_ROUNDS_BY_MODEL, distinct from "unset"
# (which falls back to DEFAULT_CYCLE_MAX_ROUNDS_BY_MODEL below). Same spelling
# as NO_EXPERTS's opt-out, minus "brain" -- there is no per-loop reading of
# this one to make "brain" a meaningful synonym for "none" here.
NO_MAX_ROUNDS_BY_MODEL = frozenset({"none", "off"})

# A cycle answered by a stronger model gets more rounds before the loop stops
# waiting for end_cycle. Matched fnmatch-style against the model that answered
# the most recent round -- ``gemini:gemini-3.8-flash`` or
# ``lan:qwen3-coder:30b @ http://host:11434`` -- so a pattern like
# ``gemini:*3.8*`` or ``lan:qwen3.8*`` matches the chain key regardless of the
# exact model string a provider hands back. First match wins; no match falls
# back to CYCLE_MAX_ROUNDS. See loop.py's _rounds_cap.
DEFAULT_CYCLE_MAX_ROUNDS_BY_MODEL = "gemini:*3.8*=32,lan:qwen3.8*=32"

# No cycle may run longer than this regardless of CYCLE_MAX_ROUNDS_BY_MODEL --
# a typo'd env value is a startup error (see _parse_max_rounds_by_model), but
# this is the belt-and-braces ceiling even a validated, generous entry cannot
# cross.
CYCLE_MAX_ROUNDS_HARD_CEILING = 64


def _parse_max_rounds_by_model(raw: str) -> list[tuple[str, int]]:
    """``pattern=N`` entries, in order, as ``(fnmatch pattern, rounds)``.

    Validated here rather than left to blow up mid-cycle: a startup that
    accepts a malformed entry only fails once some cycle happens to be
    answered by a model matching it, hours or days later. ``N`` must be
    between 1 and ``CYCLE_MAX_ROUNDS_HARD_CEILING`` -- a cap above the hard
    ceiling can never bind (the ceiling always wins), so it is rejected as
    the config mistake it is rather than silently clamped.
    """
    entries: list[tuple[str, int]] = []
    for part in _csv(raw):
        pattern, sep, value = part.partition("=")
        pattern = pattern.strip()
        value = value.strip()
        if not sep or not pattern or not value:
            raise ValueError(
                f"CYCLE_MAX_ROUNDS_BY_MODEL entry {part!r} is not pattern=N"
            )
        try:
            rounds = int(value)
        except ValueError as exc:
            raise ValueError(
                f"CYCLE_MAX_ROUNDS_BY_MODEL entry {part!r} has a non-integer N"
            ) from exc
        if not (1 <= rounds <= CYCLE_MAX_ROUNDS_HARD_CEILING):
            raise ValueError(
                f"CYCLE_MAX_ROUNDS_BY_MODEL entry {part!r} must have N between "
                f"1 and {CYCLE_MAX_ROUNDS_HARD_CEILING}"
            )
        entries.append((pattern, rounds))
    return entries


@dataclass(frozen=True)
class Settings:
    memory_root: Path
    seed_root: Path
    llm_chain: list[str]
    gemini_api_key: str
    ollama_url: str
    ollama_num_ctx: int
    lan_ollama_num_ctx: int
    experts: list[str]
    brain_heartbeat_s: int
    expert_heartbeat_s: int
    vm_url: str
    influx_token: str
    ha_url: str
    ha_token: str
    ha_todo_list: str
    docker_proxy_url: str
    gdrive_rag_url: str
    sonos_url: str
    sonos_room: str
    brave_api_key: str
    slack_bot_token: str
    slack_app_token: str
    slack_user_id: str
    dry_run: bool
    lan_subnets: list[str]
    lan_scan_s: int
    max_rounds: int
    max_rounds_by_model: list[tuple[str, int]]
    max_tokens: int
    max_prompt_tokens: int
    thinking_budget: int
    rpm: int
    tpm: int
    rpd: int
    call_timeout_s: int
    http_port: int
    repo_slug: str
    repo_ref: str
    repo_refresh_h: int
    rejection_memory_days: int


def _csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _dedupe(entries: list[str]) -> list[str]:
    """Drop repeats, keep order.

    A chain is walked in order and every entry shares one ledger key, so a
    duplicate is not a second budget -- it is the same exhausted key tried
    twice, which costs the fallback nothing but latency. Deduping here means
    ``limits_from_settings`` and the chain agree on how many providers exist.
    """
    seen: set[str] = set()
    out = []
    for entry in entries:
        if entry not in seen:
            seen.add(entry)
            out.append(entry)
    return out


def _experts(raw: str) -> list[str]:
    """``EXPERTS`` as a loop list: unset or blank means every expert."""
    names = _csv(raw) or _csv(DEFAULT_EXPERTS)
    if len(names) == 1 and names[0].lower() in NO_EXPERTS:
        return []
    return names


def _max_rounds_by_model(env: Mapping[str, str]) -> list[tuple[str, int]]:
    """``CYCLE_MAX_ROUNDS_BY_MODEL``, distinguishing unset from opted out.

    Three cases, and ``env.get`` alone cannot tell the first two apart:

    * **Key absent from ``env`` entirely** -- nobody has an opinion, so this
      falls back to ``DEFAULT_CYCLE_MAX_ROUNDS_BY_MODEL`` (the shipped
      gemini-3.8/qwen3.8 boost). This is also what a blank or whitespace-only
      value does, same as ``EXPERTS`` -- a deployment that copied
      ``.env.example`` during a rollout has a literal blank line, and that
      must not silently disable the boost either.
    * **``none`` or ``off`` (case-insensitive)** -- the explicit opt-out: no
      per-model caps at all, every cycle just gets ``CYCLE_MAX_ROUNDS``.
    * **Anything else** -- parsed as ``pattern=N`` entries, same as always.
    """
    if "CYCLE_MAX_ROUNDS_BY_MODEL" not in env:
        return _parse_max_rounds_by_model(DEFAULT_CYCLE_MAX_ROUNDS_BY_MODEL)
    raw = env["CYCLE_MAX_ROUNDS_BY_MODEL"].strip()
    if raw.lower() in NO_MAX_ROUNDS_BY_MODEL:
        return []
    return _parse_max_rounds_by_model(raw) or _parse_max_rounds_by_model(
        DEFAULT_CYCLE_MAX_ROUNDS_BY_MODEL
    )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        import os

        env = os.environ

    def get(key: str, default: str = "") -> str:
        return env.get(key, default)

    def get_int(key: str, default: int) -> int:
        raw = get(key).strip()
        return int(raw) if raw else default

    return Settings(
        memory_root=Path(get("MEMORY_ROOT", "/memory")),
        seed_root=Path(get("SEED_ROOT")) if get("SEED_ROOT") else DEFAULT_SEED_ROOT,
        llm_chain=_dedupe(_csv(get("LLM_CHAIN")) or _csv(DEFAULT_LLM_CHAIN)),
        gemini_api_key=get("GEMINI_API_KEY"),
        ollama_url=get("OLLAMA_URL", "http://ollama:11434"),
        # 4096 is Ollama's own server default and far smaller than a cycle's
        # persona+memory+tool-result prompt (observed ~9.7k tokens in
        # practice); the server truncates silently from the front when a
        # prompt overflows it, which is why this has a bigger default. This
        # is the rpi5's own in-compose ollama service (RAM-constrained, 8GB)
        # -- the LAN desktop's own Ollama gets its own, larger,
        # LAN_OLLAMA_NUM_CTX below. See ai_brain.llm.ollama.build_request.
        ollama_num_ctx=get_int("OLLAMA_NUM_CTX", 16384),
        # The lan: provider's host is a desktop machine, not the rpi5, and
        # this deployment's LAN Ollama servers are themselves configured for
        # a 32k context window -- so its default is larger than the local
        # ollama: provider's, rather than shared with it.
        lan_ollama_num_ctx=get_int("LAN_OLLAMA_NUM_CTX", 32768),
        experts=_experts(get("EXPERTS")),
        brain_heartbeat_s=get_int("BRAIN_HEARTBEAT_MIN", 30) * 60,
        expert_heartbeat_s=get_int("EXPERT_HEARTBEAT_MIN", 120) * 60,
        vm_url=get("VM_URL", "http://database-auth:8427"),
        influx_token=get("INFLUX_TOKEN"),
        ha_url=get("HA_URL", "http://192.168.68.87:8123"),
        ha_token=get("HA_TOKEN"),
        ha_todo_list=get("HA_TODO_LIST", "todo.shopping_list"),
        docker_proxy_url=get("DOCKER_PROXY_URL", "http://docker-proxy:2375"),
        gdrive_rag_url=get("GDRIVE_RAG_URL", "http://gdrive-rag:8090"),
        sonos_url=get("SONOS_URL", "http://sonos-http-api:5005"),
        sonos_room=get("SONOS_ROOM", "Kitchen"),
        brave_api_key=get("BRAVE_API_KEY"),
        slack_bot_token=get("SLACK_BOT_TOKEN"),
        slack_app_token=get("SLACK_APP_TOKEN"),
        slack_user_id=get("SLACK_USER_ID"),
        dry_run=get("DRY_RUN") == "1",
        lan_subnets=_csv(get("LAN_SUBNETS")),
        lan_scan_s=get_int("LAN_SCAN_MIN", 10) * 60,
        max_rounds=get_int("CYCLE_MAX_ROUNDS", 16),
        max_rounds_by_model=_max_rounds_by_model(env),
        max_tokens=get_int("CYCLE_MAX_TOKENS", 8000),
        # 0 disables the budget outright. 400000 is generous headroom below
        # the flash models' ~1M context window -- big enough that an ordinary
        # cycle never gets near it, there specifically for the pathological
        # one that keeps pulling in large tool results round after round
        # without ever calling end_cycle.
        max_prompt_tokens=get_int("CYCLE_MAX_PROMPT_TOKENS", 400_000),
        thinking_budget=get_int("GEMINI_THINKING_BUDGET", -1),
        rpm=get_int("RPM", 8),
        tpm=get_int("TPM", 200000),
        rpd=get_int("RPD", 200),
        call_timeout_s=get_int("CALL_TIMEOUT_S", 60),
        http_port=get_int("HTTP_PORT", 8091),
        # The public repo the code_* tools read -- see repo.py. Defaults to
        # this repo itself, on its main branch. repo_refresh_h floors at 1:
        # supervisor.py multiplies it by 3600 for _every()'s sleep interval,
        # and 0 (or a negative value from a typo'd env) would turn that into
        # a busy loop hammering the GitHub API on every tick instead of a
        # config mistake that is merely more aggressive than intended.
        repo_slug=get("REPO_SLUG", "tkhduracell/iot-fetcher"),
        repo_ref=get("REPO_REF", "main"),
        repo_refresh_h=max(1, get_int("REPO_REFRESH_H", 6)),
        # How long a rejection keeps blocking a repeat proposal on the same
        # kind+target -- see propose.py's rejection-memory guard. 30 days is
        # long enough that "Filip said no last week" still holds, short enough
        # that a genuinely stale objection eventually stops gatekeeping.
        rejection_memory_days=get_int("REJECTION_MEMORY_DAYS", 30),
    )
