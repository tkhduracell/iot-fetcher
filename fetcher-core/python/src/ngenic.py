
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from ngenicpy import Ngenic
from ngenicpy.models.node import NodeType, Node, NodeStatus
from ngenicpy.models.measurement import Measurement, MeasurementType

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

ngenic_token = os.environ.get('NGENIC_TOKEN', '')
influx_host = os.environ.get('INFLUX_HOST', '')
influx_token = os.environ.get('INFLUX_TOKEN', '')

logging.getLogger("httpx").setLevel(level=logging.WARNING)

BACKFILL_MAX_DAYS = 30
BACKFILL_GAP_THRESHOLD_MINUTES = 10
BACKFILL_PERIOD = "PT5M"


def ngenic():
    if not ngenic_token:
        logger.error(
            "[ngenic] NGENIC_TOKEN environment variable not set, ignoring...")
        return
    try:
        _ngenic()
    except:
        logger.exception("[ngenic] Failed to execute ngenice module")


def ngenic_backfill():
    if not ngenic_token:
        logger.error("[ngenic] NGENIC_TOKEN not set, skipping backfill")
        return
    try:
        _ngenic_backfill()
    except Exception:
        logger.exception("[ngenic] Failed to execute ngenic backfill")


def _get_last_ngenic_timestamp() -> Optional[datetime]:
    if not influx_host or not influx_token:
        return None
    try:
        resp = httpx.get(
            f"{influx_host}/api/v1/query",
            params={"query": "timestamp(last_over_time(ngenic_node_sensor_measurement_value_temperature_C[30d]))"},
            headers={"Authorization": f"Bearer {influx_token}"},
            timeout=10,
        )
        data = resp.json()
        results = (data.get("data") or {}).get("result") or []
        if not results:
            return None
        # One series per node — back-fill from the oldest, otherwise nodes
        # lagging behind the first-returned series never get filled.
        stamps = []
        for r in results:
            try:
                stamps.append(float(r["value"][1]))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        if not stamps:
            return None
        return datetime.fromtimestamp(min(stamps), tz=timezone.utc)
    except Exception as e:
        logger.warning("[ngenic] Failed to query last timestamp: %s", e)
        return None


def _parse_measurement_time(time_str: str) -> Optional[datetime]:
    """Parse Ngenic's measurement timestamps.

    The API returns e.g. "2026-09-06T08:43:09 Etc/UTC" — an ISO datetime and a
    space-separated IANA zone name. strptime("%Z") does NOT accept IANA names
    (only abbreviations like UTC/GMT), so parse the datetime half and treat the
    zone as UTC, which is all the API has ever emitted.
    """
    try:
        head = time_str.split(" ", 1)[0]
        return datetime.fromisoformat(head).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError, IndexError):
        logger.warning("[ngenic] Unparseable measurement time %r, skipping", time_str)
        return None


