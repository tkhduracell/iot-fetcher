# Self-Hosted GitHub Actions Runner on rpi5

Plan for turning the existing `rpi5` host into a self-hosted GitHub Actions runner for `tkhduracell/iot-fetcher`, isolated from the production iot-fetcher stack that already runs there.

## Goal

Give the repo a self-hosted runner that:
- Can run Docker containers in CI workflows (for building `iot-fetcher` images, etc.)
- Cannot see, modify, stop, or exec into the production containers running on the same host
- Has hard CPU/memory ceilings so a runaway workflow cannot starve the production stack

## Assumptions (verified 2026-04-21)

- Host: Raspberry Pi 5 Model B Rev 1.0, 8 GB RAM, 4 cores (ARM64)
- OS: Raspberry Pi OS bookworm, cgroup v2 unified, controllers: `cpuset cpu io memory pids`
- Storage: SD card (`/dev/mmcblk0p2`), 117 GB, ~48% used
- Docker: root daemon already in use by `sudo docker compose -f docker-compose.yml -f docker-compose.local.yml`
- Current memory use: ~3.4 GB of 7.9 GB
- Repo: `tkhduracell/iot-fetcher`
- Deploy dir on rpi5: `~/iot-fetcher` (hyphen, per project CLAUDE.md)

## Risks acknowledged (not blockers)

- **SD-card wear** — CI pulls/builds write a lot; card will degrade faster. Acceptable for a hobby setup.
- **Thermal throttling** — Pi 5 under sustained build load will throttle without active cooling; cgroup CPU limits become secondary.
- **Public workflows / forked PRs** — never auto-run workflows from forks on a self-hosted runner. Require approval or scope to `push` / internal PRs only.

## Security model

Two layers of isolation:

1. **Rootless Docker under a dedicated user (`ghrunner`)**
   Each user runs its own Docker daemon in its own user namespace. The `ghrunner` daemon has zero visibility into the root daemon's containers, networks, or volumes. A workflow calling `docker ps` sees only its own workloads.

2. **systemd user slice with hard resource limits**
   The runner service is pinned to `user-<UID>.slice` via the `Slice=` directive. Cgroup limits on that slice cap the runner, its dockerd, and every workflow container it spawns as one aggregate.

Why not just add `ghrunner` to the `docker` group? Because that grants access to the **root** Docker socket, which is root-equivalent on the host — the runner could `docker exec` into any production container. Rootless avoids that entirely.

## Resource budget (8 GB / 4 cores)

| Resource | Limit | Reasoning |
|---|---|---|
| `CPUQuota` | `200%` | 2 of 4 cores, leaves 2 for iot-fetcher + HA |
| `MemoryMax` | `3G` | Hard cap; iot-fetcher ~2 GB today with growth room, OS ~1 GB |
| `MemoryHigh` | `2.5G` | Soft throttle before OOM |
| `TasksMax` | `4096` | Fork-bomb protection |
| `IOWeight` | `50` | Half default; iot-fetcher wins disk contention |

## Pre-flight checks

Run before starting, confirm output matches expectation.

```bash
# RAM (expect 7.9Gi on 8GB model)
ssh rpi5 'free -h'

# cgroup v2 controllers (must include memory + cpu)
ssh rpi5 'cat /sys/fs/cgroup/cgroup.controllers'

# No existing ghrunner user (expect "no such user")
ssh rpi5 'id ghrunner' || echo "OK — user does not exist yet"

# gh CLI authed with admin on the repo
gh auth status
gh api repos/tkhduracell/iot-fetcher --jq .permissions
```

If cgroup controllers are missing `memory`, add to `/boot/firmware/cmdline.txt`:
`cgroup_memory=1 cgroup_enable=memory` and reboot.

## Install — step by step

Each block is independently verifiable. Stop and diagnose if any fails.

### Block 1 — create user, install rootless prerequisites

```bash
ssh rpi5 '
  sudo adduser --disabled-password --gecos "" ghrunner &&
  sudo loginctl enable-linger ghrunner &&
  sudo apt update &&
  sudo apt install -y uidmap dbus-user-session fuse-overlayfs slirp4netns jq curl
'
```

Verify:
```bash
ssh rpi5 'id ghrunner && loginctl show-user ghrunner | grep Linger'
# expect: Linger=yes
```

If subuid/subgid are not auto-assigned (check `grep ghrunner /etc/subuid /etc/subgid`), run:
```bash
ssh rpi5 'sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 ghrunner'
```

