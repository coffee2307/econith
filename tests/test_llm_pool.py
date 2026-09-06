"""Unit tests for LLM key parsing and RoutedLLMPool truthiness / routing gates."""
from __future__ import annotations

from core.llm_pool import (
    LLMKeyPool,
    RoutedLLMPool,
    mask_api_key,
    parse_llm_api_keys,
)


def test_parse_llm_api_keys_drops_placeholders() -> None:
    raw = "gsk_real_one, your_groq_api_key_here, gsk_real_two, gsk_real_one"
    keys = parse_llm_api_keys(raw)
    assert keys == ["gsk_real_one", "gsk_real_two"]


def test_mask_api_key() -> None:
    assert mask_api_key("abcd") == "***"
    assert "…" in mask_api_key("gsk_abcdefghijklmnop")


def test_routed_pool_bool_ollama_only() -> None:
    pool = RoutedLLMPool(
        remote=None,
        local_enabled=True,
        local_base_url="http://localhost:11434/v1",
        local_model="llama3:8b",
    )
    assert bool(pool) is True
    assert pool.keys == ()


def test_routed_pool_bool_disabled_without_remote() -> None:
    pool = RoutedLLMPool(
        remote=None,
        local_enabled=False,
        local_base_url="",
        local_model="",
    )
    assert bool(pool) is False


def test_routed_pool_bool_remote_only() -> None:
    remote = LLMKeyPool(["gsk_test_key_abcdef"])
    pool = RoutedLLMPool(
        remote=remote,
        local_enabled=False,
        local_base_url="",
        local_model="",
    )
    assert bool(pool) is True
    assert pool.keys == ("gsk_test_key_abcdef",)
