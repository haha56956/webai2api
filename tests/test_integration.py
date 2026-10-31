import base64
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from webai2api.pow import deepseek_hash
from webai2api.server import AppState, WebAIServer

SALT = "abc123"
EXPIRE_AT = "1784018472563"
POW_ANSWER = 3
POW_CHALLENGE = deepseek_hash(f"{SALT}_{EXPIRE_AT}_{POW_ANSWER}".encode()).hex()
REQUESTS = []
REQUESTS_LOCK = threading.Lock()


class MockUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _record(self, body):
        with REQUESTS_LOCK:
            REQUESTS.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
            )

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode()) if raw else {}
        except json.JSONDecodeError:
            return {"_raw": raw.decode("utf-8", "replace")}

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, frames):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        for frame in frames:
            if isinstance(frame, dict):
                frame = "data: " + json.dumps(frame, ensure_ascii=False)
            self.wfile.write((frame + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self):
        path = urlparse(self.path).path
        self._record({})
        if path == "/duckchat/v1/status":
            self.send_response(200)
            self.send_header("x-vqd-hash-1", "test-hash")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        body = self._read_body()
        self._record(body)

        if path == "/api/v0/chat_session/create":
            self._json(
                200,
                {"code": 0, "data": {"biz_data": {"chat_session": {"id": "sess-1"}}}},
            )
            return
        if path == "/api/v0/chat/create_pow_challenge":
            self._json(
                200,
                {
                    "code": 0,
                    "data": {
                        "biz_data": {
                            "challenge": {
                                "algorithm": "DeepSeekHashV1",
                                "challenge": POW_CHALLENGE,
                                "salt": SALT,
                                "signature": "sig",
                                "difficulty": 10,
                                "expire_at": EXPIRE_AT,
                                "target_path": "/api/v0/chat/completion",
                            }
                        }
                    },
                },
            )
            return
        if path == "/api/v0/chat/completion":
            header = self.headers.get("x-ds-pow-response")
            if not header:
                self._json(400, {"code": 40001, "msg": "missing pow"})
                return
            try:
                decoded = json.loads(base64.b64decode(header))
            except Exception:
                self._json(400, {"code": 40001, "msg": "bad pow"})
                return
            if decoded.get("answer") != POW_ANSWER:
                self._json(400, {"code": 40001, "msg": "wrong pow answer"})
                return
            self._sse(
                [
                    {
                        "v": {
                            "response": {
                                "message_id": 123,
                                "status": "FINISHED",
                                "fragments": [
                                    {"type": "THINK", "content": "think "},
                                    {"type": "RESPONSE", "content": "Hello "},
                                ],
                            }
                        }
                    },
                    {"p": "response/fragments/-1/content", "o": "APPEND", "v": "world"},
                    {"p": "response/fragments/-1/content", "o": "APPEND", "v": "!"},
                    {"p": "response/status", "v": "FINISHED"},
                ]
            )
            return
        if path == "/api/v2/chats/new":
            if body.get("models") == ["qwen-waf"]:
                self._json(
                    200,
                    {
                        "ret": [
                            "FAIL_SYS_USER_VALIDATE",
                            "RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试",
                        ]
                    },
                )
                return
            self._json(200, {"success": True, "data": {"id": "chat-1"}})
            return
        if path == "/api/v2/chat/completions":
            if query.get("chat_id") != ["chat-1"]:
                self._json(400, {"success": False, "data": {"code": "bad chat id"}})
                return
            self._sse(
                [
                    {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
                    {"choices": [{"delta": {"phase": "think", "content": "qthink"}}]},
                    {"choices": [{"delta": {"phase": "answer", "content": "Qwen "}}]},
                    {"choices": [{"delta": {"phase": "answer", "content": "ok"}}]},
                    {
                        "usage": {
                            "input_tokens": 5,
                            "output_tokens": 3,
                            "total_tokens": 8,
                        }
                    },
                ]
            )
            return
        if path == "/duckchat/v1/chat":
            if self.headers.get("x-vqd-hash-1") != "test-hash":
                self._json(418, {"type": "ERR_CHALLENGE", "error": "bad vqd"})
                return
            if body.get("model") == "tool-call":
                self._sse(
                    [
                        {
                            "role": "assistant",
                            "text": "\u6211\u6765\u67e5\u8be2\u3002\n[[tool_call]]\n"
                            '{"name":"get_weather","arguments":{"city":"Paris"}}\n'
                            "[[/tool_call]]",
                        }
                    ]
                )
                return
            if body.get("model") == "quota":
                self._json(429, {"error": "free quota exceeded"})
                return
            if body.get("model") == "quota-retry" and self.headers.get("cookie"):
                self._json(429, {"error": "free quota exceeded"})
                return
            self._sse(
                [
                    {"role": "assistant", "text": "Duck "},
                    {"role": "assistant", "text": "ok"},
                ]
            )
            return
        self._json(404, {"error": "not found", "path": path})


class MockUpstream:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockUpstreamHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Gateway:
    def __init__(self, config, directory):
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        self.path = path
        self.state = AppState(str(path))
        self.server = WebAIServer(("127.0.0.1", 0), self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def request(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        all_headers = {"Content-Type": "application/json"}
        all_headers.update(headers or {})
        request = urllib.request.Request(
            self.url + path, data=data, headers=all_headers, method=method
        )
        try:
            response = urllib.request.urlopen(request, timeout=30)
            return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def chat(self, payload, headers=None):
        return self.request("POST", "/v1/chat/completions", payload, headers)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def make_config(upstream_url, rpm=0, qwen_delay=None, queue=None, fallback=None):
    return {
        "listen": {"host": "127.0.0.1", "port": 0},
        "api_keys": ["sk-local"],
        "rpm": rpm,
        "rpm_window": 60,
        "queue": queue or {"enabled": False, "max_size": 0, "max_wait": 0},
        "fallback": fallback
        or {"enabled": True, "order": ["qwen", "deepseek"], "max_attempts": 3, "models": {}},
        "default_provider": "deepseek",
        "providers": {
            "deepseek": {
                "enabled": True,
                "base_url": upstream_url,
                "token": "ds-token",
                "models": {
                    "deepseek-chat": {"model_type": "default", "thinking": False},
                },
            },
            "qwen": {
                "enabled": True,
                "base_url": upstream_url,
                "token": "qwen-token",
                "bx_umidtoken": "mid-token",
                "models": {"qwen-test": "qwen-test", "qwen-waf": "qwen-waf"},
            },
            "duckduckgo": {
                "enabled": True,
                "base_url": upstream_url,
                "cookies": "dc=1",
                "vqd_hash": "test-hash",
                "fe_version": "test-fe",
                "models": {
                    "duck-test": "gpt-4o-mini",
                    "duck-tool": "tool-call",
                    "duck-quota": "quota",
                    "duck-quota-retry": "quota-retry",
                },
            },
        },
    }


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = MockUpstream()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.gateway = Gateway(make_config(cls.upstream.url), cls.tmp.name)
        cls.auth = {"Authorization": "Bearer sk-local"}

    @classmethod
    def tearDownClass(cls):
        cls.gateway.close()
        cls.upstream.close()
        cls.tmp.cleanup()

    def test_health(self):
        status, _, body = self.gateway.request("GET", "/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")
        self.assertIn("deepseek", data["providers"])

    def test_auth_required(self):
        status, _, body = self.gateway.request("POST", "/v1/chat/completions", {})
        self.assertEqual(status, 401)
        self.assertIn("error", json.loads(body))

    def test_auth_cookie(self):
        status, _, body = self.gateway.request(
            "GET", "/v1/models", headers={"Cookie": "foo=1; webai2api_key=sk-local"}
        )
        self.assertEqual(status, 200)
        ids = {m["id"] for m in json.loads(body)["data"]}
        self.assertIn("deepseek-chat", ids)

    def test_models(self):
        status, _, body = self.gateway.request("GET", "/v1/models", headers=self.auth)
        self.assertEqual(status, 200)
        ids = {m["id"] for m in json.loads(body)["data"]}
        self.assertIn("deepseek-chat", ids)
        self.assertIn("qwen-test", ids)
        self.assertIn("duck-test", ids)

    def test_deepseek_non_stream(self):
        status, headers, body = self.gateway.chat(
            {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "be brief"},
                    {"role": "user", "content": "hi"},
                ],
            },
            headers=self.auth,
        )
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["choices"][0]["message"]["content"], "Hello world!")
        self.assertEqual(data["choices"][0]["message"]["reasoning_content"], "think ")
        self.assertEqual(data["choices"][0]["finish_reason"], "stop")
        self.assertEqual(headers.get("X-WebAI-Provider"), "deepseek")
        with REQUESTS_LOCK:
            pow_requests = [r for r in REQUESTS if r["path"] == "/api/v0/chat/completion"]
        self.assertTrue(pow_requests)
        self.assertIn("x-ds-pow-response", pow_requests[-1]["headers"])
        self.assertIn("Bearer ds-token", pow_requests[-1]["headers"]["authorization"])

    def test_deepseek_stream(self):
        request = urllib.request.Request(
            self.gateway.url + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "deepseek-chat",
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "messages": [{"role": "user", "content": "hi"}],
                }
            ).encode(),
            headers={"Content-Type": "application/json", **self.auth},
            method="POST",
        )
        response = urllib.request.urlopen(request, timeout=30)
        lines = [line.decode().strip() for line in response if line.strip()]
        response.close()
        payloads = []
        for line in lines:
            if line.startswith("data: ") and line != "data: [DONE]":
                payloads.append(json.loads(line[6:]))
        content = "".join(
            p["choices"][0]["delta"].get("content", "")
            for p in payloads
            if p.get("choices")
        )
        reasoning = "".join(
            p["choices"][0]["delta"].get("reasoning_content", "")
            for p in payloads
            if p.get("choices")
        )
        finishes = [
            p["choices"][0].get("finish_reason")
            for p in payloads
            if p.get("choices") and p["choices"][0].get("finish_reason")
        ]
        self.assertEqual(content, "Hello world!")
        self.assertEqual(reasoning, "think ")
        self.assertEqual(finishes[-1], "stop")
        self.assertEqual(lines[-1], "data: [DONE]")
        usage_chunks = [p for p in payloads if p.get("usage")]
        self.assertTrue(usage_chunks)
        self.assertEqual(usage_chunks[-1]["choices"], [])
        self.assertGreater(usage_chunks[-1]["usage"]["total_tokens"], 0)

    def test_qwen_non_stream_and_headers(self):
        status, headers, body = self.gateway.chat(
            {"model": "qwen-test", "messages": [{"role": "user", "content": "hello"}]},
            headers=self.auth,
        )
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["choices"][0]["message"]["content"], "Qwen ok")
        self.assertEqual(data["usage"]["total_tokens"], 8)
        self.assertEqual(headers.get("X-WebAI-Provider"), "qwen")
        with REQUESTS_LOCK:
            qwen_requests = [
                r for r in REQUESTS if r["path"].startswith("/api/v2/chat/completions")
            ]
        self.assertTrue(qwen_requests)
        headers_seen = qwen_requests[-1]["headers"]
        self.assertIn("ssxmod_itna=", headers_seen.get("cookie", ""))
        self.assertTrue(headers_seen.get("bx-ua", "").startswith("231!"))
        self.assertEqual(headers_seen.get("authorization"), "Bearer qwen-token")

    def test_qwen_waf_error(self):
        status, _, body = self.gateway.chat(
            {"model": "qwen-waf", "messages": [{"role": "user", "content": "hello"}]},
            headers=self.auth,
        )
        self.assertEqual(status, 502, body)
        self.assertIn("WAF", json.loads(body)["error"]["message"])

    def test_duck_non_stream(self):
        status, headers, body = self.gateway.chat(
            {"model": "duck-test", "messages": [{"role": "user", "content": "hello"}]},
            headers=self.auth,
        )
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["choices"][0]["message"]["content"], "Duck ok")
        self.assertEqual(headers.get("X-WebAI-Provider"), "duckduckgo")

    def test_duck_quota_clears_cookie_and_retries(self):
        status, headers, body = self.gateway.chat(
            {
                "model": "duck-quota-retry",
                "messages": [{"role": "user", "content": "hello"}],
            },
            headers=self.auth,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "Duck ok")
        with REQUESTS_LOCK:
            duck_requests = [
                r for r in REQUESTS if r["path"] == "/duckchat/v1/chat"
            ]
        quota_requests = [
            r for r in duck_requests if r["body"].get("model") == "quota-retry"
        ]
        self.assertGreaterEqual(len(quota_requests), 2)
        self.assertIn("cookie", quota_requests[0]["headers"])
        self.assertNotIn("cookie", quota_requests[-1]["headers"])

    def test_tool_call_non_stream(self):
        payload = {
            "model": "duck-tool",
            "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get current weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                }
            ],
            "tool_choice": "auto",
        }
        status, headers, body = self.gateway.chat(payload, headers=self.auth)
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        choice = data["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["content"], "\u6211\u6765\u67e5\u8be2\u3002")
        calls = choice["message"]["tool_calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["type"], "function")
        self.assertEqual(calls[0]["function"]["name"], "get_weather")
        self.assertEqual(
            json.loads(calls[0]["function"]["arguments"]), {"city": "Paris"}
        )
        self.assertEqual(headers.get("X-WebAI-Provider"), "duckduckgo")
        with REQUESTS_LOCK:
            requests = [
                r
                for r in REQUESTS
                if r["path"] == "/duckchat/v1/chat"
                and r["body"].get("model") == "tool-call"
            ]
        self.assertTrue(requests)
        sent = json.dumps(requests[-1]["body"], ensure_ascii=False)
        self.assertIn("[Tool use protocol]", sent)
        self.assertIn("get_weather", sent)

    def test_tool_call_stream(self):
        payload = {
            "model": "duck-tool",
            "stream": True,
            "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        },
                    },
                }
            ],
        }
        request = urllib.request.Request(
            self.gateway.url + "/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **self.auth},
            method="POST",
        )
        response = urllib.request.urlopen(request, timeout=30)
        lines = [line.decode().strip() for line in response if line.strip()]
        response.close()
        payloads = [
            json.loads(line[6:])
            for line in lines
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        deltas = [
            p["choices"][0]["delta"] for p in payloads if p.get("choices")
        ]
        content = "".join(d.get("content", "") for d in deltas)
        tool_chunks = [chunk for d in deltas for chunk in d.get("tool_calls", [])]
        self.assertIn("\u6211\u6765\u67e5\u8be2\u3002", content)
        self.assertEqual(len(tool_chunks), 1)
        self.assertEqual(tool_chunks[0]["function"]["name"], "get_weather")
        self.assertEqual(
            json.loads(tool_chunks[0]["function"]["arguments"]), {"city": "Paris"}
        )
        finishes = [
            p["choices"][0].get("finish_reason")
            for p in payloads
            if p.get("choices") and p["choices"][0].get("finish_reason")
        ]
        self.assertEqual(finishes[-1], "tool_calls")
        self.assertEqual(lines[-1], "data: [DONE]")

    def test_duck_quota_routes_to_next_provider(self):
        status, headers, body = self.gateway.chat(
            {"model": "duck-quota", "messages": [{"role": "user", "content": "hello"}]},
            headers=self.auth,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "Qwen ok")
        self.assertEqual(headers.get("X-WebAI-Provider"), "qwen")
        self.assertEqual(headers.get("X-WebAI-Fallback"), "true")

    def test_duck_quota_stream_routes_to_next_provider(self):
        request = urllib.request.Request(
            self.gateway.url + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "duck-quota",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hello"}],
                }
            ).encode(),
            headers={"Content-Type": "application/json", **self.auth},
            method="POST",
        )
        response = urllib.request.urlopen(request, timeout=30)
        provider = response.headers.get("X-WebAI-Provider")
        fallback = response.headers.get("X-WebAI-Fallback")
        payloads = []
        for line in response:
            text = line.decode().strip()
            if text.startswith("data: ") and text != "data: [DONE]":
                payloads.append(json.loads(text[6:]))
        response.close()
        content = "".join(
            p["choices"][0]["delta"].get("content", "")
            for p in payloads
            if p.get("choices")
        )
        self.assertEqual(provider, "qwen")
        self.assertEqual(fallback, "true")
        self.assertEqual(content, "Qwen ok")

    def test_fallback_chain_continues_after_second_failure(self):
        upstream = MockUpstream()
        tmp = tempfile.TemporaryDirectory()
        config = make_config(
            upstream.url,
            fallback={
                "enabled": True,
                "order": ["qwen", "deepseek"],
                "max_attempts": 3,
                "models": {"qwen": "qwen-waf"},
            },
        )
        gateway = Gateway(config, tmp.name)
        try:
            status, headers, body = gateway.chat(
                {
                    "model": "duck-quota",
                    "messages": [{"role": "user", "content": "hello"}],
                },
                headers={"Authorization": "Bearer sk-local"},
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(
                json.loads(body)["choices"][0]["message"]["content"], "Hello world!"
            )
            self.assertEqual(headers.get("X-WebAI-Provider"), "deepseek")
            self.assertEqual(headers.get("X-WebAI-Fallback"), "true")
        finally:
            gateway.close()
            upstream.close()
            tmp.cleanup()

    def test_unknown_model_falls_back_to_default_provider(self):
        status, _, body = self.gateway.chat(
            {"model": "made-up-model", "messages": [{"role": "user", "content": "hi"}]},
            headers=self.auth,
        )
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["model"], "made-up-model")
        self.assertEqual(data["choices"][0]["message"]["content"], "Hello world!")

    def test_admin_reload(self):
        config = json.loads(self.gateway.path.read_text())
        config["providers"]["qwen"]["models"]["qwen-extra"] = "qwen-test"
        self.gateway.path.write_text(json.dumps(config), encoding="utf-8")
        status, _, body = self.gateway.request(
            "POST", "/admin/reload", {}, headers=self.auth
        )
        self.assertEqual(status, 200, body)
        status, _, body = self.gateway.request("GET", "/v1/models", headers=self.auth)
        ids = {m["id"] for m in json.loads(body)["data"]}
        self.assertIn("qwen-extra", ids)


