from __future__ import annotations

import copy
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Optional

from .util import env_expand

QWEN_MODELS = [
    "qwen3.8-max",
    "qwen3.7-plus",
    "qwen3.5-plus",
    "qwen3.5-flash",
    "qwen3-coder-plus",
]

DUCK_MODELS = {
    "gpt-5.4-mini": "gpt-5.4-mini",
    "gpt-6-luna": "gpt-6-luna",
    "claude-haiku-4-5": "claude-haiku-4-5",
    "mistral-small-2603": "mistral-small-2603",
    "tinfoil/gpt-oss-120b": "tinfoil/gpt-oss-120b",
    "tinfoil/gemma4-31b": "tinfoil/gemma4-31b",
    "gpt-5.6-terra": "gpt-5.6-terra",
    "gpt-5.6-sol": "gpt-5.6-sol",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-opus-4-8": "claude-opus-4-8",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "listen": {"host": "127.0.0.1", "port": 8787},
    "api_keys": [],
    "rpm": 30,
    "rpm_window": 60,
    "rpm_per_provider": {},
    "queue": {"enabled": True, "max_size": 200, "max_wait": 120},
    "fallback": {"enabled": True, "order": [], "max_attempts": 3, "models": {}},
    "timeout": 180,
    "proxy": "",
    "log_level": "INFO",
    "default_provider": "qwen",
    "cors_origins": ["*"],
    "max_body_bytes": 16 * 1024 * 1024,
    "providers": {
        "deepseek": {
            "enabled": True,
            "base_url": "https://chat.deepseek.com",
            "token": "",
            "cookies": "",
            "headers": {},
            "models": {
                "deepseek-chat": {"model_type": "default", "thinking": False, "search": False},
                "deepseek-reasoner": {"model_type": "expert", "thinking": True, "search": False},
            },
            "thinking": False,
            "search": False,
            "pow_backend": "auto",
            "timeout": 180,
            "proxy": "",
        },
        "qwen": {
            "enabled": True,
            "base_url": "https://chat.qwen.ai",
            "token": "",
            "cookies": "",
            "headers": {},
            "models": {m: m for m in QWEN_MODELS},
            "chat_mode": "",
            "client_version": "0.2.84",
            "user_agent": "",
            "auto_cookies": True,
            "auto_bxua": True,
            "bx_umidtoken": "",
            "auto_delete_chat": False,
            "timeout": 300,
            "proxy": "",
        },
        "duckduckgo": {
            "enabled": True,
            "base_url": "https://duck.ai",
            "cookies": "",
            "headers": {},
            "models": dict(DUCK_MODELS),
            "vqd_hash": "",
            "fe_version": "",
            "fe_signals": "",
            "user_agent": "",
            "timeout": 120,
            "proxy": "",
        },
    },
}

_PROVIDER_ALIASES = {
    "deepseek.com": "deepseek",
    "deepseek": "deepseek",
    "qwen.ai": "qwen",
    "qwen": "qwen",
    "duck.ai": "duckduckgo",
    "duckduckgo": "duckduckgo",
    "duck": "duckduckgo",
}


class ConfigError(RuntimeError):
    pass


def provider_alias(name: str) -> str:
    return _PROVIDER_ALIASES.get(str(name or "").strip().lower(), str(name or "").strip().lower())


def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def normalize_config(raw: dict) -> dict:
    cfg = deep_merge(DEFAULT_CONFIG, raw or {})
    if isinstance(cfg.get("api_keys"), str):
        cfg["api_keys"] = [k.strip() for k in cfg["api_keys"].split(",") if k.strip()]
    cfg["api_keys"] = [str(k) for k in (cfg.get("api_keys") or []) if str(k)]
    cfg["rpm"] = max(0, int(cfg.get("rpm") or 0))
    cfg["rpm_window"] = max(0.1, float(cfg.get("rpm_window") or 60))
    cfg["timeout"] = float(cfg.get("timeout") or 180)
    queue = cfg.setdefault("queue", {})
    if not isinstance(queue, dict):
        queue = cfg["queue"] = {}
    queue["enabled"] = bool(queue.get("enabled", True))
    queue["max_size"] = max(0, int(queue.get("max_size", 200) or 0))
    queue["max_wait"] = max(0.0, float(queue.get("max_wait", 120) or 0))
    fallback = cfg.setdefault("fallback", {})
    if not isinstance(fallback, dict):
        fallback = cfg["fallback"] = {}
    fallback["enabled"] = bool(fallback.get("enabled", True))
    fallback["max_attempts"] = max(1, int(fallback.get("max_attempts", 3) or 3))
    order = fallback.get("order") or []
    if isinstance(order, str):
        order = [x.strip() for x in order.split(",") if x.strip()]
    fallback["order"] = [provider_alias(x) for x in order if str(x).strip()]
    fb_models = fallback.get("models") or {}
    fallback["models"] = {
        provider_alias(k): str(v) for k, v in fb_models.items() if v
    } if isinstance(fb_models, dict) else {}
    listen = cfg.setdefault("listen", {})
    listen["host"] = str(listen.get("host") or "127.0.0.1")
    listen["port"] = int(listen.get("port") or 8787)
    providers = cfg.setdefault("providers", {})
    default_provider = provider_alias(cfg.get("default_provider") or "qwen")
    cfg["default_provider"] = default_provider
    for name, pcfg in list(providers.items()):
        canonical = provider_alias(name)
        if canonical != name:
            providers[canonical] = pcfg
            del providers[name]
            name = canonical
        if not isinstance(pcfg, dict):
            providers[name] = {}
            pcfg = providers[name]
        pcfg["enabled"] = bool(pcfg.get("enabled", True))
        pcfg["base_url"] = str(pcfg.get("base_url") or "").rstrip("/")
        pcfg["cookies"] = str(pcfg.get("cookies") or "")
        pcfg["token"] = str(pcfg.get("token") or "")
        pcfg["headers"] = dict(pcfg.get("headers") or {})
        pcfg["models"] = dict(pcfg.get("models") or {})
        pcfg["timeout"] = float(pcfg.get("timeout") or cfg["timeout"])
        pcfg["proxy"] = str(pcfg.get("proxy") or "")
        if canonical == "duckduckgo":
            pcfg["vqd_hash"] = str(pcfg.get("vqd_hash") or "")
            pcfg["fe_version"] = str(pcfg.get("fe_version") or "")
            pcfg["fe_signals"] = str(pcfg.get("fe_signals") or "")
            pcfg["user_agent"] = str(pcfg.get("user_agent") or "")
    rpm_map = cfg.get("rpm_per_provider") or {}
    cfg["rpm_per_provider"] = {
        provider_alias(k): int(v) for k, v in rpm_map.items() if v is not None
    }
    return env_expand(cfg)


