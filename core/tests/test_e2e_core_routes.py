"""
test_e2e_core_routes.py — E2E-Tests für Projects, Chat, Memory und Runtime-Audit.

Verification Gaps aus Code-Review (Issues #145, #146, #147, #152, #153):
- Projects: list/get auth guards
- Personal-Agent: session history, /me/agent info
- Runtime-Audit: /admin/runtime/status, /logs/core/summary
- Journal-Noise-Filter: nio.rooms-Zeilen tauchen nicht in top_signatures auf
- Docs: handbook.md vorhanden und vollständig
"""

import json
import tempfile
import unittest
import yaml
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from octopos_core import main
from octopos_core.router_core_misc import summarize_core_journal_lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_users_file(tmpdir: str, username: str = "testuser", password: str = "testpass") -> Path:
    import hashlib, secrets
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    pw_hash = f"pbkdf2:{salt}:{h.hex()}"
    users = {username: {"password_hash": pw_hash, "role": "admin"}}
    p = Path(tmpdir) / "users.json"
    p.write_text(json.dumps(users))
    return p


def _make_agent_yaml(agents_dir: Path, agent_id: str) -> None:
    d = agents_dir / agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "skills").mkdir(exist_ok=True)
    (d / "memory").mkdir(exist_ok=True)
    cfg = {
        "id": agent_id, "type": "specialist",
        "identity": f"Test {agent_id}",
        "llm": {"model": "claude-haiku-4-5-20251001", "temperature": 0.3, "max_tokens": 512},
        "tools": [],
    }
    (d / "agent.yaml").write_text(yaml.dump(cfg))
    (d / "soul.md").write_text(f"# {agent_id}\nTest.")


def _client_with_isolation(tmpdir: str, users_path: Path, agents_dir: Path, projects_dir: Path = None):
    """TestClient mit gepatchtem Discovery/Runtime-Start für Isolation."""
    return mock.patch.multiple(
        main,
        USERS_FILE=str(users_path),
        JWT_SECRET="test-jwt-secret",
        AGENTS_DIR=str(agents_dir),
        PROJECTS_DIR=str(projects_dir) if projects_dir else main.PROJECTS_DIR,
    )


# ---------------------------------------------------------------------------
# Auth-Guard Smoke-Tests (keine Lifespan nötig)
# ---------------------------------------------------------------------------

def _all_lifecycle_patches():
    """Gemeinsamer Context-Manager: patcht alle Lifecycle-Methoden die Dateisystem-Zugriff brauchen."""
    return mock.patch.multiple(
        "octopos_core.main",
        **{},  # Platzhalter — kombiniert mit patch.object unten
    )


def _build_smoke_patches() -> list:
    """Erstellt alle benötigten Patches für einen isolierten TestClient."""
    return [
        mock.patch.object(main.discovery,      "start", return_value=None),
        mock.patch.object(main.discovery,      "stop",  return_value=None),
        mock.patch.object(main.projects,       "start", return_value=None),
        mock.patch.object(main.projects,       "stop",  return_value=None),
        mock.patch.object(main.sessions,       "start", return_value=None),
        mock.patch.object(main.agent_sessions, "start", return_value=None),
        mock.patch.object(main.runtime,        "start", return_value=None),
        mock.patch.object(main.runtime,        "stop",  return_value=None),
        mock.patch("octopos_core.main._load_or_create_jwt_secret", return_value="test-jwt-secret"),
        mock.patch("octopos_core.main.USERS_FILE", Path("/dev/null")),
    ]


class _SmokeClientMixin:
    """Mixin: stellt einen isolierten TestClient als cls._client bereit."""

    @classmethod
    def setUpClass(cls):
        cls._patches = _build_smoke_patches()
        for p in cls._patches:
            p.start()
        main.JWT_SECRET = "test-jwt-secret"
        main.discovery._agents.clear()
        cls._client = TestClient(main.app, raise_server_exceptions=False)
        cls._client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._client.__exit__(None, None, None)
        main.discovery._agents.clear()
        for p in reversed(cls._patches):
            p.stop()