class RateLimitIntegrationTests(unittest.TestCase):
    def test_second_request_gets_429_when_queue_disabled(self):
        upstream = MockUpstream()
        tmp = tempfile.TemporaryDirectory()
        gateway = Gateway(
            make_config(
                upstream.url,
                rpm=1,
                queue={"enabled": False, "max_size": 0, "max_wait": 0},
            ),
            tmp.name,
        )
        try:
            payload = {
                "model": "qwen-test",
                "messages": [{"role": "user", "content": "hi"}],
            }
            headers = {"Authorization": "Bearer sk-local"}
            status, _, body = gateway.chat(payload, headers)
            self.assertEqual(status, 200, body)
            status, response_headers, body = gateway.chat(payload, headers)
            self.assertEqual(status, 429, body)
            self.assertIn("Retry-After", response_headers)
        finally:
            gateway.close()
            upstream.close()
            tmp.cleanup()

    def test_second_request_waits_in_queue(self):
        upstream = MockUpstream()
        tmp = tempfile.TemporaryDirectory()
        config = make_config(
            upstream.url,
            rpm=1,
            queue={"enabled": True, "max_size": 10, "max_wait": 5},
        )
        config["rpm_window"] = 1
        gateway = Gateway(config, tmp.name)
        try:
            payload = {
                "model": "qwen-test",
                "messages": [{"role": "user", "content": "hi"}],
            }
            headers = {"Authorization": "Bearer sk-local"}
            started = time.monotonic()
            status, _, body = gateway.chat(payload, headers)
            self.assertEqual(status, 200, body)
            status, response_headers, body = gateway.chat(payload, headers)
            elapsed = time.monotonic() - started
            self.assertEqual(status, 200, body)
            self.assertGreaterEqual(elapsed, 0.35)
            self.assertIn("X-Queue-Wait-Ms", response_headers)
        finally:
            gateway.close()
            upstream.close()
            tmp.cleanup()

    def test_queue_timeout_returns_429(self):
        upstream = MockUpstream()
        tmp = tempfile.TemporaryDirectory()
        config = make_config(
            upstream.url,
            rpm=1,
            queue={"enabled": True, "max_size": 10, "max_wait": 0.3},
        )
        gateway = Gateway(config, tmp.name)
        try:
            payload = {
                "model": "qwen-test",
                "messages": [{"role": "user", "content": "hi"}],
            }
            headers = {"Authorization": "Bearer sk-local"}
            status, _, body = gateway.chat(payload, headers)
            self.assertEqual(status, 200, body)
            status, response_headers, body = gateway.chat(payload, headers)
            self.assertEqual(status, 429, body)
            self.assertIn("Retry-After", response_headers)
        finally:
            gateway.close()
            upstream.close()
            tmp.cleanup()


class AdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = MockUpstream()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.gateway = Gateway(make_config(cls.upstream.url, rpm=5), cls.tmp.name)
        cls.auth = {"Authorization": "Bearer sk-local"}

    @classmethod
    def tearDownClass(cls):
        cls.gateway.close()
        cls.upstream.close()
        cls.tmp.cleanup()

    def test_ui_page(self):
        status, _, body = self.gateway.request("GET", "/ui")
        self.assertEqual(status, 200)
        self.assertIn("webai2api 控制台", body.decode("utf-8"))

    def test_admin_requires_auth(self):
        status, _, body = self.gateway.request("GET", "/admin/config")
        self.assertEqual(status, 401, body)

    def test_admin_config_masks_secrets(self):
        status, _, body = self.gateway.request(
            "GET", "/admin/config", headers=self.auth
        )
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        providers = data["config"]["providers"]
        self.assertTrue(providers["deepseek"]["token_set"])
        self.assertNotIn("token", providers["deepseek"])
        self.assertTrue(providers["duckduckgo"]["vqd_hash_set"])
        self.assertNotIn("vqd_hash", providers["duckduckgo"])
        self.assertNotIn("cookies", providers["duckduckgo"])

    def test_admin_rpm_update(self):
        try:
            status, _, body = self.gateway.request(
                "POST",
                "/admin/rpm",
                {
                    "rpm": 77,
                    "rpm_window": 30,
                    "rpm_per_provider": {"deepseek": 5},
                    "queue": {"enabled": True, "max_size": 10, "max_wait": 2},
                },
                headers=self.auth,
            )
            self.assertEqual(status, 200, body)
            config = json.loads(body)["config"]
            self.assertEqual(config["rpm"], 77)
            self.assertEqual(config["rpm_window"], 30)
            self.assertEqual(config["rpm_per_provider"]["deepseek"], 5)
            self.assertTrue(config["queue"]["enabled"])
            status, _, body = self.gateway.request("GET", "/health")
            rate_limit = json.loads(body)["rate_limit"]
            self.assertEqual(rate_limit["default_rpm"], 77)
            self.assertEqual(rate_limit["window_seconds"], 30)
            self.assertEqual(rate_limit["providers"]["deepseek"]["rpm"], 5)
        finally:
            self.gateway.request(
                "POST",
                "/admin/rpm",
                {
                    "rpm": 5,
                    "rpm_window": 60,
                    "rpm_per_provider": {},
                    "queue": {"enabled": False, "max_size": 0, "max_wait": 0},
                },
                headers=self.auth,
            )

    def test_admin_rpm_invalid(self):
        status, _, body = self.gateway.request(
            "POST", "/admin/rpm", {"rpm": -1}, headers=self.auth
        )
        self.assertEqual(status, 400, body)
        status, _, body = self.gateway.request(
            "POST", "/admin/rpm", {"rpm_window": 0}, headers=self.auth
        )
        self.assertEqual(status, 400, body)

    def test_admin_duck_update(self):
        try:
            status, _, body = self.gateway.request(
                "POST",
                "/admin/duck",
                {"fe_version": "fe-admin", "vqd_hash": "admin-hash"},
                headers=self.auth,
            )
            self.assertEqual(status, 200, body)
            duck = json.loads(body)["config"]["providers"]["duckduckgo"]
            self.assertTrue(duck["vqd_hash_set"])
            self.assertEqual(duck["fe_version"], "fe-admin")
            status, _, body = self.gateway.request(
                "GET", "/admin/config", headers=self.auth
            )
            duck = json.loads(body)["config"]["providers"]["duckduckgo"]
            self.assertEqual(duck["fe_version"], "fe-admin")
            self.assertEqual(duck["vqd_hash_length"], len("admin-hash"))
        finally:
            self.gateway.request(
                "POST",
                "/admin/duck",
                {"fe_version": "test-fe", "vqd_hash": "test-hash"},
                headers=self.auth,
            )

    def test_admin_duck_invalid_signals(self):
        status, _, body = self.gateway.request(
            "POST", "/admin/duck", {"fe_signals": "!!not-base64!!"}, headers=self.auth
        )
        self.assertEqual(status, 400, body)


if __name__ == "__main__":
    unittest.main()
