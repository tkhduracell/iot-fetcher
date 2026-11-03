"""Read an archive from stdin and put it in GCS. Runs inside iot-fetcher.

Fed to ``python -c`` by backup-memory.sh, since that container holds the library
and the credential and this repo's scripts are not in its image. Mirrors
fetcher-core/python/src/backup_vm.py, including the success metric, so the two
backups look alike from the outside.
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import storage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main(uri_template: str, name: str, size: int) -> None:
    uri = datetime.now(UTC).strftime(uri_template)
    if not uri.startswith("gcs://"):
        raise ValueError(f"Invalid GCS URI: {uri}")
    bucket, _, prefix = uri[len("gcs://") :].partition("/")
    started = time.monotonic()

    # Through a file, for two reasons: the client's resumable upload seeks in its
    # source, which a pipe cannot do, and a short read must be caught here. An
    # upload that simply reached EOF early would publish a truncated archive as
    # though it were last night's good one.
    with tempfile.TemporaryDirectory() as directory:
        local = Path(directory) / name
        with local.open("wb") as out:
            shutil.copyfileobj(sys.stdin.buffer, out)
        received = local.stat().st_size
        if received != size:
            raise ValueError(f"expected {size} bytes on stdin, got {received}")

        credentials = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"].strip("'"))
        client = storage.Client.from_service_account_info(credentials)
        blob = client.bucket(bucket).blob(f"{prefix}{name}".lstrip("/"))
        # The name has the time in it, so an object already there is a bug, and
        # this credential has no business replacing a snapshot.
        blob.upload_from_filename(
            str(local), content_type="application/gzip", if_generation_match=0
        )
    logging.info("Uploaded %s (%d bytes) to gs://%s/%s", name, size, bucket, blob.name)
    _emit_success_metric(size, time.monotonic() - started)


def _emit_success_metric(size_bytes: int, duration_seconds: float) -> None:
    """ai_brain_memory_backup_last_success_timestamp, beside vm_backup's own.

    The container's own write path. A backup that quietly stopped looks healthy
    until the day it is needed, so this is what a panel or an alert can watch. A
    failure to write it does not fail a backup that succeeded.
    """
    try:
        sys.path.insert(0, "python/src")
        from influx import Point, write_influx

        write_influx(
            [
                Point("ai_brain_memory_backup")
                .tag("destination", "gcs")
                .field("last_success_timestamp", float(time.time()))
                .field("size_bytes", int(size_bytes))
                .field("duration_seconds", float(duration_seconds))
            ]
        )
    except Exception as exc:
        logging.warning("Failed to write the backup success metric: %s", exc)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]))
