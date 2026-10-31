from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
from typing import Iterator, Optional

from ..util import (
    DEFAULT_UA,
    extract_text,
    http_request,
    iter_sse_data,
    messages_for_duck,
    parse_http_error,
    read_error_body,
    safe_json_loads,
)
from .base import ChatRequest, Delta, Provider, ProviderError

log = logging.getLogger("webai2api.duckduckgo")

VQD_HASH_HINT = (
    "Duck.ai 需要浏览器生成的 x-vqd-hash-1 挑战值。请安装 chrome-extension，"
    "打开 https://duck.ai/ 并发送一条消息，在扩展中复制 vqd_hash、fe_version、"
    "fe_signals，然后填入 providers.duckduckgo，或在 WebUI 的 Duck.ai 面板中保存。"
)

REASONING_MODELS = {
    "gpt-5.4-mini": "low",
    "gpt-6-luna": "none",
    "claude-haiku-4-5": "low",
    "claude-sonnet-4-6": "low",
    "gpt-5.6-terra": "low",
    "gpt-5.6-sol": "low",
    "claude-opus-4-8": "low",
    "tinfoil/gpt-oss-120b": "low",
    "tinfoil/gemma4-31b": "none",
}

ALLOWED_EFFORT = {
    "gpt-5.4-mini": ("none", "low", "medium"),
    "gpt-6-luna": ("none", "low"),
    "claude-haiku-4-5": ("none", "low"),
    "claude-sonnet-4-6": ("none", "low"),
    "gpt-5.6-terra": ("none", "low", "medium"),
    "gpt-5.6-sol": ("none", "low", "medium"),
    "claude-opus-4-8": ("none", "low", "medium"),
    "tinfoil/gpt-oss-120b": ("low",),
    "tinfoil/gemma4-31b": ("none", "low"),
}

ERROR_STATUS = {
    "ERR_RATE_LIMIT": 429,
    "ERR_CHALLENGE": 502,
    "ERR_MODEL_RESTRICTED": 404,
    "ERR_MODEL_UNAVAILABLE": 404,
    "ERR_BAD_REQUEST": 400,
}


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _event_text(event: dict) -> str:
    for key in ("text", "message", "content"):
        value = event.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, (list, dict)):
            text = extract_text(value)
            if text:
                return text
    return ""


