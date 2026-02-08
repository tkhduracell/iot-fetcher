import logging
import requests
import os

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

BASE_URL = "https://airquality.googleapis.com/v1/currentConditions"

GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY', '')
GOOGLE_LAT_LNG = os.environ.get('GOOGLE_LAT_LNG', '')


def airquality():
    if not GOOGLE_API_KEY or not GOOGLE_LAT_LNG:
        logger.error(
            "[airquality] GOOGLE_API_KEY or GOOGLE_LAT_LNG environment variable not set, ignoring...")
        return
    try:
        _airquality()
    except Exception as e:
        logger.exception(
            f"[airquality] Failed to execute airquality module: {e}")


def _airquality():
    logger.info("[airquality] Fetching air quality data from Google API...")

    url = f"{BASE_URL}:lookup?key={GOOGLE_API_KEY}"
    logger.info(f"[airquality] Fetching air quality data from {url}...")

    try:
        [lat, lng] = GOOGLE_LAT_LNG.split(',')
        resp = requests.post(url, json={
            "languageCode": "sv",
            "universalAqi": True,
            "location": {
                "latitude": float(lat),
                "longitude": float(lng)
            },
            "extraComputations": [
                "POLLUTANT_CONCENTRATION"
            ]
        })
    except requests.exceptions.RequestException as e:
        logger.error(f"[airquality] Error making API request: {e}")
        return

    if resp.status_code != 200:
        logger.error(
            f"[airquality] Error when fetching air quality data: {
                resp.status_code} - {resp.text}"
        )
        return

    json_data = resp.json()
    points = []

    # Extract AQI data
    for index in json_data.get('indexes', []):
        points.append(Point("air_quality")
                      .field("aqi", index['aqi'])
                      .field("dominant_pollutant",
                             index['dominantPollutant'])
                      .time(json_data['dateTime']))

    # Extract pollutant concentration data
    for pollutant in json_data.get('pollutants', []):
        points.append(Point("air_quality_pollutants")
                      .tag("code", pollutant['displayName'])
                      .tag("name", pollutant['fullName'])
                      .field("concentration", pollutant['concentration']['value'])
                      .field("units", pollutant['concentration']['units'])
                      .time(json_data['dateTime']))

    write_influx(points)
