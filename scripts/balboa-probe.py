#!/usr/bin/env python3
"""Read-only probe for the Balboa BP-series spa WiFi module (BP2100G0, Utö).

Talks the native Balboa "BWA" TCP protocol on port 4257 — no cloud, no Home
Assistant, no rpi5 in the path. Useful as a fallback when the Pi is down, since
the spa module is a separate host on the LAN (192.168.68.53).

It listens only; it never sends a control frame. The optional `info` command
sends documented read-only panel queries and nothing else.

    balboa-probe.py scan [cidr]          # find the module (tcp/4257 sweep)
    balboa-probe.py discover             # UDP broadcast, same L2 segment only
    balboa-probe.py status <ip>          # decoded table, one shot
    balboa-probe.py json <ip>            # same, as JSON
    balboa-probe.py watch <ip> [secs]    # stream decoded status
    balboa-probe.py raw <ip> [secs]      # hex-dump every frame
    balboa-probe.py info <ip>            # request model / setup params

Frame:  7E <len> <type…> <data…> <crc8> 7E      CRC-8: poly 0x07, init/xor 0x02.
Temps are half-degrees in Celsius mode (0x4B = 75 -> 37.5 C), whole degrees in
Fahrenheit mode. 0xFF means no reading (circulation pump idle).

Status-frame offsets were verified against a live BP2100G0 by cross-checking
f[8]/f[9] against wall-clock time and f[25] against the panel setpoint. Fields
that could not be confirmed against a known value are marked UNVERIFIED below
and echoed in the raw dump rather than guessed at.
"""

import ipaddress
import json as jsonlib
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor

PORT = 4257
DISCOVERY_PORT = 30303
DISCOVERY_MSG = b"Discovery: Who is out there?"
DEFAULT_CIDR = "192.168.68.0/24"

STATUS_UPDATE = bytes([0xFF, 0xAF, 0x13])

HEAT_MODE = {0: "ready", 1: "rest", 2: "ready-in-rest"}
HEAT_STATE = {0: "off", 1: "heating", 2: "heat-waiting"}
PUMP_STATE = {0: "off", 1: "low", 2: "high"}

# Documented read-only panel queries: payload -> expected response type.
INFO_REQUESTS = {
    "system information": bytes([0x02, 0x00, 0x00]),
    "setup parameters": bytes([0x00, 0x00, 0x01]),
}


def crc8(data: bytes) -> int:
    crc = 0x02
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc ^ 0x02


def build(msg_type: bytes, payload: bytes = b"") -> bytes:
    body = bytes([len(msg_type) + len(payload) + 2]) + msg_type + payload
    return bytes([0x7E]) + body + bytes([crc8(body), 0x7E])


def discover(timeout: float = 5.0) -> list[tuple[str, str]]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)
    sock.sendto(DISCOVERY_MSG, ("255.255.255.255", DISCOVERY_PORT))

    found, deadline = [], time.time() + timeout
    while time.time() < deadline:
        try:
            payload, (host, _) = sock.recvfrom(1024)
        except socket.timeout:
            break
        found.append((host, payload.decode("ascii", "replace").strip()))
    sock.close()
    return found


