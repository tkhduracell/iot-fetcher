import logging
import os
from typing import List

from influxdb_client_3 import InfluxDBClient3, Point, write_client_options
from influxdb_client_3.write_client.client.write_api import ASYNCHRONOUS

# InfluxDB v3 Cloud configuration
influx_host = os.environ.get('INFLUX_HOST') or "192.168.67.52:6666"
influx_token = os.environ.get('INFLUX_TOKEN') or ''
influx_database = os.environ.get('INFLUX_DATABASE') or 'irisgatan'


def write_influx(points: List[Point]):
    logging.debug("Connecting to InfluxDB v3 Cloud...")

    client = InfluxDBClient3(
        host=influx_host,
        token=influx_token,
        database=influx_database
    )

    if len(points) > 4:
        logging.info("Writing points to InfluxDB... %s (and %d more)",
                     ', '.join(map(lambda x: f"{x._name}", points[0:3])), len(points)-3)
    else:
        logging.info("Writing points to InfluxDB... %s",
                     ', '.join(map(lambda x: f"{x._name} ({len(x._fields)} fields, {len(x._tags)} tags)", points)))

    try:
        client.write(record=points, write_precision='s')
    except Exception as e:
        logging.warning("Unable to write the values: %s %s",
                        ', '.join(map(lambda x: str(x), points)), e)
