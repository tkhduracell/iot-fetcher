import asyncio
import logging
import os
from typing import List, Dict, Any

from plugp100.discovery import TapoDiscovery
from plugp100.new.device_factory import connect, DeviceConnectConfiguration
from plugp100.common.credentials import AuthCredential
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
    logger.info("[tapo] Fetching TAPO device data using local network discovery...")
    
    points: List[Point] = []
    
    try:
        # Initialize credentials
        credentials = AuthCredential(tapo_email, tapo_password)
        
        # Use network discovery to find devices
        discovered_devices = await TapoDiscovery.scan(timeout=10)
        
        logger.info(f"[tapo] Found {len(discovered_devices)} TAPO devices")
        
        # Add device count metric
        device_count_point = Point("tapo_device_count") \
            .field("count", len(discovered_devices))
        points.append(device_count_point)
        
        for discovered_device in discovered_devices:
            device_ip = discovered_device.ip
            device_mac = discovered_device.mac
            device_type = discovered_device.device_type
            device_model = discovered_device.device_model
            device_id = discovered_device.device_id
            
            logger.debug(f"[tapo] Processing device at {device_ip} ({device_model})")
            
            try:
                # Connect to device using the new API
                config = DeviceConnectConfiguration(
                    host=device_ip,
                    credentials=credentials
                )
                
                device = await connect(config)
                
                # Get device information
                device_info = await device.get_device_info()
                
                # Create base point with device tags
                base_point = Point("tapo_device") \
                    .tag("device_ip", device_ip) \
                    .tag("device_mac", device_mac) \
                    .tag("device_type", device_type) \
                    .tag("device_model", device_model)
                
                # Add device_id if available
                if device_id:
                    base_point = base_point.tag("device_id", device_id)
                
                # Add device state information
                if hasattr(device_info, 'device_on') and device_info.device_on is not None:
                    base_point = base_point.field("device_on", int(device_info.device_on))
                
                if hasattr(device_info, 'on_time') and device_info.on_time is not None:
                    base_point = base_point.field("on_time_seconds", device_info.on_time)
                
                # Add signal strength if available
                if hasattr(device_info, 'rssi') and device_info.rssi is not None:
                    base_point = base_point.field("rssi", device_info.rssi)
                
                if hasattr(device_info, 'signal_level') and device_info.signal_level is not None:
                    base_point = base_point.field("signal_level", device_info.signal_level)
                
                # Add device nickname/name if available
                if hasattr(device_info, 'nickname') and device_info.nickname:
                    base_point = base_point.tag("device_name", device_info.nickname)
                elif hasattr(device_info, 'alias') and device_info.alias:
                    base_point = base_point.tag("device_name", device_info.alias)
                
                points.append(base_point)
                
                # Try to get energy usage metrics if available (for smart plugs with energy monitoring)
                try:
                    energy_usage = await device.get_energy_usage()
                    
                    if energy_usage:
                        energy_point = Point("tapo_device_usage") \
                            .tag("device_ip", device_ip) \
                            .tag("device_mac", device_mac) \
                            .tag("device_model", device_model)
                        
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
                except Exception as energy_error:
                    logger.debug(f"[tapo] No energy usage data available for device at {device_ip}: {energy_error}")
            
            except TapoException as tapo_error:
                logger.warning(f"[tapo] TAPO API error for device at {device_ip}: {tapo_error}")
                # Still add basic device presence metric
                basic_point = Point("tapo_device") \
                    .tag("device_ip", device_ip) \
                    .tag("device_mac", device_mac) \
                    .tag("device_type", device_type) \
                    .tag("device_model", device_model) \
                    .field("device_count", 1)
                if device_id:
                    basic_point = basic_point.tag("device_id", device_id)
                points.append(basic_point)
            except Exception as device_error:
                logger.warning(f"[tapo] Failed to get detailed info for device at {device_ip}: {device_error}")
                # Still add basic device presence metric
                basic_point = Point("tapo_device") \
                    .tag("device_ip", device_ip) \
                    .tag("device_mac", device_mac) \
                    .tag("device_type", device_type) \
                    .tag("device_model", device_model) \
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