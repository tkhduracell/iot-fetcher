#!/bin/sh
set -e

# Create credentials directory and ensure proper ownership
mkdir -p /home/influxdb3/.influxdb3
chown -R influxdb3:influxdb3 /home/influxdb3/.influxdb3

# Write GCS credentials to file if GOOGLE_SERVICE_ACCOUNT_JSON is set
if [ -n "$GOOGLE_SERVICE_ACCOUNT_JSON" ]; then
    printf '%s\n' "$GOOGLE_SERVICE_ACCOUNT_JSON" > /home/influxdb3/.influxdb3/gcs-credentials.json
    chown influxdb3:influxdb3 /home/influxdb3/.influxdb3/gcs-credentials.json
    echo "GCS credentials written to /home/influxdb3/.influxdb3/gcs-credentials.json"
fi

# Setup admin token if provided - MUST be JSON format for InfluxDB v3 Core
if [ -n "$INFLUXDB3_ADMIN_TOKEN" ]; then
    TOKEN_FILE="/home/influxdb3/.influxdb3/admin-token"
    # Calculate expiry (1 year from now in milliseconds)
    EXPIRY_MS=$(($(date +%s) * 1000 + 31536000000))
    # Write JSON format required by InfluxDB v3 Core
    # Token must have apiv3_ prefix for compatibility
    cat > "$TOKEN_FILE" <<EOF
{
  "token": "apiv3_${INFLUXDB3_ADMIN_TOKEN}",
  "name": "_admin",
  "expiry_millis": ${EXPIRY_MS}
}
EOF
    chown influxdb3:influxdb3 "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    echo "Admin token configured (JSON format with apiv3_ prefix)"
fi

echo "Starting InfluxDB v3 Core as influxdb3 user..."

# Use gosu to drop privileges and run as influxdb3 user
exec gosu influxdb3 "$@"
