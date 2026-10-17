"""Source-only architecture inventory does not execute modules or mutate files."""
from __future__ import annotations

import pytest

from ci.architecture_inventory import scan_python_source


def test_scan_imports_classes_order_topics_and_exchange_calls() -> None:
    source = """
import core.mode
from bridges.quant_bridge import QuantExecutionBridge
class ResearchOnly:
    async def sample(self, broker):
        x = "order.intent"
        await broker.create_order("BTC", "limit", "buy", 1)
        return "world.macro"
"""
    r = scan_python_source(source, path="synthetic.py")
    assert "core.mode" in r["imports"]
    assert "bridges.quant_bridge" in r["imports"]
    assert r["classes"] == ["ResearchOnly"]
    assert [v["literal"] for v in r["event_literals"]] == [
        "order.intent", "world.macro"
    ]
    assert r["potential_order_api_calls"][0]["name"] == "create_order"


def test_inert_comment_does_not_generate_false_positive() -> None:
    text = "# order.intent and broker.create_order must not execute\nANSWER = 42"
    r = scan_python_source(text, path="comment.py")
    assert r["event_literals"] == []
    assert r["potential_order_api_calls"] == []


def test_source_syntax_error_reported_at_boundary() -> None:
    with pytest.raises(SyntaxError):
        scan_python_source("def broken(:", path="broken.py")