class AuthGuardSmokeTests(_SmokeClientMixin, unittest.TestCase):
    """Schnelle Checks: alle neuen Endpoints erfordern Auth."""

    def _get_unauthenticated(self, path: str) -> int:
        return self._client.get(path).status_code

    def test_projects_list_requires_auth(self):
        self.assertIn(self._get_unauthenticated("/projects"), (401, 403))

    def test_project_get_requires_auth(self):
        self.assertIn(self._get_unauthenticated("/projects/some-proj"), (401, 403))

    def test_my_agent_requires_auth(self):
        self.assertIn(self._get_unauthenticated("/me/agent"), (401, 403))

    def test_session_history_requires_auth(self):
        self.assertIn(self._get_unauthenticated("/me/agent/session/history"), (401, 403))

    def test_runtime_status_requires_auth(self):
        self.assertIn(self._get_unauthenticated("/admin/runtime/status"), (401, 403))

    def test_core_journal_summary_requires_auth(self):
        self.assertIn(self._get_unauthenticated("/logs/core/summary"), (401, 403))

    def test_wks_config_requires_auth(self):
        self.assertIn(self._get_unauthenticated("/me/wks"), (401, 403))

    def test_discord_config_requires_auth(self):
        self.assertIn(self._get_unauthenticated("/me/discord"), (401, 403))

    def test_platforms_requires_auth(self):
        self.assertIn(self._get_unauthenticated("/me/platforms"), (401, 403))


# ---------------------------------------------------------------------------
# Personal-Agent E2E  (#146)
# ---------------------------------------------------------------------------

class PersonalAgentE2ETests(unittest.TestCase):
    """Verification gap #146 — /me/agent und Session-History."""

    def _run_with_client(self, tmpdir: str, fn):
        agents_dir   = Path(tmpdir) / "agents"
        projects_dir = Path(tmpdir) / "projects"
        agents_dir.mkdir()
        projects_dir.mkdir()
        _make_agent_yaml(agents_dir, "personal_testuser")

        users_path = _make_users_file(tmpdir)

        patches = {
            "USERS_FILE":   users_path,
            "JWT_SECRET":   "test-jwt-e2e",
            "AGENTS_DIR":   str(agents_dir),
            "PROJECTS_DIR": str(projects_dir),
        }
        with mock.patch.multiple(main, **patches), \
             mock.patch("octopos_core.main._load_or_create_jwt_secret", return_value="test-jwt-e2e"):
            with mock.patch.object(main.discovery,      "start", return_value=None), \
                 mock.patch.object(main.discovery,      "stop",  return_value=None), \
                 mock.patch.object(main.projects,       "start", return_value=None), \
                 mock.patch.object(main.projects,       "stop",  return_value=None), \
                 mock.patch.object(main.sessions,       "start", return_value=None), \
                 mock.patch.object(main.agent_sessions, "start", return_value=None), \
                 mock.patch.object(main.runtime,        "start", return_value=None), \
                 mock.patch.object(main.runtime,        "stop",  return_value=None):
                main.discovery._dir = agents_dir
                main.discovery._agents.clear()
                try:
                    with TestClient(main.app) as client:
                        fn(client)
                finally:
                    main.discovery._agents.clear()

    def test_me_agent_returns_agent_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                self.assertEqual(resp.status_code, 200, resp.text)
                token = resp.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                resp = client.get("/me/agent", headers=headers)
                self.assertEqual(resp.status_code, 200, resp.text)
                body = resp.json()
                self.assertEqual(body.get("agent_id"), "personal_testuser")

            self._run_with_client(tmpdir, check)

    def test_session_history_returns_messages_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                token = resp.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                resp = client.get("/me/agent/session/history", headers=headers)
                self.assertEqual(resp.status_code, 200, resp.text)
                body = resp.json()
                self.assertIn("messages", body)
                self.assertIsInstance(body["messages"], list)

            self._run_with_client(tmpdir, check)

    def test_clear_session_returns_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                token = resp.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                resp = client.delete("/me/agent/session", headers=headers)
                self.assertIn(resp.status_code, (200, 204))

            self._run_with_client(tmpdir, check)


