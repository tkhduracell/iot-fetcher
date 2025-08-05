import logging
import os
from typing import List

from SigenAPI.api import SigenStorClient

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

sigenergy_host = os.environ.get('SIGENERGY_HOST') or os.environ.get('SIGENERGY_IP') or ''
sigenergy_port = int(os.environ.get('SIGENERGY_PORT', 502))


def sigenergy():
    try:
        _sigenergy()
    except Exception:
        logger.exception("[sigenergy] Failed to execute sigenergy module")


def _sigenergy():
    logger.info("[sigenergy] Fetching Sigenergy data...")
    
    if not sigenergy_host:
        logger.error("[sigenergy] SIGENERGY_HOST or SIGENERGY_IP environment variable not set")
        return
    
    try:
        logger.debug("[sigenergy] Connecting to SigenStore at %s:%d", sigenergy_host, sigenergy_port)
        client = SigenStorClient(sigenergy_host, sigenergy_port)
        points: List[Point] = []
        
        # Test the connection first with both unit IDs
        logger.debug("[sigenergy] Testing connection with unit ID 247...")
        test_result_247 = None
        try:
            test_result_247 = client.client_247.read_holding_registers(30003, 1)
            if test_result_247 is None:
                logger.warning("[sigenergy] No response from unit ID 247")
            else:
                logger.debug("[sigenergy] Unit ID 247 response: %s", test_result_247)
        except Exception as e:
            logger.warning("[sigenergy] Unit ID 247 connection failed: %s", e)
            
        logger.debug("[sigenergy] Testing connection with unit ID 1...")
        test_result_1 = None
        try:
            test_result_1 = client.client_1.read_holding_registers(30500, 1)  # Model type register
            if test_result_1 is None:
                logger.warning("[sigenergy] No response from unit ID 1")
            else:
                logger.debug("[sigenergy] Unit ID 1 response: %s", test_result_1)
        except Exception as e:
            logger.warning("[sigenergy] Unit ID 1 connection failed: %s", e)
            
        # If both connections fail, provide helpful error message
        if test_result_247 is None and test_result_1 is None:
            logger.error("[sigenergy] Unable to connect to device. Please check:")
            logger.error("  1. Device IP address is correct: %s", sigenergy_host)
            logger.error("  2. Device is powered on and accessible on the network")
            logger.error("  3. Modbus TCP is enabled on the device")
            logger.error("  4. Port %d is not blocked by firewall", sigenergy_port)
            logger.error("  5. Device is configured to accept Modbus connections")
            return
        
        # Proceed with data collection
        try:
            # Get system status information for tags
            operating_mode = client.get_selected_operating_mode()
            model_type = str(client.get_model_type())
            on_grid = client.is_on_grid()
            grid_sensor_connected = client.grid_sensor_connected()
            
            logger.debug("[sigenergy] Operating mode: %s, Model: %s, On grid: %s, Grid sensor: %s",
                        operating_mode, model_type, on_grid, grid_sensor_connected)
        except Exception as e:
            logger.error("[sigenergy] Failed to get system status: %s", e)
            return
        
        # System status points
        points.append(Point("sigenergy_system_status")
                     .tag("host", sigenergy_host)
                     .tag("operating_mode", operating_mode)
                     .tag("model_type", model_type)
                     .field("on_grid", 1 if on_grid else 0)
                     .field("grid_sensor_connected", 1 if grid_sensor_connected else 0)
                     )
        
        # Grid power data
        try:
            power_to_grid = client.get_current_power_to_grid()
            power_from_grid = client.get_current_power_from_grid()
            net_power = power_to_grid - power_from_grid  # Positive = export, Negative = import
            
            points.append(Point("sigenergy_grid_power")
                         .tag("host", sigenergy_host)
                         .field("net_power_kw", float(net_power))
                         .field("power_to_grid_kw", float(power_to_grid))    # Keep for debugging/transparency
                         .field("power_from_grid_kw", float(power_from_grid)) # Keep for debugging/transparency
                         )
            
            logger.debug("[sigenergy] Grid power - Net: %.2f kW (To: %.2f kW, From: %.2f kW)", 
                        net_power, power_to_grid, power_from_grid)
        except Exception as e:
            logger.warning("[sigenergy] Failed to get grid power data: %s", e)
        
        # Battery data
        try:
            battery_soc = client.get_battery_soc()
            power_to_battery = client.get_current_power_to_battery()
            power_from_battery = client.get_current_power_from_battery()
            
            points.append(Point("sigenergy_battery")
                         .tag("host", sigenergy_host)
                         .field("soc_percent", float(battery_soc))
                         .field("power_to_battery_kw", float(power_to_battery))
                         .field("power_from_battery_kw", float(power_from_battery))
                         )
            
            logger.debug("[sigenergy] Battery - SOC: %.1f%%, Charging: %.2f kW, Discharging: %.2f kW", 
                        battery_soc, power_to_battery, power_from_battery)
        except Exception as e:
            logger.warning("[sigenergy] Failed to get battery data: %s", e)
        
        # PV power data
        try:
            total_pv_power = client.get_current_total_pv_power()
            pv_string_1_power = client.get_pv_string_1_power()
            pv_string_2_power = client.get_pv_string_2_power()
            pv_string_3_power = client.get_pv_string_3_power()
            pv_string_4_power = client.get_pv_string_4_power()
            
            # Total PV power
            points.append(Point("sigenergy_pv_power")
                         .tag("host", sigenergy_host)
                         .tag("string", "total")
                         .field("power_kw", float(total_pv_power))
                         )
            
            # Individual string powers
            for string_num, power in enumerate([pv_string_1_power, pv_string_2_power, 
                                              pv_string_3_power, pv_string_4_power], 1):
                if power > 0:  # Only log strings that are producing power
                    points.append(Point("sigenergy_pv_power")
                                 .tag("host", sigenergy_host)
                                 .tag("string", f"string_{string_num}")
                                 .field("power_kw", float(power))
                                 )
            
            logger.debug("[sigenergy] PV power - Total: %.2f kW, Strings: [%.2f, %.2f, %.2f, %.2f] kW", 
                        total_pv_power, pv_string_1_power, pv_string_2_power, 
                        pv_string_3_power, pv_string_4_power)
        except Exception as e:
            logger.warning("[sigenergy] Failed to get PV power data: %s", e)
        
        # Write all points to InfluxDB
        if points:
            write_influx(points)
            logger.info("[sigenergy] Successfully wrote %d data points to InfluxDB", len(points))
        else:
            logger.warning("[sigenergy] No data points collected")
            
    except Exception as e:
        logger.error("[sigenergy] Failed to connect to SigenStore at %s:%d - %s", 
                    sigenergy_host, sigenergy_port, e)
