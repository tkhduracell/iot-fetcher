import base64
import json
import logging
import os
import time
from typing import List, Optional

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from influx import write_influx, Point

logger = logging.getLogger(__name__)

eufy_username = os.environ.get('EUFY_USERNAME', '')
eufy_password = os.environ.get('EUFY_PASSWORD', '')
eufy_country = os.environ.get('EUFY_COUNTRY', 'se')
gemini_token = os.environ.get('GEMINI_TOKEN', '')

DOMAIN_BASE = "https://extend.eufylife.com"
SERVER_PUBLIC_KEY_HEX = "04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a3868d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076"

BASE_HEADERS = {
    "App_version": "v4.6.0_1630",
    "Os_type": "android",
    "Os_version": "31",
    "Phone_model": "ONEPLUS A3003",
    "Language": "en",
    "Openudid": "5e4621b0152c0d00",
    "Net_type": "wifi",
    "Mnc": "02",
    "Mcc": "262",
    "Sn": "75814221ee75",
    "Model_type": "PHONE",
    "Timezone": "GMT+01:00",
    "Cache-Control": "no-cache",
}

# Param types we extract as InfluxDB fields (from eufy-security-client CommandType)
PARAM_BATTERY = 1101           # CMD_GET_BATTERY (%)
PARAM_BATTERY_TEMP = 1138      # CMD_GET_BATTERY_TEMP (°C)
PARAM_WIFI_RSSI = 1142         # CAMERA_WIFI_RSSI (dBm)
PARAM_SPEAKER_VOLUME = 1230    # CAMERA_SPEAKER_VOLUME
PARAM_PIR = 1011               # CAMERA_PIR (motion detection on/off)
PARAM_IR_CUT = 1013            # CAMERA_IR_CUT
PARAM_FLOODLIGHT_SWITCH = 1400 # FLOODLIGHT_MANUAL_SWITCH
PARAM_FLOODLIGHT_BRIGHTNESS = 1401  # FLOODLIGHT_MANUAL_BRIGHTNESS


def _encrypt(plaintext: str, shared_key: bytes) -> str:
    key = shared_key[:32]
    iv = shared_key[:16]
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ct).decode()


def _decrypt(ciphertext: str, shared_key: bytes) -> bytes:
    key = shared_key[:32]
    iv = shared_key[:16]
    ct = base64.b64decode(ciphertext)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _ecdh_shared_secret(private_key, server_pub_hex):
    server_pub_bytes = bytes.fromhex(server_pub_hex)
    x = int.from_bytes(server_pub_bytes[1:33], 'big')
    y = int.from_bytes(server_pub_bytes[33:65], 'big')
    server_pub_key = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    return private_key.exchange(ec.ECDH(), server_pub_key)


def _solve_captcha(captcha_img: str) -> Optional[str]:
    """Solve CAPTCHA using Gemini API. SVG sent as text, PNG via vision. Returns answer or None."""
    if not gemini_token:
        logger.warning("[eufy] GEMINI_TOKEN not set, cannot auto-solve CAPTCHA")
        return None

    if not captcha_img.startswith("data:"):
        logger.warning("[eufy] CAPTCHA image is not a data URI, cannot solve")
        return None

    header, b64data = captcha_img.split(",", 1)
    mime_type = header.split(":")[1].split(";")[0]
    prompt = "What text does this CAPTCHA image show? Reply with ONLY the characters, nothing else."

    if "svg" in mime_type:
        svg_text = base64.b64decode(b64data).decode(errors="replace")
        parts = [{"text": f"{prompt}\n\n{svg_text}"}]
    else:
        parts = [
            {"text": prompt},
            {"inline_data": {"mime_type": mime_type, "data": b64data}},
        ]

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-preview:generateContent?key={gemini_token}",
        json={"contents": [{"parts": parts}]},
        timeout=120,
    )
    resp.raise_for_status()
    answer = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    logger.info("[eufy] Gemini solved CAPTCHA: %s", answer)
    return answer


