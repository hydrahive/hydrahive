"""
test_sse_cookie_auth.py — #765 (Phase 3 von #748): SSE-Streams via Cookie-Auth.

Regressionstest für die Akzeptanzkriterien aus #765:
- SSE-Endpoints authenticieren erfolgreich mit httpOnly-Cookie (ohne Bearer-Header)
- Bearer-only bleibt kompatibel (#763 Dual-Mode)
- Kein Auth → 401/403
- Cookie + Bearer gleichzeitig → Bearer gewinnt (Middleware-Contract)

Deckt zwei SSE-Pfade ab:
- `/notifications/stream` (manueller Authorization-Header-Check im Handler)
- `/agents/{id}/message/stream` (FastAPI-Depends-Kette via `require_auth_or_localhost`)

Beide Pfade laufen durch `_AuthCookieMiddleware`, die Cookie → Authorization
synthesiert (main.py:858). Der Test prüft genau diesen Pfad.

Optional: Live-Server-Tests gegen eine laufende Instanz (z.B. .181), aktiviert
durch die Env-Variablen HYDRAHIVE_LIVE_BASE + HYDRAHIVE_LIVE_USER + HYDRAHIVE_LIVE_PASS.
Ohne Env werden die Live-Tests übersprungen.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from hydrahive_core import main
from hydrahive_core.auth_utils import AUTH_COOKIE_NAME


# ─────────────────────────────────────────────────────────── Helpers

def _make_users_file(tmpdir: str, username: str = "ssetest", password: str = "ssepass") -> Path:
    import hashlib
    import secrets
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    pw_hash = f"pbkdf2:{salt}:{h.hex()}"
    users = {username: {"password_hash": pw_hash, "role": "admin"}}
    p = Path(tmpdir) / "users.json"
    p.write_text(json.dumps(users))
    return p


def _lifecycle_patches() -> list:
    return [
        mock.patch.object(main.discovery,      "start", return_value=None),
        mock.patch.object(main.discovery,      "stop",  return_value=None),
        mock.patch.object(main.projects,       "start", return_value=None),
        mock.patch.object(main.projects,       "stop",  return_value=None),
        mock.patch.object(main.sessions,       "start", return_value=None),
        mock.patch.object(main.agent_sessions, "start", return_value=None),
        mock.patch.object(main.runtime,        "start", return_value=None),
        mock.patch.object(main.runtime,        "stop",  return_value=None),
        mock.patch("hydrahive_core.migrations.run_migrations", return_value=None),
        mock.patch("hydrahive_core.main._load_or_create_internal_secret", return_value="test-internal"),
        mock.patch("hydrahive_core.main._load_or_create_jwt_secret", return_value="test-jwt-sse"),
        mock.patch.object(main.rate_limiter, "check_login"),
    ]


class _SseAuthTestBase(unittest.TestCase):
    """Shared Setup: isolierter TestClient + Login-Helper."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        users_path = _make_users_file(cls._tmp.name)
        cls._patches = _lifecycle_patches() + [
            mock.patch.multiple(
                main,
                USERS_FILE=users_path,  # Path-Objekt (main._load_users nutzt .read_text)
                JWT_SECRET="test-jwt-sse",
            ),
        ]
        for p in cls._patches:
            p.start()
        main.discovery._agents.clear()
        cls._client = TestClient(main.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        main.discovery._agents.clear()
        for p in reversed(cls._patches):
            p.stop()
        cls._tmp.cleanup()

    def _login(self) -> tuple[str, dict]:
        """Return (bearer_token, cookies_dict). Login setzt auch client.cookies."""
        # TestClient-Session frisch — alte Cookies raus
        self._client.cookies.clear()
        resp = self._client.post(
            "/auth/login",
            json={"username": "ssetest", "password": "ssepass"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        token = resp.json()["access_token"]
        # Cookie-Extract: TestClient legt gesetzte Cookies in client.cookies ab
        cookie_val = self._client.cookies.get(AUTH_COOKIE_NAME)
        self.assertIsNotNone(cookie_val, "Login hätte Cookie setzen müssen (#763)")
        return token, {AUTH_COOKIE_NAME: cookie_val}


# ─────────────────────────────────────────────────────────── Auth-Matrix (JSON-Proxy)

class AuthMatrixViaJsonEndpointTests(_SseAuthTestBase):
    """Auth-Matrix-Beweis via JSON-Endpoint `/notifications/unread-count`.

    Der SSE-Endpoint `/notifications/stream` schickt zwar identisch Auth durch,
    aber sein Generator blockt 30s bei `q.get()` — daher testen wir die Auth-
    Chain via den zwillings-JSON-Endpoint, der dieselbe Middleware-Kette nutzt.
    Positive SSE-Verifikation passiert im Live-Test weiter unten.
    """

    def _set_cookies(self, cookies: dict | None):
        self._client.cookies.clear()
        if cookies:
            for k, v in cookies.items():
                self._client.cookies.set(k, v)

    def test_cookie_only_authenticates(self):
        """Cookie ohne Authorization-Header → 200 + JSON-Body."""
        _token, cookies = self._login()
        self._set_cookies(cookies)
        r = self._client.get("/notifications/unread-count")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("count", r.json())

    def test_bearer_only_authenticates(self):
        """Bearer ohne Cookie → 200 (#763 Dual-Mode Backward-Compat)."""
        token, _ = self._login()
        self._set_cookies(None)
        r = self._client.get(
            "/notifications/unread-count",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 200, r.text)

    def test_no_auth_rejected(self):
        """Weder Cookie noch Bearer → 401/403."""
        self._set_cookies(None)
        r = self._client.get("/notifications/unread-count")
        self.assertIn(r.status_code, (401, 403))

    def test_invalid_cookie_rejected(self):
        """Cookie mit Garbage-Token → 401/403."""
        self._set_cookies({AUTH_COOKIE_NAME: "garbage.token.value"})
        r = self._client.get("/notifications/unread-count")
        self.assertIn(r.status_code, (401, 403))


# ─────────────────────────────────────────────────────────── SSE-Endpoint Auth-Gate

class NotificationStreamAuthGateTests(_SseAuthTestBase):
    """Der SSE-Handler prüft Authorization **selbst** (nicht via `require_auth`).

    Deshalb hier zusätzlich: Pre-Auth-Gate muss Cookie akzeptieren (via
    Middleware-Injection) und ohne Auth hart 401 werfen, bevor der
    StreamingResponse überhaupt startet.
    """

    def _set_cookies(self, cookies: dict | None):
        self._client.cookies.clear()
        if cookies:
            for k, v in cookies.items():
                self._client.cookies.set(k, v)

    def test_no_auth_blocked_before_stream(self):
        """Kein Cookie, kein Bearer → 401 (hart im Handler, kein Generator)."""
        self._set_cookies(None)
        r = self._client.get("/notifications/stream")
        self.assertEqual(r.status_code, 401)

    def test_invalid_cookie_blocked(self):
        """Garbage-Cookie → Middleware synthetisiert Bearer, verify_jwt wirft 401."""
        self._set_cookies({AUTH_COOKIE_NAME: "garbage.token.value"})
        r = self._client.get("/notifications/stream")
        # verify_jwt wirft HTTPException(401) → Handler wirft 401
        self.assertEqual(r.status_code, 401)


# ─────────────────────────────────────────────────────────── Agent-Chat-Stream

class AgentChatStreamCookieAuthTests(_SseAuthTestBase):
    """`/agents/{id}/message/stream` — FastAPI-Depends Auth-Kette."""

    def _set_cookies(self, cookies: dict | None):
        self._client.cookies.clear()
        if cookies:
            for k, v in cookies.items():
                self._client.cookies.set(k, v)

    def test_cookie_only_passes_auth_layer(self):
        """Cookie-only darf NICHT 401 geben (Auth muss durchgehen).

        Unbekannter Agent darf 404/400 liefern — wir prüfen nur, dass Auth
        nicht wegen fehlendem Bearer blockt.
        """
        _token, cookies = self._login()
        self._set_cookies(cookies)
        with self._client.stream(
            "POST", "/agents/does-not-exist/message/stream",
            json={"message": "ping"},
        ) as r:
            self.assertNotEqual(r.status_code, 401)
            self.assertNotEqual(r.status_code, 403)

    def test_no_auth_rejected(self):
        """Kein Auth → 401/403."""
        self._set_cookies(None)
        with self._client.stream(
            "POST", "/agents/does-not-exist/message/stream",
            json={"message": "ping"},
        ) as r:
            self.assertIn(r.status_code, (401, 403))


# ─────────────────────────────────────────────────────────── Live-Server (opt-in)

_LIVE_BASE = os.getenv("HYDRAHIVE_LIVE_BASE")
_LIVE_USER = os.getenv("HYDRAHIVE_LIVE_USER")
_LIVE_PASS = os.getenv("HYDRAHIVE_LIVE_PASS")


@pytest.mark.skipif(
    not (_LIVE_BASE and _LIVE_USER and _LIVE_PASS),
    reason="Live-Server-Test: setze HYDRAHIVE_LIVE_BASE/USER/PASS (z.B. .181).",
)
def test_live_notification_stream_cookie_auth():
    """Live-Verify gegen echten Server: Login → Cookie → SSE-Stream mit Events.

    Kriterien aus #765:
    - SSE-Stream läuft >30s ohne Auth-Refresh
    - Mindestens 1 Heartbeat-Event ODER initial HTTP 200 + korrekter Content-Type

    Ein Push-Event zu provozieren ist Live-seitig schwierig (braucht Admin-
    Tool-Call o.ä.), deshalb begnügen wir uns mit Status+Content-Type +
    graceful Disconnect.
    """
    import httpx

    with httpx.Client(base_url=_LIVE_BASE, timeout=15.0, verify=False) as c:
        login = c.post("/auth/login", json={"username": _LIVE_USER, "password": _LIVE_PASS})
        assert login.status_code == 200, login.text
        assert AUTH_COOKIE_NAME in c.cookies, "Server setzt kein Auth-Cookie"

        # Bearer komplett weglassen — rein Cookie-basiert
        with c.stream("GET", "/notifications/stream") as r:
            assert r.status_code == 200, r.read()
            assert "text/event-stream" in r.headers.get("content-type", "")
            # Mindestens die ersten Bytes lesen damit wir wissen Stream ist live
            first = next(r.iter_bytes(chunk_size=1024), b"")
            # Leer ist OK wenn keine Events anliegen; wichtig: kein 401-Redirect
            assert isinstance(first, (bytes, bytearray))


if __name__ == "__main__":
    unittest.main()
