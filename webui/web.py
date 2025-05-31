import os
import logging
import requests

from flask import Flask, send_from_directory, request, Response

logging.basicConfig(level=logging.INFO)

dist_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dist')

app = Flask('webui')
@app.route('/')
def index():
    return send_from_directory(dist_folder,'index.html')

@app.route('/assets/<path:filename>')
def assets(filename):
    assets_folder = os.path.join(dist_folder, 'assets')
    return send_from_directory(assets_folder, filename)

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
    logging.info("Web UI port is set, starting Flask server on port {port}...")
    app.run(host='0.0.0.0', port=port)