### Block 2 — install rootless Docker under ghrunner

```bash
ssh rpi5 'sudo -iu ghrunner bash -lc "
  curl -fsSL https://get.docker.com/rootless | sh &&
  systemctl --user enable --now docker &&
  systemctl --user status docker --no-pager | head -20
"'
```

Verify:
```bash
ssh rpi5 'sudo -iu ghrunner docker run --rm hello-world'
ssh rpi5 'sudo -iu ghrunner docker info --format "{{.SecurityOptions}}"'
# expect: includes "name=rootless"
```

Confirm isolation from root Docker:
```bash
ssh rpi5 'sudo docker ps --format "{{.Names}}"'          # production containers
ssh rpi5 'sudo -iu ghrunner docker ps --format "{{.Names}}"'   # should be empty
```

### Block 3 — apply resource limits to the ghrunner slice

```bash
ssh rpi5 'UID_R=$(id -u ghrunner); \
  sudo mkdir -p /etc/systemd/system/user-${UID_R}.slice.d && \
  sudo tee /etc/systemd/system/user-${UID_R}.slice.d/limits.conf >/dev/null <<EOF
[Slice]
CPUAccounting=true
CPUQuota=200%
MemoryAccounting=true
MemoryMax=3G
MemoryHigh=2.5G
TasksMax=4096
IOAccounting=true
IOWeight=50
EOF
  sudo systemctl daemon-reload && \
  sudo systemctl restart user-${UID_R}.slice'
```

Verify:
```bash
ssh rpi5 'UID_R=$(id -u ghrunner); systemctl show user-${UID_R}.slice \
  -p CPUQuotaPerSecUSec -p MemoryMax -p MemoryHigh -p TasksMax -p IOWeight'
```

### Block 4 — register and install the runner

Fetch a registration token (single-use, ~1 hour validity):

```bash
TOKEN=$(gh api -X POST repos/tkhduracell/iot-fetcher/actions/runners/registration-token --jq .token)
```

Download, configure, and install as a system service pinned to ghrunner's slice:

```bash
ssh rpi5 "sudo -iu ghrunner bash -lc '
  mkdir -p ~/actions-runner && cd ~/actions-runner &&
  LATEST=\$(curl -s https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | sed s/^v//) &&
  curl -o runner.tar.gz -L https://github.com/actions/runner/releases/download/v\${LATEST}/actions-runner-linux-arm64-\${LATEST}.tar.gz &&
  tar xzf runner.tar.gz &&
  ./config.sh \
    --url https://github.com/tkhduracell/iot-fetcher \
    --token $TOKEN \
    --name rpi5 \
    --labels self-hosted,linux,arm64,rpi5 \
    --unattended
'"

ssh rpi5 'UID_R=$(id -u ghrunner); \
  cd /home/ghrunner/actions-runner && \
  sudo ./svc.sh install ghrunner && \
  SVC=$(ls /etc/systemd/system/actions.runner.*.service | head -1 | xargs -n1 basename) && \
  sudo mkdir -p /etc/systemd/system/${SVC}.d && \
  sudo tee /etc/systemd/system/${SVC}.d/env.conf >/dev/null <<EOF
[Service]
Slice=user-${UID_R}.slice
Environment=DOCKER_HOST=unix:///run/user/${UID_R}/docker.sock
Environment=PATH=/home/ghrunner/bin:/usr/local/bin:/usr/bin:/bin
EOF
  sudo systemctl daemon-reload && \
  sudo ./svc.sh start && \
  sudo ./svc.sh status'
```

Two load-bearing lines in the drop-in:
- `Slice=user-<UID>.slice` — forces the system service into the user slice so the cgroup limits from Block 3 actually apply.
- `Environment=DOCKER_HOST=...` — tells the runner where rootless dockerd is listening.

## End-to-end verification

```bash
# Runner appears online in GitHub
gh api repos/tkhduracell/iot-fetcher/actions/runners \
  --jq '.runners[] | {name, status, busy, labels: [.labels[].name]}'

# Slice assignment is correct
ssh rpi5 'systemctl show actions.runner.*.service -p Slice -p MainPID'

# Cgroup limits are live
ssh rpi5 'systemd-cgtop -n1 --order=memory | head -20'

# Production containers still running
ssh rpi5 'sudo docker ps'
```

