"""Authentication guardrail tests."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from core_backend import main


def test_auth_fails_closed_when_key_not_configured(monkeypatch):
    monkeypatch.setattr(main, "CONFIG", SimpleNamespace(api_key=None))
    with pytest.raises(HTTPException) as exc:
        main.require_api_key(None)
    assert exc.value.status_code == 503


def test_auth_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(main, "CONFIG", SimpleNamespace(api_key="expected"))
    with pytest.raises(HTTPException) as exc:
        main.require_api_key("wrong")
    assert exc.value.status_code == 401


def test_auth_accepts_correct_key(monkeypatch):
    monkeypatch.setattr(main, "CONFIG", SimpleNamespace(api_key="expected"))
    assert main.require_api_key("expected") is None