def _login(session: requests.Session, api_base: str) -> tuple:
    """Login and return (token, shared_key)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_numbers = private_key.public_key().public_numbers()
    client_pub_hex = "04" + format(pub_numbers.x, '064x') + format(pub_numbers.y, '064x')

    shared_key = _ecdh_shared_secret(private_key, SERVER_PUBLIC_KEY_HEX)
    encrypted_password = _encrypt(eufy_password, shared_key)

    tz_offset = time.timezone if time.daylight == 0 else time.altzone
    tz_ms = tz_offset * 1000

    login_data = {
        "ab": eufy_country.upper(),
        "client_secret_info": {"public_key": client_pub_hex},
        "enc": 0,
        "email": eufy_username,
        "password": encrypted_password,
        "time_zone": tz_ms,
        "transaction": str(int(time.time() * 1000)),
    }

    resp = session.post(f"{api_base}/v2/passport/login_sec", json=login_data)
    resp.raise_for_status()
    result = resp.json()

    code = result.get("code")
    if code in (100032, 100033):
        captcha_data = result.get("data", {})
        if isinstance(captcha_data, str):
            captcha_data = json.loads(_decrypt(captcha_data, shared_key))
        captcha_id = captcha_data.get("captcha_id", "")
        captcha_img = captcha_data.get("item", "")

        answer = _solve_captcha(captcha_img) if captcha_img else None
        if not answer:
            raise RuntimeError(f"Eufy login requires CAPTCHA (captcha_id={captcha_id}) and auto-solve failed")

        # Retry login with captcha answer
        login_data["captcha_id"] = captcha_id
        login_data["answer"] = answer
        resp = session.post(f"{api_base}/v2/passport/login_sec", json=login_data)
        resp.raise_for_status()
        result = resp.json()
        code = result.get("code")

    if code != 0:
        raise RuntimeError(f"Eufy login failed (code={code}): {result.get('msg')}")

    data = result["data"]
    if isinstance(data, str):
        data = json.loads(_decrypt(data, shared_key))

    token = data["auth_token"]

    server_key_info = data.get("server_secret_info")
    if server_key_info and server_key_info.get("public_key"):
        shared_key = _ecdh_shared_secret(private_key, server_key_info["public_key"])

    logger.info("[eufy] Logged in as %s", data.get("nick_name", data.get("email")))
    return token, shared_key


def _api_request(session: requests.Session, api_base: str, endpoint: str, token: str, shared_key: bytes, json_data=None):
    """Make authenticated API request, decrypting v2 responses."""
    resp = session.post(
        f"{api_base}/{endpoint}",
        headers={"X-Auth-Token": token},
        json=json_data or {},
    )
    resp.raise_for_status()
    result = resp.json()

    if result.get("code") != 0:
        raise RuntimeError(f"Eufy API {endpoint} failed (code={result.get('code')}): {result.get('msg')}")

    data = result.get("data", [])
    if isinstance(data, str):
        data = json.loads(_decrypt(data, shared_key))
    return data


def _get_devices(session, api_base, token, shared_key) -> list:
    return _api_request(session, api_base, "v2/house/device_list", token, shared_key, {
        "device_sn": "", "num": 100, "orderby": "", "page": 0, "station_sn": "",
    })


def _parse_params(params_list: list) -> dict:
    """Parse params list into {param_type: param_value} dict."""
    result = {}
    for p in (params_list or []):
        result[p.get("param_type")] = p.get("param_value")
    return result


SNAPSHOT_DIR = "/tmp/eufy_snapshots"

# Cached cover_path URLs from last _eufy() run: [{device_sn, device_name, cover_path}, ...]
_device_covers: list = []


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def eufy():
    if not eufy_username or not eufy_password:
        logger.error("[eufy] EUFY_USERNAME/EUFY_PASSWORD not set, ignoring...")
        return
    try:
        _eufy()
    except Exception:
        logger.exception("[eufy] Failed to execute eufy module")


def _eufy():
    logger.info("[eufy] Fetching Eufy device data...")

    # Resolve API domain
    resp = requests.get(f"{DOMAIN_BASE}/domain/{eufy_country.upper()}")
    resp.raise_for_status()
    domain_data = resp.json()
    if domain_data.get("code") != 0:
        raise RuntimeError(f"Domain resolution failed: {domain_data.get('msg')}")
    api_base = f"https://{domain_data['data']['domain']}"

    session = requests.Session()
    session.headers.update(BASE_HEADERS)
    session.headers["Country"] = eufy_country.upper()

    # Login
    token, shared_key = _login(session, api_base)

    # Fetch devices
    devices = _get_devices(session, api_base, token, shared_key)
    logger.info("[eufy] Found %d device(s)", len(devices))

    points: List[Point] = []

    # Cache cover_path URLs for eufy_snapshot()
    _device_covers.clear()
    for dev in devices:
        cover_path = dev.get("cover_path", "")
        if cover_path:
            _device_covers.append({
                "device_sn": dev.get("device_sn", "unknown"),
                "device_name": dev.get("device_name", "unknown"),
                "cover_path": cover_path,
            })

    for dev in devices:
        params = _parse_params(dev.get("params"))

        point = Point("eufy_device") \
            .tag("device_sn", dev.get("device_sn", "")) \
            .tag("device_name", dev.get("device_name", "")) \
            .tag("device_model", dev.get("device_model", ""))

        # Battery fields
        if PARAM_BATTERY in params:
            point.field("battery", _safe_int(params[PARAM_BATTERY]))
        if PARAM_BATTERY_TEMP in params:
            point.field("batteryTemperature", _safe_int(params[PARAM_BATTERY_TEMP]))

        # Network & audio
        if PARAM_WIFI_RSSI in params:
            point.field("wifiRssi", _safe_int(params[PARAM_WIFI_RSSI]))
        if PARAM_SPEAKER_VOLUME in params:
            point.field("speakerVolume", _safe_int(params[PARAM_SPEAKER_VOLUME]))

        # Detection
        if PARAM_PIR in params:
            point.field("pirEnabled", _safe_int(params[PARAM_PIR]))
        if PARAM_IR_CUT in params:
            point.field("irCut", _safe_int(params[PARAM_IR_CUT]))

        # Floodlight
        if PARAM_FLOODLIGHT_SWITCH in params:
            point.field("floodlightSwitch", _safe_int(params[PARAM_FLOODLIGHT_SWITCH]))
        if PARAM_FLOODLIGHT_BRIGHTNESS in params:
            point.field("floodlightBrightness", _safe_int(params[PARAM_FLOODLIGHT_BRIGHTNESS]))

        # Top-level stats
        for key in ["pir_total", "week_pir_total", "month_pir_total", "battery_usage_last_week"]:
            if key in dev:
                point.field(key, _safe_int(dev[key]))

        logger.info("[eufy] %s (%s): battery=%s%%, rssi=%s, pir=%s",
                     dev.get("device_name"), dev.get("device_model"),
                     params.get(PARAM_BATTERY), params.get(PARAM_WIFI_RSSI),
                     params.get(PARAM_PIR))
        points.append(point)

    if points:
        write_influx(points)
    else:
        logger.info("[eufy] No data points to write")


def eufy_snapshot():
    """Download cached cover_path images. Relies on _eufy() populating _device_covers."""
    from datetime import datetime

    if not _device_covers:
        logger.info("[eufy_snapshot] No cover URLs cached yet, skipping")
        return

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    saved = 0

    for dev in _device_covers:
        device_sn = dev["device_sn"]
        device_name = dev["device_name"]
        cover_path = dev["cover_path"]
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filepath = os.path.join(SNAPSHOT_DIR, f"{device_sn}_{ts}.jpg")

        try:
            img_resp = requests.get(cover_path, timeout=30)
            img_resp.raise_for_status()
            with open(filepath, "wb") as f:
                f.write(img_resp.content)
            logger.info("[eufy_snapshot] Saved %s (%s) -> %s (%d bytes)",
                        device_name, device_sn, filepath, len(img_resp.content))
            saved += 1
        except Exception:
            logger.exception("[eufy_snapshot] Failed to download snapshot for %s (%s)",
                             device_name, device_sn)

    logger.info("[eufy_snapshot] Saved %d snapshot(s)", saved)
