import logging
import os
from typing import List

from influxdb_client.client.influxdb_client import InfluxDBClient
from influxdb_client.client.write.point import Point
from influxdb_client.client.write_api import SYNCHRONOUS, ASYNCHRONOUS
from influxdb_client.domain.write_precision import WritePrecision

# InfluxDB configuration
influx_host = os.environ['INFLUX_HOST'] or "192.168.67.52:6666"
influx_token = os.environ['INFLUX_TOKEN'] or ''
influx_org = os.environ['INFLUX_ORG'] or 'home'
influx_bucket = os.environ['INFLUX_BUCKET'] or 'irisgatan'


def write_influx(points: List[Point]):
    logging.debug("Connecting to InfluxDB...")
    influx_client = InfluxDBClient(
        url=f"http://{influx_host}", token=influx_token, debug=False)

    write_api = influx_client.write_api(write_options=ASYNCHRONOUS)

    if len(points) > 4:
        logging.info("Writing points to InfluxDB... %s (and %d more)",
                     ', '.join(map(lambda x: f"{x._name}", points[0:3])), len(points)-3)
    else:
        logging.info("Writing points to InfluxDB... %s",
                     ', '.join(map(lambda x: f"{x._name} ({len(x._fields)} fields, {len(x._tags)} tags)", points)))

    try:
        write_api.write(bucket=influx_bucket,
                        org=influx_org,
                        record=points,
                        write_precision=WritePrecision.S)
    except Exception as e:
        logging.warning("Unable to write the values: %s %s",
                        ', '.join(map(lambda x: str(x), points)), e)
