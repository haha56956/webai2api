from __future__ import annotations

import base64
import hashlib
import json
import random
import threading
import time
from typing import Any, Optional

from .crypto import aes128_cbc_encrypt
from .util import DEFAULT_UA, http_request, random_int, safe_json_loads

CUSTOM_BASE64_CHARS = "DGi0YA7BemWnQjCl4_bR3f8SKIF9tUz/xhr2oEOgPpac=61ZqwTudLkM5vHyNXsVJ"

HASH_FIELDS = {
    16: "split",
    17: "full",
    18: "full",
    31: "full",
    34: "full",
    36: "full",
}

FINGERPRINT_TEMPLATE = {
    "sdkVersion": "websdk-2.3.15d",
    "initTimestamp": "1765348410850",
    "field3": "91",
    "field4": "1|15",
    "language": "zh-CN",
    "timezoneOffset": "-480",
    "colorDepth": "16705151|12791",
    "screenInfo": "1470|956|283|797|158|0|1470|956|1470|798|0|0",
    "field9": "5",
    "platform": "MacIntel",
    "field11": "10",
    "webglRenderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M4, Unspecified Version)|Google Inc. (Apple)",
    "field13": "30|30",
    "field14": "0",
    "field15": "28",
    "pluginCount": "5",
    "vendor": "Google Inc.",
    "field29": "8",
    "touchInfo": "-1|0|0|0|0",
    "field32": "11",
    "field35": "0",
    "mode": "P",
}


def _lzw_compress(data: Optional[str], bits: int, char_func: Any) -> str:
    if data is None:
        return ""
    dictionary: dict[str, int] = {}
    dict_to_create: dict[str, bool] = {}
    c = ""
    wc = ""
    w = ""
    enlarge_in = 2
    dict_size = 3
    num_bits = 2
    result: list[str] = []
    value = 0
    position = 0

    def put_bits(code: int, count: int) -> None:
        nonlocal value, position
        for _ in range(count):
            value = (value << 1) | (code & 1)
            if position == bits - 1:
                position = 0
                result.append(char_func(value))
                value = 0
            else:
                position += 1
            code >>= 1

    for i in range(len(data)):
        c = data[i]
        if c not in dictionary:
            dictionary[c] = dict_size
            dict_size += 1
            dict_to_create[c] = True
        wc = w + c
        if wc in dictionary:
            w = wc
            continue
        if w in dict_to_create:
            if ord(w[0]) < 256:
                for _ in range(num_bits):
                    value <<= 1
                    if position == bits - 1:
                        position = 0
                        result.append(char_func(value))
                        value = 0
                    else:
                        position += 1
                put_bits(ord(w[0]), 8)
            else:
                put_bits(1, num_bits)
                put_bits(ord(w[0]), 16)
            enlarge_in -= 1
            if enlarge_in == 0:
                enlarge_in = 2 ** num_bits
                num_bits += 1
            del dict_to_create[w]
        else:
            put_bits(dictionary[w], num_bits)
        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 2 ** num_bits
            num_bits += 1
        dictionary[wc] = dict_size
        dict_size += 1
        w = c

    if w != "":
        if w in dict_to_create:
            if ord(w[0]) < 256:
                for _ in range(num_bits):
                    value <<= 1
                    if position == bits - 1:
                        position = 0
                        result.append(char_func(value))
                        value = 0
                    else:
                        position += 1
                put_bits(ord(w[0]), 8)
            else:
                put_bits(1, num_bits)
                put_bits(ord(w[0]), 16)
            enlarge_in -= 1
            if enlarge_in == 0:
                enlarge_in = 2 ** num_bits
                num_bits += 1
            del dict_to_create[w]
        else:
            put_bits(dictionary[w], num_bits)
        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 2 ** num_bits
            num_bits += 1

    put_bits(2, num_bits)
    while True:
        value <<= 1
        if position == bits - 1:
            result.append(char_func(value))
            break
        position += 1
    return "".join(result)


def _custom_encode(data: Optional[str]) -> str:
    if data is None:
        return ""
    return _lzw_compress(data, 6, lambda index: CUSTOM_BASE64_CHARS[index])


def generate_fingerprint(options: Optional[dict] = None) -> str:
    options = options or {}
    config = dict(FINGERPRINT_TEMPLATE)
    custom = options.get("custom")
    if isinstance(custom, dict):
        config.update(custom)
    device_id = options.get("deviceId") or "".join(random.choice("0123456789abcdef") for _ in range(20))
    timestamp = int(time.time() * 1000)
    fields: list[Any] = [
        device_id,
        config["sdkVersion"],
        config["initTimestamp"],
        config["field3"],
        config["field4"],
        config["language"],
        config["timezoneOffset"],
        config["colorDepth"],
        config["screenInfo"],
        config["field9"],
        config["platform"],
        config["field11"],
        config["webglRenderer"],
        config["field13"],
        config["field14"],
        config["field15"],
        f'{config["pluginCount"]}|{random_int()}',
        random_int(),
        random_int(),
        "1",
        "0",
        "1",
        "0",
        config["mode"],
        "0",
        "0",
        "0",
        "416",
        config["vendor"],
        config["field29"],
        config["touchInfo"],
        random_int(),
        config["field32"],
        timestamp,
        random_int(),
        config["field35"],
        random.randint(10, 100),
    ]
    return "^".join(map(str, fields))


