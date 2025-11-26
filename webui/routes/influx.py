from flask import Blueprint, request, Response
import requests
import os

influx_bp = Blueprint('influx', __name__)


@influx_bp.route('/influx/api/v2/<route>', methods=['POST', 'GET'])
def influx_proxy(route):
    if route not in ['query', 'health']:
        return Response('Not authorized', status=403)

    influx_host = os.environ.get('INFLUX_HOST')
    influx_token = os.environ.get('INFLUX_TOKEN')
    if not influx_host or not influx_token:
        # Return empty response for isolated test environments (e.g., Playwright)
        # This prevents 500 errors when InfluxDB is not configured
        if route == 'health':
            return Response('{"status":"pass"}', status=200, mimetype='application/json')
        # Return empty CSV for query endpoint (no data rows)
        return Response('', status=200, mimetype='text/csv')
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
