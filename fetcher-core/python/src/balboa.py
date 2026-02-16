import pybalboa
import pybalboa.enums

import os
import asyncio
import logging
from datetime import datetime
import pytz
from pybalboa.enums import HeatMode, UnknownState

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

# Balboa configuration
spa_ip = os.environ.get('BALBOA_HOST', '')

# Timezone configuration
STOCKHOLM_TZ = pytz.timezone('Europe/Stockholm')

# Hour-to-mode mapping: index = hour (0-23), value = HeatMode
HOURLY_HEAT_MODE_SCHEDULE = [
    HeatMode.READY,  # 00:00 - Off-peak
    HeatMode.READY,  # 01:00 - Off-peak
    HeatMode.READY,  # 02:00 - Off-peak
    HeatMode.READY,  # 03:00 - Off-peak
    HeatMode.READY,  # 04:00 - Off-peak
    HeatMode.READY,  # 05:00 - Off-peak
    HeatMode.REST,   # 06:00 - Morning peak starts
    HeatMode.REST,   # 07:00 - Morning peak
    HeatMode.REST,   # 08:00 - Morning peak
    HeatMode.READY,  # 09:00 - Peak ends
    HeatMode.READY,  # 10:00 - Off-peak
    HeatMode.READY,  # 11:00 - Off-peak
    HeatMode.READY,  # 12:00 - Off-peak
    HeatMode.READY,  # 13:00 - Off-peak
    HeatMode.READY,  # 14:00 - Off-peak
    HeatMode.READY,  # 15:00 - Off-peak
    HeatMode.READY,  # 16:00 - Off-peak
    HeatMode.REST,   # 17:00 - Evening peak starts
    HeatMode.REST,   # 18:00 - Evening peak
    HeatMode.READY,  # 19:00 - Peak ends
    HeatMode.READY,  # 20:00 - Off-peak
    HeatMode.READY,  # 21:00 - Off-peak
    HeatMode.READY,  # 22:00 - Off-peak
    HeatMode.READY,  # 23:00 - Off-peak
]


def get_desired_heat_mode() -> HeatMode:
    """Get desired heat mode for current Stockholm hour."""
    now = datetime.now(STOCKHOLM_TZ)
    return HOURLY_HEAT_MODE_SCHEDULE[now.hour]


def balboa():
    if not spa_ip:
        logger.error(
            "[balboa] BALBOA_HOST environment variable not set, ignoring...")
        return
    try:
        asyncio.run(_balboa())
    except KeyboardInterrupt:
        pass
    except pybalboa.exceptions.SpaConnectionError:
        logger.info("[balboa] Unable to connect to balboa spa...")
    except Exception:
        logger.warning("[balboa] Unexpected error in balboa spa")


async def _balboa():
    logger.info("[balboa] Connecting to Balboa Spa at IP address " +
                spa_ip)
    async with pybalboa.SpaClient(spa_ip) as spa:
        await spa.connect()

        # wait for the spa to be ready for use (max 60 seconds)
        for _ in range(12):
            if spa.available:
                break
            logger.info("[balboa] Waiting for spa to be ready...")
            await asyncio.sleep(5)
        else:
            logger.warning("[balboa] Spa did not become available after 60s, aborting")
            return

        # read/run spa commands
        if not spa.connected:
            spa.disconnect()
            logger.info(f'[balboa] Not connected to spa. Reconnecting in 30 sec (available: {
                spa.available}, connected: {spa.connected})')
            return

        temperature = spa.temperature
        temperature_unit = spa.temperature_unit

        temp = Point("spa_temperature") \
            .tag("unit", 'c' if temperature_unit == pybalboa.enums.TemperatureUnit.CELSIUS else 'f') \
            .field("max", spa.temperature_maximum) \
            .field("min", spa.temperature_minimum)

        if type(temperature) is float or type(temperature) is int:
            temp = temp.field("value", temperature)
        else:
            logger.info(
                '[balboa] Temperature reading failed. Discarding temp data...')

        if spa.heat_mode.state != UnknownState.UNKNOWN:
            heat = Point("spa_mode") \
                .field("enabled", spa.heat_mode.state)
        else:
            logger.info(
                '[balboa] Heat mode state unknown. Discarding mode data...')
            heat = None

        circ = Point("spa_circulation_pump") \
            .field("enabled", spa.circulation_pump.state)

        logger.info("[balboa] Disconnecting from spa")
        await spa.disconnect()

        write_influx([p for p in [temp, heat, circ] if p is not None])


