from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        status: int = 502,
        provider: str = "",
        retry_after: Optional[float] = None,
        fallback: bool = False,
    ):
        super().__init__(message)
        self.status = int(status)
        self.provider = provider
        self.retry_after = retry_after
        self.fallback = bool(fallback)


@dataclass
class ChatRequest:
    model: str
    messages: list[dict]
    route_model: Optional[str] = None
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stop: list[str] = field(default_factory=list)
    reasoning_effort: Optional[str] = None
    tools: list[dict] = field(default_factory=list)
    tool_choice: Any = None
    extra: dict = field(default_factory=dict)


@dataclass
class Delta:
    content: str = ""
    reasoning: str = ""
    finish_reason: Optional[str] = None
    usage: Optional[dict] = None


class Provider:
    name = "base"

    def __init__(self, cfg: dict, global_cfg: dict):
        self.cfg = cfg or {}
        self.global_cfg = global_cfg or {}

    def list_models(self) -> list[str]:
        return list((self.cfg.get("models") or {}).keys())

    def resolve_model(self, requested: str) -> Any:
        models = self.cfg.get("models") or {}
        if requested in models:
            return models[requested]
        if requested and requested.startswith(self.name + "/"):
            short = requested.split("/", 1)[1]
            if short in models:
                return models[short]
        if "default" in models:
            return models["default"]
        if models:
            return next(iter(models.values()))
        return requested

    def stream_chat(self, request: ChatRequest) -> Iterator[Delta]:
        raise NotImplementedError

    def chat(self, request: ChatRequest) -> Delta:
        content: list[str] = []
        reasoning: list[str] = []
        finish_reason: Optional[str] = None
        usage: Optional[dict] = None
        for delta in self.stream_chat(request):
            if delta.content:
                content.append(delta.content)
            if delta.reasoning:
                reasoning.append(delta.reasoning)
            if delta.finish_reason:
                finish_reason = delta.finish_reason
            if delta.usage:
                usage = delta.usage
        return Delta(
            content="".join(content),
            reasoning="".join(reasoning),
            finish_reason=finish_reason or "stop",
            usage=usage,
        )

    def health(self) -> dict:
        return {"enabled": bool(self.cfg.get("enabled", True))}
