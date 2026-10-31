from __future__ import annotations

import json
import logging
import time
import urllib.parse
import uuid
from typing import Any, Iterator, Optional

from ..qwen_web import MidTokenCache, generate_bx_ua, generate_ssxmod
from ..util import (
    DEFAULT_UA,
    build_cookie_header,
    find_token_in_cookies,
    flatten_messages,
    http_request,
    iter_sse_data,
    parse_cookie_header,
    parse_http_error,
    safe_json_loads,
    timezone_header,
)
from .base import ChatRequest, Delta, Provider, ProviderError

log = logging.getLogger("webai2api.qwen")

WAF_HINT = (
    "Qwen 上游 WAF 拦截（FAIL_SYS_USER_VALIDATE）。请配置已登录账号的浏览器 Cookie"
    "（含 cna、ssxmod_itna、token 等）或 token，并尽量使用住宅 IP；"
    "纯访客流式接口经常会被拦截。"
)

_midtoken_cache = MidTokenCache()


class QwenProvider(Provider):
    name = "qwen"

    def __init__(self, cfg: dict, global_cfg: dict):
        super().__init__(cfg, global_cfg)
        self.base_url = (cfg.get("base_url") or "https://chat.qwen.ai").rstrip("/")
        self.timeout = float(cfg.get("timeout") or 300)
        self.user_cookies = parse_cookie_header(str(cfg.get("cookies") or ""))
        self.token = self._resolve_token()
        self.headers = dict(cfg.get("headers") or {})
        self.client_version = str(cfg.get("client_version") or "0.2.84")
        self.user_agent = str(cfg.get("user_agent") or DEFAULT_UA)
        self.auto_cookies = bool(cfg.get("auto_cookies", True))
        self.auto_bxua = bool(cfg.get("auto_bxua", True))
        self.chat_mode = str(cfg.get("chat_mode") or ("normal" if self.token else "guest"))
        self.auto_delete = bool(cfg.get("auto_delete_chat", False))

    def _resolve_token(self) -> str:
        token = str(self.cfg.get("token") or "").strip()
        if not token:
            token = find_token_in_cookies(
                self.cfg.get("cookies"), ("token", "qwen_token", "access_token")
            )
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token

    def _build_headers(self) -> dict[str, str]:
        fingerprint = None
        cookies = dict(self.user_cookies)
        if self.auto_cookies:
            generated = generate_ssxmod()
            fingerprint = generated["fingerprint"]
            cookies["ssxmod_itna"] = generated["ssxmod_itna"]
            cookies["ssxmod_itna2"] = generated["ssxmod_itna2"]
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8",
            "content-type": "application/json",
            "user-agent": self.user_agent,
            "origin": self.base_url,
            "referer": self.base_url + "/",
            "source": "web",
            "version": self.client_version,
            "bx-v": "2.5.37",
            "x-request-id": str(uuid.uuid4()),
            "x-requested-with": "XMLHttpRequest",
            "x-accel-buffering": "no",
            "timezone": timezone_header(),
            "sec-ch-ua": '"Google Chrome";v="138", "Not.A/Brand";v="8", "Chromium";v="138"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if self.auto_bxua:
            headers["bx-ua"] = generate_bx_ua(fingerprint)
        midtoken = str(self.cfg.get("bx_umidtoken") or "")
        if not midtoken:
            midtoken = _midtoken_cache.get(proxy=self.cfg.get("proxy") or None)
        if midtoken:
            headers["bx-umidtoken"] = midtoken
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        if cookies:
            headers["cookie"] = build_cookie_header(cookies)
        headers.update({str(k): str(v) for k, v in self.headers.items()})
        return headers

    def _request_json(self, path: str, body: Any) -> Any:
        headers = self._build_headers()
        try:
            response = http_request(
                self.base_url + path,
                method="POST",
                headers=headers,
                body=body,
                timeout=self.timeout,
                proxy=self.cfg.get("proxy") or None,
            )
            raw = response.read().decode("utf-8", "replace")
        except BaseException as exc:
            status, message = parse_http_error(exc)
            raise ProviderError(
                f"Qwen 请求失败: {message}",
                status=status,
                provider=self.name,
                fallback=status == 429,
            ) from exc
        if raw.lstrip().startswith("<"):
            raise ProviderError(WAF_HINT, status=502, provider=self.name)
        payload = safe_json_loads(raw)
        if payload is None:
            raise ProviderError(
                f"Qwen 返回非 JSON 响应: {raw[:200]}", status=502, provider=self.name
            )
        return payload

    @staticmethod
    def _extract_error(payload: dict) -> str:
        ret = payload.get("ret")
        if isinstance(ret, list) and ret:
            return " | ".join(str(x) for x in ret)
        if isinstance(ret, str):
            return ret
        data = payload.get("data")
        if isinstance(data, dict) and data.get("success") is False:
            code = data.get("code") or data.get("details")
            if code:
                return str(code)
        error = payload.get("error")
        if isinstance(error, dict) and error:
            return f'{error.get("code", "")}: {error.get("details", error)}'
        return ""

    def _create_chat(self, model: str, prompt: str) -> str:
        body = {
            "title": (prompt.strip().splitlines()[0] if prompt.strip() else "New Chat")[:60],
            "models": [model],
            "chat_mode": self.chat_mode,
            "chat_type": "t2t",
            "timestamp": int(time.time() * 1000),
            "project_id": "",
        }
        payload = self._request_json("/api/v2/chats/new", body)
        if not isinstance(payload, dict):
            raise ProviderError("Qwen 创建会话返回格式异常", provider=self.name)
        error = self._extract_error(payload)
        if error:
            status = 502
            if "FAIL_SYS_USER_VALIDATE" in error or "RGV587" in error:
                raise ProviderError(WAF_HINT, status=502, provider=self.name)
            raise ProviderError(f"Qwen 创建会话失败: {error}", status=status, provider=self.name)
        data = payload.get("data")
        chat_id = data.get("id") if isinstance(data, dict) else None
        if not payload.get("success") or not chat_id:
            raise ProviderError(
                f"Qwen 创建会话失败: {json.dumps(payload, ensure_ascii=False)[:400]}",
                provider=self.name,
            )
        return str(chat_id)

    def _feature_config(self, request: ChatRequest) -> dict:
        thinking = bool(self.cfg.get("thinking", False))
        for key in ("thinking", "enable_thinking"):
            if key in request.extra:
                thinking = bool(request.extra[key])
        if request.reasoning_effort is not None:
            thinking = str(request.reasoning_effort).lower() != "none"
        if not thinking:
            return {
                "thinking_enabled": False,
                "output_schema": "phase",
                "thinking_format": "summary",
                "thinking_budget": 81920,
            }
        return {
            "auto_thinking": False,
            "thinking_mode": "Auto",
            "thinking_enabled": True,
            "output_schema": "phase",
            "thinking_format": "summary",
            "research_mode": "normal",
            "auto_search": bool(self.cfg.get("search", False)),
            "thinking_budget": 81920,
        }

    def _completion_body(self, chat_id: str, model: str, prompt: str, request: ChatRequest) -> dict:
        now = int(time.time() * 1000)
        message_id = str(uuid.uuid4())
        return {
            "stream": True,
            "version": "2.1",
            "incremental_output": True,
            "chat_id": chat_id,
            "chat_mode": self.chat_mode,
            "model": model,
            "parent_id": None,
            "messages": [
                {
                    "fid": message_id,
                    "parentId": None,
                    "childrenIds": [],
                    "role": "user",
                    "content": prompt,
                    "user_action": "chat",
                    "files": [],
                    "timestamp": now,
                    "models": [model],
                    "chat_type": "t2t",
                    "feature_config": self._feature_config(request),
                    "extra": {"meta": {"subChatType": "t2t"}},
                    "sub_chat_type": "t2t",
                }
            ],
            "timestamp": now,
        }

    def _delete_chat(self, chat_id: str) -> None:
        try:
            headers = self._build_headers()
            http_request(
                self.base_url + "/api/v2/chats/" + urllib.parse.quote(chat_id),
                method="DELETE",
                headers=headers,
                timeout=30,
                proxy=self.cfg.get("proxy") or None,
            ).read()
        except Exception as exc:
            log.debug("删除 Qwen 会话失败: %s", exc)

    def stream_chat(self, request: ChatRequest) -> Iterator[Delta]:
        prompt = flatten_messages(request.messages)
        if not prompt.strip():
            raise ProviderError("消息内容为空", status=400, provider=self.name)
        model = str(
            self.resolve_model(request.route_model or request.model) or request.model
        )
        chat_id = self._create_chat(model, prompt)
        body = self._completion_body(chat_id, model, prompt, request)
        headers = self._build_headers()
        headers["accept"] = "text/event-stream"
        url = (
            self.base_url
            + "/api/v2/chat/completions?chat_id="
            + urllib.parse.quote(chat_id)
        )
        usage: Optional[dict] = None
        emitted = False
        try:
            try:
                response = http_request(
                    url,
                    method="POST",
                    headers=headers,
                    body=body,
                    timeout=self.timeout,
                    proxy=self.cfg.get("proxy") or None,
                )
            except BaseException as exc:
                status, message = parse_http_error(exc)
                if "RGV587" in message or "FAIL_SYS_USER_VALIDATE" in message:
                    raise ProviderError(WAF_HINT, status=502, provider=self.name) from exc
                raise ProviderError(
                    f"Qwen 流式请求失败: {message}",
                    status=status,
                    provider=self.name,
                    fallback=status == 429,
                ) from exc

            content_type = (response.headers.get("content-type") or "").lower()
            if "text/html" in content_type:
                raise ProviderError(WAF_HINT, status=502, provider=self.name)
            if "event-stream" not in content_type and "json" in content_type:
                raw = response.read().decode("utf-8", "replace")
                payload = safe_json_loads(raw)
                if isinstance(payload, dict):
                    error = self._extract_error(payload)
                    if "FAIL_SYS_USER_VALIDATE" in error or "RGV587" in error:
                        raise ProviderError(WAF_HINT, status=502, provider=self.name)
                    if error:
                        raise ProviderError(
                            f"Qwen 聊天失败: {error}", status=502, provider=self.name
                        )
                raise ProviderError(
                    f"Qwen 返回了非流式响应: {raw[:300]}", status=502, provider=self.name
                )

            try:
                for data in iter_sse_data(response):
                    if data.strip() == "[DONE]":
                        break
                    chunk = safe_json_loads(data)
                    if not isinstance(chunk, dict):
                        continue
                    error = self._extract_error(chunk)
                    if error:
                        if "FAIL_SYS_USER_VALIDATE" in error or "RGV587" in error:
                            raise ProviderError(WAF_HINT, status=502, provider=self.name)
                        raise ProviderError(
                            f"Qwen 聊天失败: {error}", status=502, provider=self.name
                        )
                    if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices or not isinstance(choices[0], dict):
                        continue
                    delta = choices[0].get("delta") or {}
                    if not isinstance(delta, dict):
                        continue
                    reasoning = delta.get("reasoning")
                    content = delta.get("content")
                    phase = delta.get("phase")
                    if isinstance(reasoning, str) and reasoning:
                        emitted = True
                        yield Delta(reasoning=reasoning)
                    if isinstance(content, str) and content:
                        if phase == "think":
                            emitted = True
                            yield Delta(reasoning=content)
                        elif phase in (None, "answer", "response"):
                            emitted = True
                            yield Delta(content=content)
            finally:
                try:
                    response.close()
                except Exception:
                    pass
        finally:
            if self.auto_delete:
                self._delete_chat(chat_id)
        if usage:
            normalized = dict(usage)
            if "input_tokens" in normalized and "prompt_tokens" not in normalized:
                normalized["prompt_tokens"] = normalized.pop("input_tokens")
            if "output_tokens" in normalized and "completion_tokens" not in normalized:
                normalized["completion_tokens"] = normalized.pop("output_tokens")
            yield Delta(usage=normalized)
        yield Delta(finish_reason="stop" if emitted else "incomplete")

    def health(self) -> dict:
        return {
            "enabled": bool(self.cfg.get("enabled", True)),
            "mode": self.chat_mode,
            "token_configured": bool(self.token),
            "cookies_configured": bool(self.user_cookies),
            "auto_cookies": self.auto_cookies,
        }
