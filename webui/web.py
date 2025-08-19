from io import StringIO
import csv
from itertools import groupby
import json
import uuid
from flask import Flask, send_from_directory, request, Response, make_response
import requests
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
# Load environment variables from .env file
load_dotenv()


class CleanLogs(logging.Filter):
    pattern: re.Pattern = re.compile(r' - - \[.+?] "')

    def filter(self, record: logging.LogRecord) -> bool:
        record.name = record.name.replace("werkzeug", "http")\
            .replace("root", os.path.basename(__file__))\
        record.name = (
            record.name.replace("werkzeug", "http")
                       .replace("root", os.path.basename(__file__))
                       .replace(".py", "")
        )
        record.msg = self.pattern.sub(' - "', record.msg)
        return True


logging.basicConfig(
    level=logging.INFO, 
    format='%(levelname)s [%(name)s] %(message)s'
)

# Requests logging
wlog = logging.getLogger('werkzeug')
wlog.setLevel(logging.INFO)
wlog.addFilter(CleanLogs())

rlog = logging.getLogger('root')
rlog.setLevel(logging.INFO)
rlog.addFilter(CleanLogs())

dist_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dist')

upload_folder = '/tmp/uploads'
os.makedirs(upload_folder, exist_ok=True)

app = Flask('webui')


@app.route('/', methods=['GET', 'HEAD'])
def index():
    return send_from_directory(dist_folder, 'index.html')


@app.route('/assets/<path:filename>', methods=['GET', 'HEAD'])
def assets(filename):
    assets_folder = os.path.join(dist_folder, 'assets')
    return send_from_directory(assets_folder, filename)


@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return 'No file part', 400
    file = request.files['file']

    hhmm = datetime.now().strftime("%H%M")
    uuid_name = str(uuid.uuid4()) + "-" + hhmm

    filepath = os.path.join(upload_folder, uuid_name)
    file.save(filepath)

    created_at = datetime.now(tz=ZoneInfo('Europe/Stockholm'))
    size_mb = round(os.path.getsize(filepath) / (1024 * 1024), 2)
    logging.info(f'File saved to {filepath} at {created_at} of {size_mb} MB')
    return make_response(
        {"name": uuid_name, "created_at": created_at.isoformat(),
         "size_mb": size_mb}, 200
    )


@app.route('/upload/<uuid>', methods=['GET', 'HEAD'])
def uploads(uuid: str):
    return send_from_directory(upload_folder, uuid)


@app.route('/metrics/<device>', methods=['GET', 'HEAD'])
def metrics_garmin(device: str):
    if device not in ['garmin']:
        return make_response('Device not supported', 400)

    _bucket = "irisgatan"
    _defs = [
        {"title": "🔋 Batteri", "unit": "%", "decimals": 0, "key": "battery_soc",
            "flux": 'filter(fn: (r) => r._measurement == "sigenergy_battery" and r._field == "soc_percent") |> last()'},
        {"title": "☀️ Solceller", "unit": "kW", "decimals": 1, "key": "solar_power",
            "flux": 'filter(fn: (r) => r._measurement == "sigenergy_pv_power" and r._field == "power_kw" and r.string == "total") |> last()'},
        {"title": "⚡️ Inköp", "unit": "kW", "decimals": 1, "key": "grid_power",
            "flux": 'filter(fn: (r) => r._measurement == "sigenergy_grid_power" and r._field == "net_power_kw") |> last()'}
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
    query = f'data = from(bucket: "{_bucket}") |> range(start: -1h)\n'

    for d in _defs:
        query += f'  data_{d["key"]} = data |> {d["flux"]} |> yield(name: "{d["key"]}")\n'

    results = []
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

        grouped = groupby(lines, key=lambda item: '_value' in item)
        grouped = [[next(group), list(next(grouped)[1])]
                   for is_header, group in grouped if is_header]

        for header, data in grouped:
            header: str
            data: list[str]
            value_idx = header.split(',').index('_value')

            if value_idx != -1:
                data_values = list(
                    map(lambda x: float(x.split(",")[value_idx]), data)).pop()
                meta = _defs[results.__len__()]
                del meta["flux"]
                results.append({**meta, 'data': data_values})
            else:
                logging.warning(
                    "_value column not found in response for header: %s", header)

    except Exception as e:
        logging.exception(
            "Error querying influx for %s:", e)

    return make_response(results, 200)


@app.route('/influx/api/v2/<route>', methods=['POST', 'GET'])
def influx_proxy(route):
    if route not in ['query', 'health']:
        return Response('Not authorized', status=403)

    influx_host = os.environ.get('INFLUX_HOST')
    influx_token = os.environ.get('INFLUX_TOKEN')
    if not influx_host or not influx_token:
        return Response('Missing INFLUX_HOST or INFLUX_TOKEN', status=500)
    if route == 'health':
        url = f"http://{influx_host}/{route}"
    else:
        url = f"http://{influx_host}/api/v2/{route}"
    headers = dict(request.headers)
    headers['Authorization'] = f"Token {influx_token}"
    resp = requests.request(
        method=request.method,
        url=url,
        headers=headers,
        data=request.get_data(),
        params=request.args,
        cookies=request.cookies,
        allow_redirects=False,
    )
    excluded_headers = ['content-encoding',
                        'content-length', 'transfer-encoding', 'connection']
    response_headers = [(name, value) for (
        name, value) in resp.raw.headers.items() if name.lower() not in excluded_headers]
    return Response(resp.content, resp.status_code, response_headers)


if __name__ == '__main__':
    port = int(os.environ.get('WEB_UI_PORT', 8080))
    logging.info(
        "Web UI port is set, starting Flask server on port %d...", port)
    app.run(host='0.0.0.0', port=port)
