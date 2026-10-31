import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webai2api.auth import AuthManager
from webai2api.providers import ProviderError
from webai2api.providers.deepseek import DeepSeekProvider
from webai2api.config import ConfigError, normalize_config, validate_config
from webai2api.qwen_web import generate_bx_ua, generate_ssxmod
from webai2api.ratelimit import QueuedLimiter, RateLimiter, SlidingWindow
from webai2api.tools import apply_tool_prompt, parse_tool_calls, tool_names
from webai2api.util import (
    extract_text,
    flatten_messages,
    merge_cookie_headers,
    messages_for_duck,
    parse_cookie_header,
)


class AuthTests(unittest.TestCase):
    def test_bearer(self):
        auth = AuthManager(["sk-test"])
        self.assertEqual(auth.authenticate({"Authorization": "Bearer sk-test"}), "sk-test")
        self.assertIsNone(auth.authenticate({"Authorization": "Bearer nope"}))

    def test_api_key_header(self):
        auth = AuthManager(["sk-test"])
        self.assertEqual(auth.authenticate({"X-API-Key": "sk-test"}), "sk-test")
        self.assertIsNone(auth.authenticate({"X-API-Key": "bad"}))

    def test_cookie(self):
        auth = AuthManager(["sk-test"])
        self.assertEqual(
            auth.authenticate({"Cookie": "foo=1; webai2api_key=sk-test"}),
            "sk-test",
        )

    def test_disabled(self):
        auth = AuthManager([])
        self.assertEqual(auth.authenticate({}), "anonymous")


class ProviderTests(unittest.TestCase):
    def test_deepseek_requires_token(self):
        provider = DeepSeekProvider(
            {"enabled": True, "base_url": "https://example.invalid"}, {}
        )
        with self.assertRaises(ProviderError) as ctx:
            provider.chat(
                __import__("webai2api.providers.base", fromlist=["ChatRequest"]).ChatRequest(
                    model="deepseek-chat", messages=[{"role": "user", "content": "hi"}]
                )
            )
        self.assertEqual(ctx.exception.status, 401)

    def test_model_resolution(self):
        from webai2api.providers.base import ChatRequest, Provider

        class Dummy(Provider):
            name = "dummy"

        dummy = Dummy({"models": {"a": "up-a", "b": "up-b"}}, {})
        self.assertEqual(dummy.resolve_model("a"), "up-a")
        self.assertEqual(dummy.resolve_model("dummy/b"), "up-b")
        self.assertEqual(dummy.resolve_model("unknown"), "up-a")


class RateLimitTests(unittest.TestCase):
    def test_window(self):
        window = SlidingWindow(2)
        self.assertTrue(window.acquire(1000.0)[0])
        self.assertTrue(window.acquire(1000.1)[0])
        allowed, retry, _ = window.acquire(1000.2)
        self.assertFalse(allowed)
        self.assertGreater(retry, 0)
        self.assertTrue(window.acquire(1060.1)[0])

    def test_limiter(self):
        limiter = RateLimiter(1)
        self.assertTrue(limiter.acquire("qwen").allowed)
        result = limiter.acquire("qwen")
        self.assertFalse(result.allowed)
        self.assertGreaterEqual(result.retry_after_int, 1)

    def test_disabled(self):
        limiter = RateLimiter(0)
        self.assertTrue(all(limiter.acquire("qwen").allowed for _ in range(100)))

    def test_queue_waits_instead_of_rejecting(self):
        limiter = QueuedLimiter(
            RateLimiter(1, window=0.4), enabled=True, max_size=4, max_wait=2
        )
        self.assertTrue(limiter.acquire("qwen").allowed)
        started = __import__("time").monotonic()
        result = limiter.acquire("qwen")
        elapsed = __import__("time").monotonic() - started
        self.assertTrue(result.allowed)
        self.assertTrue(result.queued)
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertGreater(result.queued_ms, 0)

    def test_queue_full(self):
        import threading
        import time

        limiter = QueuedLimiter(
            RateLimiter(1, window=10), enabled=True, max_size=1, max_wait=2
        )
        self.assertTrue(limiter.acquire("qwen").allowed)
        outcomes = []

        def waiter():
            outcomes.append(limiter.acquire("qwen"))

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.1)
        result = limiter.acquire("qwen")
        self.assertFalse(result.allowed)
        self.assertTrue(result.queue_full)
        thread.join(timeout=5)


