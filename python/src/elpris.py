import logging
from typing import List, TypedDict
from datetime import datetime, timedelta
import requests

from influx import write_influx, Point, WritePrecision

# Configure module-specific logger
logger = logging.getLogger(__name__)

areas = ["SE4"]


class EnergyData(TypedDict):
    SEK_per_kWh: float
    EUR_per_kWh: float
    EXR: float
    time_start: str
    time_end: str


def get_elpris_price_url(area: str, day_offset: int):
    now = datetime.now() + timedelta(days=day_offset)
    yyyy = now.strftime("%Y")
    mm = now.strftime("%m")
    dd = (now + timedelta(days=1 if now.hour >= 12 else 0)).strftime("%d")
    baseUrl = 'https://www.elprisetjustnu.se/api/v1/prices'
    return f"{baseUrl}/{yyyy}/{mm}-{dd}_{area}.json"


def elpris():
    try:
        _elpris()
    except:
        logger.exception("[elpris] Failed to execute elpris module")


def _elpris():
    logger.info("[elpris] Fetching energy prices from Elpriset justnu...")

    for area in areas:
        for i in range(-7, 1):
            url = get_elpris_price_url(area=area, day_offset=i)
            logger.info(f"[elpris] Fetching energy prices from {url}...")
            resp = requests.get(url)

            if (resp.status_code != 200):
                logger.info(
                    "[elpris] Error when fetching energy prices: " + str(resp))
                continue

            json: List[EnergyData] = resp.json()

            values = [EnergyData(p) for p in json]

            points = [Point("energy_price")
                      .tag("area", area)
                      .field("SEK_per_kWh", float(p['SEK_per_kWh']))
                      .field("100th_SEK_per_kWh", round(p['SEK_per_kWh'] * 100))
                      .field("EUR_per_kWh", float(p['EUR_per_kWh']))
                      .time(p['time_start'], write_precision=WritePrecision.S)
                      for p in values]
            write_influx(points)