class DuckDuckGoProvider(Provider):
    name = "duckduckgo"

    def __init__(self, cfg: dict, global_cfg: dict):
        super().__init__(cfg, global_cfg)
        self.base_url = (cfg.get("base_url") or "https://duck.ai").rstrip("/")
        self.timeout = float(cfg.get("timeout") or 120)
        self.user_agent = str(cfg.get("user_agent") or DEFAULT_UA)
        self.cookies = str(cfg.get("cookies") or "")
        self.headers = dict(cfg.get("headers") or {})
        self.vqd_hash = str(cfg.get("vqd_hash") or "")
        self.fe_version = str(cfg.get("fe_version") or "")
        self.fe_signals = str(cfg.get("fe_signals") or "")

    def _fetch_fe_version(self) -> str:
        if self.fe_version:
            return self.fe_version
        try:
            response = http_request(
                self.base_url + "/",
                headers={
                    "accept": "text/html",
                    "user-agent": self.user_agent,
                    "accept-language": "en-US,en;q=0.9",
                },
                timeout=min(15, self.timeout),
                proxy=self.cfg.get("proxy") or None,
            )
            html = response.read().decode("utf-8", "replace")
            tag = re.search(r'data-version-tag="([^"]+)"', html)
            sha = re.search(r'data-version-sha="([^"]+)"', html)
            if tag:
                self.fe_version = tag.group(1) + ("-" + sha.group(1) if sha else "")
                log.info("已从 duck.ai 首页读取 x-fe-version: %s", self.fe_version)
        except BaseException as exc:
            log.debug("读取 duck.ai x-fe-version 失败: %s", exc)
        return self.fe_version

    def _refresh_fe_signals(self) -> str:
        raw = self.fe_signals
        now = int(time.time() * 1000)
        if raw:
            try:
                obj = json.loads(base64.b64decode(raw))
                if isinstance(obj, dict):
                    duration = int(obj.get("end") or 0)
                    start_value = int(obj.get("start") or 0)
                    if duration > 3_600_000 or duration < 0:
                        duration = 0
                    if duration == 0 and start_value:
                        duration = max(0, now - start_value)
                    if duration == 0:
                        duration = 3200
                    obj["start"] = now - duration
                    obj["end"] = duration
                    return _b64e(
                        json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
                    )
            except Exception:
                pass
        events = [
            {"name": "action", "delta": 2860, "trusted": False},
        ]
        obj = {"start": now - 3200, "events": events, "end": 3200}
        return _b64e(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode())

    def _headers(self, use_cookies: bool = True) -> dict[str, str]:
        headers = {
            "accept": "text/event-stream",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "user-agent": self.user_agent,
            "origin": self.base_url,
            "referer": self.base_url + "/",
            "x-duckai-continuation": "resubmit-context",
            "x-duckai-consent-purposes": "user-location,image-generation,web-search",
            "x-duckai-tool-approval": "web-search",
            "x-duckai-response-id": str(uuid.uuid4()),
            "x-ddg-journey-id": uuid.uuid4().hex,
            "x-fe-signals": self._refresh_fe_signals(),
        }
        fe_version = self._fetch_fe_version()
        if fe_version:
            headers["x-fe-version"] = fe_version
        if self.vqd_hash:
            headers["x-vqd-hash-1"] = self.vqd_hash
        if use_cookies and self.cookies:
            headers["cookie"] = self.cookies
        headers.update({str(k): str(v) for k, v in self.headers.items()})
        return headers

    def _reasoning_effort(self, request: ChatRequest, model: str) -> Optional[str]:
        allowed = ALLOWED_EFFORT.get(model)
        if not allowed:
            return None
        requested = str(request.reasoning_effort or "").lower()
        if not requested or requested == "auto":
            requested = REASONING_MODELS.get(model, allowed[0])
        if requested == "high":
            requested = "medium" if "medium" in allowed else allowed[-1]
        if requested not in allowed:
            requested = REASONING_MODELS.get(model, allowed[0])
        return requested

    def _payload(self, request: ChatRequest, model: str, messages: list[dict]) -> dict:
        payload: dict = {
            "model": model,
            "messages": messages,
            "canUseTools": False,
            "canUseApproxLocation": None,
            "canDelegateImageGeneration": None,
            "canUseWebSearch": bool(
                request.extra.get("web_search") or request.extra.get("search")
            ),
            "canUploadFiles": False,
            "canShowGreeting": False,
        }
        effort = self._reasoning_effort(request, model)
        if effort is not None:
            payload["reasoningEffort"] = effort
        return payload

    @staticmethod
    def _looks_like_quota(text: str) -> bool:
        lowered = (text or "").lower()
        return any(
            hint in lowered
            for hint in (
                "quota",
                "rate limit",
                "rate-limit",
                "too many requests",
                "limit exceeded",
                "exceeded your",
                "免费额度",
                "额度用完",
                "额度已用尽",
                "达到上限",
            )
        )

    def _quota_error(self, detail: str) -> ProviderError:
        return ProviderError(
            f"DuckDuckGo 免费额度已用尽或触发限流: {str(detail or '')[:300]}",
            status=429,
            provider=self.name,
            fallback=True,
        )

    def _challenge_error(self, detail: str = "") -> ProviderError:
        message = VQD_HASH_HINT
        if detail:
            message = f"{message} 上游提示: {detail[:200]}"
        return ProviderError(message, status=502, provider=self.name, fallback=True)

    def _error_from_body(self, status: int, body: str, message: str) -> ProviderError:
        parsed = safe_json_loads(body)
        event_type = ""
        detail = message or body
        if isinstance(parsed, dict):
            event_type = str(parsed.get("type") or parsed.get("errorType") or "")
            detail = " ".join(
                str(parsed.get(key) or "")
                for key in ("type", "errorType", "message", "status", "overrideCode")
            )
        combined = f"{event_type} {detail} {body}"
        if status == 429 or event_type == "ERR_RATE_LIMIT" or self._looks_like_quota(combined):
            return self._quota_error(detail)
        if status == 418 or event_type == "ERR_CHALLENGE":
            return self._challenge_error(detail)
        mapped = ERROR_STATUS.get(event_type)
        if mapped:
            return ProviderError(
                f"DuckDuckGo 错误: {event_type} {detail[:300]}".strip(),
                status=mapped,
                provider=self.name,
                fallback=mapped in (429, 502),
            )
        return ProviderError(
            f"DuckDuckGo 请求失败: {detail[:300]}",
            status=status if 400 <= status <= 599 else 502,
            provider=self.name,
            fallback=status in (429, 502, 503),
        )

    def stream_chat(self, request: ChatRequest) -> Iterator[Delta]:
        attempts = [True, False] if self.cookies else [False]
        last_error: Optional[ProviderError] = None
        for index, use_cookies in enumerate(attempts):
            emitted = False
            try:
                for delta in self._stream_once(request, use_cookies=use_cookies):
                    if delta.content or delta.reasoning:
                        emitted = True
                    yield delta
                return
            except ProviderError as exc:
                last_error = exc
                if exc.fallback and not emitted and index + 1 < len(attempts):
                    log.warning("DuckDuckGo 可能已达免费额度上限，清除 Cookie 后重试")
                    continue
                raise
        if last_error is not None:
            raise last_error

    def _stream_once(self, request: ChatRequest, use_cookies: bool = True) -> Iterator[Delta]:
        if not self.vqd_hash:
            raise self._challenge_error("未配置 vqd_hash")
        model = str(self.resolve_model(request.route_model or request.model) or request.model)
        messages = messages_for_duck(request.messages, parts=True)
        if not messages:
            raise ProviderError("消息内容为空", status=400, provider=self.name)
        payload = self._payload(request, model, messages)
        headers = self._headers(use_cookies)
        try:
            response = http_request(
                self.base_url + "/duckchat/v1/chat",
                method="POST",
                headers=headers,
                body=payload,
                timeout=self.timeout,
                proxy=self.cfg.get("proxy") or None,
            )
        except BaseException as exc:
            status, message = parse_http_error(exc)
            body = read_error_body(exc)
            raise self._error_from_body(status, body, message) from exc

        content_type = (response.headers.get("content-type") or "").lower()
        if "event-stream" not in content_type:
            raw = response.read().decode("utf-8", "replace")
            try:
                response.close()
            except Exception:
                pass
            if raw.lstrip().startswith("<"):
                raise ProviderError(
                    "Duck.ai 返回 HTML 拦截页，可能需要有效的 vqd_hash 或代理",
                    status=502,
                    provider=self.name,
                    fallback=True,
                )
            raise self._error_from_body(response.status, raw, raw[:300])

        emitted = False
        reasoning_parts: dict[str, str] = {}
        try:
            for data in iter_sse_data(response):
                if data.strip() == "[DONE]":
                    break
                chunk = safe_json_loads(data)
                if not isinstance(chunk, dict):
                    continue
                action = str(chunk.get("action") or "")
                event_type = str(chunk.get("type") or chunk.get("errorType") or "")
                if action == "error" or event_type.startswith("ERR_"):
                    detail = " ".join(
                        str(chunk.get(key) or "")
                        for key in ("type", "errorType", "message", "status", "overrideCode")
                    )
                    status = chunk.get("status") or chunk.get("statusCode") or 502
                    try:
                        status = int(status)
                    except (TypeError, ValueError):
                        status = 502
                    raise self._error_from_body(status, json.dumps(chunk), detail)

                role = str(chunk.get("role") or "")
                state = str(chunk.get("state") or "")
                text = _event_text(chunk)
                if role == "reasoning":
                    if not text:
                        continue
                    part = str(chunk.get("partIndex", chunk.get("id", "0")))
                    previous = reasoning_parts.get(part, "")
                    if state in ("part-start", "start") and part not in reasoning_parts:
                        delta = text
                        reasoning_parts[part] = text
                    elif text.startswith(previous):
                        delta = text[len(previous):]
                        reasoning_parts[part] = text
                    elif state in ("text-delta", "part-delta", "delta"):
                        delta = text
                        reasoning_parts[part] = previous + text
                    else:
                        delta = text
                        reasoning_parts[part] = text
                    if delta:
                        emitted = True
                        yield Delta(reasoning=delta)
                elif role == "assistant":
                    if text:
                        emitted = True
                        yield Delta(content=text)
                elif role in ("ui-component", "tool"):
                    continue
                elif text:
                    emitted = True
                    yield Delta(content=text)
        finally:
            try:
                response.close()
            except Exception:
                pass
        yield Delta(finish_reason="stop" if emitted else "incomplete")

    def health(self) -> dict:
        return {
            "enabled": bool(self.cfg.get("enabled", True)),
            "base_url": self.base_url,
            "vqd_hash_configured": bool(self.vqd_hash),
            "fe_version_configured": bool(self.fe_version),
            "fe_signals_configured": bool(self.fe_signals),
            "models": len(self.list_models()),
        }
