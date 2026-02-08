from flask import Blueprint, request, Response
import requests
import logging
import os

sonos_bp = Blueprint('sonos', __name__)


@sonos_bp.route('/sonos/<path:path>', methods=['GET'])
def sonos_proxy(path):
    sonos_host = os.environ.get('SONOS_HOST')

    if not sonos_host:
        # Return empty list for zones endpoint, empty response for others
        # This prevents 500 errors in isolated test environments (e.g., Playwright)
        if path == 'zones':
            return Response('[]', status=200, mimetype='application/json')
        return Response('{}', status=200, mimetype='application/json')

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
