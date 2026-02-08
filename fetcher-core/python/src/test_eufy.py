"""Test script for Eufy Security REST API (v2 with ECDH encryption).

Authenticates and dumps all device data to discover available fields.

Usage:
    EUFY_USERNAME=email EUFY_PASSWORD=pass EUFY_COUNTRY=se python test_eufy.py
"""

import base64
import json
import os
import sys
import time
from hashlib import md5

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import requests

DOMAIN_BASE = "https://extend.eufylife.com"

# Server's ECDH public key (from eufy-security-client)
SERVER_PUBLIC_KEY_HEX = "04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a3868d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076"

# Headers mimicking the Android app (required by the API)
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

# Known param_type mappings from eufy-security-client
PARAM_NAMES = {
    1011: "CAMERA_PIR",
    1013: "CAMERA_IR_CUT",
    1101: "CMD_GET_BATTERY",
    1138: "CMD_GET_BATTERY_TEMP",
    1016: "COMMAND_MOTION_DETECTION_PACKAGE",
    1026: "COMMAND_LED_NIGHT_OPEN",
    1133: "CAMERA_UPGRADE_NOW",
    1134: "DEVICE_UPGRADE_NOW",
    1142: "CAMERA_WIFI_RSSI",
    1204: "CAMERA_MOTION_ZONES",
    1214: "WATERMARK_MODE",
    1224: "GUARD_MODE",
    1230: "CAMERA_SPEAKER_VOLUME",
    1249: "CAMERA_RECORD_CLIP_LENGTH",
    1250: "CAMERA_RECORD_RETRIGGER_INTERVAL",
    1252: "PUSH_MSG_MODE",
    1257: "DEFAULT_SCHEDULE_MODE",
    1271: "SNOOZE_MODE",
    1272: "FLOODLIGHT_MOTION_SENSITIVITY",
    1366: "CAMERA_RECORD_ENABLE_AUDIO",
    1400: "FLOODLIGHT_MANUAL_SWITCH",
    1401: "FLOODLIGHT_MANUAL_BRIGHTNESS",
    1412: "FLOODLIGHT_MOTION_BRIGHTNESS",
    1413: "FLOODLIGHT_SCHEDULE_BRIGHTNESS",
    2001: "OPEN_DEVICE",
    2002: "NIGHT_VISUAL",
    2003: "VOLUME",
    2004: "DETECT_MODE",
    2005: "DETECT_MOTION_SENSITIVE",
    2006: "DETECT_ZONE",
    2007: "UN_DETECT_ZONE",
    2010: "SDCARD",
    2015: "CHIME_STATE",
    2022: "RINGING_VOLUME",
    2023: "DETECT_EXPOSURE",
    2027: "DETECT_SWITCH",
    2028: "DETECT_SCENARIO",
    2029: "DOORBELL_HDR",
    2030: "DOORBELL_IR_MODE",
    2031: "DOORBELL_VIDEO_QUALITY",
    2032: "DOORBELL_BRIGHTNESS",
    2033: "DOORBELL_DISTORTION",
    2034: "DOORBELL_RECORD_QUALITY",
    2035: "DOORBELL_MOTION_NOTIFICATION",
    2036: "DOORBELL_NOTIFICATION_OPEN",
    2037: "DOORBELL_SNOOZE_START_TIME",
    2038: "DOORBELL_NOTIFICATION_JUMP_MODE",
    2039: "DOORBELL_LED_NIGHT_MODE",
    2040: "DOORBELL_RING_RECORD",
    2041: "DOORBELL_MOTION_ADVANCE_OPTION",
    2042: "DOORBELL_AUDIO_RECODE",
}


