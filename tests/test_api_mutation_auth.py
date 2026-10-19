"""Ensure no new state-changing API endpoint can bypass authentication.

The policy is namespace based, not an ad hoc list of sensitive URLs.
HTTP GET and WebSockets are covered independently by deployment ingress.
"""
from __future__ import annotations

import pytest

from core.api.auth import APIKeyAuthMiddleware


@pytest.mark.parametrize("path", [
    "/api/v1/mode",
    "/api/v1/runtime/pause",
    "/api/v1/runtime/resume",
    "/api/v1/world/reset",
    "/api/v1/world/agent-exchange",
    "/api/v1/quant/simulation/reset",
    "/api/v1/quant/routing/profile",
    "/api/v1/locale",
    "/api/v1/control/world-simulation",
    "/api/v1/social/proxy/submit",
    "/api/v1/new-feature-added-next-week",
])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_all_api_mutations_require_auth(path, method):
    middleware = object.__new__(APIKeyAuthMiddleware)
    middleware._api_prefix = "/api/v1"
    assert middleware._is_protected(method, path)


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/v1/health"),
    ("HEAD", "/api/v1/health"),
    ("OPTIONS", "/api/v1/health"),
    ("POST", "/api/v10/mode"),
    ("POST", "/outside-owned-api/runtime/reset"),
    ("POST", "/api/v1-but-not-namespace/reset"),
])
def test_policy_uses_method_and_exact_prefix_segments(method, path):
    middleware = object.__new__(APIKeyAuthMiddleware)
    middleware._api_prefix = "/api/v1"
    assert not middleware._is_protected(method, path)


def test_shared_router_api_key_validation(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from starlette.requests import Request
    from core.api.auth import require_api_key

    cfg = SimpleNamespace(api_auth_enabled=True, api_keys=frozenset({"expected"}))

    def request(headers):
        return Request({
            "type": "http", "method": "GET", "path": "/api/v1/vendors/status",
            "headers": [(key.lower().encode(), value.encode())
                        for key, value in headers.items()],
            "query_string": b"",
        })

    require_api_key(request({"x-api-key": "expected"}), cfg)
    require_api_key(request({"authorization": "Bearer expected"}), cfg)
    for value in ({}, {"x-api-key": "wrong"}, {"authorization": "Bearer wrong"}):
        with pytest.raises(HTTPException) as error:
            require_api_key(request(value), cfg)
        assert error.value.status_code == 401

    cfg.api_auth_enabled = False
    require_api_key(request({}), cfg)


def test_router_modules_use_one_canonical_credential_function() -> None:
    from api.endpoints import social, vendors
    from core.api.auth import require_api_key

    assert social._require_api_key is require_api_key
    assert vendors._require_api_key is require_api_key