def _ngenic_backfill():
    last_ts = _get_last_ngenic_timestamp()
    now = datetime.now(timezone.utc)

    if last_ts is None:
        backfill_from = now - timedelta(days=BACKFILL_MAX_DAYS)
        logger.info("[ngenic] No existing data found, backfilling %d days", BACKFILL_MAX_DAYS)
    else:
        gap = now - last_ts
        if gap < timedelta(minutes=BACKFILL_GAP_THRESHOLD_MINUTES):
            logger.info("[ngenic] Data is fresh (last: %s, gap: %s), skipping backfill", last_ts.isoformat(), gap)
            return
        backfill_from = last_ts
        gap_days = gap.total_seconds() / 86400
        if gap_days > BACKFILL_MAX_DAYS:
            backfill_from = now - timedelta(days=BACKFILL_MAX_DAYS)
            gap_days = BACKFILL_MAX_DAYS
        logger.info("[ngenic] Data gap detected (last: %s, gap: %.1f days), backfilling...", last_ts.isoformat(), gap_days)

    with Ngenic(token=ngenic_token) as ng:
        tune = ng.tunes()[0]
        nodes = tune.nodes()

        for node in nodes:
            node_type: NodeType = node.get_type()
            if node_type not in (NodeType.CONTROLLER, NodeType.SENSOR):
                continue

            measurement_types = node.measurement_types()
            for mtype in measurement_types:
                chunk_start = backfill_from
                while chunk_start < now:
                    chunk_end = min(chunk_start + timedelta(days=1), now)
                    from_str = chunk_start.strftime('%Y-%m-%dT%H:%M:%SZ')
                    to_str = chunk_end.strftime('%Y-%m-%dT%H:%M:%SZ')

                    try:
                        measurements = node.measurement(
                            mtype, from_dt=from_str, to_dt=to_str, period=BACKFILL_PERIOD,
                        )
                    except Exception as e:
                        logger.warning("[ngenic] Backfill fetch failed for %s node %s (%s -> %s): %s",
                                       mtype.value, node.uuid()[:8], from_str, to_str, e)
                        chunk_start = chunk_end
                        continue

                    if not measurements:
                        chunk_start = chunk_end
                        continue

                    if not isinstance(measurements, list):
                        measurements = [measurements]

                    points: List[Point] = []
                    for m in measurements:
                        # Measurement is not a dict — it has no .get(); read the
                        # underlying JSON, which also survives missing keys.
                        raw = m.json() if hasattr(m, "json") else dict(m)
                        if not raw.get("hasValue", False):
                            continue
                        ts = _parse_measurement_time(raw.get("time"))
                        if ts is None:
                            continue
                        points.append(
                            Point("ngenic_node_sensor_measurement_value")
                            .tag("node", node.uuid())
                            .tag("node_type", node_type.name)
                            .field(mtype.value, float(raw["value"]))
                            .time(int(ts.timestamp()))
                        )

                    if points:
                        logger.info("[ngenic] Backfill: %d points for %s node %s (%s)",
                                    len(points), mtype.value, node.uuid()[:8], from_str[:10])
                        write_influx(points)

                    chunk_start = chunk_end

    logger.info("[ngenic] Backfill complete")


def _ngenic():
    logger.info("[ngenic] Fetching Ngenic data...")
    with Ngenic(token=ngenic_token) as ngenic:

        tunes = ngenic.tunes()

        for tune in tunes:
            logger.debug("[ngenic] Tune %s, Name: %s, Tune Name: %s" %
                         (
                             tune.uuid(),
                             tune["name"],
                             tune["tuneName"]
                         )
                         )

        tune = tunes[0]

        rooms = tune.rooms()
        for room in rooms:
            logger.debug("[ngenic] Room %s, Name: %s, Target Temperature: %d" %
                         (
                             room.uuid(),
                             room["name"],
                             room["targetTemperature"]
                         )
                         )

        nodes: List[Node] = tune.nodes()
        points: List[Point] = []

        for node in nodes:
            logger.debug("[ngenic] Node %s, Type: %s, Mesurements: %s" %
                         (
                             node.uuid(),
                             node.get_type().name,
                             ','.join(
                                 map(lambda x: x.value, node.measurement_types())
                             )
                         )
                         )

            type: NodeType = node.get_type()

            if type in (NodeType.CONTROLLER, NodeType.SENSOR):
                node_status: Optional[NodeStatus] = node.status()

                if node_status:
                    battery = node_status.battery_percentage()
                    radio_signal = node_status.radio_signal_percentage()

                    # Skip when node is unreachable (both 0)
                    if battery == 0 and radio_signal == 0:
                        logger.warning("[ngenic] Node %s appears unreachable (battery=0, signal=0), skipping", node.uuid())
                    else:
                        points.append(Point("ngenic_node_battery")
                                      .tag("node", node.uuid())
                                      .tag("node_type", type.name)
                                      .field("value", int(battery))
                                      )

                        points.append(Point("ngenic_node_radio_signal")
                                      .tag("node", node.uuid())
                                      .tag("node_type", type.name)
                                      .field("value", int(radio_signal))
                                      )

                try:
                    measurements: List[Measurement] = node.measurements()
                    for measurement in measurements:
                        points.append(
                            Point(f"ngenic_node_sensor_measurement_value")
                            .tag("node", node.uuid())
                            .tag("node_type", type.name)
                            .field(measurement.get_type().value, float(measurement["value"]))
                        )
                except:
                    if type == NodeType.CONTROLLER:
                        measurement: Optional[Measurement] = node.measurement(
                            MeasurementType.TEMPERATURE)
                        points.append(
                            Point(f"ngenic_node_sensor_measurement_value")
                            .tag("node", node.uuid())
                            .tag("node_type", type.name)
                            .field(measurement.get_type().value, float(measurement["value"]))
                        )

        write_influx(points)
