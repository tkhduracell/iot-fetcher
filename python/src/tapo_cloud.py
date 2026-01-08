import asyncio
import base64
import binascii
import logging
import os
import re
import aiohttp
from typing import List, Dict, Any

from plugp100.discovery.cloud_client import CloudClient
from plugp100.new.device_factory import connect, DeviceConnectConfiguration
from plugp100.common.credentials import AuthCredential
from plugp100.responses.tapo_exception import TapoException

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

# Regex pattern for detecting base64-encoded strings
BASE64_PATTERN = re.compile(r'^[A-Za-z0-9+/]*={0,2}$')

# Regex pattern for verifying decoded string contains only printable ASCII characters
PRINTABLE_PATTERN = re.compile(r'^[\x20-\x7E\s]+$')

def strip_quote(s: str) -> str:
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s

def decode_if_base64(s: str) -> str:
    """
    Attempts to decode a base64-encoded string.
    TP-Link Tapo cloud API sometimes returns device names and aliases as base64-encoded strings.
    This function heuristically detects and decodes them, falling back to the original string if decoding fails.
    """
    if not s:
        return s

    # Check if string matches base64 pattern (alphanumeric + +/ and optional padding)
    if not BASE64_PATTERN.match(s):
        return s

    try:
        decoded = base64.b64decode(s)
        decoded_str = decoded.decode('utf-8')

        # Verify decoded string contains printable characters (avoid false positives from binary data)
        if not PRINTABLE_PATTERN.match(decoded_str):
            return s

        return decoded_str
    except (binascii.Error, UnicodeDecodeError):
        return s

tapo_email = strip_quote(os.environ.get('TAPO_EMAIL', ''))
tapo_password = strip_quote(os.environ.get('TAPO_PASSWORD', ''))


def tapo():
    if not tapo_email or not tapo_password:
        logger.error(
            "[tapo] TAPO_EMAIL and TAPO_PASSWORD environment variables must be set")
        return
    
    try:
        asyncio.run(_tapo())
    except Exception as e:
        logger.exception(f"[tapo] Failed to execute tapo module: {e}")


async def _tapo():
    logger.info("[tapo_cloud] Fetching TAPO device data using cloud discovery...")

    points: List[Point] = []

    try:
        # Initialize credentials
        credentials = AuthCredential(tapo_email, tapo_password)

        # Create HTTP session for cloud requests
        async with aiohttp.ClientSession() as session:
            # Use cloud discovery to find devices
            cloud_client = CloudClient()
            devices_result = await cloud_client.get_devices(tapo_email, tapo_password, session)

            # Handle the Try[List[CloudDeviceInfo]] result
            if devices_result.is_success():
                discovered_devices = devices_result.get()
                logger.info(f"[tapo_cloud] Found {len(discovered_devices)} TAPO devices via cloud")

                for cloud_device in discovered_devices:
                    device_ip = cloud_device.ipAddress
                    device_mac = cloud_device.deviceMac
                    device_type = cloud_device.deviceType
                    device_model = cloud_device.deviceModel
                    device_id = cloud_device.deviceId
                    device_name = decode_if_base64(cloud_device.deviceName)
                    device_alias = decode_if_base64(cloud_device.alias)

                    logger.info(f"[tapo_cloud] Processing device: {device_name} ({device_model}) at {device_ip}")
                    
                    try:
                        # Connect to device using the new API if IP is available
                        if device_ip:
                            config = DeviceConnectConfiguration(
                                host=device_ip,
                                credentials=credentials
                            )
                            
                            device = await connect(config)
                            
                            # Get device information
                            device_info_result = await device.get_device_info()
                            
                            # Handle the Try[Dict] result
                            if device_info_result.is_success():
                                device_info = device_info_result.get()

                                # Create base point with device tags
                                base_point = Point("tapo_cloud_device") \
                                    .tag("device_ip", device_ip) \
                                    .tag("device_mac", device_mac) \
                                    .tag("device_type", device_type) \
                                    .tag("device_model", device_model) \
                                    .tag("device_name", device_name) \
                                    .tag("device_alias", device_alias)
                                
                                # Add device_id if available
                                if device_id:
                                    base_point = base_point.tag("device_id", device_id)
                                
                                # Add device state information
                                if 'device_on' in device_info and device_info['device_on'] is not None:
                                    base_point = base_point.field("device_on", int(device_info['device_on']))
                                
                                if 'on_time' in device_info and device_info['on_time'] is not None:
                                    base_point = base_point.field("on_time_seconds", device_info['on_time'])
                                
                                # Add signal strength if available
                                if 'rssi' in device_info and device_info['rssi'] is not None:
                                    base_point = base_point.field("rssi", device_info['rssi'])
                                
                                if 'signal_level' in device_info and device_info['signal_level'] is not None:
                                    base_point = base_point.field("signal_level", device_info['signal_level'])
                                
                                points.append(base_point)

                                # Note: Energy collection removed from cloud-based discovery
                                # Energy metrics are only available via local discovery (tapo_local.py)
                                logger.debug(f"[tapo_cloud] Device info collected for {device_name} (energy metrics not available via cloud)")
                            else:
                                logger.warning(f"[tapo_cloud] Failed to get device info for {device_name}: {device_info_result}")
                                
                        else:
                            logger.warning(f"[tapo_cloud] No IP address found for device {device_name}, adding basic presence metric only")
                            # Still add basic device presence metric
                            basic_point = Point("tapo_cloud_device") \
                                .tag("device_mac", device_mac) \
                                .tag("device_type", device_type) \
                                .tag("device_model", device_model) \
                                .tag("device_name", device_name) \
                                .tag("device_alias", device_alias) \
                                .field("device_count", 1)
                            if device_id:
                                basic_point = basic_point.tag("device_id", device_id)
                            points.append(basic_point)
                                
                    except TapoException as tapo_error:
                        logger.warning(f"[tapo_cloud] TAPO API error for device {device_name}: {tapo_error}")
                        # Still add basic device presence metric
                        basic_point = Point("tapo_cloud_device") \
                            .tag("device_mac", device_mac) \
                            .tag("device_type", device_type) \
                            .tag("device_model", device_model) \
                            .tag("device_name", device_name) \
                            .tag("device_alias", device_alias) \
                            .field("device_count", 1)
                        if device_ip:
                            basic_point = basic_point.tag("device_ip", device_ip)
                        if device_id:
                            basic_point = basic_point.tag("device_id", device_id)
                        points.append(basic_point)
                    except Exception as device_error:
                        logger.warning(f"[tapo_cloud] Failed to get detailed info for device {device_name}: {device_error}")
                        # Still add basic device presence metric
                        basic_point = Point("tapo_cloud_device") \
                            .tag("device_mac", device_mac) \
                            .tag("device_type", device_type) \
                            .tag("device_model", device_model) \
                            .tag("device_name", device_name) \
                            .tag("device_alias", device_alias) \
                            .field("device_count", 1)
                        if device_ip:
                            basic_point = basic_point.tag("device_ip", device_ip)
                        if device_id:
                            basic_point = basic_point.tag("device_id", device_id)
                        points.append(basic_point)
            else:
                logger.error(f"[tapo_cloud] Failed to get devices from cloud: {devices_result}")
                return

        if points:
            write_influx(points)
            logger.info(f"[tapo_cloud] Successfully wrote {len(points)} data points to InfluxDB")
        else:
            logger.warning("[tapo_cloud] No data points to write to InfluxDB")

    except Exception as e:
        logger.error(f"[tapo_cloud] Failed to fetch TAPO device data: {e}")
        raise