def probe(ip: str, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((ip, PORT), timeout=timeout):
            return True
    except OSError:
        return False


def scan(cidr: str, workers: int = 128) -> list[str]:
    hosts = [str(h) for h in ipaddress.ip_network(cidr, strict=False).hosts()]
    print(f"scanning {len(hosts)} addresses on tcp/{PORT} ...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [ip for ip, ok in zip(hosts, pool.map(probe, hosts)) if ok]


def frames(sock: socket.socket, seconds: float):
    buf, deadline = bytearray(), time.time() + seconds
    sock.settimeout(2.0)
    while time.time() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            return
        buf += chunk

        while True:
            start = buf.find(0x7E)
            if start < 0 or len(buf) < start + 2:
                break
            length = buf[start + 1]
            end = start + length + 2
            if length < 5 or len(buf) < end:
                break
            frame = bytes(buf[start:end])
            del buf[:end]
            if frame[-1] != 0x7E:
                continue
            if crc8(frame[1:-2]) != frame[-2]:
                print(f"  ! crc mismatch: {frame.hex(' ')}", file=sys.stderr)
                continue
            yield frame


def decode_temp(raw: int, celsius: bool) -> float | None:
    return None if raw == 0xFF else (raw / 2.0 if celsius else float(raw))


def parse_status(f: bytes) -> dict:
    celsius = bool(f[14] & 0x01)
    pumps = [(f[16] >> (2 * i)) & 0x03 for i in range(4)]
    return {
        # verified against wall clock / panel setpoint / HA state
        "current_temp": decode_temp(f[7], celsius),
        "target_temp": decode_temp(f[25], celsius) if len(f) > 25 else None,
        "temp_scale": "C" if celsius else "F",
        "heat_mode": HEAT_MODE.get(f[10], f[10]),
        "heat_state": HEAT_STATE.get((f[15] >> 4) & 0x03, (f[15] >> 4) & 0x03),
        "temp_range": "high" if f[15] & 0x04 else "low",
        "panel_clock": f"{f[8]:02d}:{f[9]:02d}",
        # UNVERIFIED: plausible but not confirmed against a known-good value
        "pumps": [PUMP_STATE.get(p, p) for p in pumps],
        "circulation_pump": bool(f[18] & 0x02),
        "unknown_bytes": {
            f"f[{i}]": f"0x{f[i]:02x}"
            for i in (5, 6, 11, 12, 13, 17, 19, 20, 21, 22, 23, 24, 26, 27, 28)
            if i < len(f)
        },
        "raw": f.hex(" "),
    }


def first_status(ip: str, timeout: float = 20.0) -> dict | None:
    try:
        with socket.create_connection((ip, PORT), timeout=8) as sock:
            for frame in frames(sock, timeout):
                if frame[2:5] == STATUS_UPDATE:
                    return parse_status(frame)
    except OSError as exc:
        # The module accepts one client at a time, so this is either a genuine
        # network problem or Home Assistant already holding the socket.
        print(f"connect to {ip}:{PORT} failed: {exc}", file=sys.stderr)
    return None


def cmd_status(ip: str) -> int:
    st = first_status(ip)
    if not st:
        print("no status frame received", file=sys.stderr)
        return 1
    width = max(len(k) for k in st if k not in ("unknown_bytes", "raw"))
    for key, val in st.items():
        if key in ("unknown_bytes", "raw"):
            continue
        print(f"  {key:<{width}}  {val}")
    print(f"\n  undecoded: {' '.join(f'{k}={v}' for k, v in st['unknown_bytes'].items())}")
    print(f"  raw:       {st['raw']}")
    return 0


def cmd_info(ip: str) -> int:
    """Send documented read-only panel queries and print whatever comes back."""
    with socket.create_connection((ip, PORT), timeout=8) as sock:
        for label, payload in INFO_REQUESTS.items():
            sock.sendall(build(bytes([0x0A, 0xBF, 0x22]), payload))
        seen = {}
        for frame in frames(sock, 12.0):
            mtype = frame[2:5].hex(" ")
            if frame[2:5] != STATUS_UPDATE and mtype not in seen:
                seen[mtype] = frame
                print(f"  {mtype}: {frame.hex(' ')}")
    if not seen:
        print("  no non-status replies (module may not answer these queries)")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    ip = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == "discover":
        hits = discover()
        for host, banner in hits:
            print(f"{host}\t{banner}")
        if not hits:
            print("nothing found (expected across a VPN — try `scan`)", file=sys.stderr)
        return 0 if hits else 1

    if cmd == "scan":
        hits = scan(ip or DEFAULT_CIDR)
        print("\n".join(hits) or "", end="")
        if not hits:
            print(f"no host on tcp/{PORT}", file=sys.stderr)
        return 0 if hits else 1

    if not ip:
        print(f"usage: {sys.argv[0]} {cmd} <ip>", file=sys.stderr)
        return 2

    if cmd == "status":
        return cmd_status(ip)

    if cmd == "json":
        st = first_status(ip)
        if not st:
            return 1
        print(jsonlib.dumps(st, indent=2))
        return 0

    if cmd == "info":
        return cmd_info(ip)

    if cmd in ("watch", "raw"):
        secs = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
        with socket.create_connection((ip, PORT), timeout=8) as sock:
            for frame in frames(sock, secs):
                if cmd == "raw":
                    print(f"{frame[2:5].hex(' ')}  {frame.hex(' ')}")
                elif frame[2:5] == STATUS_UPDATE:
                    st = parse_status(frame)
                    print(
                        f"{st['panel_clock']} temp={st['current_temp']} "
                        f"target={st['target_temp']} heat={st['heat_state']} "
                        f"mode={st['heat_mode']}"
                    )
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
