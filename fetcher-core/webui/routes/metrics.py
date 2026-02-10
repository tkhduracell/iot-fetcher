from flask import Blueprint, request, make_response
import requests
import logging
import os

metrics_bp = Blueprint('metrics', __name__)


@metrics_bp.route('/metrics/<device>', methods=['GET', 'HEAD'])
def metrics_garmin(device: str):
    if device not in ['garmin']:
        return make_response('Device not supported', 400)

    _database = "irisgatan"
    _defs = [
        {
            "title": "🔋 Batteri",
            "unit": "%",
            "decimals": 0,
            "key": "battery_soc",
            "measurement": "sigenergy_battery",
            "field": "soc_percent",
            "where": ""
        },
        {
            "title": "☀️ Solceller",
            "unit": "kW",
            "decimals": 1,
            "key": "solar_power",
            "measurement": "sigenergy_pv_power",
            "field": "power_kw",
            "where": "string = 'total'"
        },
        {
            "title": "⚡️ Inköp",
            "unit": "kW",
            "decimals": 1,
            "key": "grid_power",
            "measurement": "sigenergy_grid_power",
            "field": "net_power_kw",
            "where": ""
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

    # Execute separate SQL queries for each metric
    for d in _defs:
        where_clause = "time >= now() - INTERVAL '5 minutes'"
        if d["where"]:
            where_clause += f" AND {d['where']}"

        query = f'''SELECT AVG("{d["field"]}") AS value
                    FROM "{d["measurement"]}"
                    WHERE {where_clause}'''

        try:
            resp = requests.get(
                f"{influx_host}/api/v3/query_sql",
                params={'db': _database, 'q': query, 'format': 'json'},
                headers=headers,
                timeout=10,
            )

            if not resp.ok:
                logging.warning(
                    "SQL query failed for %s: %s", d["key"], resp.text
                )
                continue

            data = resp.json()

            # Parse v3 SQL JSON response: [{"value": 42.5}]
            if data and len(data) > 0 and data[0].get('value') is not None:
                meta = {k: v for k, v in d.items() if k not in ['measurement', 'field', 'where']}
                results.append({**meta, 'data': data[0]['value']})
            else:
                logging.warning("No data returned for %s", d["key"])

        except Exception as e:
            logging.exception(
                "Error querying influx for %s:", d["key"])
            continue

    if not results:
        return make_response({"error": "No data available"}, 500)

    return make_response(results, 200)
