import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from google.cloud import storage
from typing import List

# Configuration from environment variables
# NOTE: This backup script is for v2 only and will be rewritten for v3 Cloud in Phase 7
INFLUX_HOST = os.environ.get('INFLUX_HOST', '')
INFLUX_TOKEN = os.environ.get('INFLUX_TOKEN', '')
INFLUX_ORG = os.environ.get('INFLUX_ORG', '')  # v2 only - not needed for v3
GOOGLE_BACKUP_URI = os.environ.get('GOOGLE_BACKUP_URI', '')
GOOGLE_SERVICE_ACCOUNT = os.environ.get('GOOGLE_SERVICE_ACCOUNT', '').strip('\'')


def setup_gcs_client():
    """Initialize GCS client with service account credentials."""
    try:
        service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT)
        client = storage.Client.from_service_account_info(service_account_info)
        return client
    except Exception as e:
        logging.error(f"Failed to setup GCS client: {e}")
        raise


def get_buckets() -> List[str]:
    """Get list of all buckets in the organization."""
    try:
        result = subprocess.run([
            'influx', 'bucket', 'list',
            '--host', f'http://{INFLUX_HOST}',
            '--token', INFLUX_TOKEN,
            '--org', INFLUX_ORG,
            '--json'
        ], capture_output=True, text=True, check=True)
        
        buckets_data = json.loads(result.stdout)
        bucket_names = [bucket['name'] for bucket in buckets_data if bucket['name'] != '_monitoring' and bucket['name'] != '_tasks']
        logging.info(f"Found {len(bucket_names)} buckets to backup: {', '.join(bucket_names)}")
        return bucket_names
        
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to list buckets: {e.stderr}")
        raise
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse bucket list JSON: {e}")
        raise


def backup_bucket(bucket_name: str, backup_dir: Path) -> Path:
    """Backup a single bucket using influx backup command."""
    bucket_backup_dir = backup_dir / f"backup_{bucket_name}"
    bucket_backup_dir.mkdir(exist_ok=True)
    
    try:
        logging.info(f"Backing up bucket: {bucket_name}")
        subprocess.run([
            'influx', 'backup',
            str(bucket_backup_dir),
            '--host', f'http://{INFLUX_HOST}',
            '--token', INFLUX_TOKEN,
            '--org', INFLUX_ORG,
            '--bucket', bucket_name
        ], check=True, capture_output=True, text=True)
        
        # Create compressed archive
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        archive_name = f"{bucket_name}_{timestamp}.tar.gz"
        archive_path = backup_dir / archive_name
        
        subprocess.run([
            'tar', '-czf', str(archive_path),
            '-C', str(backup_dir),
            f"backup_{bucket_name}"
        ], check=True, capture_output=True)
        
        logging.info(f"Created compressed backup: {archive_name}")
        return archive_path
        
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to backup bucket {bucket_name}: {e.stderr}")
        raise


def upload_to_gcs(local_file: Path, gcs_client: storage.Client):
    """Upload backup file to Google Cloud Storage."""
    try:
        # Parse GCS URI and apply date formatting
        date_formatted_uri = datetime.now().strftime(GOOGLE_BACKUP_URI)
        
        if not date_formatted_uri.startswith('gcs://'):
            raise ValueError(f"Invalid GCS URI: {date_formatted_uri}")
            
        # Extract bucket and path from URI
        uri_parts = date_formatted_uri[6:].split('/', 1)  # Remove 'gcs://'
        gcs_bucket_name = uri_parts[0]
        gcs_path = uri_parts[1] if len(uri_parts) > 1 else ''
        
        # Construct full object path
        blob_name = f"{gcs_path}{local_file.name}".lstrip('/')
        
        bucket = gcs_client.bucket(gcs_bucket_name)
        blob = bucket.blob(blob_name)
        
        logging.info(f"Uploading {local_file.name} to gs://{gcs_bucket_name}/{blob_name}")
        blob.upload_from_filename(str(local_file))
        
        logging.info(f"Successfully uploaded: gs://{gcs_bucket_name}/{blob_name}")
        
    except Exception as e:
        logging.error(f"Failed to upload {local_file.name} to GCS: {e}")
        raise


def backup_influx():
    """Main backup function to backup all InfluxDB buckets to GCS."""
    # Skip backup if v2-specific env vars are missing (v3 Cloud migration in progress)
    if not INFLUX_ORG or not GOOGLE_BACKUP_URI:
        logging.warning("Backup skipped: v2 backup requires INFLUX_ORG and GOOGLE_BACKUP_URI (will be rewritten for v3 Cloud)")
        return

    logging.info("Starting InfluxDB backup process...")

    try:
        # Setup GCS client
        gcs_client = setup_gcs_client()
        
        # Get list of buckets
        bucket_names = get_buckets()
        
        if not bucket_names:
            logging.warning("No buckets found to backup")
            return
            
        # Create temporary directory for backups
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_dir = Path(temp_dir)
            successful_backups = 0
            
            for bucket_name in bucket_names:
                try:
                    # Backup bucket
                    archive_path = backup_bucket(bucket_name, backup_dir)
                    
                    # Upload to GCS
                    upload_to_gcs(archive_path, gcs_client)
                    
                    # Remove local archive to save space
                    archive_path.unlink()
                    successful_backups += 1
                    
                except Exception as e:
                    logging.error(f"Failed to backup bucket {bucket_name}: {e}")
                    continue
            
            logging.info(f"Backup process completed. Successfully backed up {successful_backups}/{len(bucket_names)} buckets")
            
    except Exception as e:
        logging.error(f"Backup process failed: {e}")
        raise