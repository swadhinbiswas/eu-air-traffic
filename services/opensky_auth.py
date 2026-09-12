"""OpenSky authentication.

Accounts now issue a client id/secret pair for the OAuth2 client-credentials
flow; the legacy username/password basic auth is still accepted. Without either,
requests are anonymous and burn the small daily credit budget almost instantly,
which is what silently emptied the flight history.

Tokens are cached in-process until shortly before they expire (OpenSky issues
them for ~30 minutes).
"""

from __future__ import annotations

import time
from typing import Any

import requests

from config.logging import logger
from config.settings import Settings, settings

TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
)

# client_id → (expires_at, token)
_cache: dict[str, tuple[float, str]] = {}


def bearer_token(app_settings: Settings | None = None) -> str | None:
    """Client-credentials token, or None when no OAuth2 pair is configured."""
    cfg = app_settings or settings
    client_id = cfg.opensky_client_id
    client_secret = cfg.opensky_client_secret
    if not (client_id and client_secret):
        return None
    now = time.time()
    cached = _cache.get(client_id)
    if cached and cached[0] > now:
        return cached[1]
    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        token = str(payload["access_token"])
        expires_in = float(payload.get("expires_in", 1800))
        _cache[client_id] = (now + max(60.0, expires_in - 60.0), token)
        return token
    except Exception as exc:  # noqa: BLE001 - fall back to other credentials
        logger.warning("[opensky] token request failed: %s", exc)
        return None


def auth_for(app_settings: Settings | None = None) -> tuple[dict[str, str], tuple[str, str] | None]:
    """Return ``(extra_headers, basic_auth)`` for an OpenSky request.

    OAuth2 wins when configured; otherwise the legacy username/password pair.
    Neither set means anonymous access.
    """
    cfg = app_settings or settings
    token = bearer_token(cfg)
    if token:
        return {"Authorization": f"Bearer {token}"}, None
    if cfg.opensky_username and cfg.opensky_password:
        return {}, (cfg.opensky_username, cfg.opensky_password)
    return {}, None