Finally, trigger a test workflow that runs `docker run --rm alpine echo hello` on `runs-on: [self-hosted, rpi5]` and confirm:
- The job picks up on rpi5
- `docker ps` inside the job shows only the job's own containers
- `docker ps` from the root daemon still shows iot-fetcher

## Using the runner in workflows

```yaml
jobs:
  build-on-rpi5:
    runs-on: [self-hosted, linux, arm64, rpi5]
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t iot-fetcher:test .
```

**Do not** use `runs-on: self-hosted` alone — label-match all four to avoid accidentally routing foreign workflows here.

## Hardening — recommended but optional

- **Fork-PR safety**: in repo settings → Actions → "Fork pull request workflows" set to *Require approval for all outside collaborators*. Never enable `pull_request_target` on untrusted inputs.
- **Network**: if workflows don't need LAN access, block ghrunner's outbound to RFC1918 via nftables owner match:
  ```
  nft add rule inet filter output meta skuid 1001 ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } drop
  ```
- **Secrets hygiene**: avoid storing repo/org secrets that grant access to prod iot-fetcher unless the job genuinely needs them. Prefer ephemeral tokens.
- **Ephemeral runner mode**: add `--ephemeral` to `./config.sh` if jobs should get a clean runner per run (tradeoff: re-register each time, more setup overhead).

## Maintenance

### Update the runner

```bash
ssh rpi5 'cd /home/ghrunner/actions-runner && sudo ./svc.sh stop'

TOKEN=$(gh api -X POST repos/tkhduracell/iot-fetcher/actions/runners/registration-token --jq .token)

ssh rpi5 "sudo -iu ghrunner bash -lc '
  cd ~/actions-runner &&
  ./config.sh remove --token $TOKEN &&
  LATEST=\$(curl -s https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | sed s/^v//) &&
  curl -o runner.tar.gz -L https://github.com/actions/runner/releases/download/v\${LATEST}/actions-runner-linux-arm64-\${LATEST}.tar.gz &&
  tar xzf runner.tar.gz --overwrite &&
  ./config.sh --url https://github.com/tkhduracell/iot-fetcher --token $TOKEN --name rpi5 --labels self-hosted,linux,arm64,rpi5 --unattended
'"

ssh rpi5 'cd /home/ghrunner/actions-runner && sudo ./svc.sh start'
```

### Remove the runner entirely

```bash
TOKEN=$(gh api -X POST repos/tkhduracell/iot-fetcher/actions/runners/remove-token --jq .token)

ssh rpi5 'cd /home/ghrunner/actions-runner && sudo ./svc.sh stop && sudo ./svc.sh uninstall'
ssh rpi5 "sudo -iu ghrunner bash -lc 'cd ~/actions-runner && ./config.sh remove --token $TOKEN'"
ssh rpi5 'sudo -iu ghrunner bash -lc "systemctl --user stop docker && systemctl --user disable docker"'
ssh rpi5 'sudo loginctl disable-linger ghrunner && sudo deluser --remove-home ghrunner'
ssh rpi5 'UID_R=1001; sudo rm -rf /etc/systemd/system/user-${UID_R}.slice.d /etc/systemd/system/actions.runner.*'
ssh rpi5 'sudo systemctl daemon-reload'
```

### Watch live resource use

```bash
ssh rpi5 'systemd-cgtop --order=memory'
ssh rpi5 'journalctl -u actions.runner.* -f'
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Block 2 fails with "subuid not found" | No subordinate IDs | `sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 ghrunner` |
| Rootless dockerd won't start, logs mention cgroup delegation | Memory controller not delegated to user | Add drop-in `/etc/systemd/system/user@.service.d/delegate.conf` with `Delegate=cpu cpuset io memory pids` |
| Runner fails to find docker | `DOCKER_HOST` env not set in service | Verify drop-in at `actions.runner.*.service.d/env.conf`, `systemctl daemon-reload`, restart |
| Cgroup limits not enforced | `Slice=` directive not applied | `systemctl show actions.runner.*.service -p Slice` — must show `user-<UID>.slice` |
| Workflow stalls with no logs | OOM killed | `dmesg -T \| grep -i kill`, lower `MemoryMax` trigger, or bump limit |
| Runner goes offline after reboot | Linger disabled | `sudo loginctl enable-linger ghrunner`, reboot once more |

## References

- GitHub runner releases: https://github.com/actions/runner/releases
- Rootless Docker install: https://docs.docker.com/engine/security/rootless/
- systemd resource control: `man systemd.resource-control`
- This repo's CLAUDE.md for rpi5 deploy conventions
