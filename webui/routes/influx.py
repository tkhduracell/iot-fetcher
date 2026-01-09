from flask import Blueprint, request, Response
import requests
import os

influx_bp = Blueprint('influx', __name__)

# Define version-specific allowed routes
ALLOWED_ROUTES = {
    'v2': ['query', 'health'],
    'v3': ['query_sql', 'query_influxql', 'health']
}


def _fallback_response(version, route):
    """Fallback responses for test environments without InfluxDB configured"""
    if route == 'health':
        return Response('{"status":"pass"}', status=200, mimetype='application/json')
    if version == 'v2' and route == 'query':
        # Return empty CSV for v2 query endpoint (no data rows)
        return Response('', status=200, mimetype='text/csv')
    if version == 'v3' and route in ['query_sql', 'query_influxql']:
        # Return empty JSON array for v3 query endpoints
        return Response('[]', status=200, mimetype='application/json')
    # Default fallback
    return Response('', status=200)


@influx_bp.route('/influx/api/<version>/<route>', methods=['POST', 'GET'])
def influx_proxy(version, route):
    # Validate API version
    if version not in ['v2', 'v3']:
        return Response('Unsupported API version', status=400)

    # Check if route is whitelisted for this version
    if route not in ALLOWED_ROUTES.get(version, []):
        return Response('Not authorized', status=403)

    influx_host = os.environ.get('INFLUX_HOST')
    influx_token = os.environ.get('INFLUX_TOKEN')
    if not influx_host or not influx_token:
        # Return fallback response for isolated test environments (e.g., Playwright)
        # This prevents 500 errors when InfluxDB is not configured
        return _fallback_response(version, route)

    # Construct URL - health endpoint is at root level for both versions
    if route == 'health':
        url = f"http://{influx_host}/health"
    else:
        url = f"http://{influx_host}/api/{version}/{route}"

    # Prepare headers with version-specific authentication
    headers = dict(request.headers)
    if version == 'v2':
        headers['Authorization'] = f"Token {influx_token}"
    elif version == 'v3':
        headers['Authorization'] = f"Bearer {influx_token}"

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
