#!/usr/bin/env bash
# Install/update eth0-failover on rpi5 from this machine.
# Usage: scripts/rpi5/install-eth0-failover.sh [ssh-host]   (default: filip@192.168.68.87)
set -euo pipefail
HOST="${1:-filip@192.168.68.87}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

scp "$DIR/eth0-failover.sh" "$DIR/eth0-failover.service" "$HOST:/tmp/"
ssh "$HOST" 'sudo install -m 755 /tmp/eth0-failover.sh /usr/local/bin/eth0-failover.sh \
  && sudo install -m 644 /tmp/eth0-failover.service /etc/systemd/system/eth0-failover.service \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable --now eth0-failover.service \
  && sudo systemctl restart eth0-failover.service \
  && sleep 3 && systemctl --no-pager status eth0-failover.service | head -12'
