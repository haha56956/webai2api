from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterator, Optional

from ..pow import PowError, build_pow_response, solve_pow
from ..util import (
    DEFAULT_UA,
    find_token_in_cookies,
    flatten_messages,
    http_request,
    iter_sse_data,
    parse_http_error,
    safe_json_loads,
)
from .base import ChatRequest, Delta, Provider, ProviderError

log = logging.getLogger("webai2api.deepseek")

STATUS_FINISH = {
    "FINISHED": "stop",
    "CONTENT_FILTER": "content_filter",
    "CONTEXT_LENGTH_EXCEEDED": "length",
    "INCOMPLETE": "incomplete",
    "WIP": "wip",
    "TIMEOUT": "timeout",
}

REASONING_TYPES = {"THINK", "THINKING", "REASONING", "SEARCH", "TOOL_SEARCH", "TOOL_OPEN", "TOOL_FIND"}
RESPONSE_TYPES = {"RESPONSE", "TEMPLATE_RESPONSE"}


def _fragment_kind(fragment: dict) -> Optional[str]:
    kind = str(fragment.get("type", "")).upper()
    if not kind:
        return "response"
    if kind in RESPONSE_TYPES:
        return "response"
    if kind in REASONING_TYPES:
        return "reasoning"
    return None


class DeepSeekStreamState:
    def __init__(self) -> None:
        self.emitted = {"response": "", "reasoning": ""}
        self.fragment_kinds: dict[str, Optional[str]] = {}
        self.active_kind: Optional[str] = "response"
        self.status: Optional[str] = None
        self.message_id: Optional[Any] = None

    def append(self, kind: str, content: str) -> str:
        self.emitted[kind] += content
        return content

    def snapshot(self, kind: str, content: str) -> str:
        previous = self.emitted[kind]
        if content.startswith(previous):
            delta = content[len(previous):]
        elif previous.startswith(content):
            delta = ""
        else:
            delta = content
        self.emitted[kind] = content
        return delta


