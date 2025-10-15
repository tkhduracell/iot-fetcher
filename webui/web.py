from io import StringIO
import csv
from itertools import groupby
import json
import uuid
from flask import Flask, send_from_directory, request, Response, make_response, jsonify
import requests
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
from functools import wraps

from dotenv import load_dotenv
# Load environment variables from .env file
load_dotenv()

# Optional roborock imports
try:
    from roborock.web_api import RoborockApiClient
    from roborock.cloud_api import RoborockMqttClient
except ImportError:
    RoborockApiClient = None
    RoborockMqttClient = None


class CleanLogs(logging.Filter):
    pattern: re.Pattern = re.compile(r' - - \[.+?] "')

    def filter(self, record: logging.LogRecord) -> bool:
        if "/influx/api/v2/query" in record.msg:
            return False
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
if not os.path.exists(dist_folder):
    dist_folder = os.path.dirname(dist_folder)

upload_folder = '/tmp/uploads'
os.makedirs(upload_folder, exist_ok=True)

app = Flask('webui')


@app.route('/', methods=['GET', 'HEAD'])
def index():
    return send_from_directory(dist_folder, 'index.html')

@app.route('/health', methods=['GET', 'HEAD'])
def health():
    return make_response('OK', 200)

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


@app.route('/sonos/<path:path>', methods=['GET'])
def sonos_proxy(path):
    sonos_host = os.environ.get('SONOS_HOST')
    
    if not sonos_host:
        return Response('Missing SONOS_HOST', status=500)
    
    url = f"http://{sonos_host}/{path}"
    
    # Forward the request to the Sonos API server
    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers=dict(request.headers),
            data=request.get_data(),
            params=request.args,
            cookies=request.cookies,
            allow_redirects=False,
            timeout=5,
        )
        
        # Filter out hop-by-hop headers
        excluded_headers = ['content-encoding', 'content-length', 
                           'transfer-encoding', 'connection']
        response_headers = [(name, value) for (name, value) in resp.raw.headers.items() 
                           if name.lower() not in excluded_headers]
        
        return Response(resp.content, resp.status_code, response_headers)
        
    except requests.exceptions.RequestException as e:
        logging.error("Error proxying Sonos request to %s: %s", url, e)
        return Response('Sonos API unavailable', status=502)


def run_async(f):
    """Decorator to run async functions in Flask routes"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(f(*args, **kwargs))
        finally:
            loop.close()
    return wrapper


async def get_roborock_client():
    """Create and return authenticated roborock client and home data"""
    if RoborockApiClient is None:
        raise ImportError("python-roborock package is required. Run: pip install python-roborock")
    
    username = os.environ.get('ROBOROCK_USERNAME')
    password = os.environ.get('ROBOROCK_PASSWORD')
    
    if not username or not password:
        raise ValueError("ROBOROCK_USERNAME and ROBOROCK_PASSWORD must be set")
    
    client = RoborockApiClient(username)
    user_data = await client.pass_login(password)
    home_data = await client.get_home_data(user_data)
    
    return client, home_data


@app.route('/roborock/zones', methods=['GET'])
@run_async
async def get_roborock_zones():
    """Get available cleaning zones from roborock device"""
    try:
        client, home_data = await get_roborock_client()
        
        if not home_data or not home_data.devices:
            return jsonify({"error": "No roborock devices found"}), 404
        
        # Get the first device
        device = home_data.devices[0]
        
        device_api = RoborockMqttClient(home_data.user_data, device)
        
        try:
            await device_api.async_connect()
            
            # Get room mapping and segments
            room_mapping = await device_api.send_command("get_room_mapping")
            
            zones = []
            if room_mapping and isinstance(room_mapping, dict):
                # Parse room mapping data
                segments = room_mapping.get('segments', {})
                for segment_id, segment_data in segments.items():
                    zones.append({
                        'id': segment_id,
                        'name': segment_data.get('name', f"Zone {segment_id}"),
                        'segment_id': segment_id
                    })
            
            return jsonify(zones), 200
            
        finally:
            await device_api.async_disconnect()
    
    except Exception as e:
        logging.exception("Error getting roborock zones: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route('/roborock/clean', methods=['POST'])
@run_async
async def start_roborock_clean():
    """Start cleaning on roborock device"""
    try:
        client, home_data = await get_roborock_client()
        
        if not home_data or not home_data.devices:
            return jsonify({"error": "No roborock devices found"}), 404
        
        # Get the first device
        device = home_data.devices[0]
        
        device_api = RoborockMqttClient(home_data.user_data, device)
        
        try:
            await device_api.async_connect()
            
            # Parse request data
            data = request.get_json() if request.is_json else {}
            zone_id = data.get('zone_id')
            
            if zone_id:
                # Clean specific zone/segment
                result = await device_api.send_command("app_segment_clean", [int(zone_id)])
                action = f"segment cleaning for zone {zone_id}"
            else:
                # Start full clean
                result = await device_api.send_command("app_start")
                action = "full cleaning"
            
            return jsonify({
                "success": True,
                "message": f"Started {action}",
                "device_id": device.duid,
                "result": result
            }), 200
            
        finally:
            await device_api.async_disconnect()
    
    except Exception as e:
        logging.exception("Error starting roborock clean: %s", e)
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('WEB_UI_PORT', 8080))
    logging.info(
        "Web UI port is set, starting Flask server on port %d...", port)
    app.run(host='0.0.0.0', port=port)
