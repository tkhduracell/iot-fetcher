# iot_fetcher
The `iot_fetcher` project is designed to collect and process data from various IoT devices. It provides a framework for fetching data from sensors, storing it in a database, and analyzing it to gain insights. The project aims to simplify the integration and management of IoT devices in a scalable and efficient manner.

## Features

  - Aqualink - Connects to pool pump to fetch temperature and settings
  - Airquality - Connection to Google Maps Airquality API
  - Balboa - Connects to SPA to fetch temperature and settings
  - Elpris - Fetches energy price in Sweden SE 1-4
  - Ngenic - Fetches temperature and settings from Ngenic
  - Tapo - Connects to TP-Link Tapo smart devices to fetch power state, energy usage, and device metrics
  - **VictoriaMetrics Backup** - Automated backup that streams `/api/v1/export/native` to Google Cloud Storage every 12 hours

## Environment Variables

### General Configuration
- `INFLUX_HOST` - InfluxDB server hostname/IP
- `INFLUX_TOKEN` - InfluxDB authentication token
- `INFLUX_ORG` - InfluxDB organization name
- `INFLUX_BUCKET` - InfluxDB bucket name for data storage

### Module-Specific Configuration

#### TAPO (TP-Link Smart Devices)
- `TAPO_EMAIL` - Your TP-Link/Tapo account email address
- `TAPO_PASSWORD` - Your TP-Link/Tapo account password

Run TAPO module individually for testing:
```bash
docker run --rm --env-file .env iot-fetcher:latest -- tapo
```

## VictoriaMetrics Backup & Restore

### Automated Backups
- **Schedule**: Runs automatically every 12 hours (at `:10` past the hour)
- **Storage**: Google Cloud Storage, path templated via `GOOGLE_BACKUP_URI`
- **Process**: Streams VM's `/api/v1/export/native` → gzip → GCS (single file per run)
- **Filename**: `vm-export-YYYYMMDDTHHMMSSZ.native.gz`

### Environment Variables Required
```bash
INFLUX_HOST=http://database:8181        # VictoriaMetrics HTTP endpoint
INFLUX_TOKEN=...                        # only needed if VM is behind vmauth
GOOGLE_BACKUP_URI=gcs://your-bucket/vm-backups/%Y%m%dT%H/
GOOGLE_SERVICE_ACCOUNT='{"type":"service_account",...}'
```

### Manual Backup
```bash
# Run backup manually inside the container
docker exec iot-fetcher python python/src/main.py backup_vm
```

### Restore from Backup
```bash
# Download backup from GCS
gsutil cp gs://your-bucket/vm-backups/20260422T12/vm-export-20260422T120500Z.native.gz ./vm-export.native.gz

# Stream into VM's import endpoint (ungzip on the fly)
gunzip -c vm-export.native.gz | curl -X POST \
  --data-binary @- \
  -H "Content-Type: application/octet-stream" \
  "http://database:8181/api/v1/import/native"
```

## TP-Link Tapo Integration

The Node.js service automatically discovers and monitors TP-Link Tapo smart plugs using the TP-Link cloud API.

### Configuration
Add your TP-Link credentials to your `.env` file:
```bash
TAPO_EMAIL=your-tp-link-email@example.com
TAPO_PASSWORD=your-tp-link-password
```

### Data Collected
- **Device Status**: Power on/off state, uptime
- **Signal Quality**: WiFi signal level and RSSI
- **Energy Usage**: Current power consumption, daily and monthly energy totals
- **Device Information**: Device ID, MAC address, alias, model

### Schedule
- Runs every 10 minutes
- Automatically discovers new devices
- Stores data in InfluxDB under measurement `tapo_device`

## Make setup

  - `make build` Build the container an tag it
  - `make run` Start the application inside the docker container
  - `make run MODULE=<module-name>` Start a specifc module only
  - `make push` Build and push the container
  - `make dev` Rebuild container with pydebugger installed and start application with debugger on port `5678`

