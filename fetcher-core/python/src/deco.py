import os
import time
import logging
from typing import List

from requests.exceptions import ChunkedEncodingError, Timeout
from urllib3.exceptions import ProtocolError
from tplinkrouterc6u import TPLinkDecoClient, Connection
from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

deco_ip = os.environ.get('DECO_IP', 'http://192.168.68.1')
deco_password = os.environ.get('DECO_PASSWORD', '')

# tplinkrouterc6u's own DecoRouter._retry_request only retries on
# ConnectTimeout. A dropped connection mid-chunked-response -- observed
# live on router.authorize(), ~1 in 4 cycles -- raises ChunkedEncodingError
# or ProtocolError instead, which is not one of the errors the library's
# own retry covers. A plain read timeout (Timeout, not ConnectTimeout --
# the connection opened fine but the flaky router never finished responding)
# is the same underlying symptom under a third shape, seen live once in
# three hours of otherwise-clean runs. One retry here, at the whole-call
# level, closes that gap without needing to patch the third-party library.
RETRY_ATTEMPTS = 2
RETRY_DELAY_S = 2


def deco():
    if not deco_password:
        logger.error("[deco] DECO_PASSWORD environment variable not set, ignoring...")
        return
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            _deco()
            return
        except (ChunkedEncodingError, ProtocolError, Timeout) as e:
            if attempt >= RETRY_ATTEMPTS:
                logger.exception(f"[deco] Failed to execute deco module: {e}")
                return
            logger.warning(
                "[deco] Transient error on attempt %d/%d (%s), retrying in %ds",
                attempt, RETRY_ATTEMPTS, e, RETRY_DELAY_S,
            )
            time.sleep(RETRY_DELAY_S)
        except Exception as e:
            logger.exception(f"[deco] Failed to execute deco module: {e}")
            return


def _deco():
    logger.info("[deco] Fetching device data from %s", deco_ip)

    router = TPLinkDecoClient(deco_ip, deco_password)
    try:
        router.authorize()
        status = router.get_status()
    finally:
        # logout() raises "Not authorised" itself if authorize() never
        # completed (self._logged stays False) -- letting that propagate
        # from a finally block replaces whatever exception is already in
        # flight (e.g. the ProtocolError that made authorize() fail) with
        # this unrelated one, hiding the real cause from the caller.
        try:
            router.logout()
        except Exception:
            logger.debug("[deco] logout() failed on an unauthorized session, ignoring")

    logger.info(
        "[deco] Router: %d wired, %d wifi, %d total clients",
        status.wired_total,
        status.wifi_clients_total,
        status.clients_total,
    )

    points: List[Point] = []

    # Router-level status point
    router_point = Point("deco_status") \
        .field("wired_total", status.wired_total) \
        .field("wifi_clients_total", status.wifi_clients_total) \
        .field("clients_total", status.clients_total)
    if status.guest_clients_total is not None:
        router_point = router_point.field("guest_clients_total", status.guest_clients_total)
    if status.iot_clients_total is not None:
        router_point = router_point.field("iot_clients_total", status.iot_clients_total)
    if status.mem_usage is not None:
        router_point = router_point.field("mem_usage", status.mem_usage)
    if status.cpu_usage is not None:
        router_point = router_point.field("cpu_usage", status.cpu_usage)
    points.append(router_point)

    # Per-device points
    for device in status.devices:
        conn_type = device.type
        mac = str(device.macaddr)
        hostname = device.hostname or "unknown"

        if conn_type == Connection.WIRED:
            connection_type = "wired"
            band = ""
        elif conn_type == Connection.UNKNOWN:
            connection_type = "unknown"
            band = ""
        else:
            connection_type = conn_type.get_type()
            band = conn_type.get_band()

        point = Point("deco_device") \
            .tag("hostname", hostname) \
            .tag("mac_address", mac) \
            .tag("connection_type", connection_type)

        if band:
            point = point.tag("band", band)

        point = point.field("ip_address", str(device.ipaddr))
        point = point.field("online", int(device.active))

        if device.signal is not None:
            point = point.field("signal_strength", device.signal)
        if device.packets_sent is not None:
            point = point.field("packets_sent", device.packets_sent)
        if device.packets_received is not None:
            point = point.field("packets_received", device.packets_received)
        if device.down_speed is not None:
            point = point.field("down_speed", device.down_speed)
        if device.up_speed is not None:
            point = point.field("up_speed", device.up_speed)
        if device.tx_rate is not None:
            point = point.field("tx_rate", device.tx_rate)
        if device.rx_rate is not None:
            point = point.field("rx_rate", device.rx_rate)
        if device.online_time is not None:
            point = point.field("online_time", device.online_time)
        if device.traffic_usage is not None:
            point = point.field("traffic_usage", device.traffic_usage)

        points.append(point)

        logger.info(
            "[deco] %s (%s): %s %s, signal=%s, active=%s",
            hostname, mac, connection_type, band,
            device.signal, device.active,
        )

    if points:
        write_influx(points)