class ConfigTests(unittest.TestCase):
    def test_normalize_defaults(self):
        cfg = normalize_config({})
        self.assertEqual(cfg["listen"]["port"], 8787)
        self.assertIn("deepseek", cfg["providers"])
        self.assertIn("qwen", cfg["providers"])
        self.assertIn("duckduckgo", cfg["providers"])
        self.assertTrue(cfg["queue"]["enabled"])
        self.assertEqual(cfg["queue"]["max_size"], 200)
        self.assertTrue(cfg["fallback"]["enabled"])
        self.assertEqual(cfg["fallback"]["max_attempts"], 3)

    def test_queue_and_fallback_aliases(self):
        cfg = normalize_config(
            {
                "rpm_window": 10,
                "queue": {"max_size": 5, "max_wait": 3},
                "fallback": {"order": "duck, qwen", "models": {"duck": "gpt-4o-mini"}},
            }
        )
        self.assertEqual(cfg["rpm_window"], 10)
        self.assertEqual(cfg["queue"]["max_size"], 5)
        self.assertEqual(cfg["fallback"]["order"], ["duckduckgo", "qwen"])
        self.assertEqual(cfg["fallback"]["models"]["duckduckgo"], "gpt-4o-mini")

    def test_env_expand(self):
        with mock.patch.dict(os.environ, {"DS_TOKEN": "abc123"}):
            cfg = normalize_config({"providers": {"deepseek": {"token": "${DS_TOKEN}"}}})
        self.assertEqual(cfg["providers"]["deepseek"]["token"], "abc123")

    def test_alias_and_validation(self):
        cfg = normalize_config({"default_provider": "duck", "api_keys": "a,b"})
        self.assertEqual(cfg["default_provider"], "duckduckgo")
        self.assertEqual(cfg["api_keys"], ["a", "b"])
        warnings = validate_config(cfg)
        self.assertTrue(any("DeepSeek" in w for w in warnings))

    def test_invalid_port(self):
        cfg = normalize_config({"listen": {"port": 70000}})
        with self.assertRaises(ConfigError):
            validate_config(cfg)


