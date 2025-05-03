import base64
import os
from typing import List, Optional
import requests
import logging
import hashlib

from pprint import pformat
from influx import write_influx, Point

from _decorators import memoize_for_hours


CODES = {
    'T02': 'temp_incoming',
    'T03': 'temp_outgoing',
    'T05': 'temp_ambient',
    'R02': 'temp_target',
    'T12': 'power_usage',
    'power': 'power_mode',
    'Manual-mute': 'silenced'
}


def aquatemp():
    try:
        _aquatemp()
    except Exception as e:
        logging.exception(f"Failed to execute aquatemp module: {e}")


cloudurl = os.environ['AQUATEMP_BASEURL']


@memoize_for_hours(24)
def getToken() -> Optional[str]:
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

    logging.info('Getting new AquaTemp token...')
    login_response = requests.post(
        f"{cloudurl}/app/user/login?lang=en", json=login_payload)

    if login_response.status_code != 200:
        logging.error(
            f"Failed to login: {login_response.url} {login_response.status_code} {login_response.text}")
        return None

    resp = login_response.json()
    token = resp.get('objectResult', {}).get('x-token')

    if not token:
        logging.error(
            "Failed to retrieve token from login response. \n%s", login_response.json())
        return None
    return token


def getDevices(token: str) -> List[dict]:
    headers = {"x-token": token}
    devices_response = requests.post(
        f"{cloudurl}/app/device/deviceList?lang=en", headers=headers, json={
            'appId': '14',
        })
    if devices_response.status_code != 200:
        logging.error(f"Failed to fetch device list: {devices_response.text}")
        return

    return devices_response.json().get('objectResult', [])


def getDeviceData(token: str, deviceCode: str) -> Optional[list[dict]]:
    headers = {"x-token": token}
    deviceData_response = requests.post(
        f"{cloudurl}/app/device/getDataByCode?lang=en", headers=headers, json={
            'deviceCode': deviceCode,
            'protocalCodes': list(CODES.keys()),
            'appId': '14',
        })
    if deviceData_response.status_code != 200:
        logging.error(
            f"Failed to fetch device data: {deviceData_response.text}")
        return None

    return deviceData_response.json().get('objectResult', [])


def _aquatemp():
    logging.info("Fetching Aquatemp device list...")

    token = getToken()
    devices = getDevices(token)

    points: List[Point] = []

    for device in devices:
        deviceCode = device.get('deviceCode')

        deviceData = getDeviceData(token, deviceCode)

        p = Point('aqua_temp')\
            .tag('device_name', device['deviceNickName'])\
            .tag('device_id', device['deviceId'])\
            .tag('device_model', device['custModel'])

        for deviceDataObject in deviceData:
            metricName = CODES.get(
                deviceDataObject['code'], 'unknown_' + deviceDataObject['code'])
            p = p.field(metricName, float(deviceDataObject['value']))

        points.append(p)

    write_influx(points)
