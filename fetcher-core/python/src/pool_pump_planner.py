import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pulp
import requests

from influx import (
    Point,
    first_series_values,
    query_prom_instant,
    query_prom_range,
    write_influx,
)

logger = logging.getLogger(__name__)

# Location and PV array (reused from airquality.py convention)
GOOGLE_LAT_LNG = os.environ.get('GOOGLE_LAT_LNG', '')

# Solar forecast (forecast.solar — free tier, no key required)
POOL_PV_DECLINATION = float(os.environ.get('POOL_PV_DECLINATION', '30'))
POOL_PV_AZIMUTH = float(os.environ.get('POOL_PV_AZIMUTH', '0'))  # 0 = south, -90 = east, 90 = west
POOL_PV_KWP = float(os.environ.get('POOL_PV_KWP', '3.0'))

# Pump parameters
POOL_PUMP_KW = float(os.environ.get('POOL_PUMP_KW', '4.0'))
POOL_GRID_FEE_SEK_PER_KWH = float(os.environ.get('POOL_GRID_FEE_SEK_PER_KWH', '0.80'))

# Scheduling constraints
POOL_MIN_HOURS = int(os.environ.get('POOL_MIN_HOURS', '4'))       # hard floor
POOL_TARGET_HOURS = int(os.environ.get('POOL_TARGET_HOURS', '6')) # soft target
POOL_MAX_HOURS = int(os.environ.get('POOL_MAX_HOURS', '10'))
POOL_MAX_STARTS = int(os.environ.get('POOL_MAX_STARTS', '2'))
POOL_BLOCKED_HOURS = [
    int(h) for h in os.environ.get('POOL_BLOCKED_HOURS', '7,8,17,18,19,20').split(',') if h.strip()
]

# Safe-mode schedule used when any required input is missing.
POOL_FALLBACK_NIGHT_HOURS = [
    int(h) for h in os.environ.get('POOL_FALLBACK_NIGHT_HOURS', '1,2,3,4').split(',') if h.strip()
]
POOL_FALLBACK_AFTERNOON_HOURS = [
    int(h) for h in os.environ.get('POOL_FALLBACK_AFTERNOON_HOURS', '12,13,14,15').split(',') if h.strip()
]

# Temperature-driven target-hours override (optional)
POOL_TARGET_TEMP_C = float(os.environ.get('POOL_TARGET_TEMP_C', '29'))
POOL_HEATING_RATE_C_PER_HOUR = float(os.environ.get('POOL_HEATING_RATE_C_PER_HOUR', '0'))

PRICE_AREA = os.environ.get('POOL_PRICE_AREA', 'SE4')

HORIZON_HOURS = 24


def pool_pump_planner():
    try:
        _plan()
    except Exception:
        logger.exception("[pool_pump_planner] Failed to plan pump schedule")


def _plan():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    slots = [now + timedelta(hours=i) for i in range(HORIZON_HOURS)]

    prices = _fetch_prices(slots)
    solar = _fetch_solar_forecast(slots)
    water_temp = _fetch_water_temp()

    missing = _missing_inputs(prices, water_temp)
    if missing:
        logger.warning("[pool_pump_planner] Missing inputs %s, using fallback schedule", missing)
        schedule = _fallback_schedule(slots)
        stats = _fallback_stats(schedule, prices, solar)
        _write_plan(
            slots, schedule, prices, solar, stats, water_temp,
            target_hours=len(POOL_FALLBACK_NIGHT_HOURS) + len(POOL_FALLBACK_AFTERNOON_HOURS),
            mode='fallback', missing=missing,
        )
        return

    target_hours = _compute_target_hours(water_temp)

    logger.info(
        "[pool_pump_planner] horizon=%dh target_hours=%d water_temp=%s min=%d max=%d",
        HORIZON_HOURS, target_hours, water_temp, POOL_MIN_HOURS, POOL_MAX_HOURS,
    )

    schedule, stats = _solve(prices, solar, target_hours, slots)
    if schedule is None:
        logger.warning("[pool_pump_planner] MILP infeasible, using fallback schedule")
        schedule = _fallback_schedule(slots)
        stats = _fallback_stats(schedule, prices, solar)
        _write_plan(
            slots, schedule, prices, solar, stats, water_temp,
            target_hours=len(POOL_FALLBACK_NIGHT_HOURS) + len(POOL_FALLBACK_AFTERNOON_HOURS),
            mode='fallback', missing='infeasible',
        )
        return

    _write_plan(slots, schedule, prices, solar, stats, water_temp,
                target_hours=target_hours, mode='optimal', missing='')


