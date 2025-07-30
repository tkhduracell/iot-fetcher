import pybalboa
import pybalboa.enums

import datetime
import os
import asyncio
import logging

from influxdb_client import Point

from influx import write_influx

# Configure module-specific logger
logger = logging.getLogger(__name__)

# Balboa configuration
spa_ip = os.environ['BALBOA_HOST'] or '192.168.68.53'


def balboa():
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

        # wait for the spa to be ready for use
        while not spa.available:
            logger.info("[balboa] Waiting for spa to be ready...")
            await asyncio.sleep(5)

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

        heat = Point("spa_mode") \
            .field("enabled", spa.heat_mode.state)

        circ = Point("spa_circulation_pump") \
            .field("enabled", spa.circulation_pump.state)

        logger.info("[balboa] Disconnecting from spa")
        await spa.disconnect()

        write_influx([temp, heat, circ])
