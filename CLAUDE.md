# Repository Information
- Owner: `tkhduracell`
- Repository: `iot-fetcher` 
- Full name: `tkhduracell/iot-fetcher`

# Build Instructions
- Use "make build" to build the main (iot-fetcher) docker image
- Build Docker images locally and push to registry — don't build on rpi5

# VictoriaMetrics
For anything involving metrics, PromQL, or series shape, use the `victoria-metrics` skill — it covers `scripts/vm-shape.sh`, `scripts/vm-query.sh` (metrics/labels/series/query/range, with `--format json` when you need to parse), `scripts/vm-rename.sh`, credentials, and conventions for this deployment. Don't hand-write curl against the VM API.

# Deployment (rpi5)
- The remote directory on rpi5 is `~/iot-fetcher` (hyphen, NOT underscore). The local directory uses an underscore but the remote uses a hyphen — never create `~/iot_fetcher` on rpi5.
- On rpi5, always use `sudo` and both compose files: `sudo docker compose -f docker-compose.yml -f docker-compose.local.yml up -d`
- Get IP: `ssh rpi5 'hostname -I'`
- VM (authed): port 8427
- gdrive-rag exposes its HTTP + MCP surface on port 8090 (local only).

# Grafana
- In Grafana dashboards, use `$__interval` with `spanNulls` instead of hardcoded lookback windows
- Update the Grafana dashboard via the conversion script (`convert_dashboard.py`), not by editing JSON directly