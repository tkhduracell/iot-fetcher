from flask import Blueprint, request, send_from_directory, make_response
import uuid
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

upload_bp = Blueprint('upload', __name__)

upload_folder = '/tmp/uploads'
os.makedirs(upload_folder, exist_ok=True)


@upload_bp.route('/upload', methods=['POST'])
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


@upload_bp.route('/upload/<uuid>', methods=['GET', 'HEAD'])
def uploads(uuid: str):
    return send_from_directory(upload_folder, uuid)