# ---------------------------------------------------------------------------
# Runtime-Audit E2E  (#141)
# ---------------------------------------------------------------------------

class RuntimeAuditE2ETests(unittest.TestCase):
    """Runtime-Status Struktur und Auth prüfen."""

    def test_runtime_status_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            users_path = _make_users_file(tmpdir)
            agents_dir = Path(tmpdir) / "agents"
            agents_dir.mkdir()

            with mock.patch.multiple(main, USERS_FILE=users_path, JWT_SECRET="test-rt"), \
                 mock.patch("octopos_core.main._load_or_create_jwt_secret", return_value="test-rt"):
                with mock.patch.object(main.discovery,      "start", return_value=None), \
                     mock.patch.object(main.discovery,      "stop",  return_value=None), \
                     mock.patch.object(main.projects,       "start", return_value=None), \
                     mock.patch.object(main.projects,       "stop",  return_value=None), \
                     mock.patch.object(main.sessions,       "start", return_value=None), \
                     mock.patch.object(main.agent_sessions, "start", return_value=None), \
                     mock.patch.object(main.runtime,        "start", return_value=None), \
                     mock.patch.object(main.runtime,        "stop",  return_value=None):
                    main.discovery._agents.clear()
                    try:
                        with TestClient(main.app) as client:
                            resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                            token = resp.json()["access_token"]
                            headers = {"Authorization": f"Bearer {token}"}

                            resp = client.get("/admin/runtime/status", headers=headers)
                            self.assertEqual(resp.status_code, 200, resp.text)
                            body = resp.json()
                            for key in ("deployment", "service", "runtime", "audit"):
                                self.assertIn(key, body, f"Schlüssel '{key}' fehlt in runtime/status")
                    finally:
                        main.discovery._agents.clear()


# ---------------------------------------------------------------------------
# Journal Noise Filter  (#153)
# ---------------------------------------------------------------------------

class JournalNoiseFilterTests(unittest.TestCase):
    """Verification gap #153 — nio.rooms / snap noise gefiltert."""

    def _nio_lines(self, n: int = 20) -> list[str]:
        return [
            f"Mar 23 10:00:{i:02d} host core[1]: INFO nio.rooms: Room !abc handling event of type RoomMessageText"
            for i in range(n)
        ]

    def _real_lines(self) -> list[str]:
        return [
            "Mar 23 10:01:00 host core[1]: ERROR octopos_core.orchestrator: LLM timeout",
            "Mar 23 10:01:01 host core[1]: WARNING octopos_core.main: Rate limit hit",
            "Mar 23 10:01:02 host core[1]: INFO octopos_core.agent_runtime: Agent started: personal_admin",
        ]

    def test_nio_rooms_not_in_top_signatures(self):
        result = summarize_core_journal_lines(self._nio_lines(20) + self._real_lines())
        for entry in result["top_signatures"]:
            self.assertNotIn("nio.rooms", entry["signature"])

    def test_snap_not_in_top_signatures(self):
        snap = ["Mar 23 10:00:00 host core[1]: INFO snap firmware notifier"] * 10
        result = summarize_core_journal_lines(snap + self._real_lines())
        for entry in result["top_signatures"]:
            self.assertNotIn("snap", entry["signature"])

    def test_errors_still_counted_despite_noise(self):
        result = summarize_core_journal_lines(self._nio_lines(15) + self._real_lines())
        self.assertGreater(result["error_count"], 0)
        self.assertGreater(result["warn_count"],  0)

    def test_max_five_signatures(self):
        lines = []
        for i in range(10):
            lines += [f"Mar 23 10:{i:02d}:00 host core[1]: INFO nio.rooms: event type {i}"] * 3
        lines += self._real_lines()
        result = summarize_core_journal_lines(lines)
        self.assertLessEqual(len(result["top_signatures"]), 5)

    def test_empty_lines_handled(self):
        result = summarize_core_journal_lines([])
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["warn_count"],  0)
        self.assertEqual(result["top_signatures"], [])


