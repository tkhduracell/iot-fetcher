import logging
import os
from typing import Dict, List, Optional, Tuple

import requests
from influxdb_client_3 import InfluxDBClient3, Point

# InfluxDB v3 Cloud configuration
influx_host = os.environ.get('INFLUX_HOST', '')
influx_token = os.environ.get('INFLUX_TOKEN', '')
influx_database = os.environ.get('INFLUX_DATABASE', 'irisgatan')

# VictoriaMetrics Prometheus-compatible read endpoint
vm_query_url = os.environ.get('INFLUXDB_V3_URL', '')
vm_query_token = os.environ.get('INFLUXDB_V3_ACCESS_TOKEN', '') or influx_token


def write_influx(points: List[Point]):
    if not influx_host or not influx_token:
        logging.error("INFLUX_HOST and INFLUX_TOKEN must be configured for v3 Cloud")
        return

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


def _vm_base_url() -> str:
    if vm_query_url:
        return vm_query_url.rstrip('/')
    if influx_host:
        return f"https://{influx_host}" if '://' not in influx_host else influx_host.rstrip('/')
    return ''


def query_prom_instant(promql: str) -> List[Dict]:
    """Execute a PromQL instant query against VictoriaMetrics. Returns the 'result' list."""
    base = _vm_base_url()
    if not base:
        logging.error("VictoriaMetrics query URL not configured (INFLUXDB_V3_URL)")
        return []
    resp = requests.get(
        f"{base}/api/v1/query",
        params={'query': promql},
        headers={'Authorization': f'Bearer {vm_query_token}'} if vm_query_token else {},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get('data', {}).get('result', [])


def query_prom_range(promql: str, start: float, end: float, step: int = 3600) -> List[Dict]:
    """Execute a PromQL range query. start/end are unix epoch seconds, step in seconds."""
    base = _vm_base_url()
    if not base:
        logging.error("VictoriaMetrics query URL not configured (INFLUXDB_V3_URL)")
        return []
    resp = requests.get(
        f"{base}/api/v1/query_range",
        params={'query': promql, 'start': start, 'end': end, 'step': step},
        headers={'Authorization': f'Bearer {vm_query_token}'} if vm_query_token else {},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get('data', {}).get('result', [])


def first_series_values(result: List[Dict]) -> List[Tuple[float, float]]:
    """Extract [(ts, value), ...] from the first series in a Prom range result."""
    if not result:
        return []
    values = result[0].get('values') or ([result[0]['value']] if 'value' in result[0] else [])
    out: List[Tuple[float, float]] = []
    for ts, v in values:
        try:
            out.append((float(ts), float(v)))
        except (TypeError, ValueError):
            continue
    return out