class UtilTests(unittest.TestCase):
    def test_cookie_parse_merge(self):
        self.assertEqual(parse_cookie_header("a=1; b=2"), {"a": "1", "b": "2"})
        merged = merge_cookie_headers("a=1; b=2", "b=3; c=4")
        self.assertEqual(parse_cookie_header(merged), {"a": "1", "b": "3", "c": "4"})

    def test_extract_text(self):
        self.assertEqual(extract_text("hi"), "hi")
        self.assertEqual(
            extract_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]),
            "a\nb",
        )
        self.assertEqual(extract_text(None), "")

    def test_flatten_messages(self):
        prompt = flatten_messages(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "again"},
            ]
        )
        self.assertIn("[System]\nsys", prompt)
        self.assertIn("[User]\nhi", prompt)
        self.assertIn("[Assistant]\nhello", prompt)

    def test_messages_for_duck(self):
        messages = messages_for_duck(
            [
                {"role": "system", "content": "be nice"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        )
        self.assertEqual(messages[0]["role"], "user")
        self.assertTrue(messages[0]["content"].startswith("[System]\nbe nice"))
        self.assertEqual(messages[-1], {"role": "assistant", "content": "hello"})


class ToolTests(unittest.TestCase):
    TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "查询天气",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "搜索",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        },
    ]

    def test_tool_names(self):
        self.assertEqual(tool_names(self.TOOLS), ["get_weather", "search"])

    def test_parse_marked_blocks(self):
        text = (
            "好的，我来查一下。\n"
            "[[tool_call]]\n"
            '{"name":"get_weather","arguments":{"city":"Paris"}}\n'
            "[[/tool_call]]\n"
            "[[tool_call]]"
            '{"name":"search","arguments":{"q":"news"}}'
            "[[/tool_call]]"
        )
        calls, remaining = parse_tool_calls(text, self.TOOLS)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["type"], "function")
        self.assertEqual(calls[0]["function"]["name"], "get_weather")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"city": "Paris"})
        self.assertEqual(calls[1]["function"]["name"], "search")
        self.assertEqual(remaining, "好的，我来查一下。")
        self.assertTrue(calls[0]["id"].startswith("call_"))

    def test_parse_plain_json_and_unknown(self):
        calls, remaining = parse_tool_calls(
            json.dumps(
                {
                    "name": "get_weather",
                    "arguments": json.dumps({"city": "Rome"}),
                }
            ),
            self.TOOLS,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"city": "Rome"})
        self.assertEqual(remaining, "")
        calls, remaining = parse_tool_calls(
            '[[tool_call]]{"name":"unknown","arguments":{}}[[/tool_call]]', self.TOOLS
        )
        self.assertEqual(calls, [])
        self.assertIn("unknown", remaining)

    def test_parse_arguments_not_json(self):
        calls, _ = parse_tool_calls(
            '[[tool_call]]{"name":"search","arguments":"plain query"}[[/tool_call]]',
            self.TOOLS,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            json.loads(calls[0]["function"]["arguments"]), {"input": "plain query"}
        )

    def test_apply_tool_prompt(self):
        messages = [{"role": "user", "content": "hi"}]
        prompted = apply_tool_prompt(messages, self.TOOLS, "required")
        self.assertEqual(len(prompted), 2)
        self.assertEqual(prompted[-1]["role"], "system")
        self.assertIn("get_weather", prompted[-1]["content"])
        self.assertIn("必须至少调用一个工具", prompted[-1]["content"])
        self.assertIs(apply_tool_prompt(messages, self.TOOLS, "none"), messages)

    def test_tool_history_rendering(self):
        messages = [
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"Paris"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "get_weather",
                "content": "sunny",
            },
            {"role": "user", "content": "continue"},
        ]
        duck = messages_for_duck(messages, parts=True)
        self.assertEqual(duck[0]["role"], "user")
        self.assertIn("[System]", duck[0]["content"][0]["text"])
        self.assertEqual(duck[1]["role"], "assistant")
        self.assertIn("[Tool calls]", duck[1]["content"][0]["text"])
        self.assertIn("get_weather", duck[1]["content"][0]["text"])
        self.assertEqual(duck[2]["role"], "user")
        self.assertIn("[Tool result for get_weather", duck[2]["content"][0]["text"])
        flattened = flatten_messages(messages)
        self.assertIn("[Tool calls]", flattened)
        self.assertIn("[Tool result for get_weather", flattened)


class QwenFingerprintTests(unittest.TestCase):
    def test_ssxmod(self):
        result = generate_ssxmod("device^websdk^1^91^1|15^zh-CN^-480^1|2^3^5^MacIntel^10^r^30|30^0^28^5|1^2^3^1^0^1^0^P^0^0^0^416^Google Inc.^8^-1|0^9^11^123^10^0^50")
        self.assertTrue(result["ssxmod_itna"].startswith("1-"))
        self.assertTrue(result["ssxmod_itna2"].startswith("1-"))
        self.assertNotEqual(result["ssxmod_itna"], result["ssxmod_itna2"])

    def test_bxua(self):
        value = generate_bx_ua()
        self.assertTrue(value.startswith("231!"))
        self.assertGreater(len(value), 60)


if __name__ == "__main__":
    unittest.main()
