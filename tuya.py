import hashlib, hmac, time, uuid, os, requests

BASE_URL = "https://openapi.tuyaeu.com"
CLIENT_ID = os.environ.get("TUYA_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("TUYA_CLIENT_SECRET", "")
DEVICE_ID = os.environ.get("TUYA_DEVICE_ID", "")

_token_cache = {"token": None, "expires_at": 0}


def _sign(client_id: str, secret: str, t: str, nonce: str, access_token: str, str_to_sign: str) -> str:
    message = client_id + access_token + t + nonce + str_to_sign
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest().upper()


def _string_to_sign(method: str, body: str, url: str) -> str:
    content_hash = hashlib.sha256(body.encode()).hexdigest()
    return f"{method}\n{content_hash}\n\n{url}"


def _headers(access_token: str, method: str, url: str, body: str = "") -> dict:
    t = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex
    sts = _string_to_sign(method, body, url)
    sign = _sign(CLIENT_ID, CLIENT_SECRET, t, nonce, access_token, sts)
    return {
        "client_id": CLIENT_ID,
        "access_token": access_token,
        "sign": sign,
        "t": t,
        "nonce": nonce,
        "sign_method": "HMAC-SHA256",
        "Content-Type": "application/json",
    }


def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    url_path = "/v1.0/token?grant_type=1"
    t = str(int(now * 1000))
    nonce = uuid.uuid4().hex
    sts = _string_to_sign("GET", "", url_path)
    sign = _sign(CLIENT_ID, CLIENT_SECRET, t, nonce, "", sts)
    headers = {
        "client_id": CLIENT_ID,
        "sign": sign,
        "t": t,
        "nonce": nonce,
        "sign_method": "HMAC-SHA256",
    }
    r = requests.get(BASE_URL + url_path, headers=headers, timeout=8)
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"Tuya token fout: {data}")
    _token_cache["token"] = data["result"]["access_token"]
    _token_cache["expires_at"] = now + data["result"]["expire_time"] - 60
    return _token_cache["token"]


def _stuur_commando(commando: list):
    if not CLIENT_ID or not CLIENT_SECRET or not DEVICE_ID:
        print("[tuya] Credentials niet ingesteld, lamp overgeslagen")
        return
    token = _get_token()
    url_path = f"/v1.0/devices/{DEVICE_ID}/commands"
    import json
    body = json.dumps({"commands": commando})
    headers = _headers(token, "POST", url_path, body)
    r = requests.post(BASE_URL + url_path, headers=headers, data=body, timeout=8)
    return r.json()


def lamp_groen():
    # Wit = geaccepteerd
    return _stuur_commando([
        {"code": "switch_led", "value": True},
        {"code": "work_mode", "value": "white"},
        {"code": "bright_value_v2", "value": 1000},
        {"code": "temp_value_v2", "value": 1000},
    ])


def lamp_rood():
    # Oranje = geweigerd
    return _stuur_commando([
        {"code": "switch_led", "value": True},
        {"code": "work_mode", "value": "colour"},
        {"code": "colour_data_v2", "value": {"h": 30, "s": 1000, "v": 1000}},
    ])


def lamp_uit():
    return _stuur_commando([{"code": "switch_led", "value": False}])


def lamp_status() -> dict:
    """Haal huidige status en ondersteunde functies op."""
    if not CLIENT_ID or not DEVICE_ID:
        return {"error": "credentials ontbreken"}
    token = _get_token()
    url_path = f"/v1.0/devices/{DEVICE_ID}"
    headers = _headers(token, "GET", url_path)
    r = requests.get(BASE_URL + url_path, headers=headers, timeout=8)
    return r.json()


def lamp_functies() -> dict:
    """Haal ondersteunde commando-codes op voor dit apparaat."""
    if not CLIENT_ID or not DEVICE_ID:
        return {"error": "credentials ontbreken"}
    token = _get_token()
    url_path = f"/v1.0/devices/{DEVICE_ID}/functions"
    headers = _headers(token, "GET", url_path)
    r = requests.get(BASE_URL + url_path, headers=headers, timeout=8)
    return r.json()
