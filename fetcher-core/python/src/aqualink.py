
import asyncio
import json
import logging
import os
import time

from typing import List, Optional
from aiohttp import Payload
import httpx

from iaqualink.client import AqualinkClient, AQUALINK_HTTP_HEADERS
from iaqualink.system import AqualinkSystem
from iaqualink.device import AqualinkDevice
from iaqualink.const import (
    AQUALINK_API_KEY,
)
from iaqualink.exception import AqualinkServiceUnauthorizedException

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

aqualink_username = os.environ.get('AQUALINK_USERNAME', '')
aqualink_password = os.environ.get('AQUALINK_PASSWORD', '')


timeout = httpx.Timeout(60.0, connect=60.0, read=60.0)

# Persistent async state for session reuse across scheduled invocations
_loop: asyncio.AbstractEventLoop | None = None
_httpx_client: httpx.AsyncClient | None = None
_aqualink_client: AqualinkClient | None = None
_client_created_at: float = 0
_CLIENT_TTL_SECONDS = 3600  # 1 hour


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop


async def _get_client() -> AqualinkClient:
    global _httpx_client, _aqualink_client, _client_created_at

    # Check if client has expired (older than 1 hour)
    if _aqualink_client is not None and time.time() - _client_created_at > _CLIENT_TTL_SECONDS:
        logger.info("[aqualink] Session expired (1 hour), resetting client")
        await _reset_client()

    # Create httpx client if needed
    if _httpx_client is None or _httpx_client.is_closed:
        _httpx_client = httpx.AsyncClient(timeout=timeout)

    # Create and login AqualinkClient if needed
    if _aqualink_client is None or not _aqualink_client.logged:
        _aqualink_client = AqualinkClient(
            aqualink_username, aqualink_password, _httpx_client
        )
        await _aqualink_client.login()
        _client_created_at = time.time()
        logger.info("[aqualink] Logged in to Aqualink (new session)")

    return _aqualink_client


async def _reset_client() -> None:
    global _httpx_client, _aqualink_client, _client_created_at
    logger.info("[aqualink] Resetting client state")

    if _httpx_client is not None:
        try:
            await _httpx_client.aclose()
        except Exception:
            pass
    _httpx_client = None
    _aqualink_client = None
    _client_created_at = 0


def aqualink():
    if not aqualink_username or not aqualink_password:
        logger.error(
            "[aqualink] AQUALINK_USERNAME or AQUALINK_PASSWORD environment variable not set, ignoring...")
        return

    loop = _get_loop()
    try:
        loop.run_until_complete(_aqualink())
    except (httpx.ReadTimeout, httpx.TimeoutException):
        logger.warning("[aqualink] Aqualink request timed out", exc_info=False)
        loop.run_until_complete(_reset_client())
    except AqualinkServiceUnauthorizedException:
        logger.warning("[aqualink] Aqualink auth failed, resetting session", exc_info=False)
        loop.run_until_complete(_reset_client())
    except Exception:
        logger.warning(
            "[aqualink] Failed to run aqualink module", exc_info=True)
        loop.run_until_complete(_reset_client())


async def _aqualink():
    points: List[Point] = []

    c = await _get_client()
    logger.info("[aqualink] Fetching Aqualink data...")
    s = await c.get_systems()

    for s in s.values():
        devices = await s.get_devices()
        for device in devices.values():
            device: AquaLinkIQPump
            motor = Point("pool_iqpump_motordata") \
                .tag("system", s.serial) \
                .tag("device", device.productId) \
                .tag("firmware", device.firmware) \
                .field("speed", device.motorSpeed) \
                .field("power", device.motorPower) \
                .field("temperature", device.motorTemperature)
            points.append(motor)

            fp = Point("pool_iqpump_freezeprotect") \
                .tag("system", s.serial) \
                .tag("device", device.productId) \
                .tag("firmware", device.firmware) \
                .field("enabled", device.freezeProtectEnable) \
                .field("status", device.freezeProtectStatus)
            points.append(fp)

    write_influx(points)

IAQUA_DEVICE_URL = "https://r-api.iaqualink.net/v2/devices/"


class I2DSystem(AqualinkSystem):
    NAME = "i2d"

    def __init__(self, aqualink: AqualinkClient, data):
        super().__init__(aqualink, data)

    def __repr__(self) -> str:
        attrs = ["name", "serial", "data"]
        attrs = ["%s=%r" % (i, getattr(self, i)) for i in attrs]
        return f'{self.__class__.__name__}({" ".join(attrs)})'

    async def update(self) -> None:
        resp = await self._send_device_request()
        data: dict = resp.json()
        if not data or "alldata" not in data:
            logger.error("[i2d] No alldata found in response")
            return

        self.devices = {
            self.serial: AquaLinkIQPump(self, data["alldata"])
        }

    async def _send_device_request(
        self,
        params: Optional[Payload] = None,
    ) -> httpx.Response:
        if not params:
            params = {}

        r = await self.aqualink._send_login_request()
        id_token = r.json()['userPoolOAuth']['IdToken']

        url = f"{IAQUA_DEVICE_URL}{self.serial}/control.json"
        headers = {
            'api_key': AQUALINK_API_KEY,
            'Authorization': id_token
        }
        headers.update(**AQUALINK_HTTP_HEADERS)
        data = json.dumps({
            "user_id": self.aqualink._user_id,
            "command": "/alldata/read"
        })
        return await self.aqualink._client.post(url, headers=headers, data=data)


class AquaLinkIQPump(AqualinkDevice):
    data: dict

    @property
    def label(self) -> str:
        return self.system.name

    @property
    def state(self) -> str:
        return self.data["runstate"]

    @property
    def name(self) -> str:
        return self.system.name

    @property
    def manufacturer(self) -> str:
        return "Jandy"

    @property
    def model(self) -> str:
        return "IQPump " + self.data["motordata"]["productid"]

    @property
    def firmware(self) -> str:
        return self.data["fwversion"]

    @property
    def productId(self) -> str:
        return self.data["motordata"]["productid"]

    @property
    def motorSpeed(self) -> int:
        return int(self.data["motordata"]["speed"])

    @property
    def motorPower(self) -> int:
        return int(self.data["motordata"]["power"])

    @property
    def motorTemperature(self) -> int:
        return int(self.data["motordata"]["temperature"])

    @property
    def horsepower(self) -> str:
        return self.data["motordata"]["horsepower"]

    @property
    def horsepowerCode(self) -> str:
        return self.data["motordata"]["horsepowerCode"]

    @property
    def freezeProtectStatus(self) -> str:
        return int(self.data["freezeprotectstatus"])

    @property
    def freezeProtectEnable(self) -> str:
        return int(self.data["freezeprotectenable"])
