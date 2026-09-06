from pathlib import Path

from ai_brain.config import DEFAULT_SEED_ROOT, load_settings


def test_defaults_from_empty_env():
    s = load_settings({})
    assert s.memory_root == Path("/memory")
    assert s.seed_root == DEFAULT_SEED_ROOT
    assert s.llm_chain == []
    assert s.experts == []
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
