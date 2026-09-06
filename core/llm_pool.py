"""ECONITH :: core.llm_pool

Comma-separated Groq / OpenAI-compatible API keys with automatic failover
when a key hits rate limits or quota errors.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from openai import APIStatusError, OpenAI, RateLimitError

logger = logging.getLogger("econith.llm_pool")

__all__ = [
    "LLMKeyPool",
    "RoutedLLMPool",
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


class RoutedLLMPool:
    """Local Ollama first, remote key pool as resilience fallback.

    The public method intentionally matches :class:`LLMKeyPool`, so existing
    governors, journalist and dialogue code do not need provider-specific
    branches. Local inference is serialized: this workstation has one CPU
    runner and launching concurrent 8B generations only increases latency/RAM.
    """

    def __init__(
        self,
        remote: LLMKeyPool | None = None,
        *,
        local_base_url: str = "http://localhost:11434/v1",
        local_model: str = "llama3:8b",
        local_enabled: bool = True,
        local_first: bool = True,
        local_timeout_s: float = 240.0,
        local_max_tokens: int = 384,
        local_queue_timeout_s: float = 3.0,
        local_context_tokens: int = 2048,
    ) -> None:
        self._remote = remote
        self._local_base_url = local_base_url.rstrip("/")
        self._local_model = local_model
        self._local_enabled = bool(local_enabled and local_base_url and local_model)
        self._local_first = bool(local_first)
        self._local_timeout_s = max(30.0, float(local_timeout_s))
        self._local_max_tokens = max(64, int(local_max_tokens))
        self._local_queue_timeout_s = max(0.0, float(local_queue_timeout_s))
        self._local_context_tokens = max(512, int(local_context_tokens))
        self._local_lock = threading.BoundedSemaphore(1)
        self._last_provider = "none"

    @property
    def keys(self) -> tuple[str, ...]:
        """Remote credentials only; retained for existing telemetry."""
        return self._remote.keys if self._remote is not None else ()

    @property
    def last_provider(self) -> str:
        return self._last_provider

    @property
    def local_model(self) -> str:
        return self._local_model

    def __bool__(self) -> bool:
        return self._local_enabled or bool(self._remote)

    def _call_local(
        self,
        *,
        timeout: float,
        kwargs: dict[str, Any],
    ) -> Any:
        local_kwargs = dict(kwargs)
        requested = int(local_kwargs.get("max_tokens", self._local_max_tokens))
        local_kwargs["max_tokens"] = min(requested, self._local_max_tokens)
        extra_body = dict(local_kwargs.get("extra_body") or {})
        options = dict(extra_body.get("options") or {})
        options.setdefault("num_ctx", self._local_context_tokens)
        extra_body["options"] = options
        local_kwargs["extra_body"] = extra_body
        # Ollama's OpenAI-compatible endpoint accepts ``response_format`` for
        # JSON-mode models; if an older build rejects it, caller falls back.
        acquired = self._local_lock.acquire(timeout=self._local_queue_timeout_s)
        if not acquired:
            raise TimeoutError("local Ollama runner busy")
        try:
            client = OpenAI(
                api_key="ollama",
                base_url=self._local_base_url,
                timeout=max(timeout, self._local_timeout_s),
            )
            result = client.chat.completions.create(
                model=self._local_model,
                **local_kwargs,
            )
        finally:
            self._local_lock.release()
        self._last_provider = "ollama"
        logger.info("LLM completion provider=ollama model=%s", self._local_model)
        return result

    def _call_remote(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float,
        kwargs: dict[str, Any],
    ) -> Any:
        if self._remote is None or not self._remote:
            raise RuntimeError("No remote LLM API keys configured")
        result = self._remote.create_chat_completion(
            base_url=base_url,
            model=model,
            timeout=timeout,
            **kwargs,
        )
        self._last_provider = "remote"
        logger.info("LLM completion provider=remote model=%s", model)
        return result

    def create_chat_completion(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float = 25.0,
        **kwargs: Any,
    ) -> Any:
        routes = ("local", "remote") if self._local_first else ("remote", "local")
        errors: list[str] = []
        for route in routes:
            if route == "local":
                if not self._local_enabled:
                    continue
                try:
                    return self._call_local(timeout=timeout, kwargs=kwargs)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"ollama={type(exc).__name__}: {exc}")
                    logger.warning("local Ollama failed; trying remote LLM: %s", exc)
            else:
                if self._remote is None or not self._remote:
                    continue
                try:
                    return self._call_remote(
                        base_url=base_url,
                        model=model,
                        timeout=timeout,
                        kwargs=kwargs,
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"remote={type(exc).__name__}: {exc}")
                    logger.warning("remote LLM failed; trying local Ollama: %s", exc)
        self._last_provider = "failed"
        detail = "; ".join(errors) or "no routes enabled"
        raise RuntimeError(f"All LLM routes failed ({detail})")
