"""Optional API key authentication for sensitive endpoints."""
from typing import Optional

from fastapi import Header, HTTPException

from .config import CONFIG


def require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> None:
    """Reject requests when API_KEY is configured but header is missing or wrong."""
    if not CONFIG.api_key:
        return
    if not x_api_key or x_api_key != CONFIG.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
