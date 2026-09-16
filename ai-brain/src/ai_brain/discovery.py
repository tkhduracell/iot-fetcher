"""Finding an Ollama host on the LAN, so ultra mode has something to talk to.

The rpi5 can run a 3B model; the desktop in the next room can run something
worth asking. That machine is not a fixed address -- it is whatever is powered
on right now -- so ultra mode sweeps the local network for it instead of being
told where it lives.

The sweep is a TCP connect to the Ollama port across a /24, which is cheap and
quiet, followed by ``GET /api/tags`` on whatever answered. A host only counts
if it actually has the model pulled: a machine running Ollama with nothing
installed would otherwise be picked, 404 every call, and lose every cycle to a
provider that cannot serve one.

Subnets come from config when set, and otherwise from the deployment itself:
this process runs on a docker bridge network, so its own address is 172.x and
sweeping it would find nothing. ``HA_URL`` points at the real LAN, and its /24
is the one worth sweeping.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

OLLAMA_PORT = 11434

# How long a host has to answer a TCP connect. A LAN round trip is sub-
# millisecond; anything slower than this is a machine we do not want running
# an agent cycle anyway.
CONNECT_TIMEOUT_S = 0.4
# Concurrent connects. 254 hosts at 64 at a time is four rounds of 0.4s worst
# case, which keeps a full sweep well under two seconds.
SWEEP_CONCURRENCY = 64
TAGS_TIMEOUT_S = 5

# Hard ceiling on one scan, whatever it is doing. Every step below is bounded
# on its own, but a host can accept a connection and then never speak, and a
# sweep that hangs would hold the scan lock forever -- which means the finder
# never re-checks and ultra mode is stuck on a machine that has gone away.
SCAN_TIMEOUT_S = 30
# One /24 of connects at SWEEP_CONCURRENCY is ~2s; ten times that is a network
# behaving in a way we should abandon rather than wait out.
SWEEP_TIMEOUT_S = 20


@dataclass(frozen=True)
class OllamaHost:
    base_url: str
    model: str
    found_at: float


def subnets_for(configured: list[str], ha_url: str) -> list[ipaddress.IPv4Network]:
    """The /24s to sweep: what was configured, else the LAN Home Assistant is on."""
    raw = list(configured)
    if not raw:
        host = urlparse(ha_url).hostname or ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return []
        if not isinstance(address, ipaddress.IPv4Address) or not address.is_private:
            return []
        raw = [f"{address}/24"]

    out: list[ipaddress.IPv4Network] = []
    for entry in raw:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            log.warning("[ultra] ignoring unparsable subnet %r", entry)
            continue
        if not isinstance(network, ipaddress.IPv4Network) or not network.is_private:
            # A public range is not this house's network, and sweeping one is
            # not something this process should ever do by accident.
            log.warning("[ultra] refusing to sweep non-private subnet %s", network)
            continue
        if network.num_addresses > 1024:
            log.warning("[ultra] refusing to sweep %s: wider than a /22", network)
            continue
        out.append(network)
    return out


async def _port_open(ip: str, semaphore: asyncio.Semaphore) -> str | None:
    async with semaphore:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, OLLAMA_PORT), timeout=CONNECT_TIMEOUT_S
            )
        except (TimeoutError, OSError):
            return None
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return ip


async def _has_model(client: httpx.AsyncClient, ip: str, model: str) -> bool:
    url = f"http://{ip}:{OLLAMA_PORT}/api/tags"
    try:
        # httpx's own timeout is per network operation, so a server that keeps
        # sending a byte at a time never trips it. wait_for bounds the whole
        # request, which is what we actually care about.
        response = await asyncio.wait_for(
            client.get(url, timeout=TAGS_TIMEOUT_S), timeout=TAGS_TIMEOUT_S
        )
        if not response.is_success:
            return False
        body = response.json()
    except (TimeoutError, httpx.HTTPError, ValueError):
        return False
    names = {
        str(entry.get("name") or "")
        for entry in (body.get("models") or [])
        if isinstance(entry, dict)
    }
    # Ollama reports "deepseek-r1:8b"; a host that pulled it without a tag
    # reports "deepseek-r1:latest", which is a different model to us.
    return model in names


class OllamaFinder:
    """Keeps the current LAN host, and re-checks it on a schedule."""

    def __init__(
        self,
        model: str,
        networks: list[ipaddress.IPv4Network],
        client: httpx.AsyncClient | None = None,
        clock=time.time,
    ) -> None:
        self.model = model
        self.networks = networks
        self._client = client
        self._clock = clock
        self._host: OllamaHost | None = None
        self._scanning = asyncio.Lock()

    def current(self) -> OllamaHost | None:
        return self._host

    def forget(self) -> None:
        """Drop the current host after it has failed us, so the next scan re-sweeps."""
        if self._host is not None:
            log.info("[ultra] dropping %s", self._host.base_url)
        self._host = None

    async def scan(self) -> OllamaHost | None:
        """Confirm the known host, or sweep for a new one. Never raises."""
        if self._scanning.locked():
            # A sweep is already running; a second one would double the traffic
            # and reach the same answer.
            return self._host
        async with self._scanning:
            client = self._client or httpx.AsyncClient()
            try:
                return await asyncio.wait_for(self._scan_with(client), timeout=SCAN_TIMEOUT_S)
            except TimeoutError:
                log.warning("[ultra] scan exceeded %ss, abandoned", SCAN_TIMEOUT_S)
                return self._host
            except Exception:  # discovery is best effort, always
                log.exception("[ultra] scan failed")
                return self._host
            finally:
                if self._client is None:
                    await client.aclose()

    async def _scan_with(self, client: httpx.AsyncClient) -> OllamaHost | None:
        known = self._host
        if known is not None:
            ip = urlparse(known.base_url).hostname or ""
            if await _has_model(client, ip, self.model):
                return known
            log.info("[ultra] %s stopped answering, re-sweeping", known.base_url)
            self._host = None

        semaphore = asyncio.Semaphore(SWEEP_CONCURRENCY)
        for network in self.networks:
            hosts = [str(ip) for ip in network.hosts()]
            log.info("[ultra] sweeping %s (%d hosts)", network, len(hosts))
            try:
                open_ports = await asyncio.wait_for(
                    asyncio.gather(*(_port_open(ip, semaphore) for ip in hosts)),
                    timeout=SWEEP_TIMEOUT_S,
                )
            except TimeoutError:
                log.warning("[ultra] sweep of %s exceeded %ss, skipped", network, SWEEP_TIMEOUT_S)
                continue
            for ip in [ip for ip in open_ports if ip]:
                if await _has_model(client, ip, self.model):
                    self._host = OllamaHost(
                        base_url=f"http://{ip}:{OLLAMA_PORT}",
                        model=self.model,
                        found_at=self._clock(),
                    )
                    log.info("[ultra] found %s with %s", self._host.base_url, self.model)
                    return self._host
                log.info("[ultra] %s runs ollama but has no %s", ip, self.model)
        log.info("[ultra] no host with %s on the network", self.model)
        return None