def validate_config(cfg: dict) -> list[str]:
    warnings: list[str] = []
    host = cfg["listen"]["host"]
    port = cfg["listen"]["port"]
    if not (0 < port < 65536):
        raise ConfigError("listen.port 必须在 1-65535 之间")
    enabled = [n for n, p in cfg["providers"].items() if p.get("enabled")]
    if not enabled:
        raise ConfigError("至少需要启用一个 provider")
    if cfg["default_provider"] not in enabled:
        cfg["default_provider"] = enabled[0]
    if not cfg["api_keys"]:
        warnings.append("未设置 api_keys，API 将允许匿名访问；请勿绑定到公网地址")
    if host not in ("127.0.0.1", "localhost", "::1") and not cfg["api_keys"]:
        warnings.append("服务绑定在非本机地址且没有 API Key，存在严重安全风险")
    for name in enabled:
        pcfg = cfg["providers"][name]
        if not pcfg.get("base_url"):
            raise ConfigError(f"providers.{name}.base_url 不能为空")
    if cfg["providers"].get("deepseek", {}).get("enabled"):
        ds = cfg["providers"]["deepseek"]
        if not ds.get("token") and "usertoken" not in ds.get("cookies", "").lower():
            warnings.append("DeepSeek 未配置 token；通常需要从浏览器 Local Storage 复制 userToken")
    if cfg["providers"].get("qwen", {}).get("enabled"):
        qw = cfg["providers"]["qwen"]
        if not qw.get("token") and not qw.get("cookies"):
            warnings.append("Qwen 未配置 token/cookies，将尝试访客模式；流式接口可能被 WAF 拦截")
    return warnings


def load_config(path: str | Path) -> dict:
    path = Path(path).expanduser()
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("配置文件根节点必须是 JSON 对象")
    cfg = normalize_config(raw)
    validate_config(cfg)
    return cfg


def _template(api_key: str) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["api_keys"] = [api_key]
    return cfg


def init_config(path: str | Path, force: bool = False) -> Path:
    path = Path(path).expanduser()
    if path.exists() and not force:
        raise ConfigError(f"配置文件已存在: {path}（使用 --force 覆盖）")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = "sk-webai2api-" + secrets.token_urlsafe(24)
    path.write_text(
        json.dumps(_template(key), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def default_config_path() -> Path:
    env = os.environ.get("WEBAI2API_CONFIG")
    if env:
        return Path(env).expanduser()
    local = Path.cwd() / "config.json"
    if local.exists():
        return local
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return xdg / "webai2api" / "config.json"


class ConfigManager:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()
        self._config: Optional[dict] = None
        self._mtime_ns: Optional[int] = None
        self._version = 0

    def reload(self, force: bool = False) -> dict:
        with self._lock:
            try:
                stat = self.path.stat()
            except FileNotFoundError as exc:
                raise ConfigError(f"配置文件不存在: {self.path}") from exc
            if not force and self._config is not None and stat.st_mtime_ns == self._mtime_ns:
                return self._config
            cfg = load_config(self.path)
            self._config = cfg
            self._mtime_ns = stat.st_mtime_ns
            self._version += 1
            return cfg

    def get(self) -> tuple[dict, int]:
        cfg = self.reload()
        return cfg, self._version

    def update(self, mutator) -> dict:
        """Atomically mutate config.json and return the normalized config.

        ``mutator`` receives the raw JSON object (not the normalized config)
        and may mutate it in place or return a replacement object.
        """
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ConfigError(f"配置文件不存在: {self.path}") from exc
            except json.JSONDecodeError as exc:
                raise ConfigError(f"配置文件不是合法 JSON: {exc}") from exc
            if not isinstance(raw, dict):
                raise ConfigError("配置文件根节点必须是 JSON 对象")
            mutated = mutator(raw)
            if mutated is not None:
                raw = mutated
            if not isinstance(raw, dict):
                raise ConfigError("更新后的配置根节点必须是 JSON 对象")
            cfg = normalize_config(raw)
            validate_config(cfg)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
            self._config = cfg
            self._mtime_ns = self.path.stat().st_mtime_ns
            self._version += 1
            return cfg
