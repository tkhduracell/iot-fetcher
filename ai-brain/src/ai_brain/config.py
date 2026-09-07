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
DEFAULT_LLM_CHAIN = "gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite,ollama:llama3.2:3b"


@dataclass(frozen=True)
class Settings:
    memory_root: Path
    seed_root: Path
    llm_chain: list[str]
    gemini_api_key: str
    ollama_url: str
    experts: list[str]
    brain_heartbeat_s: int
    expert_heartbeat_s: int
    vm_url: str
    influx_token: str
    ha_url: str
    ha_token: str
    ha_todo_list: str
    gdrive_rag_url: str
    sonos_url: str
    sonos_room: str
    brave_api_key: str
    slack_bot_token: str
    slack_app_token: str
    slack_user_id: str
    dry_run: bool
    rpm: int
    tpm: int
    rpd: int
    call_timeout_s: int
    http_port: int


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
        experts=_csv(get("EXPERTS")),
        brain_heartbeat_s=get_int("BRAIN_HEARTBEAT_MIN", 30) * 60,
        expert_heartbeat_s=get_int("EXPERT_HEARTBEAT_MIN", 120) * 60,
        vm_url=get("VM_URL", "http://database-auth:8427"),
        influx_token=get("INFLUX_TOKEN"),
        ha_url=get("HA_URL", "http://192.168.68.87:8123"),
        ha_token=get("HA_TOKEN"),
        ha_todo_list=get("HA_TODO_LIST", "todo.shopping_list"),
        gdrive_rag_url=get("GDRIVE_RAG_URL", "http://gdrive-rag:8090"),
        sonos_url=get("SONOS_URL", "http://sonos-http-api:5005"),
        sonos_room=get("SONOS_ROOM", "Kitchen"),
        brave_api_key=get("BRAVE_API_KEY"),
        slack_bot_token=get("SLACK_BOT_TOKEN"),
        slack_app_token=get("SLACK_APP_TOKEN"),
        slack_user_id=get("SLACK_USER_ID"),
        dry_run=get("DRY_RUN") == "1",
        rpm=get_int("RPM", 8),
        tpm=get_int("TPM", 200000),
        rpd=get_int("RPD", 200),
        call_timeout_s=get_int("CALL_TIMEOUT_S", 60),
        http_port=get_int("HTTP_PORT", 8091),
    )
