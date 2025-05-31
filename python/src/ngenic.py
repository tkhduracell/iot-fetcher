
from enum import Enum
import logging
import os
from typing import List

from ngenicpy import Ngenic
from ngenicpy.models.node import NodeType, Node, NodeStatus
from ngenicpy.models.measurement import Measurement, MeasurementType

from influx import write_influx, Point

ngenic_token = os.environ['NGENIC_TOKEN'] or ''

logging.getLogger("httpx").setLevel(level=logging.WARNING)


def ngenic():
    try:
        _ngenic()
    except:
        logging.exception("Failed to execute ngenice module")


def _ngenic():
    logging.info("Fetching Ngenic data...")
    with Ngenic(token=ngenic_token) as ngenic:

        tunes = ngenic.tunes()

        for tune in tunes:
            logging.debug("Tune %s, Name: %s, Tune Name: %s" %
                         (
                             tune.uuid(),
                             tune["name"],
                             tune["tuneName"]
                         )
                         )

        tune = tunes[0]

        rooms = tune.rooms()
        for room in rooms:
            logging.debug("Room %s, Name: %s, Target Temperature: %d" %
                         (
                             room.uuid(),
                             room["name"],
                             room["targetTemperature"]
                         )
                         )

        nodes: List[Node] = tune.nodes()
        points: List[Point] = []

        for node in nodes:
            logging.debug("Node %s, Type: %s, Mesurements: %s" %
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
                node_status: NodeStatus = node.status()

                if node_status:
                    battery = node_status.battery_percentage()
                    points.append(Point("ngenic_node_battery")
                                  .tag("node", node.uuid())
                                  .field("value", int(battery))
                                  )

                    radio_signal = node_status.radio_signal_percentage()
                    points.append(Point("ngenic_node_radio_signal")
                                  .tag("node", node.uuid())
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
                        measurement: Measurement = node.measurement(
                            MeasurementType.TEMPERATURE)
                        points.append(
                            Point(f"ngenic_node_sensor_measurement_value")
                            .tag("node", node.uuid())
                            .tag("node_type", type.name)
                            .field(measurement.get_type().value, float(measurement["value"]))
                        )

        write_influx(points)
