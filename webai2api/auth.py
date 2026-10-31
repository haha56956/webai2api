from __future__ import annotations

import hmac
from typing import Optional

from .util import mask_secret, parse_cookie_header

COOKIE_NAMES = ("webai2api_key", "webai2api-token", "api_key")


class AuthManager:
    def __init__(self, api_keys: list[str]):
        self.api_keys = [k for k in api_keys if k]
        self._hashes = {k: hmac.digest(b"webai2api", k.encode(), "sha256") for k in self.api_keys}

    @property
    def enabled(self) -> bool:
        return bool(self.api_keys)

    def _match(self, candidate: str) -> Optional[str]:
        if not candidate:
            return None
        digest = hmac.digest(b"webai2api", candidate.encode(), "sha256")
        for key, expected in self._hashes.items():
            if hmac.compare_digest(digest, expected):
                return key
        return None

    def authenticate(self, headers: dict[str, str]) -> Optional[str]:
        if not self.enabled:
            return "anonymous"
        authorization = headers.get("Authorization") or headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            matched = self._match(authorization[7:].strip())
            if matched:
                return matched
        for name in ("X-API-Key", "x-api-key", "Api-Key", "api-key"):
            value = headers.get(name)
            if value:
                matched = self._match(value.strip())
                if matched:
                    return matched
        cookie_header = headers.get("Cookie") or headers.get("cookie") or ""
        cookies = parse_cookie_header(cookie_header)
        for name in COOKIE_NAMES:
            value = cookies.get(name)
            if value:
                matched = self._match(value.strip())
                if matched:
                    return matched
        return None

    def describe(self, key: str) -> str:
        if key == "anonymous":
            return "anonymous"
        return mask_secret(key)
