import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

import yaml

from octopos_core import main
from octopos_core.agent_config import AgentConfig
from octopos_core.execution_mode_policy import resolve_request_execution_mode
from octopos_core.gitea import resolve_repo_ref
from octopos_core.orchestrator import Orchestrator
from octopos_core.router_agent_admin import CreateAgentRequest, build_agent_admin_data
from octopos_core.router_users import (
    MyAgentUpdateRequest,
    build_personal_agent_data,
    default_personal_agent_execution_modes,
    persist_personal_agent_config,
)


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
            ("/audit/logs", "GET"),
            ("/tools", "GET"),
        ):
            route = self._route(path, method)
            endpoint = getattr(route, "endpoint", None)
            self.assertEqual(getattr(endpoint, "__module__", None), "octopos_core.router_core_misc")

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
        self.assertIn("git.write", execution_modes["elevated"]["permissions"])
        self.assertIn("git.push", execution_modes["root"]["permissions"])
        self.assertIn("shell.exec", execution_modes["root"]["permissions"])

    def test_resolve_repo_ref_accepts_url_and_short_forms(self):
        self.assertEqual(
            resolve_repo_ref("http://192.168.178.181:3002/octopos/octopos"),
            ("octopos", "octopos"),
        )
        self.assertEqual(resolve_repo_ref("octopos/octopos"), ("octopos", "octopos"))
        self.assertEqual(resolve_repo_ref("octopos", default_owner="octopos"), ("octopos", "octopos"))

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
