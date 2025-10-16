from flask import Blueprint, make_response
import logging
import os
import asyncio
from functools import wraps

from roborock import HomeDataProduct, DeviceData
from roborock.version_1_apis.roborock_mqtt_client_v1 import RoborockMqttClientV1
from roborock.version_1_apis.roborock_local_client_v1 import RoborockLocalClientV1
from roborock.web_api import RoborockApiClient

from decorators import memoize_with_ttl

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
    """Get available cleaning zones from roborock device"""
    try:
        client, user_data, home_data = await get_roborock_client()

        if not home_data or not home_data.devices:
            return make_response({"error": "No roborock devices found"}, 404)

        logging.info(f"Found {len(home_data.devices)} roborock devices in home {home_data.name}")
        for d in home_data.devices:
            logging.info(f"Found device: {d.name} ({d.duid}) online: {d.online}")

        # Get the first device
        device = home_data.devices[0]
        device_product_id = device.product_id

        logging.info(f"dict: {device}")

        scens = await client.get_scenes(user_data, device.duid)
        logging.info(f"Device scenes: {scens}")

        # Get product ids:
        product_info: dict[str, HomeDataProduct] = {
            product.id: product for product in home_data.products
        }
        logging.info(f"Product info: {product_info}")

        # Create the Mqtt(aka cloud required) Client
        device_data = DeviceData(device, device.product_id)
        mqtt_client = RoborockMqttClientV1(user_data, device_data)

        networking = await mqtt_client.get_networking()
        if not networking or not networking.ip:
            return make_response({"error": "Device is not online or has no IP"}, 503)

        logging.info(f"Device networking: {networking}")

        rooms = await mqtt_client.get_room_mapping()
        logging.info(f"Device rooms: {rooms}")

        if not rooms:
            return make_response({"error": "No rooms/zones found"}, 404)

        ## Check https://github.com/Skitionek/github-action-home-automation/blob/8db6ee43b165eda038c34ff4e6e4be523451a38f/goto.py#L70
        local_device_data = DeviceData(device, product_info[device_product_id].model, networking.ip)
        local_client = RoborockLocalClientV1(local_device_data)

        try:
            zones = []

            await local_client.async_connect()
            status = await local_client.get_status()
            logging.info(f"Local connection status: {status}")

            # Get room mapping and segments
            room_mapping = await local_client.get_room_mapping()

            logging.info(f"Local room mapping: {room_mapping}")

            if room_mapping and isinstance(room_mapping, dict):
                # Parse room mapping data
                segments = room_mapping.get('segments', {})
                for segment_id, segment_data in segments.items():
                    zones.append({
                        'zone_id': segment_id,
                        'zone_name': segment_data.get('name', f"Zone {segment_id}"),
                        'zone_segment_id': segment_id,
                        'device_id': device.duid,
                        'device_name': device.name,
                        'device_product_id': device.product_id,
                    })

            return make_response(zones, 200)

        finally:
            await local_client.async_disconnect()
            await mqtt_client.async_disconnect()

    except Exception as e:
        logging.exception("Error getting roborock zones: %s", e)
        return make_response({"error": str(e)}, 500)


@roborock_bp.route('/roborock/<device_id>/<zone_id>/clean', methods=['POST'])
@run_async
async def start_roborock_clean(device_id: str, zone_id: str):
    """Start cleaning on roborock device"""
    try:
        client, user_data, home_data = await get_roborock_client()

        if not home_data or not home_data.devices:
            return make_response({"error": "No roborock devices found"}, 404)

        device = next(d for d in home_data.devices if d.duid == device_id)

        device_data = DeviceData(device, device.product_id)
        mqtt_client = RoborockMqttClientV1(user_data, device_data)

        try:
            await mqtt_client.async_connect()
            
            action = None
            result = None
            if zone_id:
                # Clean specific zone/segment
                result = await mqtt_client.send_command("app_segment_clean", [int(zone_id)])
                action = f"segment cleaning for zone {zone_id}"
            elif zone_id == "all":
                # Start full clean
                result = await mqtt_client.send_command("app_start")
                action = "full cleaning"

            return make_response({
                "success": True,
                "message": f"Started {action}",
                "device_id": device.duid,
                "result": result
            }, 200)

        finally:
            await mqtt_client.async_disconnect()

    except Exception as e:
        logging.exception("Error starting roborock clean: %s", e)
        return make_response({"error": str(e)}, 500)
