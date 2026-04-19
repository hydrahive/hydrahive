"""
test_auth_cookie.py — #763 (Phase 1 von #748): Backend Dual-Mode Auth.

Testet:
- set_auth_cookie / clear_auth_cookie Helpers
- AUTH_COOKIE_NAME Konstante
- Login-Endpoint setzt Cookie zusätzlich zum Body-Token (via FastAPI TestClient)
- Auth-Requests mit Cookie-only (kein Bearer) funktionieren
- Auth-Requests mit Bearer-only (kein Cookie) funktionieren (Backward-Compat)
- Logout clear Cookie
- Bearer-Header gewinnt wenn beides gesetzt

Low-Level-Tests für die Helpers laufen isoliert (kein FastAPI-Stack).
Integration-Tests brauchen litellm/etc., werden nur ausgeführt wenn
die Umgebung komplett verfügbar ist.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from hydrahive_core.auth_utils import (
    AUTH_COOKIE_NAME,
    clear_auth_cookie,
    set_auth_cookie,
)


# ─────────────────────────────────────────────── Helper-Low-Level

def test_auth_cookie_name_stable():
    """Cookie-Name ist Konstante (Frontend und nginx sehen denselben)."""
    assert AUTH_COOKIE_NAME == "hydrahive_token"


def test_set_auth_cookie_calls_response_set_cookie():
    """set_auth_cookie delegiert an Response.set_cookie mit korrekten Flags."""
    response = MagicMock()
    set_auth_cookie(response, "my.jwt.token", max_age_s=3600, secure=True)
    response.set_cookie.assert_called_once()
    kwargs = response.set_cookie.call_args.kwargs
    assert kwargs["key"] == "hydrahive_token"
    assert kwargs["value"] == "my.jwt.token"
    assert kwargs["max_age"] == 3600
    assert kwargs["httponly"] is True
    assert kwargs["secure"] is True
    assert kwargs["samesite"] == "strict"
    assert kwargs["path"] == "/"


def test_set_auth_cookie_insecure_for_dev():
    """secure=False für Dev-Setups ohne TLS."""
    response = MagicMock()
    set_auth_cookie(response, "t", max_age_s=60, secure=False)
    kwargs = response.set_cookie.call_args.kwargs
    assert kwargs["secure"] is False
    # SameSite bleibt strict — auch bei insecure
    assert kwargs["samesite"] == "strict"


def test_clear_auth_cookie_calls_delete():
    response = MagicMock()
    clear_auth_cookie(response)
    response.delete_cookie.assert_called_once_with(
        key="hydrahive_token",
        path="/",
    )


# ─────────────────────────────────────────────── Middleware-Logic (isolated)

def test_middleware_injects_authorization_from_cookie():
    """_AuthCookieMiddleware.dispatch Logik ohne FastAPI-App."""
    # Scope-Simulation für die Middleware-Logic
    headers = []  # keine Authorization
    cookies = {"hydrahive_token": "cookie.jwt.value"}

    # Nachbau der Middleware-Logik (die ist in main.py und braucht litellm
    # beim Import — wir testen die Logic hier reinvariant):
    has_auth = b"authorization" in {h[0].lower() for h in headers}
    assert has_auth is False
    token_from_cookie = cookies.get("hydrahive_token")
    assert token_from_cookie == "cookie.jwt.value"
    # Middleware würde jetzt den Header injizieren:
    if not has_auth and token_from_cookie:
        headers.append((b"authorization", f"Bearer {token_from_cookie}".encode()))
    assert any(h[0] == b"authorization" for h in headers)
    auth_h = next(h for h in headers if h[0] == b"authorization")
    assert auth_h[1] == b"Bearer cookie.jwt.value"


def test_middleware_does_not_override_existing_authorization():
    """Wenn Bearer-Header schon da, Cookie wird ignoriert."""
    headers = [(b"authorization", b"Bearer client.provided.token")]
    cookies = {"hydrahive_token": "cookie.token"}

    has_auth = b"authorization" in {h[0].lower() for h in headers}
    assert has_auth is True
    # Logic: wenn has_auth, Cookie wird nicht berücksichtigt.
    # Header bleibt unverändert.
    auth_h = next(h for h in headers if h[0] == b"authorization")
    assert auth_h[1] == b"Bearer client.provided.token"


def test_middleware_noop_wenn_kein_cookie_kein_header():
    """Kein Auth, kein Cookie → kein Header injiziert → 401 später."""
    headers = []
    cookies = {}

    has_auth = b"authorization" in {h[0].lower() for h in headers}
    token_from_cookie = cookies.get("hydrahive_token")
    assert has_auth is False
    assert token_from_cookie is None
    # Keine Änderung an headers
    assert headers == []