def solve_captcha_with_gemini(captcha_img: str) -> str:
    """Solve CAPTCHA using Gemini. SVG sent as text, PNG via vision. Returns answer or empty string."""
    gemini_token = os.environ.get("GEMINI_TOKEN", "")
    if not gemini_token:
        print("GEMINI_TOKEN not set, falling back to manual CAPTCHA solving")
        return ""

    if not captcha_img.startswith("data:"):
        print("CAPTCHA image is not a data URI, cannot auto-solve")
        return ""

    header, b64data = captcha_img.split(",", 1)
    mime_type = header.split(":")[1].split(";")[0]
    prompt = "What text does this CAPTCHA image show? Reply with ONLY the characters, nothing else."

    if "svg" in mime_type:
        svg_text = base64.b64decode(b64data).decode(errors="replace")
        parts = [{"text": f"{prompt}\n\n{svg_text}"}]
        print("Sending CAPTCHA SVG text to Gemini...")
    else:
        parts = [
            {"text": prompt},
            {"inline_data": {"mime_type": mime_type, "data": b64data}},
        ]
        print(f"Sending CAPTCHA image to Gemini ({mime_type})...")

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-preview:generateContent?key={gemini_token}",
            json={"contents": [{"parts": parts}]},
            timeout=30,
        )
        resp.raise_for_status()
        answer = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"Gemini solved CAPTCHA: {answer}")
        return answer
    except Exception as e:
        print(f"Gemini CAPTCHA solving failed: {e}")
        return ""


def encrypt_api_data(plaintext: str, shared_key: bytes) -> str:
    """Encrypt data using AES-256-CBC with ECDH shared secret."""
    key = shared_key[:32]
    iv = shared_key[:16]
    from cryptography.hazmat.primitives.padding import PKCS7
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ct).decode()


def decrypt_api_data(ciphertext: str, shared_key: bytes) -> bytes:
    """Decrypt data using AES-256-CBC with ECDH shared secret."""
    key = shared_key[:32]
    iv = shared_key[:16]
    ct = base64.b64decode(ciphertext)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    from cryptography.hazmat.primitives.padding import PKCS7
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def resolve_api_domain(country: str) -> str:
    """Resolve the API domain for a country code."""
    resp = requests.get(f"{DOMAIN_BASE}/domain/{country.upper()}")
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Domain resolution failed: {data.get('msg')}")
    domain = data["data"]["domain"]
    print(f"Resolved API domain: {domain}")
    return f"https://{domain}"


def ecdh_shared_secret(private_key, server_pub_hex):
    """Compute ECDH shared secret from a private key and server public key hex."""
    server_pub_bytes = bytes.fromhex(server_pub_hex)
    x = int.from_bytes(server_pub_bytes[1:33], 'big')
    y = int.from_bytes(server_pub_bytes[33:65], 'big')
    server_pub_key = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    return private_key.exchange(ec.ECDH(), server_pub_key)


