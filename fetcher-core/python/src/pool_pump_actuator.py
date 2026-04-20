import logging
import os
from datetime import datetime, timezone
from typing import Optional

from influx import Point, query_prom_instant, write_influx

logger = logging.getLogger(__name__)

POOL_PUMP_ACTUATOR_DRY_RUN = os.environ.get('POOL_PUMP_ACTUATOR_DRY_RUN', 'true').lower() in ('1', 'true', 'yes')
POOL_PUMP_SPEED_ON = int(os.environ.get('POOL_PUMP_SPEED_ON', '100'))


def pool_pump_actuator():
    try:
        _act()
    except Exception:
        logger.exception("[pool_pump_actuator] Failed to act on plan")


def _act():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    desired = _fetch_current_plan_value(now)
    if desired is None:
        logger.info("[pool_pump_actuator] No plan for %s, skipping", now.isoformat())
        return

    current_on = _fetch_current_pump_on()
    action = _decide(current_on, desired)

    logger.info(
        "[pool_pump_actuator] plan=%s current_on=%s action=%s dry_run=%s",
        desired, current_on, action, POOL_PUMP_ACTUATOR_DRY_RUN,
    )

    executed = False
    if action != 'noop' and not POOL_PUMP_ACTUATOR_DRY_RUN:
        executed = _apply(action == 'on')

    point = Point("pool_iqpump_control") \
        .tag("action", action) \
        .tag("dry_run", str(POOL_PUMP_ACTUATOR_DRY_RUN).lower()) \
        .field("desired_on", int(desired)) \
        .field("current_on", int(current_on) if current_on is not None else -1) \
        .field("executed", int(executed)) \
        .time(now.isoformat())
    write_influx([point])


def _fetch_current_plan_value(now: datetime) -> Optional[int]:
    result = query_prom_instant('pool_iqpump_plan_on')
    if not result:
        result = query_prom_instant('pool_iqpump_plan')
    if not result:
        return None
    try:
        return int(round(float(result[0]['value'][1])))
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _fetch_current_pump_on() -> Optional[bool]:
    result = query_prom_instant('pool_iqpump_motordata_speed')
    if not result:
        return None
    try:
        speed = float(result[0]['value'][1])
        return speed > 0
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _decide(current_on: Optional[bool], desired: int) -> str:
    want_on = desired == 1
    if current_on is None:
        return 'on' if want_on else 'off'
    if want_on and not current_on:
        return 'on'
    if not want_on and current_on:
        return 'off'
    return 'noop'


def _apply(turn_on: bool) -> bool:
    """Execute the state change on the pump. Stub — wire up once the iaqualink
    control command contract is confirmed. Returns True if the call succeeded."""
    logger.warning(
        "[pool_pump_actuator] _apply(turn_on=%s) not yet wired to iaqualink /control.json",
        turn_on,
    )
    return False
