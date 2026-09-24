from pathlib import Path

import pytest

from ai_brain.config import (
    CYCLE_MAX_ROUNDS_HARD_CEILING,
    DEFAULT_LLM_CHAIN,
    DEFAULT_SEED_ROOT,
    load_settings,
)


def test_defaults_from_empty_env():
    s = load_settings({})
    assert s.memory_root == Path("/memory")
    assert s.seed_root == DEFAULT_SEED_ROOT
    assert s.llm_chain == [
        "gemini:gemini-3.8-flash",
        "gemini:gemini-3.5-flash-lite",
        "lan:qwen3-coder:30b",
        "ollama:llama3.2:3b",
    ]
    assert s.experts == ["energy", "health", "house-ops", "researcher", "infra"]
    assert s.brain_heartbeat_s == 30 * 60
    assert s.expert_heartbeat_s == 120 * 60
    assert s.vm_url == "http://database-auth:8427"
    assert s.ha_url == "http://192.168.68.87:8123"
    assert s.ha_todo_list == "todo.shopping_list"
    assert s.docker_proxy_url == "http://docker-proxy:2375"
    assert s.gdrive_rag_url == "http://gdrive-rag:8090"
    assert s.sonos_url == "http://sonos-http-api:5005"
    assert s.sonos_room == "Kitchen"
    assert s.dry_run is False
    assert (s.rpm, s.tpm, s.rpd) == (8, 200000, 200)
    assert s.call_timeout_s == 60
    assert (s.max_rounds, s.max_tokens, s.thinking_budget) == (16, 8000, -1)
    assert s.max_rounds_by_model == [("gemini:*3.8*", 32), ("lan:qwen3.8*", 32)]
    assert s.repo_slug == "tkhduracell/iot-fetcher"
    assert s.repo_ref == "main"
    assert s.repo_refresh_h == 6
    assert s.rejection_memory_days == 30


def test_repo_settings_are_overridable():
    s = load_settings({"REPO_SLUG": "someone/fork", "REPO_REF": "dev", "REPO_REFRESH_H": "1"})
    assert (s.repo_slug, s.repo_ref, s.repo_refresh_h) == ("someone/fork", "dev", 1)


def test_rejection_memory_days_is_overridable():
    assert load_settings({"REJECTION_MEMORY_DAYS": "7"}).rejection_memory_days == 7


def test_repo_refresh_h_floors_at_one():
    """0 (or a typo'd negative) would turn supervisor.py's _every() interval
    into a busy loop hammering GitHub, not a merely-too-aggressive config."""
    assert load_settings({"REPO_REFRESH_H": "0"}).repo_refresh_h == 1
    assert load_settings({"REPO_REFRESH_H": "-5"}).repo_refresh_h == 1


def test_blank_experts_is_the_default_set():
    # A deployment that copied .env.example during the rollout has a literal
    # blank EXPERTS line; it must not silently pin that box to brain-only.
    assert load_settings({"EXPERTS": ""}).experts == [
        "energy",
        "health",
        "house-ops",
        "researcher",
        "infra",
    ]
    assert load_settings({"EXPERTS": "   "}).experts == load_settings({}).experts


def test_experts_none_is_the_opt_out():
    assert load_settings({"EXPERTS": "none"}).experts == []
    assert load_settings({"EXPERTS": "NONE"}).experts == []
    # The brain is not an expert, so naming it says the same thing.
    assert load_settings({"EXPERTS": "brain"}).experts == []
    # Only on its own -- alongside a real expert it is a name we do not know,
    # and the supervisor is what refuses unknown names.
    assert load_settings({"EXPERTS": "none,energy"}).experts == ["none", "energy"]


def test_effort_knobs_are_overridable():
    s = load_settings({"CYCLE_MAX_ROUNDS": "4", "CYCLE_MAX_TOKENS": "1000", "GEMINI_THINKING_BUDGET": "0"})
    assert (s.max_rounds, s.max_tokens, s.thinking_budget) == (4, 1000, 0)


# -- CYCLE_MAX_ROUNDS_BY_MODEL ------------------------------------------


def test_max_rounds_by_model_default_is_unchanged_when_the_setting_is_empty():
    """Blank/unset must fall back to the documented default, not an empty list --
    otherwise every deployment silently loses the strong-model boost until it
    copies the new line into its .env."""
    for env in ({}, {"CYCLE_MAX_ROUNDS_BY_MODEL": ""}, {"CYCLE_MAX_ROUNDS_BY_MODEL": "   "}):
        s = load_settings(env)
        assert s.max_rounds_by_model == [("gemini:*3.8*", 32), ("lan:qwen3.8*", 32)]


def test_max_rounds_by_model_parses_pattern_equals_n():
    s = load_settings({"CYCLE_MAX_ROUNDS_BY_MODEL": "gemini:*=20, lan:qwen3-coder*=40"})
    assert s.max_rounds_by_model == [("gemini:*", 20), ("lan:qwen3-coder*", 40)]


def test_max_rounds_by_model_first_match_wins_order_is_preserved():
    s = load_settings({"CYCLE_MAX_ROUNDS_BY_MODEL": "gemini:gemini-3.8-flash=10,gemini:*=20"})
    assert s.max_rounds_by_model == [("gemini:gemini-3.8-flash", 10), ("gemini:*", 20)]


