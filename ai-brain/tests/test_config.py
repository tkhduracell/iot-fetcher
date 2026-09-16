from pathlib import Path

from ai_brain.config import DEFAULT_LLM_CHAIN, DEFAULT_SEED_ROOT, load_settings


def test_defaults_from_empty_env():
    s = load_settings({})
    assert s.memory_root == Path("/memory")
    assert s.seed_root == DEFAULT_SEED_ROOT
    assert s.llm_chain == [
        "gemini:gemini-3.8-flash",
        "gemini:gemini-3.5-flash-lite",
        "ollama:llama3.2:3b",
    ]
    assert s.experts == ["energy", "health", "house-ops", "researcher"]
    assert s.brain_heartbeat_s == 30 * 60
    assert s.expert_heartbeat_s == 120 * 60
    assert s.vm_url == "http://database-auth:8427"
    assert s.ha_url == "http://192.168.68.87:8123"
    assert s.ha_todo_list == "todo.shopping_list"
    assert s.gdrive_rag_url == "http://gdrive-rag:8090"
    assert s.sonos_url == "http://sonos-http-api:5005"
    assert s.sonos_room == "Kitchen"
    assert s.dry_run is False
    assert (s.rpm, s.tpm, s.rpd) == (8, 200000, 200)
    assert s.call_timeout_s == 60
    assert (s.max_rounds, s.max_tokens, s.thinking_budget) == (16, 8000, -1)


def test_blank_experts_is_the_default_set():
    # A deployment that copied .env.example during the rollout has a literal
    # blank EXPERTS line; it must not silently pin that box to brain-only.
    assert load_settings({"EXPERTS": ""}).experts == ["energy", "health", "house-ops", "researcher"]
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


def test_ultra_mode_is_off_unless_asked_for():
    s = load_settings({})
    assert s.ultra is False
    assert s.ultra_model == "deepseek-r1:8b"
    assert s.ultra_subnets == []
    assert s.ultra_scan_s == 600
    # Exactly "1", like DRY_RUN -- "true" is not an accident we want to honour.
    assert load_settings({"ULTRA_MODE": "true"}).ultra is False
    assert load_settings({"ULTRA_MODE": "1"}).ultra is True


def test_ultra_subnets_and_interval_are_configurable():
    s = load_settings({"ULTRA_SUBNETS": "10.0.0.0/24, 192.168.1.0/24", "ULTRA_SCAN_MIN": "2"})
    assert s.ultra_subnets == ["10.0.0.0/24", "192.168.1.0/24"]
    assert s.ultra_scan_s == 120


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
        assert provider in ("gemini", "ollama")
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
