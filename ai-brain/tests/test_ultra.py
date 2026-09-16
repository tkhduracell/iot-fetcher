import ipaddress

import httpx
import pytest
import respx

from ai_brain.config import load_settings
from ai_brain.discovery import OllamaFinder, OllamaHost, subnets_for
from ai_brain.ledger import Ledger, Limits
from ai_brain.llm import Message, ProviderChain, ProviderError, Reply, Usage
from ai_brain.llm.fake import FakeProvider
from ai_brain.llm.ultra import ULTRA_ATTEMPTS, UltraOllamaProvider

MODEL = "deepseek-r1:8b"
MSGS = [Message(role="user", content="hi")]


def tags(*names: str) -> dict:
    return {"models": [{"name": name} for name in names]}


def chat_reply(text="from-the-lan") -> dict:
    return {
        "message": {"role": "assistant", "content": text},
        "prompt_eval_count": 7,
        "eval_count": 3,
    }


# --- subnet selection -----------------------------------------------------


def test_subnets_come_from_ha_url_when_unconfigured():
    assert subnets_for([], "http://192.168.68.87:8123") == [
        ipaddress.ip_network("192.168.68.0/24")
    ]


def test_configured_subnets_win():
    assert subnets_for(["10.0.0.0/24"], "http://192.168.68.87:8123") == [
        ipaddress.ip_network("10.0.0.0/24")
    ]


def test_public_and_oversized_and_unparsable_subnets_are_refused():
    # A public range is not this house's network; a /16 is 65k connects.
    assert subnets_for(["8.8.8.0/24"], "") == []
    assert subnets_for(["10.0.0.0/16"], "") == []
    assert subnets_for(["not-a-subnet"], "") == []


def test_a_non_ip_or_public_ha_url_yields_nothing_to_sweep():
    assert subnets_for([], "http://homeassistant.local:8123") == []
    assert subnets_for([], "http://93.184.216.34:8123") == []


# --- discovery ------------------------------------------------------------


@pytest.fixture
def no_ports(monkeypatch):
    """Every TCP connect fails unless a test says otherwise."""
    open_ips: set[str] = set()

    async def _port_open(ip, _semaphore):
        return ip if ip in open_ips else None

    monkeypatch.setattr("ai_brain.discovery._port_open", _port_open)
    return open_ips


def finder_for(network="192.168.68.0/30", client=None) -> OllamaFinder:
    return OllamaFinder(MODEL, [ipaddress.ip_network(network)], client=client)


@respx.mock
async def test_scan_finds_a_host_with_the_model(no_ports):
    no_ports.add("192.168.68.1")
    respx.get("http://192.168.68.1:11434/api/tags").mock(
        return_value=httpx.Response(200, json=tags("llama3.2:3b", MODEL))
    )
    async with httpx.AsyncClient() as client:
        found = await finder_for(client=client).scan()

    assert found is not None
    assert found.base_url == "http://192.168.68.1:11434"


@respx.mock
async def test_a_host_without_the_model_is_not_a_host(no_ports):
    no_ports.add("192.168.68.1")
    respx.get("http://192.168.68.1:11434/api/tags").mock(
        return_value=httpx.Response(200, json=tags("llama3.2:3b"))
    )
    async with httpx.AsyncClient() as client:
        finder = finder_for(client=client)
        assert await finder.scan() is None
        assert finder.current() is None


@respx.mock
async def test_scan_confirms_the_known_host_without_sweeping(no_ports):
    route = respx.get("http://192.168.68.9:11434/api/tags").mock(
        return_value=httpx.Response(200, json=tags(MODEL))
    )
    async with httpx.AsyncClient() as client:
        finder = finder_for(client=client)
        finder._host = OllamaHost("http://192.168.68.9:11434", MODEL, 0.0)
        assert (await finder.scan()).base_url == "http://192.168.68.9:11434"

    # One confirmation call, and no sweep: the .1/.2 addresses were never tried.
    assert route.call_count == 1
    assert no_ports == set()


