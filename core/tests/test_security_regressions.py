import tempfile
import unittest
from pathlib import Path
from unittest import mock

from octopos_core import main


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


if __name__ == "__main__":
    unittest.main()
