import logging
import os
import asyncio
from typing import List, Dict, Any

# Import the tapo package directly to avoid naming conflicts
import tapo as tapo_api

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

tapo_email = os.environ.get('TAPO_EMAIL', '')
tapo_password = os.environ.get('TAPO_PASSWORD', '')

# Known device IPs can be configured via environment as comma-separated
tapo_device_ips = os.environ.get('TAPO_DEVICE_IPS', '').split(',') if os.environ.get('TAPO_DEVICE_IPS', '') else []

def tapo():
    """Main tapo module entry point"""
    if not tapo_email or not tapo_password:
        logger.error("[tapo] TAPO_EMAIL and TAPO_PASSWORD environment variables not set, ignoring...")
        return
    
    if not tapo_device_ips:
        logger.error("[tapo] TAPO_DEVICE_IPS environment variable not set (comma-separated IP addresses), ignoring...")
        return
    
    try:
        asyncio.run(_tapo())
    except Exception as e:
        logger.exception("[tapo] Failed to execute tapo module: %s", e)


async def _tapo():
    """Internal async function to handle tapo devices"""
    logger.info("[tapo] Fetching Tapo device data...")
    
    client = tapo_api.ApiClient(tapo_email, tapo_password, timeout_s=10)
    points: List[Point] = []
    device_count = 0
    
    for ip in tapo_device_ips:
        ip = ip.strip()
        if not ip:
            continue
            
        try:
            # Try generic device first to get basic info
            device = await client.generic_device(ip)
            device_info = await device.get_device_info()
            
            logger.info("[tapo] Device at %s: %s (%s)", ip, device_info.nickname, device_info.model)
            
            device_count += 1
            
            # Basic device info points
            points.append(Point("tapo_device_online")
                         .tag("device_ip", ip)
                         .tag("device_id", device_info.device_id)
                         .tag("device_name", device_info.nickname)
                         .tag("model", device_info.model)
                         .field("value", 1))
            
            # Device state (if available)
            if hasattr(device_info, 'device_on'):
                points.append(Point("tapo_device_state")
                             .tag("device_ip", ip)
                             .tag("device_id", device_info.device_id)
                             .tag("device_name", device_info.nickname)
                             .tag("model", device_info.model)
                             .field("on", bool(device_info.device_on)))
            
            # Try to get energy usage if it's a power monitoring device
            try:
                if hasattr(device, 'get_energy_usage'):
                    energy_usage = await device.get_energy_usage()
                    
                    if hasattr(energy_usage, 'current_power'):
                        points.append(Point("tapo_device_power")
                                     .tag("device_ip", ip)
                                     .tag("device_id", device_info.device_id)
                                     .tag("device_name", device_info.nickname)
                                     .tag("model", device_info.model)
                                     .field("current_power_mw", float(energy_usage.current_power)))
                    
                    if hasattr(energy_usage, 'today_energy'):
                        points.append(Point("tapo_device_energy")
                                     .tag("device_ip", ip)
                                     .tag("device_id", device_info.device_id)
                                     .tag("device_name", device_info.nickname)
                                     .tag("model", device_info.model)
                                     .field("today_energy_wh", float(energy_usage.today_energy)))
                    
                    if hasattr(energy_usage, 'month_energy'):
                        points.append(Point("tapo_device_energy")
                                     .tag("device_ip", ip)
                                     .tag("device_id", device_info.device_id)
                                     .tag("device_name", device_info.nickname)
                                     .tag("model", device_info.model)
                                     .field("month_energy_wh", float(energy_usage.month_energy)))
                        
            except Exception as energy_e:
                logger.debug("[tapo] No energy usage data available for device %s: %s", ip, energy_e)
            
            # Try to get device usage (runtime info)
            try:
                if hasattr(device, 'get_device_usage'):
                    device_usage = await device.get_device_usage()
                    
                    if hasattr(device_usage, 'power_usage'):
                        points.append(Point("tapo_device_usage")
                                     .tag("device_ip", ip)
                                     .tag("device_id", device_info.device_id)
                                     .tag("device_name", device_info.nickname)
                                     .tag("model", device_info.model)
                                     .field("power_usage", float(device_usage.power_usage)))
                    
                    if hasattr(device_usage, 'time_usage_today'):
                        points.append(Point("tapo_device_usage")
                                     .tag("device_ip", ip)
                                     .tag("device_id", device_info.device_id)
                                     .tag("device_name", device_info.nickname)
                                     .tag("model", device_info.model)
                                     .field("time_usage_today_min", int(device_usage.time_usage_today)))
                        
            except Exception as usage_e:
                logger.debug("[tapo] No usage data available for device %s: %s", ip, usage_e)
                
        except Exception as device_e:
            logger.warning("[tapo] Failed to get info for device at %s: %s", ip, device_e)
    
    # Add a device count metric
    points.append(Point("tapo_device_count")
                 .field("value", device_count))
    
    if points:
        write_influx(points)
        logger.info("[tapo] Successfully wrote %d data points for %d devices", len(points), device_count)
    else:
        logger.warning("[tapo] No data points collected")