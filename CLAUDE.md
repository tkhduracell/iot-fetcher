# Repository Information
- Owner: `tkhduracell`
- Repository: `iot-fetcher` 
- Full name: `tkhduracell/iot-fetcher`

# Build Instructions
- Use "make build" to build the main (iot-fetcher) docker image
- Build Docker images locally and push to registry — don't build on rpi5

# VictoriaMetrics
- Credentials live in `fetcher-core/python/.env.local` — `INFLUX_HOST` (full URL incl. scheme, e.g. `http://host:port`) and `INFLUX_TOKEN`.
- Auth header: `Authorization: Bearer $INFLUX_TOKEN`
- List all metric names: `curl -s "$INFLUX_HOST/api/v1/label/__name__/values" -H "Authorization: Bearer $INFLUX_TOKEN"`
- List label names: `curl -s "$INFLUX_HOST/api/v1/labels" -H "Authorization: Bearer $INFLUX_TOKEN"`
- List values for a label: `curl -s "$INFLUX_HOST/api/v1/label/<label>/values" -H "Authorization: Bearer $INFLUX_TOKEN"`
- Query a metric: `curl -s "$INFLUX_HOST/api/v1/query?query=<metric_name>" -H "Authorization: Bearer $INFLUX_TOKEN"`
- For a per-metric shape summary (label keys + cardinality + latest sample), run `scripts/vm-shape.sh [pattern]` — resolves the repo root (worktree-safe), reads `.env.local`, and prints a Markdown table to stdout.

# Deployment (rpi5)
- The remote directory on rpi5 is `~/iot-fetcher` (hyphen, NOT underscore). The local directory uses an underscore but the remote uses a hyphen — never create `~/iot_fetcher` on rpi5.
- On rpi5, always use `sudo` and both compose files: `sudo docker compose -f docker-compose.yml -f docker-compose.local.yml up -d`
- Get IP: `ssh rpi5 'hostname -I'`
- VM (authed): port 8427

# Grafana
- In Grafana dashboards, use `$__interval` with `spanNulls` instead of hardcoded lookback windows
- Update the Grafana dashboard via the conversion script (`convert_dashboard.py`), not by editing JSON directly