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
- **eth0 = 192.168.68.87 is the only uplink.** `eth0-failover.service` (`scripts/rpi5/eth0-failover.sh`, install with `scripts/rpi5/install-eth0-failover.sh`) keeps the Wi-Fi radio **off** while eth0 is healthy. It turns Wi-Fi on (NETGEAR58, DHCP, last seen as .94) only after eth0 has been down for 5 min, and off again once eth0 has been back for 60 s. Address things at .87 or at a name — never pin anything to the Wi-Fi address. Its log: `journalctl -u eth0-failover`.
- **If `ssh rpi5` hangs, check eth0's carrier before assuming a network config problem.** A dead patch cable takes eth0 down entirely, leaving .87 owned by nobody (`(incomplete)` in ARP) — so it looks like an addressing bug and isn't. For the first 5 min of an eth0 outage the box has no network at all (the Wi-Fi radio is off). After that eth0-failover brings Wi-Fi up, and `raspberrypi5.local` (mDNS) is the only name that can find it, though see the Deco caveat below. Once you're in, `cat /sys/class/net/eth0/carrier` → `1` means the cable is fine.
- ARP flux between the two interfaces is mitigated by `arp_ignore=1` / `arp_announce=2` in `/etc/sysctl.d/99-arp-flux.conf` (applied 2026-09-06), which makes each interface answer only for its own address. Note the Deco mesh proxy-ARPs for the Pi, so the Wi-Fi address may still resolve to eth0's MAC from the LAN — during the 2026-11-08 failover test the Pi joined Wi-Fi as .94 but was not reachable over SSH there from the Mac. Don't assume Wi-Fi is an independent way in.
- Get IP: `ssh filip@raspberrypi5.local 'hostname -I'` (first address is the LAN one; the rest are docker bridges)
- The `scripts/vm-*.sh` helpers talk to VM through the external proxy, which is not always reachable when the LAN address has moved. Fallback that works from the box itself: `ssh filip@raspberrypi5.local "curl -s -H 'Authorization: Bearer $INFLUX_TOKEN' 'http://localhost:8427/api/v1/...'"`.
- VM (authed): port 8427
- gdrive-rag exposes its HTTP + MCP surface on port 8090 (local only).

# Fetchers (fetcher-core/python)
- `main.py` wraps every scheduled job in `with_timeout(...)`, and each module has a blanket `try/except` that logs and swallows. **A broken fetcher therefore looks completely healthy from the outside** — the container stays up, the scheduler keeps ticking, other fetchers keep writing, and the only symptom is an empty series. Grep the logs for the module tag (e.g. `[aquatemp]`) rather than trusting container health.
- Cloud APIs here return `objectResult: null` rather than omitting the key, so `.get('objectResult', [])` does **not** protect you — the default only applies when the key is absent. Use `.get('objectResult') or []`.
- The AquaTemp pool pump is a **shared** device: `deviceList` returns `[]` and it only appears via `getMyAppectDeviceShareDataList`. On shared devices `deviceNickName`, `custModel`, `deviceName` and `model` are all null, and `nickName` holds the **account's email address** — never fall back to it for a metric tag. Use `deviceCode` as the identity.

# Spa (Balboa, Utö)
- To read the spa directly when HA/rpi5 are down, use `scripts/balboa-probe.py`. It talks to the BP2100G0 WiFi module at `192.168.68.53:4257` over the BWA protocol and doesn't need the cloud, HA or the Pi. Run it with no args to see the usage; `status <ip>` is the usual starting point.
- The module accepts **one TCP client at a time**. When HA's Balboa integration holds the socket, the probe fails to connect. That means the socket is taken, not that the spa is offline.
- `discover` uses UDP broadcast, so it only works on the same LAN (not over VPN). Use `scan` from elsewhere.
- Only temps, heat mode/state, range and clock are verified. The pump and circulation fields are guesses, so confirm them against HA before building on them.
- The script only listens. `info` sends read-only queries, and nothing sends control frames. Keep it that way.

# Grafana
- In Grafana dashboards, use `$__interval` with `spanNulls` instead of hardcoded lookback windows
- Update the Grafana dashboard via the conversion script (`convert_dashboard.py`), not by editing JSON directly

# CI / GitHub Actions
- To suppress actionlint warnings, use `.github/actionlint.yaml` with an `ignore:` pattern — inline comments like `# actionlint:ignore:rule` don't work