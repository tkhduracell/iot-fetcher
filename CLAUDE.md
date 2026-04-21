# Repository Information
- Owner: `tkhduracell`
- Repository: `iot-fetcher` 
- Full name: `tkhduracell/iot-fetcher`

# Build Instructions
- Use "make build" to build the main (iot-fetcher) docker image
- Build Docker images locally and push to registry — don't build on rpi5

# VictoriaMetrics
**When you need to know what metrics exist or what shape they have, run `scripts/vm-shape.sh [pattern]` first — don't hand-write curl.** It resolves the repo root (worktree-safe), loads creds from `fetcher-core/python/.env.local`, and prints a Markdown table of metric names, label keys with cardinality, and the latest sample value/timestamp. Use a substring filter (e.g. `scripts/vm-shape.sh tibber`) to keep the output small.

**For PromQL queries (instant or range), use `scripts/vm-query.sh`** — `vm-query.sh metrics|labels|label <name>|query <promql>|range <promql>`. Don't reinvent these with curl.

Raw API is only for things the scripts above don't cover. Credentials: `INFLUX_HOST` (full URL incl. scheme) and `INFLUX_TOKEN` in `fetcher-core/python/.env.local`; auth header `Authorization: Bearer $INFLUX_TOKEN`. Endpoints: `/api/v1/label/__name__/values`, `/api/v1/labels`, `/api/v1/label/<name>/values`, `/api/v1/query`, `/api/v1/query_range`.

# Deployment (rpi5)
- The remote directory on rpi5 is `~/iot-fetcher` (hyphen, NOT underscore). The local directory uses an underscore but the remote uses a hyphen — never create `~/iot_fetcher` on rpi5.
- On rpi5, always use `sudo` and both compose files: `sudo docker compose -f docker-compose.yml -f docker-compose.local.yml up -d`
- Get IP: `ssh rpi5 'hostname -I'`
- VM (authed): port 8427

# Grafana
- In Grafana dashboards, use `$__interval` with `spanNulls` instead of hardcoded lookback windows
- Update the Grafana dashboard via the conversion script (`convert_dashboard.py`), not by editing JSON directly