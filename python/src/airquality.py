import logging
import requests
import os

from influx import write_influx, Point, WritePrecision

BASE_URL = "https://airquality.googleapis.com/v1/currentConditions"

GOOGLE_API_KEY = os.environ['GOOGLE_API_KEY']
GOOGLE_LAT_LNG = os.environ['GOOGLE_LAT_LNG']


def airquality():
    try:
        _airquality()
    except Exception as e:
        logging.exception(f"Failed to execute airquality module: {e}")


def _airquality():
    logging.info("Fetching air quality data from Google API...")

    url = f"{BASE_URL}:lookup?key={GOOGLE_API_KEY}"
    logging.info(f"Fetching air quality data from {url}...")

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
        logging.error(f"Error making API request: {e}")
        return

    if resp.status_code != 200:
        logging.error(
            f"Error when fetching air quality data: {
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
                      .time(json_data['dateTime'],
                            write_precision=WritePrecision.S))

    # Extract pollutant concentration data
    for pollutant in json_data.get('pollutants', []):
        points.append(Point("air_quality_pollutants")
                      .tag("code", pollutant['displayName'])
                      .tag("name", pollutant['fullName'])
                      .field("concentration", pollutant['concentration']['value'])
                      .field("units", pollutant['concentration']['units'])
                      .time(json_data['dateTime'],
                            write_precision=WritePrecision.S))

    write_influx(points)
