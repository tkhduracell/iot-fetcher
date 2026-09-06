# Repository Information
- Owner: `tkhduracell`
- Repository: `iot-fetcher` 
- Full name: `tkhduracell/iot-fetcher`

# Build Instructions
- Use "make build" to build the main (iot-fetcher) docker image
- Build Docker images locally and push to registry — don't build on rpi5
- **CI already builds and pushes most images — check before building by hand.** A push to `main` touching `fetcher-core/**` runs `.github/workflows/build-fetcher-core.yml`, which builds linux/amd64+arm64 and pushes `iot-fetcher:latest` to the registry. Same pattern for `ai-assistant`, `https-proxy`, `pool-pump-planner`, `sigenergy-bridge`. After a merge, deploying is just pull + `up -d` on rpi5; the "build locally" rule above is for out-of-band builds, not the post-merge path.

# VictoriaMetrics
For anything involving metrics, PromQL, or series shape, use the `victoria-metrics` skill — it covers `scripts/vm-shape.sh`, `scripts/vm-query.sh` (metrics/labels/series/query/range, with `--format json` when you need to parse), `scripts/vm-rename.sh`, credentials, and conventions for this deployment. Don't hand-write curl against the VM API.

- **`vm-shape.sh`'s "last seen" column cannot detect staleness.** It reports the *query evaluation* timestamp, not the sample's own timestamp, so an instant query returns `now` for every series still inside the staleness window — every metric reads as 0m old, including dead ones. To find genuinely stale data use `time() - timestamp(last_over_time(<metric>[7d]))` with a matching `--lookback`, per metric.
- A metric that is **absent from the catalog entirely** looks identical to a healthy one in a Grafana panel (both render empty). When a panel is blank, first check the metric name actually exists: `vm-query.sh metrics | grep <name>`.
- The `victoria-metrics` skill references a `health` skill for freshness checks — it does not exist in `.claude/skills/`.

# Deployment (rpi5)
- The remote directory on rpi5 is `~/iot-fetcher` (hyphen, NOT underscore). The local directory uses an underscore but the remote uses a hyphen — never create `~/iot_fetcher` on rpi5.
- On rpi5, always use `sudo` and both compose files: `sudo docker compose -f docker-compose.yml -f docker-compose.local.yml up -d`
- The box is dual-homed on one subnet: **eth0 = 192.168.68.87 is primary** (wired, route metric 100) and wlan0 = 192.168.68.95 is the secondary (metric 600). Address things at .87 or at a name — never pin anything to .95, which forces traffic over Wi-Fi.
- **If `ssh rpi5` hangs, check eth0's carrier before assuming a network config problem.** A dead patch cable takes eth0 down entirely, leaving .87 owned by nobody (`(incomplete)` in ARP) while the box stays up on Wi-Fi — so it looks like an addressing bug and isn't. `ssh filip@raspberrypi5.local 'cat /sys/class/net/eth0/carrier'` → `1` means the cable is fine. `raspberrypi5.local` resolves via mDNS to whichever interface is up, so it's the reliable way in when eth0 is down.
- ARP flux between the two interfaces is mitigated by `arp_ignore=1` / `arp_announce=2` in `/etc/sysctl.d/99-arp-flux.conf` (applied 2026-09-06), which makes each interface answer only for its own address. Note the Deco mesh proxy-ARPs for the Pi, so .95 may still resolve to eth0's MAC from the LAN — don't assume .95 is an independent path without testing it.
- Get IP: `ssh filip@raspberrypi5.local 'hostname -I'` (first address is the LAN one; the rest are docker bridges)
- The `scripts/vm-*.sh` helpers talk to VM through the external proxy, which is not always reachable when the LAN address has moved. Fallback that works from the box itself: `ssh filip@raspberrypi5.local "curl -s -H 'Authorization: Bearer $INFLUX_TOKEN' 'http://localhost:8427/api/v1/...'"`.
- VM (authed): port 8427
- gdrive-rag exposes its HTTP + MCP surface on port 8090 (local only).

# Fetchers (fetcher-core/python)
- `main.py` wraps every scheduled job in `with_timeout(...)`, and each module has a blanket `try/except` that logs and swallows. **A broken fetcher therefore looks completely healthy from the outside** — the container stays up, the scheduler keeps ticking, other fetchers keep writing, and the only symptom is an empty series. Grep the logs for the module tag (e.g. `[aquatemp]`) rather than trusting container health.
- Cloud APIs here return `objectResult: null` rather than omitting the key, so `.get('objectResult', [])` does **not** protect you — the default only applies when the key is absent. Use `.get('objectResult') or []`.
- The AquaTemp pool pump is a **shared** device: `deviceList` returns `[]` and it only appears via `getMyAppectDeviceShareDataList`. On shared devices `deviceNickName`, `custModel`, `deviceName` and `model` are all null, and `nickName` holds the **account's email address** — never fall back to it for a metric tag. Use `deviceCode` as the identity.

# Grafana
- In Grafana dashboards, use `$__interval` with `spanNulls` instead of hardcoded lookback windows
- Update the Grafana dashboard via the conversion script (`convert_dashboard.py`), not by editing JSON directly

# CI / GitHub Actions
- To suppress actionlint warnings, use `.github/actionlint.yaml` with an `ignore:` pattern — inline comments like `# actionlint:ignore:rule` don't work