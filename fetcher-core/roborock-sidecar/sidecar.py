import asyncio
import logging
import os
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from roborock import HomeDataProduct, DeviceData
from roborock.version_1_apis.roborock_mqtt_client_v1 import RoborockMqttClientV1
from roborock.version_1_apis.roborock_local_client_v1 import RoborockLocalClientV1
from roborock.web_api import RoborockApiClient

logging.basicConfig(level=logging.INFO, format='%(levelname)s [%(name)s] %(message)s')

app = FastAPI()


def memoize_with_ttl(ttl_hours: float = 24.0):
    def decorator(func: Callable) -> Callable:
        cache: dict[str, Any] = {'data': None, 'timestamp': None}

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if cache['data'] is not None and cache['timestamp'] is not None:
                cache_age = datetime.now() - cache['timestamp']
                if cache_age < timedelta(hours=ttl_hours):
                    logging.info(f"Using cached result for {func.__name__} (age: {cache_age})")
                    return cache['data']
                else:
                    logging.info(f"Cache expired for {func.__name__} (age: {cache_age}), refreshing")

            logging.info(f"Executing {func.__name__} and caching result")
            result = await func(*args, **kwargs)
            cache['data'] = result
            cache['timestamp'] = datetime.now()
            logging.info(f"Cached result for {func.__name__}")
            return result

        return async_wrapper
    return decorator


@memoize_with_ttl(ttl_hours=24.0)
async def get_roborock_client():
    username = os.environ.get('ROBOROCK_USERNAME')
    password = os.environ.get('ROBOROCK_PASSWORD')

    if not username or not password:
        raise ValueError("ROBOROCK_USERNAME and ROBOROCK_PASSWORD must be set")

    client = RoborockApiClient(username)
    user_data = await client.pass_login(password)
    home_data = await client.get_home_data_v3(user_data)

    return (client, user_data, home_data)


@app.get("/roborock/zones")
async def get_roborock_zones():
    try:
        client, user_data, home_data = await get_roborock_client()

        if not home_data or not home_data.devices:
            raise HTTPException(status_code=404, detail="No roborock devices found")

        logging.info(f"Found {len(home_data.devices)} roborock devices in home {home_data.name}")

        room_name_map = {}
        if home_data.rooms:
            for room in home_data.rooms:
                room_name_map[room.id] = room.name
                logging.info(f"Room mapping: {room.id} ({type(room.id)}) -> {room.name}")

        device = home_data.devices[0]
        device_product_id = device.product_id

        product_info: dict[str, HomeDataProduct] = {
            product.id: product for product in home_data.products
        }

        device_data = DeviceData(device, device.product_id)
        mqtt_client = RoborockMqttClientV1(user_data, device_data)

        try:
            networking = await mqtt_client.get_networking()
            if not networking or not networking.ip:
                raise HTTPException(status_code=503, detail="Device is not online or has no IP")

            logging.info(f"Device networking: {networking}")

            local_device_data = DeviceData(device, product_info[device_product_id].model, networking.ip)
            local_client = RoborockLocalClientV1(local_device_data)

            try:
                await local_client.async_connect()

                maps_list = await local_client.get_multi_maps_list()

                if not maps_list or not maps_list.map_info:
                    raise HTTPException(status_code=404, detail="No maps found")

                logging.info(f"Found {maps_list.multi_map_count} maps")

                zones = []

                for map_info in maps_list.map_info:
                    map_name = map_info.name
                    map_flag = map_info.map_flag

                    logging.info(f"Loading map {map_flag}: {map_name}")

                    await local_client.load_multi_map(map_flag)
                    await asyncio.sleep(3.0)

                    logging.info(f"Getting room mapping for map {map_name}")
                    room_mapping = await local_client.get_room_mapping()

                    if room_mapping:
                        logging.info(f"Map '{map_name}' has {len(room_mapping)} segments")

                        if isinstance(room_mapping, list):
                            for room in room_mapping:
                                room_name = room_name_map.get(room.iot_id, f"Room {room.segment_id}")

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
                    raise HTTPException(status_code=404, detail="No zones found on any map")

                return JSONResponse(content=zones)

            finally:
                await local_client.async_disconnect()

        finally:
            await mqtt_client.async_disconnect()

    except HTTPException:
        raise
    except Exception as e:
        logging.exception("Error getting roborock zones: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/roborock/{device_id}/{map_id}/{zone_id}/clean")
async def start_roborock_clean(device_id: str, map_id: str, zone_id: str):
    try:
        client, user_data, home_data = await get_roborock_client()

        if not home_data or not home_data.devices:
            raise HTTPException(status_code=404, detail="No roborock devices found")

        device = next((d for d in home_data.devices if d.duid == device_id), None)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

        product_info: dict[str, HomeDataProduct] = {
            product.id: product for product in home_data.products
        }
        device_product_id = device.product_id

        device_data = DeviceData(device, device.product_id)
        mqtt_client = RoborockMqttClientV1(user_data, device_data)

        try:
            networking = await mqtt_client.get_networking()
            if not networking or not networking.ip:
                raise HTTPException(status_code=503, detail="Device is not online or has no IP")

            local_device_data = DeviceData(device, product_info[device_product_id].model, networking.ip)
            local_client = RoborockLocalClientV1(local_device_data)

            try:
                await local_client.async_connect()

                map_flag = int(map_id)
                logging.info(f"Loading map {map_flag} before cleaning")
                await local_client.load_multi_map(map_flag)
                await asyncio.sleep(1.5)

                maps_list = await local_client.get_multi_maps_list()

                if not maps_list or not maps_list.map_info:
                    raise HTTPException(status_code=404, detail="No maps found")

                map_names = {m.map_flag: m.name for m in maps_list.map_info}
                map_name = map_names.get(map_flag, f"Map {map_flag}")
                logging.info(f"Map loaded: {map_name}")

                action = None
                result = None

                if zone_id == "all":
                    result = await mqtt_client.send_command("app_start")
                    action = f"full cleaning on {map_name}"
                else:
                    segment_id = int(zone_id)
                    result = await mqtt_client.send_command("app_segment_clean", [segment_id])
                    action = f"segment {segment_id} cleaning on {map_name}"

                logging.info(f"Started {action}, result: {result}")

                return JSONResponse(content={
                    "success": True,
                    "message": f"Started {action}",
                    "device_id": device.duid,
                    "map_id": map_id,
                    "map_name": map_name,
                    "zone_id": zone_id,
                    "result": result
                })

            finally:
                await local_client.async_disconnect()

        finally:
            await mqtt_client.async_disconnect()

    except HTTPException:
        raise
    except ValueError as e:
        logging.exception("Invalid parameter: %s", e)
        raise HTTPException(status_code=400, detail="Invalid parameter - map_id and zone_id must be numeric or 'all'")
    except Exception as e:
        logging.exception("Error starting roborock clean: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
