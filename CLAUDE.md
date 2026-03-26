# Repository Information
- Owner: `tkhduracell`
- Repository: `iot-fetcher` 
- Full name: `tkhduracell/iot-fetcher`

# Build Instructions
- Use "make build" to build the main (iot-fetcher) docker image
- Build Docker images locally and push to registry — don't build on rpi5

# VictoriaMetrics
- Tokens and credentials (InfluxDB/VictoriaMetrics, Grafana, etc.) are in `fetcher-core/python/.env`
- The metrics backend is VictoriaMetrics, accessible at `https://$INFLUXDB_V3_URL`
- Auth: `Authorization: Bearer $INFLUXDB_V3_ACCESS_TOKEN`
- List all metric names: `curl -s "https://$INFLUXDB_V3_URL/api/v1/label/__name__/values" -H "Authorization: Bearer $INFLUXDB_V3_ACCESS_TOKEN"`
- List label names: `curl -s "https://$INFLUXDB_V3_URL/api/v1/labels" -H "Authorization: Bearer $INFLUXDB_V3_ACCESS_TOKEN"`
- List values for a label: `curl -s "https://$INFLUXDB_V3_URL/api/v1/label/<label>/values" -H "Authorization: Bearer $INFLUXDB_V3_ACCESS_TOKEN"`
- Query a metric: `curl -s "https://$INFLUXDB_V3_URL/api/v1/query?query=<metric_name>" -H "Authorization: Bearer $INFLUXDB_V3_ACCESS_TOKEN"`

# Deployment (rpi5)
- On rpi5, always use `sudo` and both compose files: `sudo docker compose -f docker-compose.yml -f docker-compose.local.yml up -d`
- Get IP: `ssh rpi5 'hostname -I'`
- VM (authed): port 8427

# Grafana
- In Grafana dashboards, use `$__interval` with `spanNulls` instead of hardcoded lookback windows
- Update the Grafana dashboard via the conversion script (`convert_dashboard.py`), not by editing JSON directly