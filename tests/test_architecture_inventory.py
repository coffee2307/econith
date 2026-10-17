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


def test_resolve_imports_from_packages_and_relative_modules() -> None:
    from ci.architecture_inventory import module_name_from_path, resolve_import

    assert module_name_from_path("econith/quant/paper_v1.py") == "econith.quant.paper_v1"
    assert module_name_from_path("core/ingestion/__init__.py") == "core.ingestion"
    assert resolve_import(".data", "econith/quant/strategies.py") == "econith.quant.data"
    assert resolve_import("..world", "econith/quant/strategies.py") == "econith.world"
    assert resolve_import(".context", "core/ingestion/__init__.py") == "core.ingestion.context"
    assert resolve_import("core.event_bus", "main.py") == "core.event_bus"


def test_direct_domain_edges_and_isolated_imports_are_review_only() -> None:
    from ci.architecture_inventory import dependency_map, logical_domain

    assert logical_domain("ai/simulator_engine/world_kernel.py") == "world"
    assert logical_domain("econith/quant/paper_v1.py") == "quant"
    assert logical_domain("infrastructure/websocket/streamer.py") == "data"
    records = [
        {"path": "core/svc.py",
         "imports": ["econith.quant.alpha", "econith_quant.bridge.ai_bridge", ".mode"]},
        {"path": "core/mode.py", "imports": []},
        {"path": "econith/quant/alpha.py", "imports": []},
    ]
    graph = dependency_map(records)
    assert any(
        edge["source"] == "core/svc.py"
        and edge["destination"] == "econith/quant/alpha.py"
        and edge["destination_domain"] == "quant"
        for edge in graph["cross_domain_edges"]
    )
    assert graph["inbound_direct_imports"]["core/mode.py"] == ["core/svc.py"]
    assert len(graph["isolated_subtree_imports"]) == 1
    assert "DOES NOT" in graph["coverage_caveat"]
