import tempfile
import unittest
import time
import json
from pathlib import Path
from unittest import mock
from types import SimpleNamespace
import types
import asyncio

import yaml
import aiohttp
from fastapi.testclient import TestClient

from octopos_core import main
from octopos_core.agent_config import AgentConfig
from octopos_core.execution_mode_policy import resolve_request_execution_mode
from octopos_core.gitea import resolve_git_target, resolve_repo_ref
from octopos_core.orchestrator import Orchestrator
from octopos_core.learning_memory import append_learning_snapshot, build_learning_prompt_snippet
from octopos_core.project_config import load_project_config
from octopos_core.rate_limiter import RateLimitSettings, RateLimiter
from octopos_core.router_agent_admin import CreateAgentRequest, build_agent_admin_data
from octopos_core import router_user_integrations as user_integrations
from octopos_core.router_core_misc import summarize_core_journal_lines
from octopos_core.router_users import (
    MyAgentUpdateRequest,
    build_personal_agent_data,
    default_personal_agent_execution_modes,
    persist_personal_agent_config,
)
from octopos_core.tool_registry import GitStatusTool, GiteaCreateIssueTool, GiteaCommentIssueTool, GiteaUpdateIssueTool, ShellExecTool


class SecurityRegressionTests(unittest.TestCase):
    def _route(self, path: str, method: str):
        for route in main.app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route
        self.fail(f"Route not found: {method} {path}")

    def _dependency_names(self, path: str, method: str) -> set[str]:
        route = self._route(path, method)
        return {
            dep.call.__name__
            for dep in route.dependant.dependencies
            if getattr(dep, "call", None) is not None
        }

    def _route_provider(self, path: str, method: str) -> str | None:
        for route in main.app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                endpoint = getattr(route, "endpoint", None)
                if endpoint is None:
                    return None
                return getattr(endpoint, "__name__", None)
        return None

    def test_sensitive_routes_keep_auth_guards(self):
        self.assertIn("require_auth", self._dependency_names("/tools", "GET"))
        self.assertIn("require_auth", self._dependency_names("/system/gpu", "GET"))
        self.assertIn("require_auth", self._dependency_names("/llm/available-models", "GET"))
        self.assertIn("require_auth", self._dependency_names("/gitea/repos", "GET"))
        self.assertIn("require_auth", self._dependency_names("/mcp/servers", "GET"))
        self.assertIn("require_admin", self._dependency_names("/audit/logs", "GET"))
        self.assertIn("require_admin", self._dependency_names("/admin/update/status", "GET"))
        self.assertIn("require_admin", self._dependency_names("/admin/runtime/status", "GET"))
        self.assertIn("require_admin", self._dependency_names("/admin/update/trigger", "POST"))
        self.assertIn("require_admin", self._dependency_names("/gitea/config", "GET"))
        self.assertIn("require_admin", self._dependency_names("/gitea/config", "PUT"))
        self.assertIn("require_admin", self._dependency_names("/llm/config", "GET"))
        self.assertIn("require_admin", self._dependency_names("/mcp/servers/{server_id}", "DELETE"))
        self.assertIn("require_admin", self._dependency_names("/admin/backups", "GET"))

    def test_public_routes_stay_public(self):
        self.assertNotIn("require_auth", self._dependency_names("/setup/status", "GET"))
        self.assertNotIn("require_admin", self._dependency_names("/setup/status", "GET"))
        self.assertNotIn("require_auth", self._dependency_names("/webhooks/gitea/{project_id}", "POST"))
        self.assertNotIn("require_admin", self._dependency_names("/webhooks/gitea/{project_id}", "POST"))

    def test_auth_login_end_to_end_roundtrip(self):
        original_users_file = main.USERS_FILE
        original_jwt_secret = main.JWT_SECRET
        with tempfile.TemporaryDirectory() as tmpdir:
            users_path = Path(tmpdir) / "users.json"
            users_path.write_text(
                json.dumps(
                    {
                        "alice": {
                            "password_hash": main._hash_password("sicheres-passwort"),
                            "role": "admin",
                            "matrix_id": "@alice:matrix.local",
                            "matrix_ok": True,
                            "created_at": "2026-03-23T10:00:00",
                        }
                    }
                ),
                encoding="utf-8",
            )

            main.USERS_FILE = users_path
            main.JWT_SECRET = "test-secret-for-login-e2e"

            with mock.patch.object(main.discovery, "start", return_value=None), \
                mock.patch.object(main.projects, "start", return_value=None), \
                mock.patch.object(main.sessions, "start", return_value=None), \
                mock.patch.object(main.agent_sessions, "start", return_value=None), \
                mock.patch.object(main.runtime, "start", return_value=None), \
                mock.patch.object(main.runtime, "stop", return_value=None), \
                mock.patch.object(main.discovery, "stop", return_value=None), \
                mock.patch.object(main.projects, "stop", return_value=None), \
                mock.patch.object(main, "_ensure_audit_log_path", return_value=None), \
                mock.patch.object(main, "_load_or_create_jwt_secret", return_value="test-secret-for-login-e2e"), \
                mock.patch.object(main, "_setup_matrix_clients", return_value=None), \
                mock.patch("octopos_core.gitea.get_gitea_client", side_effect=RuntimeError("gitea disabled for test")), \
                mock.patch.object(main, "setup_discord_clients", return_value=None):
                with TestClient(main.app) as client:
                    login = client.post(
                        "/auth/login",
                        json={"username": "alice", "password": "sicheres-passwort"},
                    )
                    self.assertEqual(login.status_code, 200)
                    token = login.json()["access_token"]
                    self.assertTrue(token)
                    whoami = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
                    self.assertEqual(whoami.status_code, 200)
                    self.assertEqual(whoami.json()["username"], "alice")
                    self.assertEqual(whoami.json()["role"], "admin")
                    missing = client.get("/auth/me")
                    self.assertEqual(missing.status_code, 401)

        main.USERS_FILE = original_users_file
        main.JWT_SECRET = original_jwt_secret

    def test_agent_lifecycle_end_to_end_roundtrip(self):
        original_users_file = main.USERS_FILE
        original_jwt_secret = main.JWT_SECRET
        original_agents_dir = main.AGENTS_DIR
        original_projects_dir = main.PROJECTS_DIR
        original_discovery_dir = main.discovery._dir
        original_projects_loader_dir = main.projects._dir
        original_sessions_projects_dir = main.sessions._projects_dir
        original_agent_sessions_projects_dir = main.agent_sessions._projects_dir
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            agents_dir = tmpdir / "agents"
            projects_dir = tmpdir / "projects"
            users_path = tmpdir / "users.json"
            agents_dir.mkdir()
            projects_dir.mkdir()
            users_path.write_text(
                json.dumps(
                    {
                        "alice": {
                            "password_hash": main._hash_password("sicheres-passwort"),
                            "role": "admin",
                            "matrix_id": "@alice:matrix.local",
                            "matrix_ok": True,
                            "created_at": "2026-03-23T10:00:00",
                        }
                    }
                ),
                encoding="utf-8",
            )

            main.USERS_FILE = users_path
            main.JWT_SECRET = "test-secret-for-agent-e2e"
            main.AGENTS_DIR = str(agents_dir)
            main.PROJECTS_DIR = str(projects_dir)
            main.discovery._dir = agents_dir
            main.projects._dir = projects_dir
            main.sessions._projects_dir = projects_dir
            main.agent_sessions._projects_dir = projects_dir
            main.discovery._agents.clear()
            main.projects._projects.clear()
            main.runtime._handles.clear()

            def _redirect_agent_path(value="."):
                if str(value) == "/agents":
                    return agents_dir
                return Path(value)

            with mock.patch.object(main, "_ensure_audit_log_path", return_value=None), \
                mock.patch.object(main, "_load_or_create_jwt_secret", return_value="test-secret-for-agent-e2e"), \
                mock.patch.object(main, "_setup_matrix_clients", return_value=None), \
                mock.patch("octopos_core.gitea.get_gitea_client", side_effect=RuntimeError("gitea disabled for test")), \
                mock.patch.object(main, "setup_discord_clients", return_value=None), \
                mock.patch("octopos_core.router_agent_admin.Path", side_effect=_redirect_agent_path):
                with TestClient(main.app) as client:
                    login = client.post(
                        "/auth/login",
                        json={"username": "alice", "password": "sicheres-passwort"},
                    )
                    self.assertEqual(login.status_code, 200)
                    token = login.json()["access_token"]
                    headers = {"Authorization": f"Bearer {token}"}

                    create = client.post(
                        "/agents",
                        headers=headers,
                        json={
                            "id": "worker_one",
                            "type": "worker",
                            "identity": "Worker Eins",
                            "model": "gpt-4o",
                            "temperature": 0.2,
                            "max_tokens": 1024,
                            "soul": "# Worker Eins\n\nArbeitet zuverlässig.",
                            "tools": ["git.read"],
                            "fallback_models": ["gpt-4o-mini"],
                            "mcp_servers": [],
                            "heartbeat_interval": "1s",
                            "heartbeat_timeout": "10s",
                            "heartbeat_on_failure": "restart",
                        },
                    )
                    self.assertEqual(create.status_code, 201)
                    self.assertTrue(create.json()["created"])
                    self.assertTrue((agents_dir / "worker_one" / "agent.yaml").exists())
                    self.assertTrue((agents_dir / "worker_one" / "soul.md").exists())

                    agent_get = client.get("/agents/worker_one", headers=headers)
                    self.assertEqual(agent_get.status_code, 200)
                    self.assertEqual(agent_get.json()["config"]["id"], "worker_one")

                    update = client.put(
                        "/agents/worker_one",
                        headers=headers,
                        json={
                            "id": "worker_one",
                            "type": "worker",
                            "identity": "Worker Zwei",
                            "model": "gpt-4o-mini",
                            "temperature": 0.4,
                            "max_tokens": 2048,
                            "soul": "# Worker Zwei\n\nAktualisiert.",
                            "tools": ["git.read", "git.write"],
                            "fallback_models": [],
                            "mcp_servers": [],
                            "heartbeat_interval": "1s",
                            "heartbeat_timeout": "10s",
                            "heartbeat_on_failure": "restart",
                        },
                    )
                    self.assertEqual(update.status_code, 200)
                    soul = client.get("/agents/worker_one/soul", headers=headers)
                    self.assertEqual(soul.status_code, 200)
                    self.assertIn("Worker Zwei", soul.json()["soul"])

                    spawn = client.post("/agents/spawn", headers=headers, json={"agent_id": "worker_one"})
                    self.assertEqual(spawn.status_code, 200)
                    runtime_before = client.get("/agents/worker_one", headers=headers).json()["runtime"]
                    self.assertIsNotNone(runtime_before)

                    heartbeat = client.post("/agents/worker_one/heartbeat", headers=headers)
                    self.assertEqual(heartbeat.status_code, 200)
                    runtime_after = client.get("/agents/worker_one", headers=headers).json()["runtime"]
                    self.assertIsNotNone(runtime_after)
                    self.assertEqual(runtime_after["status"], "running")

                    delete = client.delete("/agents/worker_one", headers=headers)
                    self.assertEqual(delete.status_code, 200)
                    self.assertTrue(delete.json()["disabled"])
                    self.assertTrue((agents_dir / "_worker_one_disabled").exists())

        main.USERS_FILE = original_users_file
        main.JWT_SECRET = original_jwt_secret
        main.AGENTS_DIR = original_agents_dir
        main.PROJECTS_DIR = original_projects_dir
        main.discovery._dir = original_discovery_dir
        main.projects._dir = original_projects_loader_dir
        main.sessions._projects_dir = original_sessions_projects_dir
        main.agent_sessions._projects_dir = original_agent_sessions_projects_dir
        main.discovery._agents.clear()
        main.projects._projects.clear()
        main.runtime._handles.clear()

    def test_localhost_internal_route_keeps_local_bypass(self):
        deps = self._dependency_names("/agents/{agent_id}/message", "POST")
        self.assertIn("require_auth_or_localhost", deps)

    def test_message_routes_keep_json_body_params(self):
        for path in (
            "/agents/{agent_id}/message",
            "/agents/{agent_id}/message/stream",
            "/me/agent/message/stream",
            "/projects/{project_id}/message",
            "/projects/{project_id}/message/stream",
        ):
            route = self._route(path, "POST")
            body_param_names = [param.name for param in route.dependant.body_params]
            self.assertEqual(len(body_param_names), 1)
            self.assertIn(body_param_names[0], {"body", "req"})
            query_param_names = {param.name for param in route.dependant.query_params}
            self.assertNotIn("req", query_param_names)
            self.assertNotIn("body", query_param_names)

    def test_project_message_routes_use_auth_execution_mode_path(self):
        for path in (
            "/projects/{project_id}/message",
            "/projects/{project_id}/message/stream",
        ):
            deps = self._dependency_names(path, "POST")
            self.assertIn("require_auth", deps)

    def test_me_platforms_route_is_registered(self):
        route = self._route("/me/platforms", "GET")
        endpoint = getattr(route, "endpoint", None)
        self.assertEqual(getattr(endpoint, "__module__", None), "octopos_core.router_user_integrations")

    def test_execution_mode_policy_defaults_and_internal_passthrough(self):
        self.assertEqual(resolve_request_execution_mode(("alice", "user"), None), "safe")
        self.assertEqual(resolve_request_execution_mode(("admin", "admin"), "elevated"), "elevated")
        self.assertIsNone(resolve_request_execution_mode(("internal", "admin"), None))
        self.assertEqual(resolve_request_execution_mode(("internal", "admin"), "root"), "root")

    def test_execution_mode_policy_blocks_non_admin_and_audits_admin(self):
        with self.assertRaises(main.HTTPException) as ctx:
            resolve_request_execution_mode(("alice", "user"), "root")
        self.assertEqual(ctx.exception.status_code, 403)

        audit_log = mock.Mock()
        mode = resolve_request_execution_mode(
            ("admin", "admin"),
            "root",
            audit_log=audit_log,
            audit_target="personal_admin",
            audit_source="agents.message",
        )

        self.assertEqual(mode, "root")
        audit_log.assert_called_once_with(
            "agent.execution_mode",
            user="admin",
            target="personal_admin",
            details={"requested_mode": "root", "source": "agents.message"},
        )

    def test_audit_log_write_and_read_roundtrip(self):
        original_audit_log_file = main.AUDIT_LOG_FILE
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit" / "audit.jsonl"
            main.AUDIT_LOG_FILE = audit_file
            main.audit_log("security.regression", user="tester", target="unit")
            logs = main._read_audit_logs(limit=10, action="security.")

        main.AUDIT_LOG_FILE = original_audit_log_file

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["action"], "security.regression")
        self.assertEqual(logs[0]["user"], "tester")
        self.assertEqual(logs[0]["target"], "unit")

    def test_gitea_config_get_masks_token(self):
        original_config_file = main.GITEA_CONFIG_FILE
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "gitea_config.json"
            config_path.write_text(
                '{"url":"http://127.0.0.1:3001","org":"octopos","token":"1234567890abcdef","webhook_secret":"secret"}',
                encoding="utf-8",
            )
            main.GITEA_CONFIG_FILE = str(config_path)
            cfg = main.get_gitea_config()

        main.GITEA_CONFIG_FILE = original_config_file

        self.assertTrue(cfg["has_token"])
        self.assertEqual(cfg["token_masked"], "12345678...cdef")
        self.assertNotIn("token", cfg)

    def test_personal_agent_project_manifest_is_created(self):
        original_projects_dir = main.PROJECTS_DIR
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                main.PROJECTS_DIR = tmpdir
                project_yaml = main._ensure_personal_project_manifest("alice")
                self.assertTrue(project_yaml.exists())
                cfg = load_project_config(project_yaml.parent)
                self.assertIsNotNone(cfg)
                self.assertEqual(cfg.id, "personal_alice")
                self.assertEqual(cfg.agents.boss, "personal_alice")
                self.assertEqual(cfg.identity.name, "Personal Agent")
        finally:
            main.PROJECTS_DIR = original_projects_dir

    def test_only_special_cases_remain_direct_app_routes(self):
        direct_app_routes = {
            (route.path, tuple(sorted(route.methods)))
            for route in main.app.routes
            if getattr(route, "endpoint", None) is not None
            and getattr(route.endpoint, "__name__", None) in {
                "spawn_task_agent",
                "agent_heartbeat",
                "agent_message_sync",
                "agent_message_stream",
                "gitea_webhook",
            }
        }
        expected = {
            ("/agents/spawn", ("POST",)),
            ("/agents/{agent_id}/heartbeat", ("POST",)),
            ("/agents/{agent_id}/message", ("POST",)),
            ("/agents/{agent_id}/message/stream", ("POST",)),
            ("/webhooks/gitea/{project_id}", ("POST",)),
        }
        self.assertEqual(direct_app_routes, expected)

    def test_project_delete_route_is_registered_once_from_lifecycle_router(self):
        matches = [
            route
            for route in main.app.routes
            if getattr(route, "path", None) == "/projects/{project_id}"
            and "DELETE" in getattr(route, "methods", set())
        ]
        self.assertEqual(len(matches), 1)
        endpoint = getattr(matches[0], "endpoint", None)
        self.assertEqual(getattr(endpoint, "__name__", None), "delete_project")
        self.assertEqual(getattr(endpoint, "__module__", None), "octopos_core.router_project_lifecycle")

    def test_llm_routes_are_registered_from_router_llm(self):
        for path, method in (
            ("/llm/config", "GET"),
            ("/llm/available-models", "GET"),
            ("/llm/oauth/openai_codex/start", "POST"),
            ("/llm/ollama/models", "GET"),
        ):
            route = self._route(path, method)
            endpoint = getattr(route, "endpoint", None)
            self.assertEqual(getattr(endpoint, "__module__", None), "octopos_core.router_llm")

    def test_mcp_routes_are_registered_from_router_mcp(self):
        for path, method in (
            ("/mcp/servers", "GET"),
            ("/mcp/servers", "POST"),
            ("/mcp/servers/{server_id}", "PUT"),
            ("/mcp/servers/{server_id}", "DELETE"),
        ):
            route = self._route(path, method)
            endpoint = getattr(route, "endpoint", None)
            self.assertEqual(getattr(endpoint, "__module__", None), "octopos_core.router_mcp")

    def test_backup_routes_are_registered_from_router_backup_restore(self):
        for path, method in (
            ("/admin/backups", "GET"),
            ("/admin/backup", "POST"),
            ("/admin/backups/{name}/download", "GET"),
            ("/admin/backups/{name}", "DELETE"),
            ("/admin/restore/{name}", "POST"),
        ):
            route = self._route(path, method)
            endpoint = getattr(route, "endpoint", None)
            self.assertEqual(getattr(endpoint, "__module__", None), "octopos_core.router_backup_restore")

    def test_core_misc_routes_are_registered_from_router_core_misc(self):
        for path, method in (
            ("/setup/status", "GET"),
            ("/setup", "POST"),
            ("/auth/login", "POST"),
            ("/auth/me", "GET"),
            ("/health", "GET"),
            ("/agents", "GET"),
            ("/agents/{agent_id}", "GET"),
            ("/agents/{agent_id}/llm", "PATCH"),
            ("/logs/core", "GET"),
            ("/logs/core/summary", "GET"),
            ("/audit/logs", "GET"),
            ("/tools", "GET"),
        ):
            route = self._route(path, method)
            endpoint = getattr(route, "endpoint", None)
            self.assertEqual(getattr(endpoint, "__module__", None), "octopos_core.router_core_misc")

    def test_system_routes_are_registered_from_router_system(self):
        route = self._route("/admin/runtime/status", "GET")
        endpoint = getattr(route, "endpoint", None)
        self.assertEqual(getattr(endpoint, "__module__", None), "octopos_core.router_system")

    def test_core_journal_summary_extracts_counts_and_signatures(self):
        report = summarize_core_journal_lines(
            [
                "2026-03-23 01:00:00 host octopos-core[123]: INFO startup complete",
                "2026-03-23 01:01:00 host octopos-core[123]: WARN retrying connection",
                "2026-03-23 01:02:00 host octopos-core[123]: ERROR retrying connection",
                "2026-03-23 01:03:00 host octopos-core[123]: ERROR retrying connection",
            ]
        )
        self.assertEqual(report["available"], True)
        self.assertEqual(report["count"], 4)
        self.assertEqual(report["warn_count"], 1)
        self.assertEqual(report["error_count"], 2)
        self.assertEqual(report["first_timestamp"], "2026-03-23 01:00:00")
        self.assertEqual(report["last_timestamp"], "2026-03-23 01:03:00")
        self.assertEqual(report["top_signatures"][0]["signature"], "retrying connection")
        self.assertEqual(report["top_signatures"][0]["count"], 3)

    def test_read_server_name_priority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            toml_path = tmpdir / "conduwuit.toml"
            config_path = tmpdir / "matrix_server_name"
            toml_path.write_text('server_name = "from-toml"\n', encoding="utf-8")

            with mock.patch.dict("os.environ", {}, clear=False):
                self.assertEqual(
                    main._read_server_name(str(toml_path), str(config_path)),
                    "from-toml",
                )

                config_path.write_text("from-config\n", encoding="utf-8")
                self.assertEqual(
                    main._read_server_name(str(toml_path), str(config_path)),
                    "from-config",
                )

                with mock.patch.dict("os.environ", {"OCTOPOS_MATRIX_SERVER_NAME": "from-env"}, clear=False):
                    self.assertEqual(
                        main._read_server_name(str(toml_path), str(config_path)),
                        "from-env",
                    )

            toml_path.unlink()
            config_path.unlink()

            with mock.patch.dict("os.environ", {}, clear=False):
                self.assertEqual(
                    main._read_server_name(str(toml_path), str(config_path)),
                    "octopos-devmaster",
                )

    def test_personal_agent_update_persists_empty_lists(self):
        req = MyAgentUpdateRequest(
            identity="Mein Agent",
            model="gpt-4o",
            fallback_models=[],
            tools=[],
            allowed_agents=[],
            mcp_servers=[],
        )

        agent_data = build_personal_agent_data("personal_till", req)

        self.assertEqual(agent_data["llm"]["fallback_models"], [])
        self.assertEqual(agent_data["tools"], [])
        self.assertEqual(agent_data["allowed_agents"], [])
        self.assertEqual(agent_data["mcp_servers"], [])
        self.assertIn("git.read", agent_data["execution_modes"]["safe"]["permissions"])

    def test_personal_agent_default_execution_modes_include_git_read(self):
        execution_modes = default_personal_agent_execution_modes()
        self.assertEqual(execution_modes["default"], "safe")
        self.assertIn("git.read", execution_modes["safe"]["permissions"])
        self.assertIn("git.issue", execution_modes["safe"]["permissions"])
        self.assertIn("git.write", execution_modes["elevated"]["permissions"])
        self.assertIn("git.push", execution_modes["root"]["permissions"])
        self.assertIn("shell.exec", execution_modes["root"]["permissions"])

    def test_rate_limiter_local_fallback_enforces_and_prunes(self):
        limiter = RateLimiter(
            settings=RateLimitSettings(login_max=2, login_window_s=60, message_max=2, message_window_s=60),
            backend="memory",
            logger=mock.Mock(),
        )

        limiter.check_login("127.0.0.1")
        limiter.check_login("127.0.0.1")
        with self.assertRaises(main.HTTPException) as ctx:
            limiter.check_login("127.0.0.1")
        self.assertEqual(ctx.exception.status_code, 429)

        limiter._login_attempts["198.51.100.1"] = [time.time() - 120]
        limiter.cleanup_local()
        self.assertNotIn("198.51.100.1", limiter._login_attempts)

    def test_rate_limiter_redis_backend_uses_shared_script(self):
        class FakeScript:
            def __init__(self, results):
                self.results = list(results)
                self.calls = []

            def __call__(self, *, keys=None, args=None):
                self.calls.append((keys, args))
                return self.results.pop(0)

        script = FakeScript([1, 0])
        limiter = RateLimiter(
            settings=RateLimitSettings(login_max=1, login_window_s=60, message_max=1, message_window_s=60),
            redis_client=object(),
            redis_script=script,
            logger=mock.Mock(),
        )

        limiter.check_login("203.0.113.10")
        with self.assertRaises(main.HTTPException) as ctx:
            limiter.check_login("203.0.113.10")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(script.calls[0][0][0], "octopos:rate:login:203.0.113.10")

    def test_rate_limiter_redis_failure_falls_back_to_local_state(self):
        script = mock.Mock(side_effect=RuntimeError("redis down"))
        limiter = RateLimiter(
            settings=RateLimitSettings(login_max=1, login_window_s=60, message_max=1, message_window_s=60),
            redis_client=object(),
            redis_script=script,
            logger=mock.Mock(),
        )

        limiter.check_message("alice", "project-x")
        with self.assertRaises(main.HTTPException) as ctx:
            limiter.check_message("alice", "project-x")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertTrue(limiter._redis_failed)
        self.assertGreaterEqual(script.call_count, 1)

    def test_rate_limiter_recovers_after_redis_cooldown(self):
        class FakeScript:
            def __init__(self):
                self.calls = 0

            def __call__(self, *, keys=None, args=None):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("redis down")
                return 1

        script = FakeScript()
        limiter = RateLimiter(
            settings=RateLimitSettings(
                login_max=1,
                login_window_s=60,
                message_max=1,
                message_window_s=60,
                redis_retry_after_s=1,
            ),
            redis_client=object(),
            redis_script=script,
            logger=mock.Mock(),
        )

        limiter.check_login("203.0.113.99")
        self.assertTrue(limiter._redis_failed)
        limiter._redis_failed_at -= 2
        limiter.check_login("203.0.113.99")
        self.assertFalse(limiter._redis_failed)
        self.assertEqual(script.calls, 2)

    def test_append_learning_snapshot_writes_human_readable_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_dir = Path(tmpdir) / "agents" / "personal_test"
            target = append_learning_snapshot(
                agent_dir,
                "Wichtige Erkenntnis: Die Session wird kompaktiert und als Lernnotiz gesichert.",
                source="test.compact",
            )

            self.assertEqual(target, agent_dir / "memory" / "learned-facts.md")
            content = target.read_text(encoding="utf-8")
            self.assertIn("source: test.compact", content)
            self.assertIn("- hash:", content)
            self.assertIn("Wichtige Erkenntnis", content)
            index_path = agent_dir / "memory" / "learning-index.jsonl"
            index_lines = index_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(index_lines), 1)
            record = json.loads(index_lines[0])
            self.assertEqual(record["source"], "test.compact")
            self.assertEqual(record["source_group"], "test")
            self.assertEqual(record["project"], "personal_test")
            self.assertEqual(record["topic"], "chat")
            self.assertIn("session", record["tags"])

    def test_append_learning_snapshot_truncates_long_summaries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_dir = Path(tmpdir) / "agents" / "personal_test"
            long_summary = "A" * 10000
            target = append_learning_snapshot(agent_dir, long_summary, source="test.compact")

            content = target.read_text(encoding="utf-8")
            self.assertIn("[gekürzt]", content)
            self.assertLessEqual(len(content), 2600)

    def test_append_learning_snapshot_skips_exact_duplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_dir = Path(tmpdir) / "agents" / "personal_test"
            summary = "Exakte Wiederholung fuer Dedup"
            first = append_learning_snapshot(agent_dir, summary, source="test.compact")
            second = append_learning_snapshot(agent_dir, summary, source="test.compact")

            self.assertEqual(first, second)
            content = first.read_text(encoding="utf-8")
            self.assertEqual(content.count("Exakte Wiederholung fuer Dedup"), 1)
            index_path = agent_dir / "memory" / "learning-index.jsonl"
            self.assertEqual(len(index_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_platform_overview_includes_supported_and_planned_channels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wks_keys_dir = Path(tmpdir) / "wks-keys"
            wks_keys_dir.mkdir(parents=True, exist_ok=True)
            (wks_keys_dir / "till").write_text("dummy-key", encoding="utf-8")
            overview_users = {
                "till": {
                    "matrix_id": "@till:matrix.local",
                    "wks": {"ip": "192.168.1.50", "ssh_user": "till", "ollama_port": 11434},
                }
            }
            with mock.patch.object(user_integrations, "_wks_connected", return_value=True), \
                mock.patch.object(user_integrations, "discord_client_connected", return_value=True), \
                mock.patch("octopos_core.discord_agent.load_discord_config", return_value={"guild_id": "guild-1", "channel_ids": ["1", "2"]}):
                overview = user_integrations._build_platform_overview("till", overview_users, wks_keys_dir)

        by_platform = {entry["platform"]: entry for entry in overview}
        self.assertTrue(by_platform["matrix"]["supported"])
        self.assertTrue(by_platform["discord"]["supported"])
        self.assertTrue(by_platform["wks"]["supported"])
        self.assertFalse(by_platform["telegram"]["supported"])
        self.assertEqual(by_platform["telegram"]["details"]["status"], "planned")
        self.assertEqual(by_platform["whatsapp"]["details"]["status"], "planned")
        self.assertEqual(by_platform["signal"]["details"]["status"], "planned")
        self.assertTrue(by_platform["discord"]["connected"])

    def test_wks_connected_helper_requires_real_probe_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wks_keys_dir = Path(tmpdir) / "wks-keys"
            wks_keys_dir.mkdir(parents=True, exist_ok=True)
            (wks_keys_dir / "till").write_text("dummy-key", encoding="utf-8")
            wks = {"ip": "192.168.1.50", "ssh_user": "till"}

            with mock.patch("octopos_core.router_user_integrations._sp.run", return_value=SimpleNamespace(returncode=0)) as run_mock:
                self.assertTrue(user_integrations._wks_connected("till", wks, wks_keys_dir))
                run_mock.assert_called_once()

            with mock.patch("octopos_core.router_user_integrations._sp.run", return_value=SimpleNamespace(returncode=1)):
                self.assertFalse(user_integrations._wks_connected("till", wks, wks_keys_dir))

    def test_wks_shell_exec_blocks_destructive_commands(self):
        from octopos_core.tool_registry import WksShellExecTool

        tool = WksShellExecTool()
        with mock.patch("octopos_core.tool_registry._get_wks_config", return_value={"ip": "192.0.2.10", "ssh_user": "till"}):
            result = asyncio.run(tool.execute("till", "personal_till", "rm -rf /"))

        self.assertTrue(result["blocked"])
        self.assertIn("blockiert", result["error"])

    def test_discord_connected_helper_accepts_callable_is_connected(self):
        class CallableDiscordClient:
            def is_connected(self):
                return True

        with mock.patch("octopos_core.tool_registry._discord_clients", {"till": CallableDiscordClient()}):
            self.assertTrue(user_integrations.discord_client_connected("till"))

    def test_learning_prompt_snippet_prioritizes_latest_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_dir = Path(tmpdir) / "agents" / "personal_test"
            memory_dir = agent_dir / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            (memory_dir / "learned-facts.md").write_text(
                "## 2026-01-01T00:00:00+00:00\n- source: old\n\nAlte Erkenntnis\n\n---\n\n"
                "## 2026-01-02T00:00:00+00:00\n- source: new\n\nNeue Erkenntnis\n\n---\n\n",
                encoding="utf-8",
            )

            snippet = build_learning_prompt_snippet(agent_dir, max_entries=1, max_chars=2000)

        self.assertIn("Neue Erkenntnis", snippet)
        self.assertNotIn("Alte Erkenntnis", snippet)

    def test_session_compact_persists_learning_snapshot(self):
        route = next(
            route
            for route in main.app.routes
            if getattr(route, "path", None) == "/agents/{agent_id}/session/compact"
            and "POST" in getattr(route, "methods", set())
        )
        endpoint = getattr(route, "endpoint")

        class FakeMessages:
            async def create(self, **kwargs):
                return SimpleNamespace(content=[SimpleNamespace(text="Zusammenfassung aus dem Test")])

        class FakeAnthropicClient:
            def __init__(self, *args, **kwargs):
                self.messages = FakeMessages()

        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.AsyncAnthropic = FakeAnthropicClient

        with mock.patch.object(main.agent_sessions, "get_context", return_value=[
            {"role": "user", "content": "Hallo"},
            {"role": "assistant", "content": "Antwort"},
        ]), mock.patch.object(main.agent_sessions, "replace_messages") as replace_mock, \
            mock.patch("octopos_core.orchestrator._load_claude_oauth_token", return_value="token"), \
            mock.patch("octopos_core.router_agent_chat.append_learning_snapshot", return_value=Path("/tmp/learned-facts.md")) as append_mock, \
            mock.patch.dict("sys.modules", {"anthropic": fake_anthropic}):
            result = asyncio.run(endpoint("personal_test", _a=("admin", "admin")))

        self.assertTrue(result["compacted"])
        self.assertEqual(result["learning_snapshot"], "/tmp/learned-facts.md")
        append_mock.assert_called_once()
        write_path = append_mock.call_args.args[0]
        self.assertEqual(Path(write_path).name, "personal_test")
        replace_mock.assert_called_once()

    def test_system_prompt_prioritizes_learning_memory_before_general_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_dir = Path(tmpdir)
            (agent_dir / "memory").mkdir(parents=True, exist_ok=True)
            (agent_dir / "memory" / "learned-facts.md").write_text(
                "## 2026-01-02T00:00:00+00:00\n- source: compact\n\nLernfakt\n\n---\n\n",
                encoding="utf-8",
            )
            (agent_dir / "memory" / "project-context.md").write_text(
                "Allgemeiner Kontext",
                encoding="utf-8",
            )
            cfg = AgentConfig.model_validate(
                {
                    "id": "personal_test",
                    "type": "specialist",
                    "identity": "Test",
                    "llm": {"model": "openai-codex/gpt-5.3-codex"},
                }
            )
            cfg.agent_dir = agent_dir
            orchestrator = Orchestrator(mock.MagicMock(), mock.MagicMock(), mock.MagicMock())
            prompt = orchestrator._build_system_prompt(cfg, "Sag mir, was wichtig ist")

        self.assertIn("## Lernfakten (zuletzt)", prompt)
        self.assertLess(prompt.index("## Lernfakten (zuletzt)"), prompt.index("### project-context"))

    def test_upgrade_personal_agent_data_backfills_issue_tool_and_permission(self):
        agent_data = {
            "tools": ["gitea_repo_inspect", "gitea_repo_tree"],
            "execution_modes": {
                "default": "safe",
                "safe": {"permissions": ["git.read"]},
                "elevated": {"permissions": ["git.read", "git.write"]},
                "root": {"permissions": ["git.read", "git.write", "git.push", "shell.exec"]},
            },
        }

        upgraded, changed = main.upgrade_personal_agent_data(agent_data)

        self.assertTrue(changed)
        self.assertIn("gitea_create_issue", upgraded["tools"])
        self.assertIn("git.issue", upgraded["execution_modes"]["safe"]["permissions"])

    def test_gitea_create_issue_tool_uses_repo_reference(self):
        tool = GiteaCreateIssueTool()
        fake_client = mock.Mock(org="octopos")
        fake_client.create_issue_for_repo = mock.AsyncMock(return_value={
            "number": 131,
            "html_url": "http://example.local/octopos/octopos/issues/131",
            "title": "Test issue",
        })

        with mock.patch("octopos_core.gitea.get_gitea_client", return_value=fake_client):
            result = asyncio.run(
                tool.execute(
                    "personal_till",
                    "personal_till",
                    repo="octopos/octopos",
                    title="Test issue",
                    body="Body",
                    labels=["review"],
                )
            )

        self.assertTrue(result["created"])
        self.assertEqual(result["issue_number"], 131)
        fake_client.create_issue_for_repo.assert_awaited_once_with(
            "octopos",
            "octopos",
            "Test issue",
            body="Body",
            labels=["review"],
        )

    def test_gitea_create_issue_tool_rejects_overlong_title(self):
        tool = GiteaCreateIssueTool()
        result = asyncio.run(
            tool.execute(
                "personal_till",
                "personal_till",
                repo="octopos/octopos",
                title="T" * 300,
                body="Body",
            )
        )

        self.assertIn("zu lang", result["error"])

    def test_shell_exec_blocks_nested_destructive_shells(self):
        tool = ShellExecTool()
        result = asyncio.run(tool.execute("till", "personal_till", 'bash -c "rm -rf /"'))

        self.assertTrue(result["blocked"])
        self.assertIn("blockiert", result["error"])

    def test_gitea_comment_issue_tool_uses_repo_reference(self):
        tool = GiteaCommentIssueTool()
        fake_client = mock.Mock(org="octopos")
        fake_client.comment_issue_for_repo = mock.AsyncMock(return_value={
            "id": 99,
            "html_url": "http://example.local/octopos/octopos/issues/1#issuecomment-99",
        })

        with mock.patch("octopos_core.gitea.get_gitea_client", return_value=fake_client):
            result = asyncio.run(
                tool.execute(
                    "personal_till",
                    "personal_till",
                    repo="octopos/octopos",
                    issue_number=138,
                    body="Kommentar",
                )
            )

        self.assertTrue(result["commented"])
        fake_client.comment_issue_for_repo.assert_awaited_once_with(
            "octopos",
            "octopos",
            138,
            "Kommentar",
        )

    def test_gitea_update_issue_tool_can_close_issue(self):
        tool = GiteaUpdateIssueTool()
        fake_client = mock.Mock(org="octopos")
        fake_client.update_issue_for_repo = mock.AsyncMock(return_value={
            "number": 139,
            "html_url": "http://example.local/octopos/octopos/issues/139",
            "state": "closed",
            "title": "Temp placeholder - ignore",
        })

        with mock.patch("octopos_core.gitea.get_gitea_client", return_value=fake_client):
            result = asyncio.run(
                tool.execute(
                    "personal_till",
                    "personal_till",
                    repo="octopos/octopos",
                    issue_number=139,
                    state="closed",
                )
            )

        self.assertTrue(result["updated"])
        self.assertEqual(result["state"], "closed")
        fake_client.update_issue_for_repo.assert_awaited_once_with(
            "octopos",
            "octopos",
            139,
            title=None,
            body=None,
            state="closed",
            labels=None,
        )

    def test_resolve_repo_ref_accepts_url_and_short_forms(self):
        self.assertEqual(
            resolve_repo_ref("http://192.168.178.181:3002/octopos/octopos"),
            ("octopos", "octopos"),
        )
        self.assertEqual(resolve_repo_ref("octopos/octopos"), ("octopos", "octopos"))
        self.assertEqual(resolve_repo_ref("octopos", default_owner="octopos"), ("octopos", "octopos"))

    def test_repo_review_guidance_is_added_for_repo_queries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_dir = Path(tmpdir)
            soul = agent_dir / "soul.md"
            soul.write_text("Basis-Soul", encoding="utf-8")
            cfg = AgentConfig.model_validate(
                {
                    "id": "personal_test",
                    "type": "specialist",
                    "identity": "Test",
                    "soul": "soul.md",
                    "llm": {"model": "openai-codex/gpt-5.3-codex"},
                    "tools": ["gitea_repo_inspect", "gitea_repo_tree", "gitea_repo_file", "git_status"],
                }
            )
            cfg.agent_dir = agent_dir
            orchestrator = Orchestrator(mock.MagicMock(), mock.MagicMock(), mock.MagicMock())
            prompt = orchestrator._build_system_prompt(cfg, "Schau dir das octopos repo an und reviewe die Aenderungen")

        self.assertIn("Repo-Review-Arbeitsrahmen", prompt)
        self.assertIn("gitea_repo_tree", prompt)
        self.assertIn("gitea_repo_file", prompt)

    def test_resolve_git_target_uses_project_heuristics(self):
        client = mock.Mock(org="octopos")
        async def fake_get_repo(owner, repo):
            if (owner, repo) == ("octopos", "octopos"):
                return {"full_name": f"{owner}/{repo}"}
            raise aiohttp.ClientResponseError(
                mock.Mock(real_url="http://test"),
                (),
                status=404,
                message="not found",
            )

        client.get_repo_by_full_name = mock.AsyncMock(side_effect=fake_get_repo)
        target = asyncio.run(resolve_git_target(client, project_id="octopos_dev"))
        self.assertEqual(target["owner"], "octopos")
        self.assertEqual(target["repo"], "octopos")
        self.assertEqual(target["workspace_key"], "octopos__octopos")

    def test_resolve_git_target_fails_on_ambiguous_matches(self):
        client = mock.Mock(org="octopos")

        async def fake_get_repo(owner, repo):
            if repo in {"octopos", "octopos-dev"}:
                return {"full_name": f"{owner}/{repo}"}
            raise aiohttp.ClientResponseError(
                mock.Mock(real_url="http://test"),
                (),
                status=404,
                message="not found",
            )

        client.get_repo_by_full_name = mock.AsyncMock(side_effect=fake_get_repo)

        with self.assertRaises(ValueError) as ctx:
            asyncio.run(resolve_git_target(client, project_id="octopos_dev"))

        self.assertIn("mehrdeutig", str(ctx.exception))
        self.assertIn("octopos/octopos", str(ctx.exception))
        self.assertIn("octopos/octopos-dev", str(ctx.exception))

    def test_git_status_accepts_explicit_repo_reference(self):
        tool = GitStatusTool()
        with mock.patch("octopos_core.gitea.get_gitea_client") as get_client, \
             mock.patch("octopos_core.gitea.resolve_git_target", new=mock.AsyncMock(return_value={
                 "owner": "octopos",
                 "repo": "octopos",
                 "full_name": "octopos/octopos",
                 "workspace_key": "octopos__octopos",
                 "source": "repo",
             })), \
             mock.patch("octopos_core.gitea.GiteaClient.git_workspace", new=mock.AsyncMock(return_value=Path("/tmp/octopos-git/octopos__octopos"))), \
             mock.patch("octopos_core.gitea.GiteaClient._git", new=mock.AsyncMock(side_effect=[
                 ("## main\n", "", 0),
                 ("main\n", "", 0),
             ])):
            get_client.return_value = mock.Mock(org="octopos")
            result = asyncio.run(
                tool.execute("personal_admin", "personal_admin", repo="octopos/octopos")
            )

        self.assertEqual(result["full_name"], "octopos/octopos")
        self.assertEqual(result["branch"], "main")

    def test_gitea_repo_diff_defaults_to_latest_two_commits(self):
        from octopos_core.tool_registry import GiteaRepoDiffTool

        tool = GiteaRepoDiffTool()
        fake_client = mock.Mock(org="octopos")
        fake_client.list_commits = mock.AsyncMock(return_value=[
            {"sha": "headsha123456"},
            {"sha": "basesha654321"},
        ])
        with mock.patch("octopos_core.gitea.get_gitea_client", return_value=fake_client), \
             mock.patch("octopos_core.gitea.GiteaClient.git_workspace", new=mock.AsyncMock(return_value=Path("/tmp/octopos-git/octopos__octopos"))), \
             mock.patch("octopos_core.gitea.GiteaClient._git", new=mock.AsyncMock(side_effect=[
                 ("", "", 0),
                 (" file1 | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)", "", 0),
                 ("diff --git a/file1 b/file1\n--- a/file1\n+++ b/file1\n@@ -1 +1 @@\n-old\n+new\n", "", 0),
             ])):
            result = asyncio.run(tool.execute("personal_admin", "personal_admin", repo="octopos/octopos"))

        self.assertEqual(result["base"], "basesha654321")
        self.assertEqual(result["head"], "headsha123456")
        self.assertIn("file changed", result["stat"])
        self.assertIn("diff --git", result["diff"])

    def test_execute_tool_uses_project_id_override_without_duplicate_kwarg(self):
        orchestrator = Orchestrator(mock.MagicMock(), mock.MagicMock(), mock.MagicMock())
        boss_cfg = AgentConfig.model_validate(
            {
                "id": "personal_test",
                "type": "specialist",
                "identity": "Test",
                "llm": {"model": "openai-codex/gpt-5.3-codex"},
                "tools": ["git_status"],
            }
        )

        tool = mock.AsyncMock()
        asyncio.run(
            orchestrator._execute_tool(
                tool,
                boss_cfg=boss_cfg,
                project_id="personal_test",
                tool_name="git_status",
                tool_input={"project_id": "octopos_dev"},
            )
        )

        tool.execute.assert_awaited_once_with(
            agent_id="personal_test",
            project_id="octopos_dev",
        )

    def test_tool_loop_uses_agent_max_tool_rounds_and_breaks_on_repeat(self):
        orchestrator = Orchestrator(mock.MagicMock(), mock.MagicMock(), mock.MagicMock())
        boss_cfg = AgentConfig.model_validate(
            {
                "id": "personal_test",
                "type": "specialist",
                "identity": "Test",
                "llm": {"model": "openai-codex/gpt-5.3-codex"},
                "tools": ["file_read"],
                "max_tool_rounds": 2,
            }
        )

        repeated_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                item_id="fc_1",
                                function=SimpleNamespace(name="file_read", arguments='{"path":"README.md"}'),
                            )
                        ],
                    )
                )
            ]
        )

        class FakeTool:
            async def execute(self, **kwargs):
                return {"ok": True}

        orchestrator._resolve_allowed_tool = mock.Mock(return_value=FakeTool())
        orchestrator._llm_call = mock.AsyncMock(return_value=repeated_response)
        orchestrator._finalize_tool_loop_response = mock.AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="final summary", tool_calls=None))]
            )
        )

        result, _workers = asyncio.run(
            orchestrator._tool_loop(
                boss_cfg,
                "project-test",
                mock.MagicMock(),
                [{"role": "system", "content": "sys"}],
                repeated_response,
            )
        )

        self.assertEqual(result, "final summary")
        orchestrator._finalize_tool_loop_response.assert_awaited_once()

    def test_project_message_routes_accept_execution_mode_field(self):
        route = self._route("/projects/{project_id}/message", "POST")
        body_param = route.dependant.body_params[0]
        annotation = body_param.field_info.annotation
        fields = set(annotation.model_fields.keys())
        self.assertIn("execution_mode", fields)

    def test_personal_agent_update_reloads_discovery_after_write(self):
        req = MyAgentUpdateRequest(
            identity="Mein Agent",
            soul="# Soul",
            model="gpt-4o",
            fallback_models=[],
            tools=[],
            allowed_agents=[],
            mcp_servers=[],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            agent_dir = Path(tmpdir)
            loader = mock.Mock()

            persist_personal_agent_config(
                agent_dir,
                "personal_till",
                req,
                load_agent_config_direct=loader,
            )

            self.assertTrue((agent_dir / "agent.yaml").exists())
            self.assertEqual((agent_dir / "soul.md").read_text(encoding="utf-8"), "# Soul")
            loader.assert_called_once_with(agent_dir)

    def test_admin_agent_update_persists_empty_lists(self):
        req = CreateAgentRequest(
            id="test_agent",
            type="specialist",
            identity="Test Agent",
            model="gpt-4o",
            fallback_models=[],
            tools=[],
            mcp_servers=[],
        )

        agent_data = build_agent_admin_data(req)

        self.assertEqual(agent_data["llm"]["fallback_models"], [])
        self.assertEqual(agent_data["tools"], [])
        self.assertEqual(agent_data["mcp_servers"], [])

    def test_network_profile_status_detects_unexpected_ports(self):
        with mock.patch.object(main, "_read_network_profile", return_value="minimal"), \
             mock.patch.object(main, "_list_public_listening_ports", return_value={"tcp": [22, 80, 3002, 8008, 445], "udp": [137]}), \
             mock.patch.object(main, "_ufw_status_summary", return_value={"available": True, "active": False, "rules": []}):
            status = main._network_profile_status()

        self.assertEqual(status["profile"], "minimal")
        self.assertIn("ufw_inactive_for_profile", status["deviations"])
        self.assertIn("missing_tcp_rules:22,80,3002,8008", status["deviations"])

    def test_network_profile_status_full_mode_allows_disabled_ufw(self):
        with mock.patch.object(main, "_read_network_profile", return_value="full"), \
             mock.patch.object(main, "_list_public_listening_ports", return_value={"tcp": [22, 80, 445], "udp": [137]}), \
             mock.patch.object(main, "_ufw_status_summary", return_value={"available": True, "active": False, "rules": []}):
            status = main._network_profile_status()

        self.assertEqual(status["profile"], "full")
        self.assertEqual(status["deviations"], [])

    def test_network_profile_status_uses_ufw_rules_not_raw_listeners(self):
        with mock.patch.object(main, "_read_network_profile", return_value="minimal"), \
             mock.patch.object(main, "_list_public_listening_ports", return_value={"tcp": [22, 80, 3002, 8008, 445], "udp": [137]}), \
             mock.patch.object(main, "_ufw_status_summary", return_value={
                 "available": True,
                 "active": True,
                 "rules": [
                     {"rule": "22/tcp", "from": "Anywhere"},
                     {"rule": "80/tcp", "from": "Anywhere"},
                     {"rule": "3002/tcp", "from": "Anywhere"},
                     {"rule": "8008/tcp", "from": "Anywhere"},
                 ],
             }):
            status = main._network_profile_status()

        self.assertEqual(status["allowed"]["tcp"], [22, 80, 3002, 8008])
        self.assertEqual(status["allowed"]["udp"], [])
        self.assertEqual(status["deviations"], [])

    def test_ufw_status_summary_accepts_localized_inactive_status(self):
        class Result:
            returncode = 0
            stdout = "Status: Inaktiv\n"
            stderr = ""

        with mock.patch("subprocess.run", return_value=Result()):
            status = main._ufw_status_summary()

        self.assertEqual(status, {"available": True, "active": False, "rules": []})

    def test_execution_modes_filter_tools_by_permissions(self):
        cfg = AgentConfig.model_validate(
            {
                "id": "personal_test",
                "type": "specialist",
                "identity": "Test",
                "llm": {"model": "gpt-4o-mini"},
                "tools": ["file_read", "file_write", "shell_exec"],
                "execution_modes": {
                    "default": "safe",
                    "safe": {"permissions": ["filesystem.read"]},
                    "elevated": {"permissions": ["filesystem.read", "filesystem.write"]},
                    "root": {"permissions": ["filesystem.read", "filesystem.write", "shell.exec"]},
                },
            }
        )
        orchestrator = Orchestrator(mock.MagicMock(), mock.MagicMock(), mock.MagicMock())

        safe_tools = [tool.id for tool in orchestrator._allowed_tools(cfg, "safe")]
        elevated_tools = [tool.id for tool in orchestrator._allowed_tools(cfg, "elevated")]
        root_tools = [tool.id for tool in orchestrator._allowed_tools(cfg, "root")]

        self.assertEqual(safe_tools, ["file_read"])
        self.assertEqual(elevated_tools, ["file_read", "file_write"])
        self.assertEqual(root_tools, ["file_read", "file_write", "shell_exec"])

    def test_create_personal_agent_writes_default_execution_modes(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch.object(main, "AGENTS_DIR", tmpdir), \
             mock.patch.object(main, "PROJECTS_DIR", tmpdir), \
             mock.patch.object(main.discovery, "_register"), \
             mock.patch.object(main, "audit_log"):
            agent_id = main._create_personal_agent("alice")

            agent_yaml = Path(tmpdir) / agent_id / "agent.yaml"
            raw = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))

        self.assertEqual(raw["execution_modes"]["default"], "safe")
        self.assertIn("git.read", raw["execution_modes"]["safe"]["permissions"])
        self.assertIn("git.write", raw["execution_modes"]["elevated"]["permissions"])
        self.assertIn("git.push", raw["execution_modes"]["root"]["permissions"])
        self.assertIn("shell.exec", raw["execution_modes"]["root"]["permissions"])

    def test_tool_loop_preserves_codex_item_id_in_history(self):
        cfg = AgentConfig.model_validate(
            {
                "id": "personal_test",
                "type": "specialist",
                "identity": "Test",
                "llm": {"model": "openai-codex/gpt-5.3-codex"},
                "tools": ["file_read"],
            }
        )
        orchestrator = Orchestrator(mock.MagicMock(), mock.MagicMock(), mock.MagicMock())
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call_123",
                                item_id="fc_123",
                                function=SimpleNamespace(name="file_read", arguments='{"path":"README.md"}'),
                            )
                        ],
                    )
                )
            ]
        )

        class FakeTool:
            async def execute(self, **kwargs):
                return {"ok": True}

        captured = {}

        async def fake_llm_call(agent_cfg, messages, tools=None):
            captured["messages"] = messages
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))]
            )

        orchestrator._resolve_allowed_tool = mock.Mock(return_value=FakeTool())
        orchestrator._llm_call = fake_llm_call

        import asyncio
        result, _workers = asyncio.run(
            orchestrator._tool_loop(
                cfg,
                "project-test",
                mock.MagicMock(),
                [],
                response,
            )
        )

        self.assertEqual(result, "done")
        assistant_msg = next(m for m in captured["messages"] if m.get("role") == "assistant")
        self.assertEqual(assistant_msg["tool_calls"][0]["id"], "call_123")
        self.assertEqual(assistant_msg["tool_calls"][0]["item_id"], "fc_123")

    def test_openai_codex_call_repairs_missing_item_id_from_history(self):
        cfg = AgentConfig.model_validate(
            {
                "id": "personal_test",
                "type": "specialist",
                "identity": "Test",
                "llm": {"model": "openai-codex/gpt-5.3-codex"},
            }
        )
        orchestrator = Orchestrator(mock.MagicMock(), mock.MagicMock(), mock.MagicMock())
        captured = {}

        class FakeStream:
            def __init__(self, payload):
                self.payload = payload
                self.status_code = 200

            async def __aenter__(self):
                captured["payload"] = self.payload
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def aiter_lines(self):
                if False:
                    yield ""
                return

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, method, url, headers=None, json=None):
                return FakeStream(json)

        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_legacy123",
                        "type": "function",
                        "function": {"name": "file_read", "arguments": '{"path":"README.md"}'},
                    }
                ],
            }
        ]

        with mock.patch("httpx.AsyncClient", FakeClient):
            import asyncio
            asyncio.run(
                orchestrator._openai_codex_call(
                    cfg,
                    messages,
                    tools=None,
                    token_data={"access_token": "x", "account_id": "y"},
                    model_name="openai-codex/gpt-5.3-codex",
                )
            )

        function_call = captured["payload"]["input"][0]
        self.assertEqual(function_call["id"], "fc_legacy123")
        self.assertEqual(function_call["call_id"], "call_legacy123")


if __name__ == "__main__":
    unittest.main()
