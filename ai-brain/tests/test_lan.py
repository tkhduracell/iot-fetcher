import ipaddress

import httpx
import pytest
import respx

from ai_brain.config import load_settings
from ai_brain.discovery import OllamaFinder, OllamaHost, subnets_for
from ai_brain.ledger import Ledger, Limits
from ai_brain.llm import Message, ProviderChain, ProviderError, Reply, Usage
from ai_brain.llm.fake import FakeProvider
from ai_brain.llm.lan import LAN_ATTEMPTS, LAN_DEFAULT_NUM_CTX, LanOllamaProvider

MODEL = "qwen3-coder:30b"
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
    provider = LanOllamaProvider(finder)
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
    reply = await LanOllamaProvider(finder).complete(MSGS, [], 64)

    assert route.called
    assert reply.text == "from-the-lan"
    # The trace has to say where the answer came from, not just which model.
    assert reply.model == f"{MODEL} @ http://192.168.68.9:11434"


@respx.mock
async def test_provider_forwards_its_num_ctx_to_the_discovered_host():
    route = respx.post("http://192.0.2.10:11434/api/chat").mock(
        return_value=httpx.Response(200, json=chat_reply())
    )
    finder = finder_for()
    finder._host = OllamaHost("http://192.0.2.10:11434", MODEL, 0.0)
    await LanOllamaProvider(finder, num_ctx=12345).complete(MSGS, [], 64)

    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent["options"]["num_ctx"] == 12345


def test_lan_provider_defaults_num_ctx_larger_than_the_local_ollama_default():
    # The lan: host is a desktop machine on the network, not the
    # RAM-constrained rpi5 running the in-compose ollama: service, so it gets
    # its own, larger, default context window rather than sharing
    # ai_brain.llm.ollama.DEFAULT_NUM_CTX.
    from ai_brain.llm.ollama import DEFAULT_NUM_CTX

    assert LAN_DEFAULT_NUM_CTX == 32768
    assert LAN_DEFAULT_NUM_CTX > DEFAULT_NUM_CTX

    finder = finder_for()
    provider = LanOllamaProvider(finder)
    assert provider._num_ctx == LAN_DEFAULT_NUM_CTX


@respx.mock
async def test_the_host_is_forgotten_after_its_attempts_run_out():
    respx.post("http://192.168.68.9:11434/api/chat").mock(
        return_value=httpx.Response(500, text="boom")
    )
    finder = finder_for()
    finder._host = OllamaHost("http://192.168.68.9:11434", MODEL, 0.0)
    provider = LanOllamaProvider(finder)

    for _ in range(LAN_ATTEMPTS):
        assert finder.current() is not None
        with pytest.raises(ProviderError):
            await provider.complete(MSGS, [], 64)

    # Out of tries: the next scan looks for another machine.
    assert finder.current() is None


# --- the chain ------------------------------------------------------------


def limits_for(settings):
    from ai_brain.llm import limits_from_settings

    return limits_from_settings(settings)


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


def test_a_lan_key_is_unmetered_and_the_rest_are_not():
    from ai_brain.ledger import Limits
    from ai_brain.llm import UNMETERED, limits_from_settings

    settings = load_settings(
        {"LLM_CHAIN": f"lan:{MODEL},gemini:flash", "RPM": "3", "TPM": "500", "RPD": "40"}
    )
    limits = limits_from_settings(settings)

    assert limits[f"lan:{MODEL}"] == UNMETERED
    assert limits["gemini:flash"] == Limits(rpm=3, tpm=500, rpd=40)


def test_the_lan_provider_keeps_its_place_in_the_chain(tmp_path):
    from ai_brain.ledger import Ledger

    settings = load_settings({"LLM_CHAIN": f"fake:x,lan:{MODEL}"})
    ledger = Ledger(limits_for(settings), tmp_path / "ledger.json", clock=lambda: 1.0)
    chain = ProviderChain.from_settings(settings, ledger)

    assert [p.key for p in chain.providers] == ["fake:x", f"lan:{MODEL}"]
    # The supervisor drives the sweep through these handles.
    assert [f.model for f in chain.lan_finders] == [MODEL]


def test_from_settings_gives_the_lan_provider_its_own_num_ctx(tmp_path):
    """LAN_OLLAMA_NUM_CTX, not OLLAMA_NUM_CTX, drives the lan: provider.

    The lan: host is a desktop machine on the network, not the
    RAM-constrained rpi5 running the in-compose ollama: service -- the two
    must not be conflated.
    """
    from ai_brain.ledger import Ledger

    settings = load_settings(
        {
            "LLM_CHAIN": f"lan:{MODEL}",
            "OLLAMA_NUM_CTX": "4096",
            "LAN_OLLAMA_NUM_CTX": "65536",
        }
    )
    ledger = Ledger(limits_for(settings), tmp_path / "ledger.json", clock=lambda: 1.0)
    chain = ProviderChain.from_settings(settings, ledger)

    assert chain.providers[0]._num_ctx == 65536


def test_a_chain_without_a_lan_entry_has_no_finders(tmp_path):
    from ai_brain.ledger import Ledger

    settings = load_settings({"LLM_CHAIN": "fake:x"})
    ledger = Ledger(limits_for(settings), tmp_path / "ledger.json", clock=lambda: 1.0)

    assert ProviderChain.from_settings(settings, ledger).lan_finders == []


