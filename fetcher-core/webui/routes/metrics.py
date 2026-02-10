from flask import Blueprint, request, make_response
import requests
import logging
import os

metrics_bp = Blueprint('metrics', __name__)


@metrics_bp.route('/metrics/<device>', methods=['GET', 'HEAD'])
def metrics_garmin(device: str):
    if device not in ['garmin']:
        return make_response('Device not supported', 400)

    _defs = [
        {
            "title": "🔋 Batteri",
            "unit": "%",
            "decimals": 0,
            "key": "battery_soc",
            "metric": "sigenergy_battery_soc_percent",
            "labels": {}
        },
        {
            "title": "☀️ Solceller",
            "unit": "kW",
            "decimals": 1,
            "key": "solar_power",
            "metric": "sigenergy_pv_power_power_kw",
            "labels": {"string": "total"}
        },
        {
            "title": "⚡️ Inköp",
            "unit": "kW",
            "decimals": 1,
            "key": "grid_power",
            "metric": "sigenergy_grid_power_net_power_kw",
            "labels": {}
        }
    ]

    influx_host = os.environ.get('INFLUX_HOST')
    influx_token = os.environ.get('INFLUX_TOKEN')

    if not influx_host:
        return make_response('Missing INFLUX_HOST', 500)
    if not influx_token:
        return make_response('Missing INFLUX_TOKEN', 500)

    headers = {
        'Authorization': f'Bearer {influx_token}',
        'Accept': 'application/json'
    }

    results = []

    # Execute PromQL queries via Prometheus API
    for d in _defs:
        label_selector = ','.join(
            f'{k}="{v}"' for k, v in d["labels"].items()
        )
        selector = d["metric"]
        if label_selector:
            selector += '{' + label_selector + '}'
        query = f'avg_over_time({selector}[5m])'

        try:
            resp = requests.get(
                f"{influx_host}/api/v1/query",
                params={'query': query},
                headers=headers,
                timeout=10,
            )

            if not resp.ok:
                logging.warning(
                    "PromQL query failed for %s: %s", d["key"], resp.text
                )
                continue

            data = resp.json()

            # Parse Prometheus response:
            # {"status":"success","data":{"result":[{"value":[timestamp,"42.5"]}]}}
            prom_result = data.get('data', {}).get('result', [])
            if prom_result and len(prom_result) > 0:
                value = float(prom_result[0]['value'][1])
                meta = {k: v for k, v in d.items() if k not in ['metric', 'labels']}
                results.append({**meta, 'data': value})
            else:
                logging.warning("No data returned for %s", d["key"])

        except Exception as e:
            logging.exception(
                "Error querying for %s:", d["key"])
            continue

    if not results:
        return make_response({"error": "No data available"}, 500)

    return make_response(results, 200)
