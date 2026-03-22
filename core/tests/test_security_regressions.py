import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
