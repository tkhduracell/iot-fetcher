from flask import Blueprint, request, make_response
import requests
import logging
import os
from itertools import groupby

metrics_bp = Blueprint('metrics', __name__)


@metrics_bp.route('/metrics/<device>', methods=['GET', 'HEAD'])
def metrics_garmin(device: str):
    if device not in ['garmin']:
        return make_response('Device not supported', 400)

    _bucket = "irisgatan"
    _defs = [
        {"title": "🔋 Batteri", "unit": "%", "decimals": 0, "key": "battery_soc",
            "flux": 'filter(fn: (r) => r._measurement == "sigenergy_battery" and r._field == "soc_percent")'},
        {"title": "☀️ Solceller", "unit": "kW", "decimals": 1, "key": "solar_power",
            "flux": 'filter(fn: (r) => r._measurement == "sigenergy_pv_power" and r._field == "power_kw" and r.string == "total")'},
        {"title": "⚡️ Inköp", "unit": "kW", "decimals": 1, "key": "grid_power",
            "flux": 'filter(fn: (r) => r._measurement == "sigenergy_grid_power" and r._field == "net_power_kw")'}
    ]

    influx_host = os.environ.get('INFLUX_HOST')

    if not influx_host:
        return make_response('Missing INFLUX_HOST', 500)

    headers = {
        'Authorization': request.headers.get('Authorization'),
        'Content-Type': 'application/vnd.flux',
        'Accept': 'application/json'
    }

    # Combine the query into one.
    query = f'data = from(bucket: "{_bucket}") |> range(start: -5m)\n'

    for d in _defs:
        query += f'  data_{d["key"]} = data |> {d["flux"]} |> aggregateWindow(every: 1m, fn: mean, createEmpty: false) |> last() |> yield(name: "{d["key"]}")\n'

    try:
        resp = requests.post(
            f"http://{influx_host}/api/v2/query",
            params={'org': 'home'},
            headers=headers,
            data=query,
            timeout=10,
        )

        if not resp.ok:
            logging.warning(
                "Influx query failed: %s", resp.text
            )

        text = resp.text

        # Parse CSV response: skip comment lines starting with '#', first non-comment row is header
        lines = [l for l in text.splitlines() if l and not l.startswith('#')]
        if len(lines) < 2:
            logging.warning(
                "No data rows in query response: %s", text)
            return make_response(results, 500)

        # Group the lines by whether they are headers or data rows
        grouped = groupby(lines, key=lambda item: '_value' in item)
        grouped = [[next(group), list(next(grouped)[1])]
                   for is_header, group in grouped if is_header]

        # Parse results series
        results_value = {}
        for header, data in grouped:
            header: str
            data: list[str]
            value_idx = header.split(',').index('_value')
            result_name_idx = header.split(',').index('result')

            if value_idx > -1:

                for r in data:
                    row = r.split(",")
                    result_name = row[result_name_idx]
                    value = float(row[value_idx])

                    if result_name not in results_value:
                        results_value[result_name] = []

                    results_value[result_name].append(value)

        # Reconstruct results with data
        results = []
        for d in _defs:
            if d["key"] in results_value:
                meta = {**d}
                del meta["flux"]
                results.append({ **meta, 'data': sum(results_value[d["key"]]) })
            else:
                logging.warning(
                    "_value column not found in response for header: %s", header)

        return make_response(results, 200)

    except Exception as e:
        logging.exception(
            "Error querying influx for %s:", e)
        return make_response({"error": "Error when querying InfluxDB"}, 500)