def balboa_control():
    """Hourly task to control spa heat mode based on energy price peak hours."""
    if not spa_ip:
        logger.error(
            "[balboa_control] BALBOA_HOST environment variable not set, ignoring...")
        return

    try:
        asyncio.run(_balboa_control())
    except KeyboardInterrupt:
        pass
    except pybalboa.exceptions.SpaConnectionError:
        logger.info(
            "[balboa_control] Unable to connect to balboa spa, will retry next hour")
    except AttributeError as e:
        logger.error(
            f"[balboa_control] API error - verify pybalboa version: {e}", exc_info=True)
    except Exception:
        logger.exception(
            "[balboa_control] Unexpected error in balboa spa control")


async def _balboa_control():
    """Async implementation of spa heat mode control."""
    logger.info(
        "[balboa_control] Connecting to Balboa Spa for heat mode control...")

    async with pybalboa.SpaClient(spa_ip) as spa:
        await spa.connect()

        # Wait for spa to be ready (max 60 seconds)
        for _ in range(12):
            if spa.available:
                break
            logger.info("[balboa_control] Waiting for spa to be ready...")
            await asyncio.sleep(5)
        else:
            logger.warning("[balboa_control] Spa did not become available after 60s, aborting")
            return

        if not spa.connected:
            logger.warning(
                "[balboa_control] Not connected to spa, aborting control")
            await spa.disconnect()
            return

        if spa.heat_mode.state == UnknownState.UNKNOWN:
            logger.warning(
                "[balboa_control] Heat mode state unknown, skipping this run")
            await spa.disconnect()
            return

        # Safety check: Don't change mode if any pumps are running
        pumps_running = []
        for i, pump in enumerate(spa.pumps, start=1):
            # Check if pump state is not OFF (typically state > 0 means running)
            if pump.state and pump.state.value > 0:
                pumps_running.append(f"Pump {i}")

        if pumps_running:
            logger.info(
                f"[balboa_control] Skipping heat mode change - pumps active: {', '.join(pumps_running)}")
            await spa.disconnect()
            return

        # Determine desired mode
        desired_mode = get_desired_heat_mode()
        current_mode = spa.heat_mode.state

        # Get mode names for logging
        desired_name = desired_mode.name.lower()
        current_name = current_mode.name.lower()
        current_time = datetime.now(STOCKHOLM_TZ)

        logger.info(
            f"[balboa_control] Current time: {current_time.strftime('%H:%M %Z')} (hour {current_time.hour})")
        logger.info(
            f"[balboa_control] Current mode: {current_name}, Desired mode: {desired_name}")

        # Only change if different
        if current_mode != desired_mode:
            logger.info(
                f"[balboa_control] Changing heat mode from {current_name} to {desired_name}")

            success = await spa.heat_mode.set_state(desired_mode)

            if success:
                logger.info(
                    f"[balboa_control] Successfully changed heat mode to {desired_name}")

                # Optional: Write control event to InfluxDB for tracking
                control_point = Point("spa_heat_mode_control") \
                    .field("mode", int(desired_mode)) \
                    .field("changed", True) \
                    .field("previous_mode", int(current_mode)) \
                    .field("hour", current_time.hour)
                write_influx([control_point])
            else:
                logger.error(
                    f"[balboa_control] Failed to change heat mode to {desired_name}")
        else:
            logger.info(
                f"[balboa_control] Heat mode already set to {desired_name}, no change needed")

        await spa.disconnect()
