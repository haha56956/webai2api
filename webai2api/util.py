from __future__ import annotations

import base64
import json
import os
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Iterable, Iterator, Optional

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)


def new_id(prefix: str = "chatcmpl") -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def now_ts() -> int:
    return int(time.time())


def json_dumps(obj: Any, **kwargs: Any) -> str:
    kwargs.setdefault("ensure_ascii", False)
    kwargs.setdefault("separators", (",", ":"))
    return json.dumps(obj, **kwargs)


def random_hex(n: int = 20) -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


def random_int(bits: int = 32) -> int:
    return random.randint(0, (1 << bits) - 1)


def extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (int, float, bool)):
        return str(content)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                item_type = item.get("type")
                if item_type in ("text", "input_text", "output_text"):
                    parts.append(str(item.get("text", "")))
                elif item_type == "image_url":
                    parts.append("[image]")
                elif "text" in item:
                    parts.append(str(item.get("text", "")))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return extract_text(content.get("text", ""))
    return str(content)


def render_tool_calls(msg: dict) -> str:
    calls = msg.get("tool_calls")
    if not isinstance(calls, list):
        return ""
    lines = ["[Tool calls]"]
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name") or call.get("name") or ""
        arguments = function.get("arguments", call.get("arguments", {}))
        if not isinstance(arguments, str):
            arguments = json_dumps(arguments)
        call_id = call.get("id") or call.get("tool_call_id") or ""
        suffix = f" (id={call_id})" if call_id else ""
        lines.append(f"{name}{suffix}: {arguments}")
    return "\n".join(lines) if len(lines) > 1 else ""


def render_tool_result(msg: dict, text: str) -> str:
    name = str(msg.get("name") or "")
    call_id = str(msg.get("tool_call_id") or msg.get("id") or "")
    label = "[Tool result"
    if name:
        label += f" for {name}"
    if call_id:
        label += f" (id={call_id})"
    label += "]"
    return f"{label}\n{text}"


def flatten_messages(messages: Iterable[dict], style: str = "labels") -> str:
    system_parts: list[str] = []
    turns: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "user"))
        text = extract_text(msg.get("content"))
        if role in ("system", "developer"):
            if text:
                system_parts.append(text)
            continue
        if role == "assistant":
            parts = [text] if text else []
            calls = render_tool_calls(msg)
            if calls:
                parts.append(calls)
            if parts:
                turns.append("[Assistant]\n" + "\n\n".join(parts))
        elif role == "tool":
            if text:
                turns.append(render_tool_result(msg, text))
        else:
            if text:
                turns.append(f"[User]\n{text}")
    if style == "plain":
        head = "\n\n".join(system_parts)
        body = "\n\n".join(turns)
        return (head + "\n\n" + body).strip() if head else body
    head = "[System]\n" + "\n\n".join(system_parts) if system_parts else ""
    body = "\n\n".join(turns)
    return (head + "\n\n" + body).strip() if head else body


def _content_for_duck(text: str, parts: bool) -> Any:
    if parts:
        return [{"type": "text", "text": text}]
    return text


def messages_for_duck(messages: Iterable[dict], parts: bool = False) -> list[dict]:
    system_parts: list[str] = []
    out: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "user"))
        text = extract_text(msg.get("content"))
        if role in ("system", "developer"):
            if text:
                system_parts.append(text)
            continue
        if role in ("assistant", "model"):
            pieces = [text] if text else []
            calls = render_tool_calls(msg)
            if calls:
                pieces.append(calls)
            if pieces:
                out.append(
                    {"role": "assistant", "content": _content_for_duck("\n\n".join(pieces), parts)}
                )
        elif role == "tool":
            if text:
                out.append(
                    {
                        "role": "user",
                        "content": _content_for_duck(render_tool_result(msg, text), parts),
                    }
                )
        else:
            if text:
                out.append({"role": "user", "content": _content_for_duck(text, parts)})
    if system_parts:
        prefix = "[System]\n" + "\n\n".join(system_parts)
        if out and out[0]["role"] == "user":
            existing = out[0]["content"]
            if parts and isinstance(existing, list):
                existing[0]["text"] = prefix + "\n\n" + existing[0].get("text", "")
            else:
                out[0]["content"] = prefix + "\n\n" + str(existing)
        else:
            out.insert(0, {"role": "user", "content": _content_for_duck(prefix, parts)})
    return out


