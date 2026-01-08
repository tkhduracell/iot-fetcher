import asyncio
import logging
import os
from typing import List, Dict, Any

from plugp100.discovery.tapo_discovery import TapoDiscovery
from plugp100.common.credentials import AuthCredential
from plugp100.responses.tapo_exception import TapoException

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

def strip_quote(s: str) -> str:
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
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
    logger.info("[tapo] Fetching TAPO device data using local network discovery...")

    points: List[Point] = []

    try:
        # Initialize credentials
        credentials = AuthCredential(tapo_email, tapo_password)

        # Use local network discovery to find devices
        # Use broadcast address 192.168.71.255 for the /22 network (192.168.68.0 - 192.168.71.255)
        logger.info("[tapo] Scanning local network for TAPO devices...")
        discovered_devices = await TapoDiscovery.scan(timeout=5, broadcast="192.168.71.255")

        logger.info(f"[tapo] Found {len(discovered_devices)} TAPO devices via local discovery")

        # Add device count metric
        device_count_point = Point("tapo_device_count") \
            .field("count", len(discovered_devices))
        points.append(device_count_point)

        for discovered_device in discovered_devices:
            device_ip = discovered_device.ip
            device_mac = discovered_device.mac
            device_type = discovered_device.device_type
            device_model = discovered_device.device_model
            device_id = discovered_device.device_id or ""

            # Use model as name if no alias available
            device_name = device_model
            device_alias = device_model

            logger.info(f"[tapo] Processing device: {device_name} ({device_model}) at {device_ip}")

            try:
                # Connect to device using the discovered device helper
                device = await discovered_device.get_tapo_device(credentials)

                # Update device state
                await device.update()

                # Get device information
                device_info_result = await device.get_device_info()

                # Handle the Try[Dict] result
                if device_info_result.is_success():
                    device_info = device_info_result.get()

                    # Update device name/alias from actual device info if available
                    if 'alias' in device_info and device_info['alias']:
                        device_alias = device_info['alias']
                        device_name = device_info['alias']
                    elif 'nickname' in device_info and device_info['nickname']:
                        device_alias = device_info['nickname']
                        device_name = device_info['nickname']

                    # Create base point with device tags
                    base_point = Point("tapo_device") \
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

                    # Try to get energy usage metrics if available (for smart plugs with energy monitoring)
                    # Only attempt for device models known to support energy monitoring
                    energy_monitoring_models = ['P110', 'P115', 'P125M', 'KP115']
                    supports_energy = any(model in device_model for model in energy_monitoring_models)

                    if supports_energy:
                        try:
                            energy_usage_result = await device.get_energy_usage()

                            if energy_usage_result.is_success():
                                energy_usage = energy_usage_result.get()

                                energy_point = Point("tapo_device_usage") \
                                    .tag("device_ip", device_ip) \
                                    .tag("device_mac", device_mac) \
                                    .tag("device_model", device_model) \
                                    .tag("device_name", device_name)

                                # Add device_id if available
                                if device_id:
                                    energy_point = energy_point.tag("device_id", device_id)

                                if hasattr(energy_usage, 'today_runtime') and energy_usage.today_runtime is not None:
                                    energy_point = energy_point.field("today_runtime_minutes", energy_usage.today_runtime)

                                if hasattr(energy_usage, 'month_runtime') and energy_usage.month_runtime is not None:
                                    energy_point = energy_point.field("month_runtime_minutes", energy_usage.month_runtime)

                                if hasattr(energy_usage, 'today_energy') and energy_usage.today_energy is not None:
                                    energy_point = energy_point.field("today_energy_wh", energy_usage.today_energy)

                                if hasattr(energy_usage, 'month_energy') and energy_usage.month_energy is not None:
                                    energy_point = energy_point.field("month_energy_wh", energy_usage.month_energy)

                                if hasattr(energy_usage, 'current_power') and energy_usage.current_power is not None:
                                    energy_point = energy_point.field("current_power_w", energy_usage.current_power)

                                points.append(energy_point)
                                logger.info(f"[tapo] Successfully retrieved energy data for {device_name}")
                            else:
                                logger.warning(f"[tapo] Energy usage query failed for {device_name}: {energy_usage_result}")
                        except Exception as energy_error:
                            logger.warning(f"[tapo] Failed to get energy usage for {device_name}: {energy_error}")
                    else:
                        logger.debug(f"[tapo] Skipping energy query for {device_name} - model {device_model} does not support energy monitoring")
                else:
                    logger.warning(f"[tapo] Failed to get device info for {device_name}: {device_info_result}")

                # Clean up device connection
                try:
                    await device.client.close()
                except:
                    pass

            except TapoException as tapo_error:
                logger.warning(f"[tapo] TAPO API error for device {device_name} at {device_ip}: {tapo_error}")
                # Still add basic device presence metric
                basic_point = Point("tapo_device") \
                    .tag("device_mac", device_mac) \
                    .tag("device_type", device_type) \
                    .tag("device_model", device_model) \
                    .tag("device_name", device_name) \
                    .tag("device_alias", device_alias) \
                    .tag("device_ip", device_ip) \
                    .field("device_count", 1)
                if device_id:
                    basic_point = basic_point.tag("device_id", device_id)
                points.append(basic_point)
            except Exception as device_error:
                logger.warning(f"[tapo] Failed to get detailed info for device {device_name} at {device_ip}: {device_error}")
                # Still add basic device presence metric
                basic_point = Point("tapo_device") \
                    .tag("device_mac", device_mac) \
                    .tag("device_type", device_type) \
                    .tag("device_model", device_model) \
                    .tag("device_name", device_name) \
                    .tag("device_alias", device_alias) \
                    .tag("device_ip", device_ip) \
                    .field("device_count", 1)
                if device_id:
                    basic_point = basic_point.tag("device_id", device_id)
                points.append(basic_point)

        if points:
            write_influx(points)
            logger.info(f"[tapo] Successfully wrote {len(points)} data points to InfluxDB")
        else:
            logger.warning("[tapo] No data points to write to InfluxDB")

    except Exception as e:
        logger.error(f"[tapo] Failed to fetch TAPO device data: {e}")
        raise