def login(session, api_base, email, password, country):
    """Authenticate using v2 ECDH-encrypted login. Returns (token, shared_key)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_numbers = private_key.public_key().public_numbers()
    client_pub_hex = "04" + format(pub_numbers.x, '064x') + format(pub_numbers.y, '064x')

    shared_key = ecdh_shared_secret(private_key, SERVER_PUBLIC_KEY_HEX)
    encrypted_password = encrypt_api_data(password, shared_key)

    tz_offset = time.timezone if time.daylight == 0 else time.altzone
    tz_ms = tz_offset * 1000

    login_data = {
        "ab": country.upper(),
        "client_secret_info": {
            "public_key": client_pub_hex,
        },
        "enc": 0,
        "email": email,
        "password": encrypted_password,
        "time_zone": tz_ms,
        "transaction": str(int(time.time() * 1000)),
    }

    resp = session.post(f"{api_base}/v2/passport/login_sec", json=login_data)
    resp.raise_for_status()
    result = resp.json()

    code = result.get("code")
    if code == 26052:
        print("2FA verification required. This script does not support 2FA yet.")
        sys.exit(1)
    if code in (100032, 100033):
        # CAPTCHA required — try Gemini first, fall back to manual
        captcha_data = result.get("data", {})
        if isinstance(captcha_data, str):
            captcha_data = json.loads(decrypt_api_data(captcha_data, shared_key))
        captcha_id = captcha_data.get("captcha_id", "")
        captcha_img = captcha_data.get("item", "")
        if not captcha_img:
            print(f"CAPTCHA required but no image data received. Full response:")
            print(json.dumps(result, indent=2, default=str))
            sys.exit(1)

        # Save captcha image to file for inspection
        if captcha_img.startswith("data:"):
            header, b64data = captcha_img.split(",", 1)
            ext = "svg" if "svg" in header else "png"
            captcha_path = f"/tmp/eufy_captcha_{captcha_id}.{ext}"
            with open(captcha_path, "wb") as f:
                f.write(base64.b64decode(b64data))
            print(f"CAPTCHA saved to: {captcha_path}")

        answer = solve_captcha_with_gemini(captcha_img)
        if not answer:
            print(f"Captcha ID: {captcha_id}")
            answer = input("Enter captcha answer: ").strip()

        # Retry login with captcha answer
        login_data["captcha_id"] = captcha_id
        login_data["answer"] = answer
        resp = session.post(f"{api_base}/v2/passport/login_sec", json=login_data)
        resp.raise_for_status()
        result = resp.json()
        code = result.get("code")
        if code != 0:
            print(f"Login failed after captcha (code={code}): {result.get('msg', 'Unknown error')}")
            sys.exit(1)
    elif code != 0:
        print(f"Login failed (code={code}): {result.get('msg', 'Unknown error')}")
        print(f"Full response: {json.dumps(result, indent=2, default=str)}")
        sys.exit(1)

    data = result["data"]

    if isinstance(data, str):
        decrypted = decrypt_api_data(data, shared_key)
        data = json.loads(decrypted)

    token = data["auth_token"]

    # Server may return an updated public key — recompute shared secret with same client key
    server_key_info = data.get("server_secret_info")
    if server_key_info and server_key_info.get("public_key"):
        new_server_pub_hex = server_key_info["public_key"]
        print(f"Server provided new public key, recomputing shared secret...")
        shared_key = ecdh_shared_secret(private_key, new_server_pub_hex)

    print(f"Logged in as: {data.get('nick_name', data.get('email'))}")
    print(f"Token expires: {data.get('token_expires_at')}")
    print(f"User ID: {data.get('user_id')}")

    return token, shared_key


def api_request(session, api_base, endpoint, token, shared_key, json_data=None):
    """Make an authenticated API request. Decrypts v2 responses."""
    headers = {"X-Auth-Token": token}
    resp = session.post(f"{api_base}/{endpoint}", headers=headers, json=json_data or {})
    resp.raise_for_status()
    result = resp.json()

    if result.get("code") != 0:
        print(f"API request {endpoint} failed (code={result.get('code')}): {result.get('msg', 'Unknown error')}")
        return None

    data = result.get("data", [])

    # v2 endpoints return encrypted string responses
    if isinstance(data, str) and shared_key:
        try:
            decrypted = decrypt_api_data(data, shared_key)
            data = json.loads(decrypted)
        except Exception as e:
            print(f"  Failed to decrypt response from {endpoint}: {e}")
            return None

    return data


def get_devices(session, api_base, token, shared_key):
    """Fetch device list via v2 endpoint."""
    for endpoint in ["v2/house/device_list", "v1/app/get_devs_list"]:
        try:
            body = {"device_sn": "", "num": 100, "orderby": "", "page": 0, "station_sn": ""}
            data = api_request(session, api_base, endpoint, token, shared_key, body)
            if data is not None:
                print(f"  (using endpoint: {endpoint})")
                return data
        except Exception as e:
            print(f"  {endpoint} failed: {e}, trying next...")
    return []


def get_hubs(session, api_base, token, shared_key):
    """Fetch hub list via v2 endpoint."""
    for endpoint in ["v2/house/station_list", "v1/app/get_hub_list"]:
        try:
            data = api_request(session, api_base, endpoint, token, shared_key)
            if data is not None:
                print(f"  (using endpoint: {endpoint})")
                return data
        except Exception as e:
            print(f"  {endpoint} failed: {e}, trying next...")
    return []


def print_params(params, indent="    "):
    """Print params with known names."""
    if not params:
        print(f"{indent}(no params)")
        return

    for p in params:
        param_type = p.get("param_type", "?")
        param_value = p.get("param_value", "")
        name = PARAM_NAMES.get(param_type, f"UNKNOWN_{param_type}")

        display_value = str(param_value)
        if len(display_value) > 80:
            display_value = display_value[:80] + "..."

        print(f"{indent}{param_type:>5} ({name}): {display_value}")


def main():
    email = os.environ.get("EUFY_USERNAME", "")
    password = os.environ.get("EUFY_PASSWORD", "")
    country = os.environ.get("EUFY_COUNTRY", "se")

    if not email or not password:
        print("Set EUFY_USERNAME and EUFY_PASSWORD environment variables")
        sys.exit(1)

    print(f"Country: {country}")
    print()

    session = requests.Session()
    session.headers.update(BASE_HEADERS)
    session.headers["Country"] = country.upper()

    # Resolve API domain
    print("=== DOMAIN RESOLUTION ===")
    api_base = resolve_api_domain(country)
    print()

    # Login
    print("=== LOGIN ===")
    token, shared_key = login(session, api_base, email, password, country)
    print()

    session.headers["X-Auth-Token"] = token

    # Hubs
    print("=== HUBS ===")
    hubs = get_hubs(session, api_base, token, shared_key)
    if isinstance(hubs, list):
        print(f"Found {len(hubs)} hub(s)")
        for hub in hubs:
            print(f"\n  Hub: {hub.get('station_name')} ({hub.get('station_model')})")
            print(f"  SN: {hub.get('station_sn')}")
            print(f"  IP: {hub.get('ip_addr')}")
            print(f"  WiFi: {hub.get('wifi_ssid')}")
            print(f"  FW: {hub.get('main_sw_version')}")
            print(f"  Devices in hub: {len(hub.get('devices', []))}")

            print(f"  Hub params:")
            print_params(hub.get("params", []))

            for dev in hub.get("devices", []):
                print(f"\n    Device (from hub): {dev.get('device_name')} ({dev.get('device_model')})")
                print(f"    SN: {dev.get('device_sn')}")
                print(f"    Type: {dev.get('device_type')}")
    else:
        print(f"Hubs response (non-list): {json.dumps(hubs, indent=2, default=str)[:500]}")
    print()

    # Devices
    print("=== DEVICES ===")
    devices = get_devices(session, api_base, token, shared_key)
    if isinstance(devices, list):
        print(f"Found {len(devices)} device(s)")
        for dev in devices:
            print(f"\n  Device: {dev.get('device_name')} ({dev.get('device_model')})")
            print(f"  SN: {dev.get('device_sn')}")
            print(f"  Type: {dev.get('device_type')}")
            print(f"  Station SN: {dev.get('station_sn')}")
            print(f"  FW: {dev.get('main_sw_version')}")
            print(f"  IP: {dev.get('ip_addr')}")
            print(f"  WiFi: {dev.get('wifi_ssid')}")

            for key in ["pir_total", "pir_none", "week_pir_total", "week_pir_none",
                         "month_pir_total", "month_pir_none", "event_num"]:
                if key in dev:
                    print(f"  {key}: {dev[key]}")

            print(f"  Params:")
            print_params(dev.get("params", []))

            skip_keys = {"device_id", "device_sn", "device_name", "device_model",
                          "device_type", "device_channel", "station_sn", "schedule",
                          "schedulex", "wifi_mac", "sub1g_mac", "main_sw_version",
                          "main_hw_version", "sec_sw_version", "sec_hw_version",
                          "sector_id", "event_num", "wifi_ssid", "ip_addr", "volume",
                          "main_sw_time", "sec_sw_time", "bind_time", "bt_mac",
                          "cover_path", "cover_time", "local_ip", "create_time",
                          "update_time", "status", "svr_domain", "svr_port",
                          "station_conn", "family_num", "member", "permission",
                          "params", "pir_total", "pir_none", "week_pir_total",
                          "week_pir_none", "month_pir_total", "month_pir_none",
                          "time_zone"}
            extra = {k: v for k, v in dev.items() if k not in skip_keys}
            if extra:
                print(f"  Extra fields:")
                for k, v in extra.items():
                    display = str(v)
                    if len(display) > 80:
                        display = display[:80] + "..."
                    print(f"    {k}: {display}")
    else:
        print(f"Devices response (non-list): {json.dumps(devices, indent=2, default=str)[:500]}")

    # Dump raw JSON
    print("\n=== RAW DEVICE JSON ===")
    print(json.dumps(devices, indent=2, default=str))


if __name__ == "__main__":
    main()
