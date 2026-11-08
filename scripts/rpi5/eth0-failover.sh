#!/usr/bin/env bash
# eth0-first network failover for rpi5.
#
# eth0 is the only uplink while it's healthy; the Wi-Fi radio stays off.
# If eth0 is unhealthy for DOWN_GRACE seconds, Wi-Fi is switched on and
# WIFI_CON is brought up. Once eth0 is healthy again for UP_GRACE seconds,
# Wi-Fi is switched off.
#
# "Healthy" = carrier up, NetworkManager reports eth0 connected, and eth0's
# default gateway answers a ping sent out of eth0.
#
# Installed by scripts/rpi5/install-eth0-failover.sh; runs as eth0-failover.service.
set -u

IFACE="${IFACE:-eth0}"
WIFI_CON="${WIFI_CON:-netplan-wlan0-NETGEAR58}"
DOWN_GRACE="${DOWN_GRACE:-300}"
UP_GRACE="${UP_GRACE:-60}"
INTERVAL="${INTERVAL:-10}"

eth_healthy() {
  local carrier="" route gw
  { read -r carrier < "/sys/class/net/$IFACE/carrier"; } 2>/dev/null
  [[ "$carrier" == "1" ]] || return 1
  nmcli -t -g GENERAL.STATE device show "$IFACE" 2>/dev/null | grep -q '^100' || return 1
  route="$(ip -4 route show default dev "$IFACE")"
  gw="${route#default via }"
  gw="${gw%% *}"
  [[ -n "$gw" && "$gw" != "$route" ]] || return 1
  ping -I "$IFACE" -c 2 -W 2 "$gw" >/dev/null 2>&1
}

wifi_on() {
  [[ "$(nmcli radio wifi)" == "enabled" ]]
}

down_since=""
up_since=""

echo "starting: iface=$IFACE wifi_con=$WIFI_CON down_grace=${DOWN_GRACE}s up_grace=${UP_GRACE}s"

while true; do
  now="$EPOCHSECONDS"
  if eth_healthy; then
    down_since=""
    [[ -n "$up_since" ]] || { up_since="$now"; echo "eth0 is up"; }
    if (( now - up_since >= UP_GRACE )) && wifi_on; then
      echo "eth0 healthy for >= ${UP_GRACE}s - disabling Wi-Fi"
      nmcli radio wifi off
    fi
  else
    up_since=""
    [[ -n "$down_since" ]] || { down_since="$now"; echo "eth0 is down"; }
    if (( now - down_since >= DOWN_GRACE )) && ! wifi_on; then
      echo "eth0 down for >= ${DOWN_GRACE}s - enabling Wi-Fi and connecting $WIFI_CON"
      nmcli radio wifi on
      sleep 5
      nmcli connection up "$WIFI_CON" || echo "warning: nmcli connection up $WIFI_CON failed; NM autoconnect will retry"
    fi
  fi
  sleep "$INTERVAL"
done