def test_two_lan_entries_get_two_independent_finders(tmp_path):
    from ai_brain.ledger import Ledger

    settings = load_settings({"LLM_CHAIN": f"lan:qwen3.8:27b-mlx,lan:{MODEL}"})
    ledger = Ledger(limits_for(settings), tmp_path / "ledger.json", clock=lambda: 1.0)
    chain = ProviderChain.from_settings(settings, ledger)

    assert [p.key for p in chain.providers] == ["lan:qwen3.8:27b-mlx", f"lan:{MODEL}"]
    assert [f.model for f in chain.lan_finders] == ["qwen3.8:27b-mlx", MODEL]
    # Each provider's own finder, not one shared instance the second entry
    # would silently overwrite.
    assert chain.providers[0].finder is not chain.providers[1].finder


# --- hard timeouts --------------------------------------------------------


async def test_a_hung_scan_is_abandoned_and_keeps_the_old_host(monkeypatch):
    import asyncio

    monkeypatch.setattr("ai_brain.discovery.SCAN_TIMEOUT_S", 0.05)

    async def _forever(*_args, **_kwargs):
        await asyncio.Event().wait()

    finder = finder_for()
    known = OllamaHost("http://192.168.68.9:11434", MODEL, 0.0)
    finder._host = known
    monkeypatch.setattr("ai_brain.discovery._has_model", _forever)

    # Bounded by SCAN_TIMEOUT_S rather than hanging: the lock is released and
    # the next scan can run.
    assert await finder.scan() is known
    assert not finder._scanning.locked()


async def test_a_hung_sweep_is_abandoned(monkeypatch):
    import asyncio

    monkeypatch.setattr("ai_brain.discovery.SWEEP_TIMEOUT_S", 0.05)

    async def _forever(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("ai_brain.discovery._port_open", _forever)
    assert await finder_for().scan() is None


@respx.mock
async def test_a_host_that_never_finishes_answering_tags_is_not_a_host(monkeypatch, no_ports):
    import asyncio

    monkeypatch.setattr("ai_brain.discovery.TAGS_TIMEOUT_S", 0.05)
    no_ports.add("192.168.68.1")

    async def _slow(_request):
        await asyncio.sleep(5)
        return httpx.Response(200, json=tags(MODEL))

    respx.get("http://192.168.68.1:11434/api/tags").mock(side_effect=_slow)
    async with httpx.AsyncClient() as client:
        assert await finder_for(client=client).scan() is None


# --- timeouts ---------------------------------------------------------
#
# A local model can legitimately need minutes where a cloud API needs
# seconds, so lan: gets its own, far longer, request timeout than the chain's
# default -- while the connect phase stays tight, since a host not answering
# the port at all is never worth 900s of patience.


def test_lan_provider_declares_its_own_call_timeout():
    from ai_brain.llm.lan import LAN_REQUEST_TIMEOUT_S

    finder = OllamaFinder(MODEL, [])
    provider = LanOllamaProvider(finder)

    assert provider.call_timeout_s == LAN_REQUEST_TIMEOUT_S
    assert provider.call_timeout_s > 60  # longer than the chain's own default


def test_lan_providers_default_client_timeout_has_a_tight_connect():
    from ai_brain.llm.lan import LAN_CONNECT_TIMEOUT_S, LAN_REQUEST_TIMEOUT_S

    finder = OllamaFinder(MODEL, [])
    provider = LanOllamaProvider(finder)

    assert provider._timeout_s.connect == LAN_CONNECT_TIMEOUT_S
    assert provider._timeout_s.read == LAN_REQUEST_TIMEOUT_S


async def test_chain_uses_the_lan_providers_own_timeout_not_its_default(tmp_path, monkeypatch):
    """A short chain-wide default must not cut off a slow-but-fine lan: call."""
    import asyncio

    from ai_brain.ledger import Ledger

    finder = OllamaFinder(MODEL, [])
    finder._host = OllamaHost("http://192.168.68.9:11434", MODEL, 1.0)
    provider = LanOllamaProvider(finder)
    # Real value (900s) would make the test slow for nothing; what matters is
    # that it is used at all instead of the chain's much smaller default.
    monkeypatch.setattr(provider, "call_timeout_s", 0.2)

    async def _slow(*_args, **_kwargs):
        await asyncio.sleep(0.05)  # longer than the chain's default, shorter than the provider's
        return Reply(text="ok", tool_calls=(), usage=Usage(1, 1), model=MODEL)

    monkeypatch.setattr(
        "ai_brain.llm.ollama.OllamaProvider.complete",
        lambda self, *a, **k: _slow(),
    )
    ledger = Ledger({f"lan:{MODEL}": Limits(rpm=100, tpm=1_000_000, rpd=1000)}, tmp_path / "l.json")
    # The chain's own default (0.01s) is far shorter than the sleep above;
    # only the provider's own call_timeout_s override keeps this from timing
    # out and falling through to ChainExhausted.
    chain = ProviderChain([provider], ledger, call_timeout_s=0.01)

    out = await chain.complete(MSGS, [], 512, "brain")

    assert out.text == "ok"
