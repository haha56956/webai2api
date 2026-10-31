from __future__ import annotations

from typing import Optional

from .base import ChatRequest, Delta, Provider, ProviderError
from .deepseek import DeepSeekProvider
from .duckduckgo import DuckDuckGoProvider
from .qwen import QwenProvider

PROVIDER_CLASSES = {
    "deepseek": DeepSeekProvider,
    "qwen": QwenProvider,
    "duckduckgo": DuckDuckGoProvider,
}


class ProviderRegistry:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.providers: dict[str, Provider] = {}
        for name, provider_cfg in (cfg.get("providers") or {}).items():
            if not provider_cfg.get("enabled", True):
                continue
            cls = PROVIDER_CLASSES.get(name)
            if cls is None:
                continue
            self.providers[name] = cls(provider_cfg, cfg)

    def get(self, name: str) -> Optional[Provider]:
        return self.providers.get(name)

    def enabled(self) -> list[str]:
        return list(self.providers.keys())

    def resolve(self, model: str) -> tuple[Provider, str, str]:
        requested = (model or "").strip()
        if "/" in requested:
            prefix, rest = requested.split("/", 1)
            provider = self.providers.get(prefix)
            if provider is not None:
                return provider, requested, rest
        for name, provider in self.providers.items():
            if requested in provider.list_models():
                return provider, requested, requested
        default_name = self.cfg.get("default_provider")
        provider = self.providers.get(default_name) or next(iter(self.providers.values()))
        upstream = requested or next(iter(provider.list_models()), "")
        if upstream not in provider.list_models():
            provider_models = provider.list_models()
            upstream = provider_models[0] if provider_models else requested
        return provider, requested, upstream

    def all_models(self) -> list[dict]:
        result = []
        seen = set()
        for name, provider in self.providers.items():
            for model in provider.list_models():
                if model in seen:
                    continue
                seen.add(model)
                result.append(
                    {
                        "id": model,
                        "object": "model",
                        "created": 0,
                        "owned_by": name,
                    }
                )
        return result


__all__ = [
    "ChatRequest",
    "Delta",
    "Provider",
    "ProviderError",
    "ProviderRegistry",
    "DeepSeekProvider",
    "QwenProvider",
    "DuckDuckGoProvider",
]
