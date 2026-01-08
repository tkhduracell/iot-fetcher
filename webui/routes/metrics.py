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
        'Authorization': f'Token {influx_token}',
        'Accept': 'application/json'
    }

    results = []

    # Execute separate InfluxQL query for each metric
    for d in _defs:
        where_clause = f"time > now() - 5m"
        if d["where"]:
            where_clause += f" AND {d['where']}"

        # Build InfluxQL query: get mean over 1m windows, then take the last value
        query = f'''SELECT MEAN("{d["field"]}") AS value
                    FROM "{d["measurement"]}"
                    WHERE {where_clause}
                    GROUP BY time(1m)
                    ORDER BY time DESC
                    LIMIT 1'''

        try:
            resp = requests.get(
                f"{influx_host}/query",
                params={'db': _database, 'q': query},
                headers=headers,
                timeout=10,
            )

            if not resp.ok:
                logging.warning(
                    "InfluxQL query failed for %s: %s", d["key"], resp.text
                )
                continue

            data = resp.json()

            # Parse InfluxQL JSON response
            # Expected format: {"results": [{"series": [{"values": [[time, value]]}]}]}
            if 'results' in data and len(data['results']) > 0:
                result = data['results'][0]
                if 'series' in result and len(result['series']) > 0:
                    series = result['series'][0]
                    if 'values' in series and len(series['values']) > 0:
                        value = series['values'][0][1]  # First row, second column (value)
                        if value is not None:
                            meta = {k: v for k, v in d.items() if k not in ['measurement', 'field', 'where']}
                            results.append({**meta, 'data': value})
                        else:
                            logging.warning("Null value returned for %s", d["key"])
                    else:
                        logging.warning("No values in series for %s", d["key"])
                else:
                    logging.warning("No series in result for %s", d["key"])
            else:
                logging.warning("No results returned for %s", d["key"])

        except Exception as e:
            logging.exception(
                "Error querying influx for %s:", d["key"])
            continue

    if not results:
        return make_response({"error": "No data available"}, 500)

    return make_response(results, 200)
