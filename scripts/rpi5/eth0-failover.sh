#!/usr/bin/env bash
# eth0-first network failover for rpi5.
#
# eth0 is the only uplink while it's healthy; the Wi-Fi radio stays off.
# If eth0 is unhealthy for DOWN_GRACE seconds, Wi-Fi is switched on and
# WIFI_CON is brought up. Once eth0 is healthy again for UP_GRACE seconds,
# Wi-Fi is switched off.
#
# "Healthy" = carrier up, NetworkManager reports eth0 connected, and the
# default gateway answers a ping sent out of eth0.
#
# Installed by scripts/rpi5/install-eth0-failover.sh; runs as eth0-failover.service.
set -u

IFACE="${IFACE:-eth0}"
WIFI_CON="${WIFI_CON:-netplan-wlan0-NETGEAR58}"
DOWN_GRACE="${DOWN_GRACE:-300}"
UP_GRACE="${UP_GRACE:-60}"
INTERVAL="${INTERVAL:-10}"

log() { echo "$*"; }

gateway() {
  ip -4 route show default | awk '{print $3; exit}'
}

eth_healthy() {
  [[ "$(cat "/sys/class/net/$IFACE/carrier" 2>/dev/null)" == "1" ]] || return 1
  nmcli -t -g GENERAL.STATE device show "$IFACE" 2>/dev/null | grep -q '^100' || return 1
  local gw
  gw="$(gateway)"
  [[ -n "$gw" ]] || return 1
  ping -I "$IFACE" -c 1 -W 2 "$gw" >/dev/null 2>&1 || ping -I "$IFACE" -c 1 -W 2 "$gw" >/dev/null 2>&1
}

wifi_on() {
  [[ "$(nmcli radio wifi)" == "enabled" ]]
}

enable_wifi() {
  log "eth0 down for >= ${DOWN_GRACE}s - enabling Wi-Fi and connecting $WIFI_CON"
  nmcli radio wifi on
  sleep 5
  nmcli connection up "$WIFI_CON" || log "warning: nmcli connection up $WIFI_CON failed; NM autoconnect will retry"
}

disable_wifi() {
  log "eth0 healthy for >= ${UP_GRACE}s - disabling Wi-Fi"
  nmcli radio wifi off
}

down_since=""
up_since=""
last_state=""

log "starting: iface=$IFACE wifi_con=$WIFI_CON down_grace=${DOWN_GRACE}s up_grace=${UP_GRACE}s"

while true; do
  now="$(date +%s)"
  if eth_healthy; then
    state=up
    down_since=""
    [[ -n "$up_since" ]] || up_since="$now"
    if wifi_on && (( now - up_since >= UP_GRACE )); then
      disable_wifi
    fi
  else
    state=down
    up_since=""
    [[ -n "$down_since" ]] || down_since="$now"
    if ! wifi_on && (( now - down_since >= DOWN_GRACE )); then
      enable_wifi
    fi
  fi
  if [[ "$state" != "$last_state" ]]; then
    log "eth0 is $state (wifi radio: $(nmcli radio wifi))"
    last_state="$state"
  fi
  sleep "$INTERVAL"
done
