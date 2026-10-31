from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from .util import extract_text, random_hex

CALL_OPEN = "[[tool_call]]"
CALL_CLOSE = "[[/tool_call]]"

_BLOCK_RE = re.compile(
    r"\[\[\s*tool_call\s*\]\](.*?)\[\[\s*/\s*tool_call\s*\]\]",
    re.IGNORECASE | re.DOTALL,
)
_FENCE_RE = re.compile(
    r"```(?:tool_call|tool|json)\s*(.*?)```",
    re.IGNORECASE | re.DOTALL,
)


def tool_names(tools: Iterable[dict]) -> list[str]:
    names: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(function.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _choice_instruction(tool_choice: Any, names: list[str]) -> str:
    if not names:
        return ""
    if tool_choice in ("required", "any"):
        return "你必须至少调用一个工具。"
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
        name = str(function.get("name") or tool_choice.get("name") or "").strip()
        if name:
            return f"你必须调用工具 `{name}`。"
    return "需要时调用工具；如果不需要，直接回答即可。"


def build_tool_prompt(tools: Iterable[dict], tool_choice: Any = None) -> str:
    tools = list(tools or [])
    names = tool_names(tools)
    if not names:
        return ""
    schema_lines = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        description = str(function.get("description") or "").strip()
        parameters = function.get("parameters") or {}
        schema_lines.append(
            f"- `{name}`: {description or '(no description)'}\n"
            f"  参数 JSON Schema: {json.dumps(parameters, ensure_ascii=False, separators=(',', ':'))}"
        )
    instruction = _choice_instruction(tool_choice, names)
    return (
        "[Tool use protocol]\n"
        "你可以使用下面这些工具：\n"
        + "\n".join(schema_lines)
        + "\n\n"
        + instruction
        + "\n当你需要调用工具时，必须只输出下面的标记块，可以连续输出多个；"
        "不要在标记块之外输出额外说明：\n"
        f"{CALL_OPEN}\n"
        '{"name":"工具名","arguments":{"参数名":参数值}}\n'
        f"{CALL_CLOSE}\n"
        "arguments 必须是 JSON 对象。不要伪造工具结果；调用后会收到 [Tool result] 消息。"
    )


def apply_tool_prompt(messages: list[dict], tools: Iterable[dict], tool_choice: Any = None) -> list[dict]:
    tools = list(tools or [])
    if not tools or tool_choice == "none":
        return messages
    prompt = build_tool_prompt(tools, tool_choice)
    if not prompt:
        return messages
    result = [dict(msg) for msg in messages if isinstance(msg, dict)]
    result.append({"role": "system", "content": prompt})
    return result


def _normalize_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        text = arguments.strip()
        if not text:
            return "{}"
        try:
            parsed = json.loads(text)
        except Exception:
            return json.dumps({"input": arguments}, ensure_ascii=False)
        if isinstance(parsed, (dict, list)):
            return json.dumps(parsed, ensure_ascii=False)
        return json.dumps({"value": parsed}, ensure_ascii=False)
    if isinstance(arguments, (dict, list)):
        return json.dumps(arguments, ensure_ascii=False)
    if arguments is None:
        return "{}"
    return json.dumps({"value": arguments}, ensure_ascii=False)


def _make_call(name: str, arguments: Any, valid: set[str]) -> Optional[dict]:
    name = str(name or "").strip()
    if not name or (valid and name not in valid):
        return None
    return {
        "id": "call_" + random_hex(24),
        "type": "function",
        "function": {
            "name": name,
            "arguments": _normalize_arguments(arguments),
        },
    }


def _calls_from_object(obj: Any, valid: set[str]) -> list[dict]:
    calls: list[dict] = []
    if isinstance(obj, dict):
        if isinstance(obj.get("tool_calls"), list):
            for item in obj["tool_calls"]:
                if isinstance(item, dict):
                    function = item.get("function") if isinstance(item.get("function"), dict) else item
                    call = _make_call(
                        function.get("name"),
                        function.get("arguments", function.get("args", {})),
                        valid,
                    )
                    if call:
                        calls.append(call)
            return calls
        if obj.get("name") and ("arguments" in obj or "args" in obj):
            call = _make_call(obj.get("name"), obj.get("arguments", obj.get("args")), valid)
            if call:
                calls.append(call)
    elif isinstance(obj, list):
        for item in obj:
            calls.extend(_calls_from_object(item, valid))
    return calls


def parse_tool_calls(text: str, tools: Iterable[dict]) -> tuple[list[dict], str]:
    """Return (openai_tool_calls, remaining_text)."""
    valid = set(tool_names(tools))
    if not text or not valid:
        return [], text
    calls: list[dict] = []
    spans: list[tuple[int, int]] = []
    for match in _BLOCK_RE.finditer(text):
        body = match.group(1).strip()
        parsed = None
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = None
        if parsed is None:
            found = False
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:
                    continue
                calls.extend(_calls_from_object(parsed, valid))
                found = True
            if found:
                spans.append(match.span())
            continue
        calls.extend(_calls_from_object(parsed, valid))
        spans.append(match.span())

    if not calls:
        for match in _FENCE_RE.finditer(text):
            body = match.group(1).strip()
            if not body:
                continue
            try:
                parsed = json.loads(body)
            except Exception:
                continue
            found = _calls_from_object(parsed, valid)
            if found:
                calls.extend(found)
                spans.append(match.span())

    if not calls:
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except Exception:
                parsed = None
            if parsed is not None:
                found = _calls_from_object(parsed, valid)
                if found:
                    return found, ""

    if calls:
        remaining = text
        for start, end in sorted(spans, reverse=True):
            remaining = remaining[:start] + remaining[end:]
        return calls, remaining.strip()
    return [], text


def tool_call_chunk(call: dict, index: int = 0) -> dict:
    return {
        "index": index,
        "id": call["id"],
        "type": "function",
        "function": {
            "name": call["function"]["name"],
            "arguments": call["function"]["arguments"],
        },
    }


def message_content_text(message: dict) -> str:
    return extract_text(message.get("content"))
