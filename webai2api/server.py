from __future__ import annotations

import base64
import json
import logging
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse

from . import __version__
from .auth import AuthManager
from .config import ConfigError, ConfigManager, validate_config
from .providers import ChatRequest, ProviderError, ProviderRegistry
from .ratelimit import QueuedLimiter, RateLimiter
from .util import estimate_tokens, flatten_messages, json_dumps, new_id
from .tools import apply_tool_prompt, parse_tool_calls, tool_call_chunk
from .ui import UI_HTML

log = logging.getLogger("webai2api.server")


def estimate_usage(prompt: str, completion: str) -> dict:
    prompt_tokens = estimate_tokens(prompt)
    completion_tokens = estimate_tokens(completion)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


class AppState:
    def __init__(self, config_path: str):
        self.manager = ConfigManager(config_path)
        self.config: dict = {}
        self.version = 0
        self.registry: Optional[ProviderRegistry] = None
        self.limiter = RateLimiter(0)
        self.auth = AuthManager([])
        self.warnings: list[str] = []
        self.started_at = time.time()
        self._lock = threading.RLock()
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        cfg, version = self.manager.get() if not force else (
            self.manager.reload(force=True),
            self.manager._version,
        )
        with self._lock:
            if version == self.version and self.registry is not None:
                return
            warnings = validate_config(cfg)
            self.config = cfg
            self.registry = ProviderRegistry(cfg)
            queue_cfg = cfg.get("queue") or {}
            self.limiter = QueuedLimiter(
                RateLimiter(
                    cfg["rpm"],
                    cfg.get("rpm_per_provider") or {},
                    window=cfg.get("rpm_window") or 60,
                ),
                enabled=queue_cfg.get("enabled", True),
                max_size=queue_cfg.get("max_size", 200),
                max_wait=queue_cfg.get("max_wait", 120),
            )
            self.auth = AuthManager(cfg["api_keys"])
            self.warnings = warnings
            self.version = version
            log.info(
                "配置已加载 (version=%s, providers=%s, rpm=%s)",
                version,
                ",".join(self.registry.enabled()),
                cfg.get("rpm"),
            )
            for warning in warnings:
                log.warning("%s", warning)


class WebAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"webai2api/{__version__}"
    state: AppState

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _request_id(self) -> str:
        return getattr(self, "_rid", "")

    def _cors_headers(self) -> None:
        origins = self.state.config.get("cors_origins") or []
        origin = self.headers.get("Origin")
        allowed = "*"
        if origins and "*" not in origins:
            allowed = origin if origin in origins else (origins[0] if origins else "")
        if allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
            if allowed != "*":
                self.send_header("Vary", "Origin")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-API-Key, Api-Key",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "X-Request-Id, Retry-After")

    def _send_json(self, status: int, payload: Any, extra: Optional[dict] = None) -> None:
        body = json_dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-Id", self._request_id())
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        for key, value in (extra or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(
        self,
        status: int,
        message: str,
        error_type: str = "invalid_request_error",
        code: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        self._send_json(
            status,
            {
                "error": {
                    "message": message,
                    "type": error_type,
                    "param": None,
                    "code": code,
                }
            },
            extra=extra,
        )

    def _refresh_state(self) -> bool:
        try:
            self.state.refresh()
            return True
        except ConfigError as exc:
            log.error("配置错误: %s", exc)
            self._error(500, f"配置错误: {exc}", "server_error", "config_error")
            return False

    def _read_json(self) -> Optional[dict]:
        raw = self._read_body()
        if raw is None:
            return None
        if not raw:
            self._error(400, "请求体不能为空", "invalid_request_error", "invalid_json")
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._error(400, f"请求体不是合法 JSON: {exc}", "invalid_request_error", "invalid_json")
            return None
        if not isinstance(data, dict):
            self._error(400, "请求体必须是 JSON 对象", "invalid_request_error", "invalid_json")
            return None
        return data

    @staticmethod
    def _masked(value: Any) -> dict:
        text = str(value or "")
        return {"set": bool(text), "length": len(text)}

    def _provider_public(self, name: str, cfg: dict) -> dict:
        models = cfg.get("models") or {}
        info: dict[str, Any] = {
            "enabled": bool(cfg.get("enabled")),
            "base_url": str(cfg.get("base_url") or ""),
            "models": list(models.keys()) if isinstance(models, dict) else [],
            "configured": bool(
                cfg.get("token")
                or cfg.get("cookies")
                or (name == "duckduckgo" and cfg.get("vqd_hash"))
            ),
        }
        if name == "duckduckgo":
            vqd = self._masked(cfg.get("vqd_hash"))
            signals = self._masked(cfg.get("fe_signals"))
            cookies = self._masked(cfg.get("cookies"))
            info.update(
                {
                    "vqd_hash_set": vqd["set"],
                    "vqd_hash_length": vqd["length"],
                    "fe_version": str(cfg.get("fe_version") or ""),
                    "fe_signals_set": signals["set"],
                    "cookies_set": cookies["set"],
                    "user_agent": str(cfg.get("user_agent") or ""),
                }
            )
        else:
            info["token_set"] = bool(cfg.get("token"))
            info["cookies_set"] = bool(cfg.get("cookies"))
        return info

    def _config_payload(self) -> dict:
        cfg = self.state.config
        providers = {
            name: self._provider_public(name, pcfg)
            for name, pcfg in (cfg.get("providers") or {}).items()
        }
        return {
            "config_version": self.state.version,
            "config": {
                "rpm": cfg.get("rpm"),
                "rpm_window": cfg.get("rpm_window"),
                "rpm_per_provider": cfg.get("rpm_per_provider") or {},
                "queue": cfg.get("queue") or {},
                "fallback": cfg.get("fallback") or {},
                "default_provider": cfg.get("default_provider"),
                "api_key_required": bool(cfg.get("api_keys")),
                "providers": providers,
            },
        }

    def _handle_admin_config(self) -> None:
        self._send_json(200, self._config_payload())

    def _handle_admin_rpm(self) -> None:
        body = self._read_json()
        if body is None:
            return
        try:
            rpm = int(body.get("rpm", self.state.config.get("rpm", 0)))
            window = float(body.get("rpm_window", self.state.config.get("rpm_window", 60)))
            if rpm < 0 or rpm > 1_000_000:
                raise ValueError("rpm 必须在 0 到 1000000 之间")
            if window < 0.1 or window > 86400:
                raise ValueError("rpm_window 必须在 0.1 到 86400 秒之间")
            per_provider = body.get("rpm_per_provider", None)
            if per_provider is not None:
                if not isinstance(per_provider, dict):
                    raise ValueError("rpm_per_provider 必须是对象")
                per_provider = {
                    str(name): int(value) for name, value in per_provider.items()
                }
                if any(value < 0 or value > 1_000_000 for value in per_provider.values()):
                    raise ValueError("provider RPM 必须在 0 到 1000000 之间")
            queue = body.get("queue", None)
            if queue is not None:
                if not isinstance(queue, dict):
                    raise ValueError("queue 必须是对象")
                queue = {
                    "enabled": bool(queue.get("enabled", True)),
                    "max_size": int(queue.get("max_size", 200)),
                    "max_wait": float(queue.get("max_wait", 120)),
                }
                if queue["max_size"] < 0 or queue["max_size"] > 1_000_000:
                    raise ValueError("queue.max_size 必须在 0 到 1000000 之间")
                if queue["max_wait"] < 0 or queue["max_wait"] > 86400:
                    raise ValueError("queue.max_wait 必须在 0 到 86400 秒之间")
        except (TypeError, ValueError) as exc:
            self._error(400, f"参数无效: {exc}", "invalid_request_error", "invalid_rpm")
            return

        def mutate(raw: dict) -> None:
            raw["rpm"] = rpm
            raw["rpm_window"] = window
            if per_provider is not None:
                raw["rpm_per_provider"] = per_provider
            if queue is not None:
                current = raw.get("queue")
                merged = dict(current) if isinstance(current, dict) else {}
                merged.update(queue)
                raw["queue"] = merged

        try:
            self.state.manager.update(mutate)
            self.state.refresh()
        except ConfigError as exc:
            self._error(400, f"保存失败: {exc}", "invalid_request_error", "config_error")
            return
        payload = self._config_payload()
        payload["status"] = "ok"
        self._send_json(200, payload)

    def _handle_admin_duck(self) -> None:
        body = self._read_json()
        if body is None:
            return
        string_fields = ("vqd_hash", "fe_version", "fe_signals", "cookies", "user_agent")
        updates: dict[str, str] = {}
        for key in string_fields:
            if key not in body:
                continue
            value = body.get(key)
            if value is None:
                updates[key] = ""
                continue
            if not isinstance(value, str):
                self._error(400, f"{key} 必须是字符串", "invalid_request_error", "invalid_duck")
                return
            updates[key] = value.strip()
        base_url = body.get("base_url")
        if base_url is not None and not isinstance(base_url, str):
            self._error(400, "base_url 必须是字符串", "invalid_request_error", "invalid_duck")
            return
        models = body.get("models")
        if models is not None and not isinstance(models, dict):
            self._error(400, "models 必须是对象", "invalid_request_error", "invalid_duck")
            return
        enabled = body.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            self._error(400, "enabled 必须是布尔值", "invalid_request_error", "invalid_duck")
            return
        if updates.get("fe_signals"):
            try:
                base64.b64decode(updates["fe_signals"].encode("ascii"), validate=True)
            except Exception:
                self._error(
                    400,
                    "fe_signals 必须是合法的 base64 字符串",
                    "invalid_request_error",
                    "invalid_duck",
                )
                return

        def mutate(raw: dict) -> None:
            providers = raw.setdefault("providers", {})
            duck = providers.setdefault("duckduckgo", {})
            for key, value in updates.items():
                if value or key in body:
                    duck[key] = value
            if base_url:
                duck["base_url"] = base_url.strip().rstrip("/")
            if models is not None:
                duck["models"] = {
                    str(route): str(upstream) for route, upstream in models.items()
                }
            if enabled is not None:
                duck["enabled"] = enabled

        try:
            self.state.manager.update(mutate)
            self.state.refresh()
        except ConfigError as exc:
            self._error(400, f"保存失败: {exc}", "invalid_request_error", "config_error")
            return
        payload = self._config_payload()
        payload["status"] = "ok"
        self._send_json(200, payload)

    def _handle_ui(self) -> None:
        body = UI_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authorize(self) -> bool:
        key = self.state.auth.authenticate({k: v for k, v in self.headers.items()})
        if key is None:
            self._send_json(
                401,
                {
                    "error": {
                        "message": "无效或缺少 API Key。请使用 Authorization: Bearer <key>、"
                        "X-API-Key 或 Cookie webai2api_key。",
                        "type": "authentication_error",
                        "param": None,
                        "code": "invalid_api_key",
                    }
                },
                extra={"WWW-Authenticate": 'Bearer realm="webai2api"'},
            )
            return False
        self._api_key = key
        return True

    def _read_body(self) -> Optional[bytes]:
        length = self.headers.get("Content-Length")
        if length is None:
            self._error(411, "请求必须包含 Content-Length", "invalid_request_error")
            return None
        try:
            size = int(length)
        except ValueError:
            self._error(400, "Content-Length 无效", "invalid_request_error")
            return None
        limit = int(self.state.config.get("max_body_bytes") or 16 * 1024 * 1024)
        if size < 0 or size > limit:
            self._error(413, f"请求体超过限制 {limit} 字节", "invalid_request_error")
            return None
        data = self.rfile.read(size)
        return data

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._rid = uuid.uuid4().hex
        if not self._refresh_state():
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/ui":
            self._handle_ui()
            return
        if path == "/health":
            self._handle_health()
            return
        if path in ("/", "/v1"):
            self._send_json(
                200,
                {
                    "name": "webai2api",
                    "version": __version__,
                    "endpoints": [
                        "/v1/models",
                        "/v1/chat/completions",
                        "/health",
                        "/ui",
                    ],
                    "providers": self.state.registry.enabled(),
                },
            )
            return
        if path == "/admin/config":
            if not self._authorize():
                return
            self._handle_admin_config()
            return
        if path == "/v1/models":
            if not self._authorize():
                return
            self._handle_models()
            return
        if path.startswith("/v1/models/"):
            if not self._authorize():
                return
            model_id = path[len("/v1/models/") :]
            models = {m["id"]: m for m in self.state.registry.all_models()}
            if model_id in models:
                self._send_json(200, models[model_id])
            else:
                self._error(404, f"模型不存在: {model_id}", "invalid_request_error", "model_not_found")
            return
        self._error(404, "接口不存在", "invalid_request_error", "not_found")

    def do_POST(self) -> None:
        self._rid = uuid.uuid4().hex
        if not self._refresh_state():
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/v1/chat/completions":
            if not self._authorize():
                return
            self._handle_chat()
            return
        if path == "/admin/reload":
            if not self._authorize():
                return
            self._handle_reload()
            return
        if path == "/admin/rpm":
            if not self._authorize():
                return
            self._handle_admin_rpm()
            return
        if path == "/admin/duck":
            if not self._authorize():
                return
            self._handle_admin_duck()
            return
        self._error(404, "接口不存在", "invalid_request_error", "not_found")

    def _handle_health(self) -> None:
        providers = {}
        for name, provider in self.state.registry.providers.items():
            try:
                providers[name] = provider.health()
            except Exception as exc:
                providers[name] = {"error": str(exc)}
        self._send_json(
            200,
            {
                "status": "ok",
                "version": __version__,
                "config_version": self.state.version,
                "uptime": int(time.time() - self.state.started_at),
                "providers": providers,
                "rate_limit": self.state.limiter.stats(),
                "warnings": self.state.warnings,
            },
        )

    def _handle_models(self) -> None:
        models = self.state.registry.all_models()
        self._send_json(200, {"object": "list", "data": models})

    def _handle_reload(self) -> None:
        try:
            self.state.manager.reload(force=True)
            self.state.version = -1
            self.state.refresh()
        except ConfigError as exc:
            self._error(400, f"重新加载失败: {exc}", "invalid_request_error", "config_error")
            return
        self._send_json(
            200,
            {
                "status": "ok",
                "config_version": self.state.version,
                "providers": self.state.registry.enabled(),
                "warnings": self.state.warnings,
            },
        )

    def _parse_chat_request(self, body: dict) -> Optional[ChatRequest]:
        if not isinstance(body, dict):
            self._error(400, "请求体必须是 JSON 对象", "invalid_request_error")
            return None
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            self._error(400, "messages 必须是非空数组", "invalid_request_error", "invalid_messages")
            return None
        cleaned = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            content = item.get("content")
            tool_calls = item.get("tool_calls")
            if content is None and not tool_calls:
                continue
            entry = {"role": role, "content": content}
            if tool_calls is not None:
                entry["tool_calls"] = tool_calls
            for key in ("name", "tool_call_id"):
                if item.get(key) is not None:
                    entry[key] = item.get(key)
            cleaned.append(entry)
        if not cleaned:
            self._error(400, "messages 中没有有效消息", "invalid_request_error", "invalid_messages")
            return None
        model = str(body.get("model") or "").strip()
        if not model:
            default_provider = self.state.registry.providers.get(
                self.state.config.get("default_provider")
            )
            model = next(iter(default_provider.list_models()), "") if default_provider else ""
        stop = body.get("stop")
        if isinstance(stop, str):
            stop_list = [stop]
        elif isinstance(stop, list):
            stop_list = [str(x) for x in stop if x is not None]
        else:
            stop_list = []
        extra = {}
        for key in (
            "thinking",
            "enable_thinking",
            "search",
            "web_search",
            "reasoning_effort",
            "chat_mode",
            "parallel_tool_calls",
        ):
            if key in body:
                extra[key] = body[key]
        tools = body.get("tools")
        if tools is not None:
            if not isinstance(tools, list):
                self._error(
                    400, "tools 必须是数组", "invalid_request_error", "invalid_tools"
                )
                return None
            tools = [tool for tool in tools if isinstance(tool, dict)]
        else:
            tools = []
        tool_choice = body.get("tool_choice")
        if tool_choice is not None and not isinstance(tool_choice, (str, dict)):
            self._error(
                400,
                "tool_choice 必须是字符串或对象",
                "invalid_request_error",
                "invalid_tool_choice",
            )
            return None
        reasoning_effort = body.get("reasoning_effort")
        return ChatRequest(
            model=model,
            messages=cleaned,
            stream=bool(body.get("stream")),
            temperature=body.get("temperature"),
            top_p=body.get("top_p"),
            max_tokens=body.get("max_tokens"),
            stop=stop_list,
            reasoning_effort=str(reasoning_effort) if reasoning_effort is not None else None,
            tools=tools,
            tool_choice=tool_choice,
            extra=extra,
        )

    def _check_rate_limit(self, provider_name: str) -> bool:
        result = self.state.limiter.acquire(provider_name)
        self._rate_headers = {}
        if result.limit >= 0:
            self._rate_headers["X-RateLimit-Limit"] = result.limit
        if result.remaining >= 0:
            self._rate_headers["X-RateLimit-Remaining"] = max(0, result.remaining)
        if result.queued:
            self._rate_headers["X-Queue-Wait-Ms"] = result.queued_ms
        if result.allowed:
            return True
        retry = result.retry_after_int
        if result.queue_full:
            reason = "等待队列已满"
        elif result.queued:
            reason = "排队等待超时"
        else:
            reason = f"请在 {retry} 秒后重试"
        self._error(
            429,
            f"请求过于频繁（{provider_name}），{reason}。",
            "rate_limit_error",
            "rate_limit_exceeded",
            extra={
                "Retry-After": retry,
                "X-RateLimit-Limit": result.limit,
                "X-RateLimit-Remaining": 0,
            },
        )
        return False

    def _fallback_candidates(self, provider: Any) -> list[Any]:
        fallback_cfg = self.state.config.get("fallback") or {}
        if not fallback_cfg.get("enabled"):
            return [provider]
        order = fallback_cfg.get("order") or list(self.state.registry.providers.keys())
        candidates: list[Any] = []
        for name in [provider.name, *order]:
            candidate = self.state.registry.providers.get(name)
            if candidate is not None and candidate not in candidates:
                candidates.append(candidate)
        for candidate in self.state.registry.providers.values():
            if candidate not in candidates:
                candidates.append(candidate)
        max_attempts = max(1, int(fallback_cfg.get("max_attempts") or len(candidates)))
        return candidates[:max_attempts]

    def _prepare_attempt(self, provider: Any, request: ChatRequest) -> str:
        fallback_cfg = self.state.config.get("fallback") or {}
        models = fallback_cfg.get("models") or {}
        request.route_model = models.get(provider.name) or None
        upstream = provider.resolve_model(request.route_model or request.model)
        if not isinstance(upstream, str):
            upstream = json.dumps(upstream, ensure_ascii=True, separators=(",", ":"))
        return upstream

    def _response_headers_for(self, provider: Any, upstream: str, attempt: int) -> dict:
        headers = {
            "X-WebAI-Provider": provider.name,
            "X-WebAI-Upstream-Model": upstream,
        }
        headers.update(getattr(self, "_rate_headers", {}) or {})
        if attempt > 0:
            headers["X-WebAI-Fallback"] = "true"
        return headers

    def _handle_chat(self) -> None:
        raw = self._read_body()
        if raw is None:
            return
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._error(400, f"JSON 解析失败: {exc}", "invalid_request_error", "invalid_json")
            return
        self._chat_body = body
        request = self._parse_chat_request(body)
        if request is None:
            return
        try:
            provider, requested_model, _ = self.state.registry.resolve(request.model)
        except StopIteration:
            self._error(503, "没有可用的 provider", "server_error", "no_provider")
            return
        request.model = requested_model or request.model
        if request.tools and request.tool_choice != "none":
            request.messages = apply_tool_prompt(
                request.messages, request.tools, request.tool_choice
            )
        candidates = self._fallback_candidates(provider)
        if request.stream:
            self._handle_stream(candidates, request)
        else:
            self._handle_non_stream(candidates, request)

    def _handle_non_stream(self, candidates: list[Any], request: ChatRequest) -> None:
        started = time.time()
        last_error: Optional[ProviderError] = None
        for attempt, provider in enumerate(candidates):
            if not self._check_rate_limit(provider.name):
                return
            upstream = self._prepare_attempt(provider, request)
            try:
                result = provider.chat(request)
            except ProviderError as exc:
                last_error = exc
                if (exc.fallback or attempt > 0) and attempt + 1 < len(candidates):
                    log.warning(
                        "provider=%s 请求失败，自动路由到下一个 provider: %s",
                        provider.name,
                        exc,
                    )
                    continue
                self._send_provider_error(exc)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                log.error(
                    "provider=%s 非流式请求异常: %s\n%s",
                    provider.name,
                    exc,
                    traceback.format_exc(),
                )
                self._error(500, f"服务内部错误: {exc}", "server_error", "internal_error")
                return
            self._response_headers = self._response_headers_for(provider, upstream, attempt)
            prompt = flatten_messages(request.messages)
            usage = result.usage or estimate_usage(prompt, result.content)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage.setdefault(key, 0)
            calls, remaining = (
                parse_tool_calls(result.content, request.tools)
                if request.tools
                else ([], result.content)
            )
            message: dict[str, Any] = {
                "role": "assistant",
                "content": (remaining or None) if calls else result.content,
            }
            finish_reason = result.finish_reason or "stop"
            if calls:
                message["tool_calls"] = calls
                finish_reason = "tool_calls"
            if result.reasoning:
                message["reasoning_content"] = result.reasoning
            response = {
                "id": new_id("chatcmpl"),
                "object": "chat.completion",
                "created": int(started),
                "model": request.model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": usage,
            }
            self._send_json(200, response, extra=self._response_headers)
            return
        self._send_provider_error(
            last_error or ProviderError("没有可用的 provider", status=503)
        )

    def _handle_stream(self, candidates: list[Any], request: ChatRequest) -> None:
        provider: Any = None
        generator: Any = None
        first: Any = None
        upstream = ""
        attempt = 0
        last_error: Optional[ProviderError] = None
        for attempt, candidate in enumerate(candidates):
            if not self._check_rate_limit(candidate.name):
                return
            upstream = self._prepare_attempt(candidate, request)
            candidate_generator = candidate.stream_chat(request)
            try:
                first = next(candidate_generator)
            except StopIteration:
                first = None
            except ProviderError as exc:
                last_error = exc
                try:
                    candidate_generator.close()
                except Exception:
                    pass
                if (exc.fallback or attempt > 0) and attempt + 1 < len(candidates):
                    log.warning(
                        "provider=%s 请求失败，自动路由到下一个 provider: %s",
                        candidate.name,
                        exc,
                    )
                    continue
                self._send_provider_error(exc)
                return
            except (BrokenPipeError, ConnectionResetError):
                try:
                    candidate_generator.close()
                except Exception:
                    pass
                return
            except Exception as exc:
                log.error(
                    "provider=%s 流式请求异常: %s\n%s",
                    candidate.name,
                    exc,
                    traceback.format_exc(),
                )
                try:
                    candidate_generator.close()
                except Exception:
                    pass
                self._error(500, f"服务内部错误: {exc}", "server_error", "internal_error")
                return
            provider = candidate
            generator = candidate_generator
            break
        if provider is None:
            self._send_provider_error(
                last_error or ProviderError("没有可用的 provider", status=503)
            )
            return

        self._response_headers = self._response_headers_for(provider, upstream, attempt)
        include_usage = False
        try:
            stream_options = (self._chat_body or {}).get("stream_options") or {}
            include_usage = bool(stream_options.get("include_usage"))
        except Exception:
            include_usage = False
        tool_mode = bool(request.tools) and request.tool_choice != "none"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Request-Id", self._request_id())
        self._cors_headers()
        for key, value in (self._response_headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.close_connection = True

        completion_id = new_id("chatcmpl")
        created = int(time.time())

        def emit(payload: Any) -> None:
            self.wfile.write(b"data: " + json_dumps(payload).encode("utf-8") + b"\n\n")
            self.wfile.flush()

        def chunk(delta: dict, finish_reason: Optional[str] = None) -> dict:
            return {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": finish_reason,
                    }
                ],
            }

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: Optional[dict] = None
        finish_reason: Optional[str] = None
        try:
            emit(chunk({"role": "assistant", "content": ""}))
            pending = [first] if first is not None else []
            while True:
                delta = pending.pop(0) if pending else next(generator)
                if delta is None:
                    continue
                if delta.usage:
                    usage = delta.usage
                if delta.reasoning:
                    reasoning_parts.append(delta.reasoning)
                    emit(chunk({"reasoning_content": delta.reasoning}))
                if delta.content:
                    content_parts.append(delta.content)
                    if not tool_mode:
                        emit(chunk({"content": delta.content}))
                if delta.finish_reason:
                    finish_reason = delta.finish_reason
                    break

            text = "".join(content_parts)
            if tool_mode:
                calls, remaining = parse_tool_calls(text, request.tools)
                if calls:
                    if remaining:
                        emit(chunk({"content": remaining}))
                    for index, call in enumerate(calls):
                        emit(chunk({"tool_calls": [tool_call_chunk(call, index)]}))
                    final = chunk({}, "tool_calls")
                    if usage and not include_usage:
                        final["usage"] = usage
                    emit(final)
                else:
                    if text:
                        emit(chunk({"content": text}))
                    reason = finish_reason or (
                        "stop" if text or reasoning_parts else "incomplete"
                    )
                    final = chunk({}, reason)
                    if usage and not include_usage:
                        final["usage"] = usage
                    emit(final)
            else:
                reason = finish_reason or (
                    "stop" if text or reasoning_parts else "incomplete"
                )
                final = chunk({}, reason)
                if usage and not include_usage:
                    final["usage"] = usage
                emit(final)
            if include_usage:
                prompt = flatten_messages(request.messages)
                final_usage = usage or estimate_usage(prompt, text)
                emit(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": request.model,
                        "choices": [],
                        "usage": final_usage,
                    }
                )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log.info("客户端已断开流式连接")
        except ProviderError as exc:
            try:
                emit(
                    {
                        "error": {
                            "message": str(exc),
                            "type": "provider_error",
                            "param": None,
                            "code": "provider_error",
                        }
                    }
                )
            except (BrokenPipeError, ConnectionResetError):
                pass
            try:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        except Exception as exc:
            log.error(
                "provider=%s 流式中断: %s\n%s",
                provider.name,
                exc,
                traceback.format_exc(),
            )
            try:
                emit(
                    {
                        "error": {
                            "message": f"上游流中断: {exc}",
                            "type": "server_error",
                            "param": None,
                            "code": "stream_error",
                        }
                    }
                )
            except (BrokenPipeError, ConnectionResetError):
                pass
            try:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            try:
                generator.close()
            except Exception:
                pass

    def _send_provider_error(self, exc: ProviderError) -> None:
        status = exc.status if 400 <= exc.status <= 599 else 502
        extra = {}
        if exc.retry_after:
            extra["Retry-After"] = max(1, int(exc.retry_after))
        if status == 401:
            error_type = "authentication_error"
        elif status == 429:
            error_type = "rate_limit_error"
        elif status == 400:
            error_type = "invalid_request_error"
        else:
            error_type = "provider_error"
        self._error(
            status,
            str(exc),
            error_type,
            "provider_error",
            extra=extra or None,
        )


class WebAIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: AppState):
        self.state = state
        handler = type("BoundWebAIHandler", (WebAIHandler,), {"state": state})
        super().__init__(address, handler)


def serve(config_path: str, host: Optional[str] = None, port: Optional[int] = None) -> None:
    state = AppState(config_path)
    bind_host = host or state.config["listen"]["host"]
    bind_port = state.config["listen"]["port"] if port is None else port
    server = WebAIServer((bind_host, bind_port), state)
    log.info("webai2api %s 正在监听 http://%s:%s", __version__, bind_host, bind_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("收到中断，正在退出")
    finally:
        server.server_close()