@pytest.mark.parametrize(
    "raw",
    [
        "gemini:*",  # missing =N
        "gemini:*=",  # missing N
        "=20",  # missing pattern
        "gemini:*=abc",  # non-integer N
        "gemini:*=0",  # below the 1..64 range
        "gemini:*=65",  # above CYCLE_MAX_ROUNDS_HARD_CEILING
        "gemini:*=-1",
    ],
)
def test_max_rounds_by_model_malformed_entry_is_a_startup_error(raw):
    with pytest.raises(ValueError):
        load_settings({"CYCLE_MAX_ROUNDS_BY_MODEL": raw})


def test_max_rounds_by_model_accepts_the_ceiling_itself():
    s = load_settings({"CYCLE_MAX_ROUNDS_BY_MODEL": f"gemini:*={CYCLE_MAX_ROUNDS_HARD_CEILING}"})
    assert s.max_rounds_by_model == [("gemini:*", CYCLE_MAX_ROUNDS_HARD_CEILING)]


def test_lan_sweep_defaults_and_overrides():
    s = load_settings({})
    assert s.lan_subnets == []
    assert s.lan_scan_s == 600

    s = load_settings({"LAN_SUBNETS": "10.0.0.0/24, 192.168.1.0/24", "LAN_SCAN_MIN": "2"})
    assert s.lan_subnets == ["10.0.0.0/24", "192.168.1.0/24"]
    assert s.lan_scan_s == 120


def test_default_seed_root_is_the_shipped_seed_dir():
    assert DEFAULT_SEED_ROOT.is_dir()
    assert (DEFAULT_SEED_ROOT / "constitution.md").is_file()


def test_env_overrides_and_list_parsing():
    s = load_settings(
        {
            "MEMORY_ROOT": "/tmp/mem",
            "SEED_ROOT": "/tmp/seed",
            "LLM_CHAIN": "gemini-2.5-flash, gemini-2.0-flash ,",
            "EXPERTS": "energy,health",
            "BRAIN_HEARTBEAT_MIN": "5",
            "EXPERT_HEARTBEAT_MIN": "15",
            "DRY_RUN": "1",
            "RPM": "3",
            "GEMINI_API_KEY": "k",
        }
    )
    assert s.memory_root == Path("/tmp/mem")
    assert s.seed_root == Path("/tmp/seed")
    assert s.llm_chain == ["gemini-2.5-flash", "gemini-2.0-flash"]
    assert s.experts == ["energy", "health"]
    assert s.brain_heartbeat_s == 300
    assert s.expert_heartbeat_s == 900
    assert s.dry_run is True
    assert s.rpm == 3
    assert s.gemini_api_key == "k"


def test_dry_run_only_true_for_one():
    assert load_settings({"DRY_RUN": "0"}).dry_run is False
    assert load_settings({"DRY_RUN": "true"}).dry_run is False


def test_blank_llm_chain_falls_back_to_the_documented_default():
    """An empty or whitespace LLM_CHAIN must not produce a zero-provider chain."""
    for raw in ("", "   ", ",,"):
        s = load_settings({"LLM_CHAIN": raw})
        assert s.llm_chain == DEFAULT_LLM_CHAIN.split(",")


def test_default_chain_entries_are_all_provider_qualified():
    """Every entry must parse as provider:model or from_settings raises."""
    for entry in DEFAULT_LLM_CHAIN.split(","):
        provider, sep, model = entry.partition(":")
        assert sep == ":"
        assert provider in ("lan", "gemini", "ollama")
        assert model


def test_a_repeated_chain_entry_is_deduped_in_order():
    """Every entry shares one ledger key, so a repeat is the same exhausted key twice."""
    s = load_settings({"LLM_CHAIN": "gemini:a,gemini:b,gemini:a,gemini:c,gemini:b"})
    assert s.llm_chain == ["gemini:a", "gemini:b", "gemini:c"]


def test_dedupe_leaves_a_chain_without_repeats_alone():
    s = load_settings({"LLM_CHAIN": "gemini:a,gemini:b"})
    assert s.llm_chain == ["gemini:a", "gemini:b"]


def test_http_port_defaults_to_8091():
    assert load_settings({}).http_port == 8091


def test_http_port_is_read_from_the_environment():
    assert load_settings({"HTTP_PORT": "9000"}).http_port == 9000


def test_http_port_zero_disables_the_api():
    assert load_settings({"HTTP_PORT": "0"}).http_port == 0


def test_ollama_num_ctx_defaults_to_a_pi5_sized_value():
    # Ollama's own server default (4096) is too small for a cycle's
    # persona+memory+tool-result prompt (observed ~9.7k tokens); this is our
    # own, larger default.
    assert load_settings({}).ollama_num_ctx == 16384


def test_ollama_num_ctx_is_read_from_the_environment():
    assert load_settings({"OLLAMA_NUM_CTX": "8192"}).ollama_num_ctx == 8192


def test_lan_ollama_num_ctx_defaults_larger_than_the_local_ollama_default():
    # The lan: host is a desktop machine, not the RAM-constrained rpi5
    # running the in-compose ollama: service, so it gets its own, larger,
    # default rather than sharing OLLAMA_NUM_CTX.
    s = load_settings({})
    assert s.lan_ollama_num_ctx == 32768
    assert s.lan_ollama_num_ctx > s.ollama_num_ctx


def test_lan_ollama_num_ctx_is_read_from_the_environment():
    assert (
        load_settings({"LAN_OLLAMA_NUM_CTX": "65536"}).lan_ollama_num_ctx == 65536
    )
