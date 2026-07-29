import os
import requests
import logging
import hashlib

from typing import Dict, List, Optional, Tuple
from pprint import pformat
from influx import write_influx, Point

from _decorators import memoize_for_hours

# Configure module-specific logger
logger = logging.getLogger(__name__)

CODES = {
    'T02': 'temp_incoming',
    'T03': 'temp_outgoing',
    'T05': 'temp_ambient',
    'R02': 'temp_target',
    'power': 'power_mode',
    'Manual-mute': 'silenced',
}

# Codes we fetch but don't emit as their own fields — used only as inputs
# to the derived `power_usage` metric (compressor current × supply voltage).
# Per the radical-squared/aquatemp HA integration's per-device entity map
# (entity_description.1442284873216843776.json), T07 is "Compressor current
# Detect [A]" and T14 is "Inverter plate AC voltage [V]". The legacy default
# parameter map said T06=amper / T13=volt, which is correct for older units
# but NOT this device — verified live: T07 reports 9.5, T14 reports 222 (V).
DERIVATION_CODES = ('T07', 'T14')

PROTOCOL_CODES = list(CODES.keys()) + list(DERIVATION_CODES)


def aquatemp():
    try:
        _aquatemp()
    except Exception as e:
        logger.exception(f"[aquatemp] Failed to execute aquatemp module: {e}")


cloudurl = os.environ['AQUATEMP_BASEURL']


@memoize_for_hours(24)
def getToken() -> Optional[Tuple[str, str]]:
    username = os.environ['AQUATEMP_USERNAME']
    password = os.environ['AQUATEMP_PASSWORD']

    # Step 1: Get token
    hashed_password = hashlib.md5(password.encode()).hexdigest()
    login_payload = {
        "userName": username,
        "password": hashed_password,
        "type": '2',
        "appId": '14',
        "loginSource": 'IOS'
    }

    logger.info('[aquatemp] Getting new AquaTemp token...')
    login_response = requests.post(
        f"{cloudurl}/app/user/login?lang=en", json=login_payload, timeout=30)

    if login_response.status_code != 200:
        logger.error(
            f"[aquatemp] Failed to login: {login_response.url} {login_response.status_code} {login_response.text}")
        return None

    resp = login_response.json()
    token = resp.get('objectResult', {}).get('x-token')
    user_id = resp.get('objectResult', {}).get('userId')

    if not token:
        logger.error(
            "[aquatemp] Failed to retrieve token from login response. \n%s", login_response.json())
        return None
    return token, user_id


def getDevices(token: str, user_id: str) -> List[Dict[str, str]]:
    headers = {"x-token": token}
    devices_response = requests.post(
        f"{cloudurl}/app/device/deviceList?lang=en", headers=headers, json={
            'appId': '14',
        }, timeout=30)
    if devices_response.status_code != 200:
        logger.error(
            f"[aquatemp] Failed to fetch device list: {devices_response.text}")
        return []
    devices_response = devices_response.json().get('objectResult', [])
    logger.info(f"[aquatemp] Found {len(devices_response)} devices")

    devices_response_share = requests.post(
        f"{cloudurl}/app/device/getMyAppectDeviceShareDataList?lang=en", headers=headers, json={
            'appId': '14',
            'toUser': user_id
        }, timeout=30)
    if devices_response_share.status_code != 200:
        logger.error(
            f"[aquatemp] Failed to fetch shared devices: {devices_response_share.text}")
        return []
    devices_response_share = devices_response_share.json().get('objectResult', [])
    logger.info(
        f"[aquatemp] Found {len(devices_response_share)} shared devices")

    out = devices_response + devices_response_share
    return out


def getDeviceData(token: str, deviceCode: str) -> Optional[list[Dict[str, str]]]:
    headers = {"x-token": token}
    deviceData_response = requests.post(
        f"{cloudurl}/app/device/getDataByCode?lang=en", headers=headers, json={
            'deviceCode': deviceCode,
            'protocalCodes': PROTOCOL_CODES,
            'appId': '14',
        }, timeout=30)
    if deviceData_response.status_code != 200:
        logger.error(
            f"[aquatemp] Failed to fetch device data: {deviceData_response.text}")
        return None

    return deviceData_response.json().get('objectResult', [])


def _aquatemp():
    token_data = getToken()
    if not token_data:
        logger.error(
            "[aquatemp] Failed to get token for Aquatemp, aborting task.")
        return
    token, user_id = token_data

    logger.info("[aquatemp] Fetching Aquatemp device list...")
    devices = getDevices(token, user_id)

    points: List[Point] = []
    if not devices:
        logger.error("[aquatemp] No devices found or failed to fetch devices.")
        return

    logger.info(f"[aquatemp] Found {len(devices)} Aquatemp devices")

    for device in devices:
        deviceCode = device.get('deviceCode')

        if not deviceCode:
            logger.warning(
                f"[aquatemp] Device {device.get('deviceNickName', 'Unknown')} has no deviceCode, skipping.")
            continue

        deviceData = getDeviceData(token, deviceCode)

        if deviceData is None:
            logger.warning(
                f"[aquatemp] Device {deviceCode} has no data, skipping.")
            continue

        # Collect every returned value first so we can both write the
        # straightforward fields and derive composites (e.g. power = I × V).
        values: Dict[str, float] = {}
        for deviceDataObject in deviceData:
            code = deviceDataObject['code']
            raw = deviceDataObject.get('value')
            if raw in (None, ''):
                logger.warning(
                    f"[aquatemp] Device {deviceCode} has no value for {code}, skipping.")
                continue
            values[code] = float(raw)

        p = Point('aqua_temp')\
            .tag('device_name', device['deviceNickName'])\
            .tag('device_id', device['deviceId'])\
            .tag('device_model', device['custModel'])

        has_fields = False
        for code, metricName in CODES.items():
            if code in values:
                p = p.field(metricName, values[code])
                has_fields = True

        # Derived input power: compressor current (T07, A) × inverter plate
        # AC voltage (T14, V). Stored under the same `power_usage` field name
        # as before but now reflects real instantaneous draw instead of the
        # previous T12-as-power misread (T12 is actually fan target RPM).
        if 'T07' in values and 'T14' in values:
            p = p.field('power_usage', values['T07'] * values['T14'])
            has_fields = True

        if has_fields:
            points.append(p)
        else:
            logger.warning(
                f"[aquatemp] Device {deviceCode} has no valid data points, skipping influx write for this device.")

    write_influx(points)