def _missing_inputs(prices: List[Optional[float]], water_temp: Optional[float]) -> str:
    missing = []
    if not prices or sum(1 for p in prices if p is not None) < HORIZON_HOURS:
        missing.append('prices')
    if water_temp is None:
        missing.append('water_temp')
    return ','.join(missing)


def _fallback_schedule(slots: List[datetime]) -> List[int]:
    """Deterministic 4h-night + 4h-afternoon schedule in site-local time."""
    night = set(POOL_FALLBACK_NIGHT_HOURS)
    afternoon = set(POOL_FALLBACK_AFTERNOON_HOURS)
    return [1 if slot.astimezone().hour in night or slot.astimezone().hour in afternoon else 0
            for slot in slots]


def _fallback_stats(schedule: List[int], prices: List[Optional[float]], solar: List[float]) -> Dict:
    cost_per_hour: List[float] = []
    total = 0.0
    for t in range(HORIZON_HOURS):
        p = prices[t] if prices and prices[t] is not None else 0.0
        grid_kwh = max(0.0, POOL_PUMP_KW - (solar[t] if solar else 0.0))
        c = POOL_PUMP_KW * p + grid_kwh * POOL_GRID_FEE_SEK_PER_KWH
        cost_per_hour.append(c)
        if schedule[t]:
            total += c
    return {
        'planned_hours': sum(schedule),
        'expected_cost_sek': total,
        'slack': 0.0,
        'cost_per_hour': cost_per_hour,
    }


