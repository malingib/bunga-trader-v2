"""W1: optional dashboard shared-secret header auth.

When DASHBOARD_TOKEN is configured, mutating requests (POST/PUT/DELETE) must
carry a matching X-Dashboard-Token header; GETs stay open. When unset, no auth
is required (the default loopback-only deployment).
"""
import pytest
from starlette.requests import Request
from starlette.datastructures import Headers

from core_backend import main as m
from core_backend import config as cfg_mod
from dataclasses import replace


async def _fake_call_next(request):
    return "PASSED"


def _make_request(method: str, token: str = ""):
    headers = Headers({"x-dashboard-token": token}) if token else Headers()
    scope = {
        "type": "http",
        "method": method,
        "headers": [(k.encode().lower(), v.encode()) for k, v in headers.items()],
        "path": "/x",
    }
    return Request(scope)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_mutating_blocked_without_token(method, monkeypatch):
    monkeypatch.setattr(m, "CONFIG", replace(cfg_mod.CONFIG, dashboard_token="secret"))
    result = await m._dashboard_auth_middleware(_make_request(method), _fake_call_next)
    assert result.status_code == 403


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_mutating_allowed_with_token(method, monkeypatch):
    monkeypatch.setattr(m, "CONFIG", replace(cfg_mod.CONFIG, dashboard_token="secret"))
    result = await m._dashboard_auth_middleware(
        _make_request(method, token="secret"), _fake_call_next
    )
    assert result == "PASSED"


async def test_read_open_with_token(monkeypatch):
    monkeypatch.setattr(m, "CONFIG", replace(cfg_mod.CONFIG, dashboard_token="secret"))
    result = await m._dashboard_auth_middleware(_make_request("GET"), _fake_call_next)
    assert result == "PASSED"


async def test_no_token_means_open(monkeypatch):
    monkeypatch.setattr(m, "CONFIG", replace(cfg_mod.CONFIG, dashboard_token=""))
    for method in ("POST", "GET", "DELETE"):
        result = await m._dashboard_auth_middleware(_make_request(method), _fake_call_next)
        assert result == "PASSED"
