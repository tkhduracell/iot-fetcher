import logging
import os
from typing import List
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# InfluxDB configuration
influx_host = os.environ['INFLUX_HOST'] or "192.168.67.52:6666"
influx_token = os.environ['INFLUX_TOKEN'] or ''
influx_org = os.environ['INFLUX_ORG'] or 'home'
influx_bucket = os.environ['INFLUX_BUCKET'] or 'irisgatan'


def write_influx(points: List[Point]):
    # Create InfluxDB client
    logging.info("Connecting to InfluxDB...")
    influx_client = InfluxDBClient(
        url=f"http://{influx_host}", token=influx_token, debug=False)

    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    # Write data to InfluxDB
    logging.info("Writing points to InfluxDB... %s",
                 ', '.join(map(lambda x: x._name, points)))
    try:
        write_api.write(bucket=influx_bucket,
                        org=influx_org,
                        record=points,
                        write_precision=WritePrecision.S)
    except Exception as e:
        logging.warning("Unable to write the values: %s %s",
                        ', '.join(map(lambda x: str(x), points)), e)