def _fetch_prices(slots: List[datetime]) -> List[Optional[float]]:
    start = slots[0].timestamp()
    end = slots[-1].timestamp()
    promql = f'energy_price_SEK_per_kWh{{area="{PRICE_AREA}"}}'
    result = query_prom_range(promql, start=start, end=end, step=3600)
    if not result:
        # Field-name convention may vary; fall back to the base metric
        result = query_prom_range(f'energy_price{{area="{PRICE_AREA}"}}', start=start, end=end, step=3600)
    series = first_series_values(result)
    by_hour: Dict[int, float] = {int(ts) // 3600 * 3600: v for ts, v in series}
    return [by_hour.get(int(s.timestamp())) for s in slots]


def _fetch_solar_forecast(slots: List[datetime]) -> List[float]:
    """Return kWh of PV production estimated for each slot hour."""
    if not GOOGLE_LAT_LNG:
        logger.info("[pool_pump_planner] No GOOGLE_LAT_LNG, skipping solar forecast")
        return [0.0] * len(slots)
    try:
        lat, lng = [s.strip() for s in GOOGLE_LAT_LNG.split(',')]
        url = (
            f"https://api.forecast.solar/estimate/watthours/period/"
            f"{lat}/{lng}/{POOL_PV_DECLINATION}/{POOL_PV_AZIMUTH}/{POOL_PV_KWP}"
        )
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        raw = resp.json().get('result', {}) or {}
    except Exception:
        logger.exception("[pool_pump_planner] forecast.solar lookup failed")
        return [0.0] * len(slots)

    # forecast.solar returns local-time keys 'YYYY-MM-DD HH:MM:SS' → Wh for that period.
    # We align by hour-of-date in the API's local tz (lat/lon based). Simplification:
    # bucket by "date hour" string in both keys.
    by_key: Dict[str, float] = {}
    for k, wh in raw.items():
        try:
            dt = datetime.fromisoformat(k)
            key = dt.strftime('%Y-%m-%d %H')
            by_key[key] = by_key.get(key, 0.0) + float(wh) / 1000.0  # Wh → kWh
        except Exception:
            continue

    # Match slot timestamps in local time (forecast.solar uses site-local tz; we compare
    # on an hourly basis in UTC which is close enough for high-latitude scheduling).
    out = []
    for s in slots:
        local = s.astimezone()
        key = local.strftime('%Y-%m-%d %H')
        out.append(by_key.get(key, 0.0))
    return out


def _fetch_water_temp() -> Optional[float]:
    result = query_prom_instant('pool_temperatur_value')
    if not result:
        return None
    try:
        return float(result[0]['value'][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _compute_target_hours(water_temp: Optional[float]) -> int:
    if water_temp is None or POOL_HEATING_RATE_C_PER_HOUR <= 0:
        return POOL_TARGET_HOURS
    delta = max(0.0, POOL_TARGET_TEMP_C - water_temp)
    needed = math.ceil(delta / POOL_HEATING_RATE_C_PER_HOUR)
    return max(POOL_TARGET_HOURS, min(POOL_MAX_HOURS, needed))


def _solve(
    prices: List[Optional[float]],
    solar: List[float],
    target_hours: int,
    slots: List[datetime],
):
    T = list(range(HORIZON_HOURS))

    # Cost per hour if the pump runs:
    #   opportunity spot cost for full pump draw + grid fee only for the grid portion
    cost = []
    for t in T:
        p = prices[t] if prices[t] is not None else 0.0
        grid_kwh = max(0.0, POOL_PUMP_KW - solar[t])
        cost.append(POOL_PUMP_KW * p + grid_kwh * POOL_GRID_FEE_SEK_PER_KWH)

    # Hours unavailable: missing price, or blocked by clock-hour in site-local tz.
    blocked = set()
    for t in T:
        if prices[t] is None:
            blocked.add(t)
            continue
        local_hour = slots[t].astimezone().hour
        if local_hour in POOL_BLOCKED_HOURS:
            blocked.add(t)

    available = [t for t in T if t not in blocked]
    if len(available) < POOL_MIN_HOURS:
        logger.warning(
            "[pool_pump_planner] Only %d available hours after blocking (need >= %d)",
            len(available), POOL_MIN_HOURS,
        )
        return None, {}

    prob = pulp.LpProblem("pool_pump", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", T, cat=pulp.LpBinary)
    y = pulp.LpVariable.dicts("start", T, cat=pulp.LpBinary)  # start indicator
    slack = pulp.LpVariable("slack", lowBound=0)

    # Penalty per missed hour must exceed the worst single-hour cost so the solver only
    # falls back when it's truly infeasible within the hard floor.
    big_m = max(cost) * 10 + 100

    prob += pulp.lpSum(cost[t] * x[t] for t in T) + big_m * slack

    for t in blocked:
        prob += x[t] == 0

    prob += pulp.lpSum(x[t] for t in T) >= POOL_MIN_HOURS
    prob += pulp.lpSum(x[t] for t in T) + slack >= target_hours
    prob += pulp.lpSum(x[t] for t in T) <= min(POOL_MAX_HOURS, len(available))

    # Start-indicator linking: y_t >= x_t - x_{t-1}
    for t in T:
        prev = x[t - 1] if t > 0 else 0
        prob += y[t] >= x[t] - prev
    prob += pulp.lpSum(y[t] for t in T) <= POOL_MAX_STARTS

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != 'Optimal':
        logger.warning("[pool_pump_planner] Solver status: %s", pulp.LpStatus[status])
        return None, {}

    schedule = [int(round(pulp.value(x[t]))) for t in T]
    planned_hours = sum(schedule)
    expected_cost = sum(cost[t] for t in T if schedule[t])
    stats = {
        'planned_hours': planned_hours,
        'expected_cost_sek': expected_cost,
        'slack': float(pulp.value(slack) or 0),
        'cost_per_hour': cost,
    }
    return schedule, stats


def _write_plan(slots, schedule, prices, solar, stats, water_temp, target_hours,
                mode: str = 'optimal', missing: str = ''):
    points: List[Point] = []
    for t, slot in enumerate(slots):
        p = Point("pool_iqpump_plan") \
            .tag("horizon", f"{HORIZON_HOURS}h") \
            .tag("mode", mode) \
            .field("on", int(schedule[t])) \
            .field("cost_sek", float(stats['cost_per_hour'][t])) \
            .field("price_sek_per_kwh", float(prices[t]) if prices and prices[t] is not None else 0.0) \
            .field("solar_kwh", float(solar[t]) if solar else 0.0) \
            .time(slot.isoformat())
        points.append(p)

    summary = Point("pool_iqpump_plan_summary") \
        .tag("horizon", f"{HORIZON_HOURS}h") \
        .tag("mode", mode) \
        .field("planned_hours", int(stats['planned_hours'])) \
        .field("target_hours", int(target_hours)) \
        .field("expected_cost_sek", float(stats['expected_cost_sek'])) \
        .field("slack_hours", float(stats['slack'])) \
        .field("water_temp_c", float(water_temp) if water_temp is not None else 0.0) \
        .field("missing_inputs", missing) \
        .time(slots[0].isoformat())
    points.append(summary)

    write_influx(points)
    logger.info(
        "[pool_pump_planner] Plan written (mode=%s): %d/%d hours, cost=%.2f SEK (slack=%.1f missing=%s)",
        mode, stats['planned_hours'], target_hours, stats['expected_cost_sek'], stats['slack'], missing or '-',
    )