class DeepSeekProvider(Provider):
    name = "deepseek"

    def __init__(self, cfg: dict, global_cfg: dict):
        super().__init__(cfg, global_cfg)
        self.base_url = (cfg.get("base_url") or "https://chat.deepseek.com").rstrip("/")
        self.timeout = float(cfg.get("timeout") or 180)
        self.cookies = str(cfg.get("cookies") or "")
        self.token = self._resolve_token()
        self.headers = dict(cfg.get("headers") or {})
        self.pow_backend = str(cfg.get("pow_backend") or "auto")

    def _resolve_token(self) -> str:
        token = str(self.cfg.get("token") or "").strip()
        if not token:
            token = find_token_in_cookies(
                self.cfg.get("cookies"), ("userToken", "user_token", "ds_token", "token")
            )
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "user-agent": DEFAULT_UA,
            "origin": self.base_url,
            "referer": self.base_url + "/",
            "x-app-version": "2.0.0",
            "x-client-version": "2.4.0",
            "x-client-platform": "web",
            "x-client-bundle-id": "com.deepseek.chat",
            "x-client-locale": "en_US",
            "x-client-timezone-offset": str(-int(time.timezone / 60)),
            "accept-language": "en-US,en;q=0.9",
        }
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        if self.cookies:
            headers["cookie"] = self.cookies
        headers.update({str(k): str(v) for k, v in self.headers.items()})
        return headers

    def _post_json(self, path: str, body: Any, headers: Optional[dict] = None) -> Any:
        request_headers = self._base_headers()
        if headers:
            request_headers.update(headers)
        try:
            response = http_request(
                self.base_url + path,
                method="POST",
                headers=request_headers,
                body=body,
                timeout=self.timeout,
                proxy=self.cfg.get("proxy") or None,
            )
            raw = response.read().decode("utf-8", "replace")
        except BaseException as exc:
            status, message = parse_http_error(exc)
            raise ProviderError(
                f"DeepSeek 请求失败: {message}",
                status=status,
                provider=self.name,
                fallback=status == 429,
            ) from exc
        payload = safe_json_loads(raw)
        if payload is None:
            raise ProviderError(
                f"DeepSeek 返回非 JSON 响应: {raw[:200]}",
                status=502,
                provider=self.name,
            )
        return payload

    @staticmethod
    def _unwrap(payload: dict, context: str) -> dict:
        if not isinstance(payload, dict):
            raise ProviderError(f"DeepSeek {context} 返回格式异常", provider="deepseek")
        code = payload.get("code")
        message = payload.get("msg") or payload.get("message")
        data = payload.get("data")
        biz = None
        if isinstance(data, dict):
            biz_code = data.get("biz_code")
            if biz_code not in (None, 0, "0"):
                raise ProviderError(
                    f"DeepSeek {context} 失败: {data.get('biz_msg') or message or biz_code}",
                    provider="deepseek",
                    status=401 if str(biz_code) in ("40002", "40003") else 502,
                )
            biz = data.get("biz_data")
            if message is None:
                message = data.get("biz_msg")
        if code not in (None, 0, "0"):
            raise ProviderError(
                f"DeepSeek {context} 失败: {message or code}",
                provider="deepseek",
                status=401 if str(code) in ("40002", "40003") else 502,
            )
        if biz is None:
            if isinstance(data, dict):
                biz = data
            elif isinstance(payload.get("biz_data"), dict):
                biz = payload["biz_data"]
            else:
                raise ProviderError(
                    f"DeepSeek {context} 返回缺少 biz_data: {message or payload}",
                    provider="deepseek",
                )
        return biz

    def _require_token(self) -> None:
        if not self.token:
            raise ProviderError(
                "DeepSeek 未配置 token。请登录 chat.deepseek.com，"
                "从浏览器 Local Storage 的 userToken 复制 value 到 providers.deepseek.token，"
                "或通过 Cookie 提供 userToken。",
                status=401,
                provider=self.name,
            )

    def _create_session(self) -> str:
        payload = self._post_json("/api/v0/chat_session/create", {})
        biz = self._unwrap(payload, "创建会话")
        session = biz.get("chat_session") if isinstance(biz.get("chat_session"), dict) else None
        session_id = (session or {}).get("id") or biz.get("id")
        if not session_id:
            raise ProviderError("DeepSeek 创建会话失败：响应中没有会话 id", provider=self.name)
        return str(session_id)

    def _pow_header(self, target_path: str) -> str:
        payload = self._post_json(
            "/api/v0/chat/create_pow_challenge", {"target_path": target_path}
        )
        biz = self._unwrap(payload, "获取 PoW challenge")
        challenge = biz.get("challenge")
        if not isinstance(challenge, dict):
            raise ProviderError("DeepSeek PoW challenge 格式异常", provider=self.name)
        if challenge.get("algorithm") not in (None, "DeepSeekHashV1"):
            raise ProviderError(
                f"DeepSeek 返回了不支持的 PoW 算法: {challenge.get('algorithm')}",
                provider=self.name,
            )
        try:
            answer = solve_pow(
                challenge["salt"],
                challenge["expire_at"],
                challenge["challenge"],
                int(challenge["difficulty"]),
                backend=self.pow_backend,
            )
        except (PowError, KeyError, ValueError) as exc:
            raise ProviderError(
                f"DeepSeek PoW 求解失败: {exc}",
                status=502,
                provider=self.name,
            ) from exc
        return build_pow_response(challenge, answer)

    def _setup(self, request: ChatRequest) -> tuple[str, dict, dict]:
        self._require_token()
        model_cfg = self.resolve_model(request.route_model or request.model)
        if not isinstance(model_cfg, dict):
            model_cfg = {"model_type": str(model_cfg)}
        thinking = bool(model_cfg.get("thinking", self.cfg.get("thinking", False)))
        if request.reasoning_effort is not None:
            thinking = str(request.reasoning_effort).lower() != "none"
        search = bool(model_cfg.get("search", self.cfg.get("search", False)))
        prompt = flatten_messages(request.messages)
        if not prompt.strip():
            raise ProviderError("消息内容为空", status=400, provider=self.name)
        session_id = self._create_session()
        payload = {
            "action": None,
            "chat_session_id": session_id,
            "parent_message_id": None,
            "model_type": model_cfg.get("model_type") or ("expert" if thinking else "default"),
            "prompt": prompt,
            "ref_file_ids": [],
            "thinking_enabled": thinking,
            "search_enabled": search,
            "preempt": False,
        }
        return session_id, payload, model_cfg

    def stream_chat(self, request: ChatRequest) -> Iterator[Delta]:
        target_path = "/api/v0/chat/completion"
        session_id, payload, _ = self._setup(request)
        pow_response = self._pow_header(target_path)
        headers = self._base_headers()
        headers.update(
            {
                "accept": "text/event-stream",
                "x-ds-pow-response": pow_response,
                "referer": f"{self.base_url}/a/chat/s/{session_id}",
            }
        )
        try:
            response = http_request(
                self.base_url + target_path,
                method="POST",
                headers=headers,
                body=payload,
                timeout=self.timeout,
                proxy=self.cfg.get("proxy") or None,
            )
        except BaseException as exc:
            status, message = parse_http_error(exc)
            if "pow" in message.lower() or status == 400:
                message += "（可能是 PoW 求解错误或 token 过期）"
            raise ProviderError(
                f"DeepSeek 流式请求失败: {message}",
                status=status,
                provider=self.name,
                fallback=status == 429,
            ) from exc

        content_type = (response.headers.get("content-type") or "").lower()
        if "json" in content_type and "event-stream" not in content_type:
            raw = response.read().decode("utf-8", "replace")
            parsed = safe_json_loads(raw)
            if isinstance(parsed, dict):
                try:
                    self._unwrap(parsed, "聊天")
                except ProviderError:
                    raise
            raise ProviderError(
                f"DeepSeek 返回了非流式响应: {raw[:300]}",
                status=502,
                provider=self.name,
            )

        state = DeepSeekStreamState()
        try:
            for data in iter_sse_data(response):
                if data.strip() == "[DONE]":
                    break
                obj = safe_json_loads(data)
                if not isinstance(obj, dict):
                    continue
                yield from self._process_frame(obj, state)
        finally:
            try:
                response.close()
            except Exception:
                pass
        finish = STATUS_FINISH.get(state.status or "", None)
        if finish is None:
            finish = "stop" if state.emitted["response"] else "incomplete"
        yield Delta(finish_reason=finish)

    def _process_frame(self, obj: dict, state: DeepSeekStreamState) -> Iterator[Delta]:
        if obj.get("response_message_id") is not None:
            state.message_id = obj["response_message_id"]
        value = obj.get("v")
        operation = obj.get("o")
        if operation == "BATCH" and isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from self._process_frame(item, state)
            return

        if isinstance(value, dict) and isinstance(value.get("response"), dict):
            response = value["response"]
            if response.get("message_id") is not None:
                state.message_id = response["message_id"]
            if response.get("status") is not None:
                state.status = str(response["status"]).upper()
            fragments = response.get("fragments")
            if isinstance(fragments, list):
                combined = {"response": "", "reasoning": ""}
                order: list[str] = []
                for index, fragment in enumerate(fragments):
                    if not isinstance(fragment, dict):
                        continue
                    kind = _fragment_kind(fragment)
                    if kind is None:
                        continue
                    state.fragment_kinds[str(index)] = kind
                    content = fragment.get("content")
                    if not isinstance(content, str):
                        continue
                    if kind not in order:
                        order.append(kind)
                    state.active_kind = kind
                    combined[kind] += content
                if fragments:
                    state.fragment_kinds["-1"] = state.active_kind
                for kind in order:
                    delta = state.snapshot(kind, combined[kind])
                    if delta:
                        if kind == "reasoning":
                            yield Delta(reasoning=delta)
                        else:
                            yield Delta(content=delta)
            return

        path = obj.get("p")
        if isinstance(path, str):
            if path in ("response/status", "quasi_status", "response/quasi_status"):
                if isinstance(value, str):
                    state.status = value.upper()
                return
            if path == "response/fragments" and isinstance(value, list):
                if operation == "SET":
                    for fragment in value:
                        yield from self._fragment_delta(fragment, state, snapshot=True)
                else:
                    for fragment in value:
                        yield from self._fragment_delta(fragment, state, snapshot=False)
                return
            if path.startswith("response/fragments/"):
                parts = path.split("/")
                if len(parts) >= 4:
                    index = parts[2]
                    field = parts[3]
                    if field == "type" and isinstance(value, str):
                        state.fragment_kinds[index] = _fragment_kind({"type": value})
                        state.active_kind = state.fragment_kinds[index]
                        return
                    if field == "content" and isinstance(value, str):
                        kind = state.fragment_kinds.get(index) or state.active_kind or "response"
                        if operation == "SET":
                            delta = state.snapshot(kind, value)
                        else:
                            delta = state.append(kind, value)
                        if delta:
                            yield Delta(reasoning=delta) if kind == "reasoning" else Delta(content=delta)
                        return
            if path.endswith("/content") and isinstance(value, str):
                kind = state.active_kind or "response"
                if operation == "SET":
                    delta = state.snapshot(kind, value)
                else:
                    delta = state.append(kind, value)
                if delta:
                    yield Delta(reasoning=delta) if kind == "reasoning" else Delta(content=delta)
                return
            return

        if isinstance(value, str) and value:
            kind = state.active_kind or "response"
            delta = state.append(kind, value)
            if kind == "reasoning":
                yield Delta(reasoning=delta)
            else:
                yield Delta(content=delta)

    def _fragment_delta(self, fragment: Any, state: DeepSeekStreamState, snapshot: bool) -> Iterator[Delta]:
        if not isinstance(fragment, dict):
            return
        kind = _fragment_kind(fragment)
        if kind is None:
            return
        state.active_kind = kind
        content = fragment.get("content")
        if not isinstance(content, str):
            return
        delta = state.snapshot(kind, content) if snapshot else state.append(kind, content)
        if delta:
            yield Delta(reasoning=delta) if kind == "reasoning" else Delta(content=delta)

    def health(self) -> dict:
        return {
            "enabled": bool(self.cfg.get("enabled", True)),
            "token_configured": bool(self.token),
            "cookies_configured": bool(self.cookies),
            "pow_backend": self.pow_backend,
        }
