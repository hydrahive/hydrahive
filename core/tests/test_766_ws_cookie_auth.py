"""
test_766_ws_cookie_auth.py — #766: WebSocket Cookie-Auth Verifikation

Verifiziert:
1. WS-Endpoint /projects/{id}/collab mit AUTH_COOKIE_NAME Cookie → kein 1008
2. WS-Endpoint OHNE Cookie + OHNE Query-Param → 1008 (Policy Violation)
3. WS-Endpoint mit Query-Param als Fallback → kein 1008 (Rückkompatibilität)

Backend: collab_ws() liest websocket.cookies.get(AUTH_COOKIE_NAME) (#766)
Frontend: useProjectYjs.ts sendet Cookie direkt im WS-Handshake (#766)
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


class TestWSCookieAuth(unittest.TestCase):
    """#766: WebSocket Cookie-Auth Tests (Collab / Yjs)."""

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
                    client = TestClient(main.app, raise_server_exceptions=False)
                    fn(client)
                finally:
                    main.discovery._agents.clear()

    # ── Test 1: WS-Connect mit Cookie → kein 1008 ─────────────────────

    def test_ws_endpoint_with_cookie_no_1008(self):
        """#766.1: WS /projects/testproj/collab mit AUTH_COOKIE_NAME Cookie → kein 1008."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                from starlette.websockets import WebSocketDisconnect
                # Login → Cookie holen
                login_resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                self.assertEqual(login_resp.status_code, 200, login_resp.text)
                self.assertIn(AUTH_COOKIE_NAME, login_resp.cookies)
                cookie_value = login_resp.cookies[AUTH_COOKIE_NAME]

                # Yjs-Server ist im Test nicht gestartet → 4503 erwartet.
                with mock.patch("hydrahive_core.collab_yjs.get_yjs_server", return_value=None):
                    try:
                        with client.websocket_connect(
                            f"/projects/testproj/collab",
                            cookies={AUTH_COOKIE_NAME: cookie_value},
                        ) as ws:
                            ws.receive()
                    except WebSocketDisconnect as e:
                        # 1008 = Auth fehlgeschlagen (Cookie nicht akzeptiert)
                        # 4503 = Yjs-Server nicht gestartet (Cookie hat funktioniert)
                        self.assertNotEqual(
                            e.code, 1008,
                            f"Cookie-Auth gab 1008 — Cookie wurde nicht akzeptiert: {e.reason}"
                        )

            self._run_with_client(tmpdir, check)

    # ── Test 2: Ohne Cookie + OHNE Query → 1008 ───────────────────────

    def test_ws_endpoint_without_cookie_or_token_returns_1008(self):
        """#766.2: /projects/testproj/collab ohne Cookie und ohne Query-Param → 1008."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                from starlette.websockets import WebSocketDisconnect
                # WS-Connect OHNE Cookie, OHNE Query-Param → Server schließt mit 1008
                with self.assertRaises(WebSocketDisconnect) as ctx:
                    with client.websocket_connect("/projects/testproj/collab") as ws:
                        ws.receive()
                self.assertEqual(ctx.exception.code, 1008)
                self.assertIn("Auth fehlgeschlagen", ctx.exception.reason)

            self._run_with_client(tmpdir, check)

    # ── Test 3: Query-Param als Fallback → kein 1008 ──────────────────

    def test_ws_endpoint_with_query_param_fallback_no_1008(self):
        """#766.3: /projects/testproj/collab mit ?token=<ws-ticket> (Fallback) → kein 1008."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                from starlette.websockets import WebSocketDisconnect
                # Erst WS-Ticket via /auth/ws-token holen (mit Cookie-Auth)
                login_resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                self.assertEqual(login_resp.status_code, 200)
                cookie_value = login_resp.cookies[AUTH_COOKIE_NAME]

                # WS-Ticket per Cookie-Auth
                ticket_resp = client.post(
                    "/auth/ws-token",
                    cookies={AUTH_COOKIE_NAME: cookie_value},
                )
                self.assertEqual(ticket_resp.status_code, 200)
                ticket = ticket_resp.json()["ticket"]

                # WS mit Query-Param (Fallback) — kein Cookie
                with mock.patch("hydrahive_core.collab_yjs.get_yjs_server", return_value=None):
                    try:
                        with client.websocket_connect(
                            f"/projects/testproj/collab?token={ticket}",
                        ) as ws:
                            ws.receive()
                    except WebSocketDisconnect as e:
                        self.assertNotEqual(
                            e.code, 1008,
                            f"Query-Param-Fallback gab 1008 — Fallback funktioniert nicht: {e.reason}"
                        )

            self._run_with_client(tmpdir, check)


if __name__ == "__main__":
    unittest.main()
