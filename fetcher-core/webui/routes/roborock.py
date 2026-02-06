from flask import Blueprint, make_response
import logging
import os
import asyncio
from functools import wraps

from roborock import HomeDataProduct, DeviceData
from roborock.version_1_apis.roborock_mqtt_client_v1 import RoborockMqttClientV1
from roborock.version_1_apis.roborock_local_client_v1 import RoborockLocalClientV1
from roborock.web_api import RoborockApiClient

from lib.decorators import memoize_with_ttl

roborock_bp = Blueprint('roborock', __name__)


def run_async(f):
    """Decorator to run async functions in Flask routes"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(f(*args, **kwargs))
        finally:
            if loop and not loop.is_closed():
                loop.close()
    return wrapper


@memoize_with_ttl(ttl_hours=24.0)
async def get_roborock_client():
    """Create and return authenticated roborock client, user data, and home data.
    Results are cached for 24 hours to avoid repeated authentication.
    """
    try:
        username = os.environ.get('ROBOROCK_USERNAME')
        password = os.environ.get('ROBOROCK_PASSWORD')

        if not username or not password:
            raise ValueError("ROBOROCK_USERNAME and ROBOROCK_PASSWORD must be set")

        client = RoborockApiClient(username)
        user_data = await client.pass_login(password)
        home_data = await client.get_home_data_v3(user_data)

        return (client, user_data, home_data)
    except ImportError:
        raise ImportError("python-roborock package is required. Run: pip install python-roborock")


@roborock_bp.route('/roborock/zones', methods=['GET'])
@run_async
async def get_roborock_zones():
    """Get available cleaning zones from roborock device with room names"""
    try:
        client, user_data, home_data = await get_roborock_client()

        if not home_data or not home_data.devices:
            return make_response({"error": "No roborock devices found"}, 404)

        logging.info(f"Found {len(home_data.devices)} roborock devices in home {home_data.name}")

        # Build room name mapping from home_data.rooms
        # This contains the actual room names like "Kitchen", "Bedroom", etc.
        room_name_map = {}
        if home_data.rooms:
            for room in home_data.rooms:
                room_name_map[room.id] = room.name
                logging.info(f"Room mapping: {room.id} ({type(room.id)}) -> {room.name}")

        # Get the first device
        device = home_data.devices[0]
        device_product_id = device.product_id

        # Get product info
        product_info: dict[str, HomeDataProduct] = {
            product.id: product for product in home_data.products
        }

        # Create MQTT client for device networking info
        device_data = DeviceData(device, device.product_id)
        mqtt_client = RoborockMqttClientV1(user_data, device_data)

        try:
            networking = await mqtt_client.get_networking()
            if not networking or not networking.ip:
                return make_response({"error": "Device is not online or has no IP"}, 503)

            logging.info(f"Device networking: {networking}")

            # Create local connection to device
            local_device_data = DeviceData(device, product_info[device_product_id].model, networking.ip)
            local_client = RoborockLocalClientV1(local_device_data)

            try:
                await local_client.async_connect()

                # Get all available maps
                maps_list = await local_client.get_multi_maps_list()

                if not maps_list or not maps_list.map_info:
                    return make_response({"error": "No maps found"}, 404)

                logging.info(f"Found {maps_list.multi_map_count} maps")

                zones = []

                # Iterate through each map and get its segments/rooms
                for map_info in maps_list.map_info:
                    map_name = map_info.name
                    map_flag = map_info.map_flag

                    logging.info(f"Loading map {map_flag}: {map_name}")

                    # Load the map
                    await local_client.load_multi_map(map_flag)
                    await asyncio.sleep(3.0)  # Wait for map to load

                    # Get room mapping (segments) for this map
                    # Returns a list of RoomMapping objects with segment_id and iot_id
                    logging.info(f"Getting room mapping for map {map_name}")
                    room_mapping = await local_client.get_room_mapping()

                    if room_mapping:
                        logging.info(f"Map '{map_name}' has {len(room_mapping)} segments")

                        if isinstance(room_mapping, list):
                            for room in room_mapping:
                                
                                # Match segment's iot_id with room name from home_data.rooms
                                room_name = room_name_map.get(room.iot_id, f"Room {room.segment_id}")

                                print(f"room_name: {room_name}, name: {room_name_map.get(room.iot_id)}, iot_id: {room.iot_id} {type(room.iot_id)}")

                                zones.append({
                                    'zone_id': str(room.segment_id),
                                    'zone_name': room_name,
                                    'zone_segment_id': room.segment_id,
                                    'iot_id': room.iot_id,
                                    'map_name': map_name,
                                    'map_flag': map_flag,
                                    'device_id': device.duid,
                                    'device_name': device.name,
                                    'device_product_id': device.product_id,
                                })

                                logging.info(f"Zone: {room_name} (segment_id={room.segment_id}, map={map_name})")
                    else:
                        logging.info(f"No room mapping found for map {map_name}")

                if not zones:
                    return make_response({"error": "No zones found on any map"}, 404)

                return make_response(zones, 200)

            finally:
                await local_client.async_disconnect()

        finally:
            await mqtt_client.async_disconnect()

    except Exception as e:
        logging.exception("Error getting roborock zones: %s", e)
        return make_response({"error": str(e)}, 500)


@roborock_bp.route('/roborock/<device_id>/<map_id>/<zone_id>/clean', methods=['POST'])
@run_async
async def start_roborock_clean(device_id: str, map_id: str, zone_id: str):
    """Start cleaning on roborock device for a specific zone/segment

    Args:
        device_id: Device DUID
        map_id: Map flag (0=Floor 1, 1=Uterum, 2=Floor 2, etc.)
        zone_id: Segment ID to clean, or "all" for full cleaning
    """
    try:
        client, user_data, home_data = await get_roborock_client()

        if not home_data or not home_data.devices:
            return make_response({"error": "No roborock devices found"}, 404)

        # Find the device
        device = next((d for d in home_data.devices if d.duid == device_id), None)
        if not device:
            return make_response({"error": f"Device {device_id} not found"}, 404)

        # Get product info for local connection
        product_info: dict[str, HomeDataProduct] = {
            product.id: product for product in home_data.products
        }
        device_product_id = device.product_id

        # Create MQTT client
        device_data = DeviceData(device, device.product_id)
        mqtt_client = RoborockMqttClientV1(user_data, device_data)

        try:
            # Get device networking for local connection
            networking = await mqtt_client.get_networking()
            if not networking or not networking.ip:
                return make_response({"error": "Device is not online or has no IP"}, 503)

            # Create local client to load the correct map
            local_device_data = DeviceData(device, product_info[device_product_id].model, networking.ip)
            local_client = RoborockLocalClientV1(local_device_data)

            try:
                await local_client.async_connect()

                # Load the specified map
                map_flag = int(map_id)
                logging.info(f"Loading map {map_flag} before cleaning")
                await local_client.load_multi_map(map_flag)
                await asyncio.sleep(1.5)  # Wait for map to load

                # Get map name for logging
                maps_list = await local_client.get_multi_maps_list()
                
                if not maps_list or not maps_list.map_info:
                    return make_response({"error": "No maps found"}, 404)

                map_names = {m.map_flag: m.name for m in maps_list.map_info}
                map_name = map_names.get(map_flag, f"Map {map_flag}")
                logging.info(f"Map loaded: {map_name}")

                action = None
                result = None

                if zone_id == "all":
                    # Start full clean
                    result = await mqtt_client.send_command("app_start")
                    action = f"full cleaning on {map_name}"
                else:
                    # Clean specific zone/segment (repeat=1)
                    segment_id = int(zone_id)
                    result = await mqtt_client.send_command("app_segment_clean", [segment_id])
                    action = f"segment {segment_id} cleaning on {map_name}"

                logging.info(f"Started {action}, result: {result}")

                return make_response({
                    "success": True,
                    "message": f"Started {action}",
                    "device_id": device.duid,
                    "map_id": map_id,
                    "map_name": map_name,
                    "zone_id": zone_id,
                    "result": result
                }, 200)

            finally:
                await local_client.async_disconnect()

        finally:
            await mqtt_client.async_disconnect()

    except ValueError as e:
        logging.exception("Invalid parameter: %s", e)
        return make_response({"error": f"Invalid parameter - map_id and zone_id must be numeric or 'all'"}, 400)
    except Exception as e:
        logging.exception("Error starting roborock clean: %s", e)
        return make_response({"error": str(e)}, 500)
