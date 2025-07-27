
import asyncio
import json
import logging
import os
from typing import List, Optional

from aiohttp import Payload
import httpx
from iaqualink.client import AqualinkClient, AQUALINK_HTTP_HEADERS
from iaqualink.system import AqualinkSystem
from iaqualink.device import AqualinkDevice
from iaqualink.const import (
    AQUALINK_API_KEY,
)

from influx import write_influx, Point

aqualink_username = os.environ['AQUALINK_USERNAME'] or ''
aqualink_password = os.environ['AQUALINK_PASSWORD'] or ''


def aqualink():
    try:
        asyncio.run(_aqualink())
    except:
        logging.waring("Failed to run aqualink module", exc_info=True)


async def _aqualink():
    logging.info("Fetching Aqualink data...")

    async with AqualinkClient(aqualink_username, aqualink_password) as c:
        points: List[Point] = []
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
