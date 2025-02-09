import logging
from typing import List, TypedDict
from datetime import datetime, timedelta
import requests

from influx import write_influx, Point, WritePrecision

areas = map(lambda i: f"SE{i}", range(1, 5))


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
        logging.exception("Failed to execute elpris module")


def _elpris():
    logging.info("Fetching energy prices from Elpriset justnu...")
    points = []
    for area in areas:
        for i in range(-2, 1):
            url = get_elpris_price_url(area=area, day_offset=i)
            logging.info(f"Fetching energy prices from {url}...")
            resp = requests.get(url)

            if (resp.status_code != 200):
                logging.info("Error when fetching energy prices: " + str(resp))
                continue

            json: List[dict] = resp.json()

            values = [EnergyData(p) for p in json]

            areapoints = [Point("energy_price")
                          .tag("area", area)
                          .field("SEK_per_kWh", float(p['SEK_per_kWh']))
                          .field("100th_SEK_per_kWh", round(p['SEK_per_kWh'] * 100))
                          .field("EUR_per_kWh", float(p['EUR_per_kWh']))
                          .time(p['time_start'], write_precision=WritePrecision.S)
                          for p in values]
            points.extend(areapoints)

    write_influx(points)