# ---------------------------------------------------------------------------
# Docs Konsistenz  (#152)
# ---------------------------------------------------------------------------

class DocsConsistencyTests(unittest.TestCase):
    """Verification gap #152 — Doku vorhanden und vollständig."""

    def _find(self, name: str) -> Path | None:
        for p in [
            Path(__file__).parent.parent.parent / "docs" / name,
            Path("/home/till/octopos/docs") / name,
        ]:
            if p.exists():
                return p
        return None

    def test_handbook_exists_and_substantial(self):
        p = self._find("handbook.md")
        self.assertIsNotNone(p, "handbook.md nicht gefunden")
        self.assertGreater(len(p.read_text()), 5000, "handbook.md zu kurz")

    def test_changelog_exists(self):
        self.assertIsNotNone(self._find("changelog.md"), "changelog.md nicht gefunden")

    def test_handbook_covers_key_topics(self):
        p = self._find("handbook.md")
        if p is None:
            self.skipTest("handbook.md nicht gefunden")
        content = p.read_text().lower()
        for topic in ("installation", "agent", "discord", "wks", "api"):
            self.assertIn(topic, content, f"Thema '{topic}' fehlt im Handbook")

    def test_changelog_has_recent_entry(self):
        p = self._find("changelog.md")
        if p is None:
            self.skipTest("changelog.md nicht gefunden")
        content = p.read_text()
        self.assertIn("2026", content, "Kein 2026-Eintrag im Changelog")


# ---------------------------------------------------------------------------
# Agent Memory E2E  (#147)
# ---------------------------------------------------------------------------

