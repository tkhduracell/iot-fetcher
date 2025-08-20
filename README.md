# iot_fetcher
The `iot_fetcher` project is designed to collect and process data from various IoT devices. It provides a framework for fetching data from sensors, storing it in a database, and analyzing it to gain insights. The project aims to simplify the integration and management of IoT devices in a scalable and efficient manner.

## Features

  - Aqualink - Connects to pool pump to fetch temperature and settings
  - Airquality - Connection to Google Maps Airquality API
  - Balboa - Connects to SPA to fetch temperature and settings
  - Elpris - Fetches energy price in Sweden SE 1-4
  - Ngenic - Fetches temperature and settings from Ngenic
  - **InfluxDB Backup** - Automated backup system that exports all buckets to Google Cloud Storage every 12 hours

## InfluxDB Backup & Restore

### Automated Backups
- **Schedule**: Runs automatically every 12 hours
- **Storage**: Google Cloud Storage with date-based folders (`backup/YYYY-MM-DD/`)
- **Process**: Exports all buckets sequentially, compresses with gzip, uploads to GCS
- **Memory optimized**: Processes one bucket at a time to minimize memory usage

### Environment Variables Required
```bash
GOOGLE_BACKUP_URI=gcs://your-bucket/backup/%Y-%m-%d/
GOOGLE_SERVICE_ACCOUNT='{"type":"service_account",...}'
```

### Manual Backup
```bash
# Run backup manually inside the container
docker exec iot-fetcher python python/src/main.py backup_influx
```

### Restore from Backup
```bash
# Download backup from GCS (on host machine)
gsutil cp gs://your-bucket/backup/2025-08-20/bucket_name_timestamp.tar.gz ./backup.tar.gz

# Extract backup locally
tar -xzf backup.tar.gz

# Copy extracted backup into container
docker cp ./backup_bucket_name iot-fetcher:/tmp/backup_bucket_name

# Restore inside the container using influx CLI
docker exec iot-fetcher influx restore /tmp/backup_bucket_name --org your-org --bucket target-bucket
```

## Make setup

  - `make build` Build the container an tag it
  - `make run` Start the application inside the docker container
  - `make run MODULE=<module-name>` Start a specifc module only
  - `make push` Build and push the container
  - `make dev` Rebuild container with pydebugger installed and start application with debugger on port `5678`

