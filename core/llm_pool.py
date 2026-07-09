"""ECONITH :: core.llm_pool

Comma-separated Groq / OpenAI-compatible API keys with automatic failover
when a key hits rate limits or quota errors.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from openai import APIStatusError, OpenAI, RateLimitError

logger = logging.getLogger("econith.llm_pool")

__all__ = [
    "LLMKeyPool",
    "is_llm_quota_error",
    "mask_api_key",
    "parse_llm_api_keys",
]


def _is_real_credential(value: str) -> bool:
    v = (value or "").strip().lower()
    return bool(v) and not v.startswith("your_") and "here" not in v


def parse_llm_api_keys(raw: str) -> list[str]:
    """Split ``LLM_API_KEY`` on commas; drop blanks and placeholder values."""
    keys: list[str] = []
    seen: set[str] = set()
    for tok in (raw or "").split(","):
        key = tok.strip()
        if not key or not _is_real_credential(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def mask_api_key(key: str) -> str:
    k = (key or "").strip()
    if len(k) <= 8:
        return "***"
    return f"{k[:4]}…{k[-4:]}"


def is_llm_quota_error(exc: BaseException) -> bool:
    """True when another API key might succeed (rate limit / quota / overload)."""
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError):
        code = int(getattr(exc, "status_code", 0) or 0)
        if code in (402, 403, 408, 409, 429, 500, 502, 503, 504):
            return True
        body = str(exc).lower()
        if any(
            token in body
            for token in (
                "rate limit",
                "rate_limit",
                "quota",
                "insufficient",
                "capacity",
                "overloaded",
                "too many requests",
            )
        ):
            return True
    text = str(exc).lower()
    return any(
        token in text
        for token in ("rate limit", "rate_limit", "quota", "too many requests")
    )


class LLMKeyPool:
    """Round-robin pool of API keys with short cooldown after quota errors."""

    def __init__(
        self,
        api_keys: list[str],
        *,
        cooldown_s: float = 90.0,
    ) -> None:
        self._keys = list(api_keys)
        self._cooldown_s = cooldown_s
        self._exhausted_until: dict[str, float] = {}
        self._rr_index = 0

    @classmethod
    def from_raw(cls, raw: str, *, cooldown_s: float = 90.0) -> LLMKeyPool:
        return cls(parse_llm_api_keys(raw), cooldown_s=cooldown_s)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._keys)

    def __bool__(self) -> bool:
        return bool(self._keys)

    def mark_exhausted(self, key: str) -> None:
        self._exhausted_until[key] = time.monotonic() + self._cooldown_s
        logger.warning(
            "LLM key %s marked exhausted for %.0fs; trying other keys",
            mask_api_key(key),
            self._cooldown_s,
        )

    def ordered_keys(self) -> list[str]:
        """Prefer keys not in cooldown; round-robin among available keys."""
        if not self._keys:
            return []
        now = time.monotonic()
        active = [k for k in self._keys if self._exhausted_until.get(k, 0.0) <= now]
        if not active:
            active = list(self._keys)
        start = self._rr_index % len(active)
        self._rr_index += 1
        return active[start:] + active[:start]

    def create_chat_completion(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float = 25.0,
        **kwargs: Any,
    ) -> Any:
        """``client.chat.completions.create`` with automatic key failover."""
        last_err: BaseException | None = None
        tried: list[str] = []
        for key in self.ordered_keys():
            tried.append(key)
            try:
                client = OpenAI(api_key=key, base_url=base_url, timeout=timeout)
                return client.chat.completions.create(model=model, **kwargs)
            except Exception as exc:  # noqa: BLE001 — inspect for quota vs fatal
                if is_llm_quota_error(exc):
                    self.mark_exhausted(key)
                    last_err = exc
                    continue
                raise
        tried_masks = ", ".join(mask_api_key(k) for k in tried) or "(none)"
        if last_err is not None:
            raise RuntimeError(
                f"All LLM API keys exhausted ({tried_masks})"
            ) from last_err
        raise RuntimeError("No LLM API keys configured")