@respx.mock
async def test_a_host_that_went_away_is_dropped_and_re_swept(no_ports):
    respx.get("http://192.168.68.9:11434/api/tags").mock(side_effect=httpx.ConnectError("gone"))
    no_ports.add("192.168.68.2")
    respx.get("http://192.168.68.2:11434/api/tags").mock(
        return_value=httpx.Response(200, json=tags(MODEL))
    )
    async with httpx.AsyncClient() as client:
        finder = finder_for(client=client)
        finder._host = OllamaHost("http://192.168.68.9:11434", MODEL, 0.0)
        found = await finder.scan()

    assert found.base_url == "http://192.168.68.2:11434"


async def test_scan_never_raises(monkeypatch):
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("network is on fire")

    monkeypatch.setattr("ai_brain.discovery._port_open", _boom)
    assert await finder_for().scan() is None


# --- the provider ---------------------------------------------------------


@respx.mock
async def test_provider_is_unavailable_until_a_host_is_found():
    finder = finder_for()
    provider = UltraOllamaProvider(finder)
    assert provider.available() is False

    finder._host = OllamaHost("http://192.168.68.9:11434", MODEL, 0.0)
    assert provider.available() is True


@respx.mock
async def test_provider_calls_the_discovered_host():
    route = respx.post("http://192.168.68.9:11434/api/chat").mock(
        return_value=httpx.Response(200, json=chat_reply())
    )
    finder = finder_for()
    finder._host = OllamaHost("http://192.168.68.9:11434", MODEL, 0.0)
    reply = await UltraOllamaProvider(finder).complete(MSGS, [], 64)

    assert route.called
    assert reply.text == "from-the-lan"
    # The trace has to say where the answer came from, not just which model.
    assert reply.model == f"{MODEL} @ http://192.168.68.9:11434"


@respx.mock
async def test_the_host_is_forgotten_after_its_attempts_run_out():
    respx.post("http://192.168.68.9:11434/api/chat").mock(
        return_value=httpx.Response(500, text="boom")
    )
    finder = finder_for()
    finder._host = OllamaHost("http://192.168.68.9:11434", MODEL, 0.0)
    provider = UltraOllamaProvider(finder)

    for _ in range(ULTRA_ATTEMPTS):
        assert finder.current() is not None
        with pytest.raises(ProviderError):
            await provider.complete(MSGS, [], 64)

    # Out of tries: the next scan looks for another machine.
    assert finder.current() is None


# --- the chain ------------------------------------------------------------


def make_ledger(tmp_path, keys):
    limits = {key: Limits(rpm=10, tpm=100_000, rpd=100) for key in keys}
    return Ledger(limits, tmp_path / "ledger.json", clock=lambda: 1_757_000_000.0)


class Unavailable(FakeProvider):
    def available(self) -> bool:
        return False


def reply(text="ok", model="m"):
    return Reply(text=text, tool_calls=(), usage=Usage(prompt_tokens=1, completion_tokens=1),
                 model=model)


async def test_chain_skips_an_unavailable_provider_without_spending(tmp_path):
    ledger = make_ledger(tmp_path, ["a", "b"])
    a = Unavailable("a", [reply("from-a")])
    b = FakeProvider("b", [reply("from-b")])

    out = await ProviderChain([a, b], ledger).complete(MSGS, [], 512, "brain")

    assert out.text == "from-b"
    assert a.calls == []
    # Nothing was recorded against the skipped key: it was never dialled.
    assert ledger.snapshot()["buckets"]["a"]["requests_day"] == 0


async def test_chain_honours_a_providers_own_attempt_budget(tmp_path):
    ledger = make_ledger(tmp_path, ["a", "b"])
    a = FakeProvider("a", [ProviderError("boom", kind="server")] * 3)
    a.max_attempts = 3
    b = FakeProvider("b", [reply("from-b")])

    out = await ProviderChain([a, b], ledger).complete(MSGS, [], 512, "brain")

    assert out.text == "from-b"
    assert len(a.calls) == 3


def test_ultra_key_gets_an_unmetered_budget(tmp_path):
    from ai_brain.llm import UNMETERED, limits_from_settings

    settings = load_settings({"ULTRA_MODE": "1", "LLM_CHAIN": "fake:a"})
    limits = limits_from_settings(settings)
    assert limits[f"ultra:{MODEL}"] == UNMETERED