def parse_cookie_header(header: Optional[str]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if not header:
        return cookies
    for part in header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            cookies[key] = value.strip()
    return cookies


def build_cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if k)


def merge_cookie_headers(*headers: Optional[str]) -> str:
    merged: dict[str, str] = {}
    for header in headers:
        merged.update(parse_cookie_header(header))
    return build_cookie_header(merged)


def find_token_in_cookies(cookie_header: Optional[str], names: Iterable[str]) -> str:
    cookies = parse_cookie_header(cookie_header)
    for name in names:
        value = cookies.get(name)
        if value:
            if value.startswith("{"):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict) and parsed.get("value"):
                        return str(parsed["value"])
                except Exception:
                    pass
            return value
    return ""


def iter_sse_data(response: Any) -> Iterator[str]:
    buffer: list[str] = []
    for raw in response:
        try:
            line = raw.decode("utf-8", "replace")
        except AttributeError:
            line = str(raw)
        line = line.rstrip("\r\n")
        if not line:
            if buffer:
                yield "\n".join(buffer)
                buffer = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
        elif line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
            continue
        else:
            if buffer:
                buffer.append(line)
    if buffer:
        yield "\n".join(buffer)


def safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def truncate(text: str, limit: int = 400) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "..."


def timezone_header() -> str:
    now = time.localtime()
    if time.daylight and now.tm_isdst > 0:
        offset = -time.altzone
    else:
        offset = -time.timezone
    sign = "+" if offset >= 0 else "-"
    minutes = abs(int(offset)) // 60
    tz = time.strftime("%Z", now) or "UTC"
    return time.strftime("%a %b %d %Y %H:%M:%S", now) + f" GMT{sign}{minutes // 60:02d}{minutes % 60:02d} ({tz})"


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def http_request(
    url: str,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    body: Any = None,
    timeout: float = 120,
    proxy: Optional[str] = None,
    raw_body: Optional[bytes] = None,
) -> Any:
    data = raw_body
    if body is not None and data is None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def read_error_body(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        cached = getattr(exc, "_webai2api_body", None)
        if cached is not None:
            return cached
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        try:
            setattr(exc, "_webai2api_body", body)
        except Exception:
            pass
        return body
    return ""


def parse_http_error(exc: BaseException) -> tuple[int, str]:
    if isinstance(exc, urllib.error.HTTPError):
        body = read_error_body(exc)
        message = body
        parsed = safe_json_loads(body)
        if isinstance(parsed, dict):
            if isinstance(parsed.get("error"), dict):
                message = str(parsed["error"].get("message") or parsed["error"])
            elif parsed.get("message"):
                message = str(parsed["message"])
            elif isinstance(parsed.get("data"), dict):
                message = str(parsed["data"].get("details") or parsed["data"].get("code") or body)
        return int(exc.code), truncate(message, 600)
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return 504, "upstream request timed out"
    if isinstance(exc, urllib.error.URLError):
        return 502, truncate(str(exc.reason), 400)
    return 502, truncate(str(exc), 400)


def mask_secret(value: str, show: int = 6) -> str:
    if not value:
        return ""
    if len(value) <= show * 2:
        return "*" * len(value)
    return value[:show] + "..." + value[-4:]


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text)


def url_join(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def parse_query(url: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def env_expand(value: Any) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            return os.environ.get(name, "")
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, value)
    if isinstance(value, list):
        return [env_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: env_expand(v) for k, v in value.items()}
    return value