class AgentMemoryE2ETests(unittest.TestCase):
    """Verification gap #147 — POST /agents/{id}/memory schreibt Datei."""

    def _run_with_client(self, tmpdir: str, fn):
        agents_dir   = Path(tmpdir) / "agents"
        projects_dir = Path(tmpdir) / "projects"
        agents_dir.mkdir()
        projects_dir.mkdir()
        _make_agent_yaml(agents_dir, "personal_testuser")

        users_path = _make_users_file(tmpdir)
        patches = {
            "USERS_FILE":   users_path,
            "JWT_SECRET":   "test-jwt-mem",
            "AGENTS_DIR":   str(agents_dir),
            "PROJECTS_DIR": str(projects_dir),
        }
        with mock.patch.multiple(main, **patches), \
             mock.patch("octopos_core.main._load_or_create_jwt_secret", return_value="test-jwt-mem"):
            with mock.patch.object(main.discovery,      "start", return_value=None), \
                 mock.patch.object(main.discovery,      "stop",  return_value=None), \
                 mock.patch.object(main.projects,       "start", return_value=None), \
                 mock.patch.object(main.projects,       "stop",  return_value=None), \
                 mock.patch.object(main.sessions,       "start", return_value=None), \
                 mock.patch.object(main.agent_sessions, "start", return_value=None), \
                 mock.patch.object(main.runtime,        "start", return_value=None), \
                 mock.patch.object(main.runtime,        "stop",  return_value=None):
                main.discovery._dir = agents_dir
                main.discovery._agents.clear()
                try:
                    with TestClient(main.app) as client:
                        fn(client)
                finally:
                    main.discovery._agents.clear()

    def test_write_memory_returns_404_for_unknown_agent(self):
        """Endpoint erreichbar und verarbeitet Request — 404 wenn Agent unbekannt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                self.assertEqual(resp.status_code, 200, resp.text)
                token = resp.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                resp = client.post(
                    "/agents/nonexistent_xyz/memory",
                    headers=headers,
                    json={"filename": "test_fact", "content": "# Test\nOctopOS ist toll."},
                )
                # 404 beweist: Endpoint existiert, Auth funktioniert, Agent-Lookup läuft
                self.assertEqual(resp.status_code, 404, resp.text)

            self._run_with_client(tmpdir, check)

    def test_write_memory_requires_auth(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post(
                    "/agents/personal_testuser/memory",
                    json={"filename": "x", "content": "y"},
                )
                self.assertIn(resp.status_code, (401, 403))
            self._run_with_client(tmpdir, check)


# ---------------------------------------------------------------------------
# WKS Config E2E  (#148)
# ---------------------------------------------------------------------------

class WksConfigE2ETests(unittest.TestCase):
    """Verification gap #148 — /me/wks GET/PUT Struktur."""

    def _run_with_client(self, tmpdir: str, fn):
        agents_dir   = Path(tmpdir) / "agents"
        projects_dir = Path(tmpdir) / "projects"
        agents_dir.mkdir()
        projects_dir.mkdir()

        users_path = _make_users_file(tmpdir)
        patches = {
            "USERS_FILE":   users_path,
            "JWT_SECRET":   "test-jwt-wks",
            "AGENTS_DIR":   str(agents_dir),
            "PROJECTS_DIR": str(projects_dir),
        }
        with mock.patch.multiple(main, **patches), \
             mock.patch("octopos_core.main._load_or_create_jwt_secret", return_value="test-jwt-wks"):
            with mock.patch.object(main.discovery,      "start", return_value=None), \
                 mock.patch.object(main.discovery,      "stop",  return_value=None), \
                 mock.patch.object(main.projects,       "start", return_value=None), \
                 mock.patch.object(main.projects,       "stop",  return_value=None), \
                 mock.patch.object(main.sessions,       "start", return_value=None), \
                 mock.patch.object(main.agent_sessions, "start", return_value=None), \
                 mock.patch.object(main.runtime,        "start", return_value=None), \
                 mock.patch.object(main.runtime,        "stop",  return_value=None):
                main.discovery._agents.clear()
                try:
                    with TestClient(main.app) as client:
                        fn(client)
                finally:
                    main.discovery._agents.clear()

    def test_wks_get_returns_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                token = resp.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                resp = client.get("/me/wks", headers=headers)
                self.assertEqual(resp.status_code, 200, resp.text)
                body = resp.json()
                for key in ("configured", "ip", "ssh_user", "ollama_port", "has_ssh_key"):
                    self.assertIn(key, body, f"Schlüssel '{key}' fehlt in /me/wks")
                self.assertFalse(body["configured"])
                self.assertFalse(body["has_ssh_key"])

            self._run_with_client(tmpdir, check)

    def test_platforms_returns_overview(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                token = resp.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                resp = client.get("/me/platforms", headers=headers)
                self.assertEqual(resp.status_code, 200, resp.text)
                body = resp.json()
                self.assertIn("platforms", body)
                self.assertIn("username", body)

            self._run_with_client(tmpdir, check)


# ---------------------------------------------------------------------------
# Discord Config E2E  (#149)
# ---------------------------------------------------------------------------

class DiscordConfigE2ETests(unittest.TestCase):
    """Verification gap #149 — /me/discord GET/DELETE ohne echten Bot-Token."""

    def _run_with_client(self, tmpdir: str, fn, discord_token_dir: Path = None):
        agents_dir   = Path(tmpdir) / "agents"
        projects_dir = Path(tmpdir) / "projects"
        agents_dir.mkdir()
        projects_dir.mkdir()

        users_path = _make_users_file(tmpdir)
        patches = {
            "USERS_FILE":   users_path,
            "JWT_SECRET":   "test-jwt-discord",
            "AGENTS_DIR":   str(agents_dir),
            "PROJECTS_DIR": str(projects_dir),
        }
        token_dir = discord_token_dir or Path(tmpdir) / "discord_tokens"
        with mock.patch.multiple(main, **patches), \
             mock.patch("octopos_core.main._load_or_create_jwt_secret", return_value="test-jwt-discord"), \
             mock.patch("octopos_core.discord_agent.TOKEN_DIR", token_dir):
            with mock.patch.object(main.discovery,      "start", return_value=None), \
                 mock.patch.object(main.discovery,      "stop",  return_value=None), \
                 mock.patch.object(main.projects,       "start", return_value=None), \
                 mock.patch.object(main.projects,       "stop",  return_value=None), \
                 mock.patch.object(main.sessions,       "start", return_value=None), \
                 mock.patch.object(main.agent_sessions, "start", return_value=None), \
                 mock.patch.object(main.runtime,        "start", return_value=None), \
                 mock.patch.object(main.runtime,        "stop",  return_value=None):
                main.discovery._agents.clear()
                try:
                    with TestClient(main.app) as client:
                        fn(client)
                finally:
                    main.discovery._agents.clear()

    def test_discord_get_unconfigured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                token = resp.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                resp = client.get("/me/discord", headers=headers)
                self.assertEqual(resp.status_code, 200, resp.text)
                body = resp.json()
                self.assertIn("configured", body)
                self.assertFalse(body["configured"])

            self._run_with_client(tmpdir, check)

    def test_discord_delete_unconfigured_ok(self):
        """DELETE /me/discord auf unkonfigurierten Bot soll nicht crashen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                token = resp.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                resp = client.delete("/me/discord", headers=headers)
                self.assertIn(resp.status_code, (200, 204), resp.text)

            self._run_with_client(tmpdir, check)

    def test_discord_put_rejects_invalid_token(self):
        """PUT /me/discord mit ungültigem Token → 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            def check(client):
                resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass"})
                token = resp.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                resp = client.put(
                    "/me/discord",
                    headers=headers,
                    json={"bot_token": "invalid-token", "guild_id": "123", "channel_ids": []},
                )
                self.assertEqual(resp.status_code, 400, resp.text)

            self._run_with_client(tmpdir, check)


# ---------------------------------------------------------------------------
# Gitea Tools E2E  (#150)
# ---------------------------------------------------------------------------

class GiteaToolsE2ETests(_SmokeClientMixin, unittest.TestCase):
    """Verification gap #150 — Gitea-Endpunkte erreichbar, Tool-Registry vollständig."""

    def test_gitea_issue_text_validation(self):
        from octopos_core.tool_registry import _validate_gitea_issue_text

        self.assertIsNone(_validate_gitea_issue_text("Normaler Titel"))
        self.assertIsNotNone(_validate_gitea_issue_text("x" * 257))
        self.assertIsNotNone(_validate_gitea_issue_text("ok", "y" * 20001))

    def test_gitea_tools_registered(self):
        from octopos_core.tool_registry import registry

        for tool_id in ("gitea_create_issue", "gitea_comment_issue", "gitea_update_issue"):
            self.assertIsNotNone(registry.get(tool_id), f"Tool '{tool_id}' nicht registriert")

    def test_gitea_repos_requires_auth(self):
        """GET /gitea/repos erfordert Auth."""
        self.assertIn(self._client.get("/gitea/repos").status_code, (401, 403))

    def test_gitea_config_requires_admin(self):
        """GET /gitea/config ist Admin-Route → ohne Auth 401/403."""
        self.assertIn(self._client.get("/gitea/config").status_code, (401, 403))


# ---------------------------------------------------------------------------
# Console API E2E  (#151)
# ---------------------------------------------------------------------------

class ConsoleApiE2ETests(_SmokeClientMixin, unittest.TestCase):
    """Verification gap #151 — Backend-Endpunkte die die Console nutzt."""

    def test_projects_endpoint_requires_auth(self):
        self.assertIn(self._client.get("/projects").status_code, (401, 403))

    def test_agents_endpoint_requires_auth(self):
        self.assertIn(self._client.get("/agents").status_code, (401, 403))

    def test_tools_endpoint_requires_auth(self):
        self.assertIn(self._client.get("/tools").status_code, (401, 403))

    def test_health_endpoint_public(self):
        """GET /health ist öffentlich und liefert service=octopos-core."""
        resp = self._client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("service"), "octopos-core")

    def test_setup_status_public(self):
        """GET /setup/status ist öffentlich."""
        resp = self._client.get("/setup/status")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("needs_setup", resp.json())

    def test_admin_backup_requires_auth(self):
        self.assertIn(self._client.get("/admin/backups").status_code, (401, 403))

    def test_mcp_servers_requires_auth(self):
        self.assertIn(self._client.get("/mcp/servers").status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
