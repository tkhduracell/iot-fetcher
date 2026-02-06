from flask import Flask, send_from_directory, make_response
import logging
import os

from dotenv import load_dotenv
# Load environment variables from .env file
load_dotenv()

# Setup logging
from lib.logging_config import setup_logging
setup_logging()

# Import route blueprints
from routes.sonos import sonos_bp
from routes.upload import upload_bp
from routes.roborock import roborock_bp
from routes.metrics import metrics_bp
from routes.influx import influx_bp

dist_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dist')
if not os.path.exists(dist_folder):
    dist_folder = os.path.dirname(dist_folder)

app = Flask('webui')

# Register blueprints
app.register_blueprint(sonos_bp)
app.register_blueprint(upload_bp)
app.register_blueprint(roborock_bp)
app.register_blueprint(metrics_bp)
app.register_blueprint(influx_bp)


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


if __name__ == '__main__':
    port = int(os.environ.get('WEB_UI_PORT', 8080))
    logging.info(
        "Web UI port is set, starting Flask server on port %d...", port)
    app.run(host='0.0.0.0', port=port)