def generate_ssxmod(raw_fingerprint: Optional[str] = None) -> dict[str, Any]:
    fingerprint = raw_fingerprint or generate_fingerprint()
    fields = fingerprint.split("^")
    processed: list[Any] = list(fields)
    timestamp = int(time.time() * 1000)
    for index, kind in HASH_FIELDS.items():
        if index >= len(processed):
            continue
        if kind == "split":
            parts = str(processed[index]).split("|")
            if len(parts) == 2:
                processed[index] = f"{parts[0]}|{random_int()}"
        elif kind == "full":
            if index == 36:
                processed[index] = random.randint(10, 100)
            else:
                processed[index] = random_int()
    if len(processed) > 33:
        processed[33] = timestamp
    data1 = "^".join(map(str, processed))
    data2 = "^".join(
        map(
            str,
            [
                processed[0],
                processed[1],
                processed[23],
                0,
                "",
                0,
                "",
                "",
                0,
                0,
                0,
                processed[32],
                processed[33],
                0,
                0,
                0,
                0,
                0,
            ],
        )
    )
    return {
        "fingerprint": "^".join(map(str, fields)),
        "ssxmod_itna": "1-" + _custom_encode(data1),
        "ssxmod_itna2": "1-" + _custom_encode(data2),
        "timestamp": timestamp,
    }


def generate_bx_ua(fingerprint: Optional[str] = None, seed: Optional[str] = None) -> str:
    fingerprint = fingerprint or generate_fingerprint()
    timestamp = int(time.time() * 1000)
    fields = fingerprint.split("^")
    payload = {
        "v": "231",
        "ts": timestamp,
        "fp": fingerprint,
        "d": {
            "deviceId": fields[0],
            "sdkVer": fields[1],
            "lang": fields[5],
            "tz": fields[6],
            "platform": fields[10],
            "renderer": fields[12],
            "mode": fields[23],
            "vendor": fields[28],
        },
        "rnd": random.randint(1000, 9999),
        "seq": 1,
    }
    payload["cs"] = hashlib.md5(
        f"{fingerprint}{timestamp}{payload['rnd']}".encode()
    ).hexdigest()[:8]
    raw = json.dumps(payload, separators=(",", ":")).encode()
    digest = hashlib.sha256((seed or fingerprint).encode()).digest()
    encrypted = aes128_cbc_encrypt(digest[:16], digest[16:32], raw)
    return "231!" + base64.b64encode(encrypted).decode("ascii")


class MidTokenCache:
    def __init__(self, url: str = "https://sg-wum.alibaba.com/w/wu.json"):
        self.url = url
        self._lock = threading.Lock()
        self._value = ""
        self._uses = 0
        self._fetched_at = 0.0
        self._failed_at = 0.0

    def get(self, proxy: Optional[str] = None, timeout: float = 10) -> str:
        with self._lock:
            now = time.monotonic()
            if self._value and self._uses < 100 and now - self._fetched_at < 600:
                self._uses += 1
                return self._value
            if not self._value and self._failed_at and now - self._failed_at < 120:
                return ""
        value = ""
        try:
            response = http_request(
                self.url,
                headers={"User-Agent": DEFAULT_UA, "Accept": "*/*"},
                timeout=timeout,
                proxy=proxy,
            )
            text = response.read().decode("utf-8", "replace")
            marker = "umx.wu('"
            start = text.find(marker)
            if start < 0:
                marker = "__fycb('"
                start = text.find(marker)
            if start >= 0:
                start += len(marker)
                end = text.find("')", start)
                if end > start:
                    value = text[start:end]
        except Exception:
            value = ""
        with self._lock:
            if value:
                self._value = value
                self._uses = 1
                self._fetched_at = time.monotonic()
                self._failed_at = 0.0
            else:
                self._failed_at = time.monotonic()
            return self._value


def parse_midtoken(text: str) -> str:
    parsed = safe_json_loads(text)
    if isinstance(parsed, dict):
        return str(parsed.get("bx_umidtoken") or parsed.get("token") or "")
    for marker in ("umx.wu('", "__fycb('"):
        start = text.find(marker)
        if start >= 0:
            start += len(marker)
            end = text.find("')", start)
            if end > start:
                return text[start:end]
    return ""


def fetch_midtoken(proxy: Optional[str] = None, timeout: float = 10) -> str:
    try:
        response = http_request(
            "https://sg-wum.alibaba.com/w/wu.json",
            headers={"User-Agent": DEFAULT_UA, "Accept": "*/*"},
            timeout=timeout,
            proxy=proxy,
        )
        return parse_midtoken(response.read().decode("utf-8", "replace"))
    except Exception:
        return ""
