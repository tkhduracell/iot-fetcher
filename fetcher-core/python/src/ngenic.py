
import logging
import os
from typing import List, Optional

from ngenicpy import Ngenic
from ngenicpy.models.node import NodeType, Node, NodeStatus
from ngenicpy.models.measurement import Measurement, MeasurementType

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

ngenic_token = os.environ.get('NGENIC_TOKEN', '')

logging.getLogger("httpx").setLevel(level=logging.WARNING)


def ngenic():
    if not ngenic_token:
        logger.error(
            "[ngenic] NGENIC_TOKEN environment variable not set, ignoring...")
        return
    try:
        _ngenic()
    except:
        logger.exception("[ngenic] Failed to execute ngenice module")


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
                    points.append(Point("ngenic_node_battery")
                                  .tag("node", node.uuid())
                                  .tag("node_type", type.name)
                                  .field("value", int(battery))
                                  )

                    radio_signal = node_status.radio_signal_percentage()
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
