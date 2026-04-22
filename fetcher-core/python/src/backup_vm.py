import gzip
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.cloud import storage

INFLUX_HOST = os.environ.get('INFLUX_HOST', '')
INFLUX_TOKEN = os.environ.get('INFLUX_TOKEN', '')
GOOGLE_BACKUP_URI = os.environ.get('GOOGLE_BACKUP_URI', '')
GOOGLE_SERVICE_ACCOUNT = os.environ.get('GOOGLE_SERVICE_ACCOUNT', '').strip('\'')

EXPORT_PATH = '/api/v1/export/native'
EXPORT_MATCH = '{__name__!=""}'
EXPORT_TIMEOUT = (30, 3600)  # (connect, read) seconds


def _setup_gcs_client() -> storage.Client:
    service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT)
    return storage.Client.from_service_account_info(service_account_info)


def _export_to_file(dest: Path) -> int:
    url = f"{INFLUX_HOST.rstrip('/')}{EXPORT_PATH}"
    headers = {}
    if INFLUX_TOKEN:
        headers['Authorization'] = f'Bearer {INFLUX_TOKEN}'

    logging.info("Streaming VM export from %s", url)
    with requests.get(
        url,
        params={'match[]': EXPORT_MATCH},
        headers=headers,
        stream=True,
        timeout=EXPORT_TIMEOUT,
    ) as resp:
        resp.raise_for_status()
        with gzip.open(dest, 'wb') as gz:
            shutil.copyfileobj(resp.raw, gz, length=1024 * 1024)

    return dest.stat().st_size


def _upload_to_gcs(local_file: Path, gcs_client: storage.Client) -> str:
    date_formatted_uri = datetime.now(timezone.utc).strftime(GOOGLE_BACKUP_URI)
    if not date_formatted_uri.startswith('gcs://'):
        raise ValueError(f"Invalid GCS URI: {date_formatted_uri}")

    uri_parts = date_formatted_uri[6:].split('/', 1)
    gcs_bucket_name = uri_parts[0]
    gcs_path = uri_parts[1] if len(uri_parts) > 1 else ''
    blob_name = f"{gcs_path}{local_file.name}".lstrip('/')

    bucket = gcs_client.bucket(gcs_bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_file))

    gcs_url = f"gs://{gcs_bucket_name}/{blob_name}"
    logging.info("Uploaded %s (%d bytes) to %s",
                 local_file.name, local_file.stat().st_size, gcs_url)
    return gcs_url


def backup_vm():
    """Export VictoriaMetrics via /api/v1/export/native and upload to GCS."""
    if not INFLUX_HOST or not GOOGLE_BACKUP_URI or not GOOGLE_SERVICE_ACCOUNT:
        logging.warning("Backup skipped: INFLUX_HOST, GOOGLE_BACKUP_URI, and GOOGLE_SERVICE_ACCOUNT are required")
        return

    logging.info("Starting VictoriaMetrics backup...")

    gcs_client = _setup_gcs_client()
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    filename = f"vm-export-{timestamp}.native.gz"

    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = Path(temp_dir) / filename
        try:
            size = _export_to_file(archive_path)
            logging.info("Export completed: %s (%d bytes)", filename, size)
            _upload_to_gcs(archive_path, gcs_client)
            logging.info("VictoriaMetrics backup completed successfully")
        except Exception as e:
            logging.error("VictoriaMetrics backup failed: %s", e)
            raise
