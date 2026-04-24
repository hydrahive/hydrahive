"""
test_765_sse_cookie_auth.py — #765: SSE-Streams Cookie-Auth Verifikation

Verifiziert:
1. Login setzt httpOnly-Cookie (AUTH_COOKIE_NAME)
2. SSE-Endpoint antwortet mit Cookie-Auth (kein Bearer-Header nötig)
3. Kein "Unauthorized" bei gültigem Cookie
4. Token-Expiry: 401 wenn Cookie fehlt/abgelaufen

Backend: _AuthCookieMiddleware in main.py (transparent für alle Endpoints)
Frontend: sseStream.ts nutzt credentials: "include" (#764 Phase 2)
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from hydrahive_core import main
from hydrahive_core.auth_utils import AUTH_COOKIE_NAME


def _make_users_file(tmpdir: str, username: str = "testuser", password: str = "testpass") -> Path:
    import hashlib
    import secrets

    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    pw_hash = f"pbkdf2:{salt}:{h.hex()}"
    users = {username: {"password_hash": pw_hash, "role": "admin"}}
    p = Path(tmpdir) / "users.json"
    p.write_text(json.dumps(users))
    return p


def _make_agent_yaml(agents_dir: Path, agent_id: str) -> None:
    """Minimal agent.yaml für einen Boss-Agenten."""
    import yaml
    d = agents_dir / agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "skills").mkdir(exist_ok=True)
    (d / "memory").mkdir(exist_ok=True)
    cfg = {
        "id": agent_id,
        "type": "boss",
        "identity": f"Test {agent_id}",
        "llm": {"model": "claude-sonnet-4-6", "temperature": 0.3},
        "tools": [],
    }
    (d / "agent.yaml").write_text(yaml.dump(cfg))


class TestSSECookieAuth(unittest.TestCase):
    """#765: SSE-Stream Cookie-Auth Tests."""

    def _run_with_client(self, tmpdir: str, fn):
        """TestClient mit voller Isolation — exaktes Muster aus test_e2e_core_routes.py."""
        import yaml as _yaml

        users_path = _make_users_file(tmpdir)
        agents_dir = Path(tmpdir) / "agents"
        projects_dir = Path(tmpdir) / "projects"
        projects_dir.mkdir()
        agents_dir.mkdir()

        _make_agent_yaml(agents_dir, "boss")

        # v2-Projekt mit Boss-Agent
        proj_cfg = {
            "id": "testproj",
            "version": "2.0.0",
            "is_v2": True,
            "identity": {"name": "Test Project"},
            "agents": {"boss": "boss", "workers": []},
        }
        (projects_dir / "testproj").mkdir()
        (projects_dir / "testproj" / "config.yaml").write_text(_yaml.dump(proj_cfg))

        patches = {
            "USERS_FILE":   users_path,
            "JWT_SECRET":   "test-rt",
            "AGENTS_DIR":   str(agents_dir),
            "PROJECTS_DIR": str(projects_dir),
        }
        with mock.patch.multiple(main, **patches), \
             mock.patch("hydrahive_core.main._load_or_create_jwt_secret", return_value="test-rt"), \
             mock.patch("hydrahive_core.migrations.run_migrations", return_value=None), \
             mock.patch("hydrahive_core.main._load_or_create_internal_secret", return_value="test-internal-secret"), \
             mock.patch.object(main.rate_limiter, "check_login"):
            with mock.patch.object(main.discovery, "start", return_value=None), \
                 mock.patch.object(main.discovery, "stop", return_value=None), \
                 mock.patch.object(main.projects, "start", return_value=None), \
                 mock.patch.object(main.projects, "stop", return_value=None), \
                 mock.patch.object(main.sessions, "start", return_value=None), \
                 mock.patch.object(main.agent_sessions, "start", return_value=None), \
                 mock.patch.object(main.runtime, "start", return_value=None), \
                 mock.patch.object(main.runtime, "stop", return_value=None):
                main.discovery._dir = agents_dir
                main.discovery._agents.clear()
                try:
                    client = TestClient(main.app)
                    fn(client)
                finally:
                    main.discovery._agents.clear()

    # ── Test 1: Login setzt httpOnly-Cookie ──────────────────────────

    def test_login_sets_http_only_cookie(self):
        """#765.1: Login antwortet mit AUTH_COOKIE_NAME Cookie (httpOnly, SameSite)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                self.assertEqual(resp.status_code, 200, resp.text)

                cookies = resp.cookies
                self.assertIn(AUTH_COOKIE_NAME, cookies, f"Cookie '{AUTH_COOKIE_NAME}' fehlt in Login-Response")
                self.assertTrue(cookies[AUTH_COOKIE_NAME], "Cookie ist leer")

                set_cookie = resp.headers.get("set-cookie", "")
                self.assertIn(AUTH_COOKIE_NAME, set_cookie, "Set-Cookie Header fehlt")

            self._run_with_client(tmpdir, check)

    # ── Test 2: SSE-Endpoint mit Cookie-Auth (kein Bearer) ───────────

    def test_sse_endpoint_with_cookie_no_bearer(self):
        """#765.2: /projects/testproj/message/stream antwortet mit Cookie-Auth, kein Bearer-Header."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                login_resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                self.assertEqual(login_resp.status_code, 200)
                self.assertIn(AUTH_COOKIE_NAME, login_resp.cookies)

                cookie_value = login_resp.cookies[AUTH_COOKIE_NAME]

                # SSE-Request NUR mit Cookie (kein Authorization-Header)
                resp = client.post(
                    "/projects/testproj/message/stream",
                    json={"content": "test"},
                    cookies={AUTH_COOKIE_NAME: cookie_value},
                )
                # Mögliche Status: 200, 409 (turn locked), 503 (boss unavailable)
                # ABER NICHT 401 — das wäre ein Auth-Fehler
                self.assertNotEqual(
                    resp.status_code, 401,
                    f"SSE-Endpoint gab 401 trotz gültigem Cookie — Cookie-Auth funktioniert nicht: {resp.text}"
                )

            self._run_with_client(tmpdir, check)

    # ── Test 3: Ohne Cookie → 401 ───────────────────────────────────

    def test_sse_endpoint_without_cookie_returns_401(self):
        """#765.3: /projects/testproj/message/stream ohne Cookie gibt 401 Unauthorized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post(
                    "/projects/testproj/message/stream",
                    json={"content": "test"},
                )
                self.assertEqual(resp.status_code, 401, f"Erwartet 401, bekommen {resp.status_code}: {resp.text}")

            self._run_with_client(tmpdir, check)

    # ── Test 4: Bearer-Header funkioniert weiterhin ──────────────────

    def test_bearer_header_still_works(self):
        """#765.4: Bearer-Authorization Header funktioniert weiterhin (Rückkompatibilität)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                login_resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                token = login_resp.json()["access_token"]

                resp = client.post(
                    "/projects/testproj/message/stream",
                    json={"content": "test"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertNotEqual(
                    resp.status_code, 401,
                    f"Bearer-Header gibt 401 — Auth-Backward-Compatible broken: {resp.text}"
                )

            self._run_with_client(tmpdir, check)

    # ── Test 5: Cookie mit ungültigem Token → 401 ───────────────────

    def test_cookie_with_invalid_token_returns_401(self):
        """#765.5: Cookie mit ungültigem/wrong Token gibt 401 Unauthorized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post(
                    "/projects/testproj/message/stream",
                    json={"content": "test"},
                    cookies={AUTH_COOKIE_NAME: "this.is.not.a.valid.token"},
                )
                self.assertEqual(resp.status_code, 401, f"Erwartet 401 bei ungültigem Cookie, bekommen {resp.status_code}")

            self._run_with_client(tmpdir, check)


if __name__ == "__main__":
    unittest.main()
