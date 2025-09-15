import asyncio
import logging
import os
from typing import List, Dict, Any

from plugp100.api.tapo_client import TapoClient
from plugp100.api.login_credential import LoginCredential
from plugp100.responses.tapo_exception import TapoException

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

tapo_email = os.environ.get('TAPO_EMAIL', '')
tapo_password = os.environ.get('TAPO_PASSWORD', '')


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
    logger.info("[tapo] Fetching TAPO device data...")
    
    points: List[Point] = []
    
    try:
        # Initialize the TAPO client with credentials
        credential = LoginCredential(tapo_email, tapo_password)
        
        # Create client and login
        client = TapoClient(credential)
        
        # Get cloud session and device list
        cloud_session = await client.get_cloud_session()
        
        if not cloud_session:
            logger.error("[tapo] Failed to create cloud session")
            return
        
        # Get devices from cloud
        discovered_devices = await cloud_session.get_device_list()
        
        logger.info(f"[tapo] Found {len(discovered_devices)} TAPO devices")
        
        # Add device count metric
        device_count_point = Point("tapo_device_count") \
            .field("count", len(discovered_devices))
        points.append(device_count_point)
        
        for device_info in discovered_devices:
            device_id = device_info.device_id
            device_name = device_info.device_name
            device_type = device_info.device_type
            device_model = device_info.device_model
            device_ip = device_info.device_ip
            
            logger.debug(f"[tapo] Processing device: {device_name} ({device_model}) at {device_ip}")
            
            try:
                # Connect to individual device to get detailed metrics if IP is available
                if device_ip:
                    device_client = TapoClient(credential, address=device_ip)
                    
                    # Get device information
                    device_data = await device_client.get_device_info()
                    
                    # Create base point with device tags
                    base_point = Point("tapo_device") \
                        .tag("device_id", device_id) \
                        .tag("device_name", device_name) \
                        .tag("device_type", device_type) \
                        .tag("device_model", device_model) \
                        .tag("device_ip", device_ip)
                    
                    # Add device state information
                    if hasattr(device_data, 'device_on') and device_data.device_on is not None:
                        base_point = base_point.field("device_on", int(device_data.device_on))
                    
                    if hasattr(device_data, 'on_time') and device_data.on_time is not None:
                        base_point = base_point.field("on_time_seconds", device_data.on_time)
                    
                    # Add signal strength if available
                    if hasattr(device_data, 'rssi') and device_data.rssi is not None:
                        base_point = base_point.field("rssi", device_data.rssi)
                    
                    if hasattr(device_data, 'signal_level') and device_data.signal_level is not None:
                        base_point = base_point.field("signal_level", device_data.signal_level)
                    
                    points.append(base_point)
                    
                    # Try to get energy usage metrics if available (for smart plugs with energy monitoring)
                    try:
                        energy_usage = await device_client.get_energy_usage()
                        
                        if energy_usage:
                            energy_point = Point("tapo_device_usage") \
                                .tag("device_id", device_id) \
                                .tag("device_name", device_name) \
                                .tag("device_model", device_model)
                            
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
                    except Exception as energy_error:
                        logger.debug(f"[tapo] No energy usage data available for device {device_name}: {energy_error}")
                
                else:
                    logger.warning(f"[tapo] No IP address found for device {device_name}, adding basic presence metric only")
                    # Still add basic device presence metric
                    basic_point = Point("tapo_device") \
                        .tag("device_id", device_id) \
                        .tag("device_name", device_name) \
                        .tag("device_type", device_type) \
                        .tag("device_model", device_model) \
                        .field("device_count", 1)
                    points.append(basic_point)
                    
            except TapoException as tapo_error:
                logger.warning(f"[tapo] TAPO API error for device {device_name}: {tapo_error}")
                # Still add basic device presence metric
                basic_point = Point("tapo_device") \
                    .tag("device_id", device_id) \
                    .tag("device_name", device_name) \
                    .tag("device_type", device_type) \
                    .tag("device_model", device_model) \
                    .field("device_count", 1)
                points.append(basic_point)
            except Exception as device_error:
                logger.warning(f"[tapo] Failed to get detailed info for device {device_name}: {device_error}")
                # Still add basic device presence metric
                basic_point = Point("tapo_device") \
                    .tag("device_id", device_id) \
                    .tag("device_name", device_name) \
                    .tag("device_type", device_type) \
                    .tag("device_model", device_model) \
                    .field("device_count", 1)
                points.append(basic_point)
        
        if points:
            write_influx(points)
            logger.info(f"[tapo] Successfully wrote {len(points)} data points to InfluxDB")
        else:
            logger.warning("[tapo] No data points to write to InfluxDB")
            
    except Exception as e:
        logger.error(f"[tapo] Failed to fetch TAPO device data: {e}")
        raise