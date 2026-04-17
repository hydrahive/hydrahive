"""#685: prepare_host_key_policy matrix + helper Tests.

Deckt die vier Kombinationen (warn/strict × verified/unverified) ab plus
temp-known_hosts-Format und stderr-Mismatch-Erkennung.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import ssh_known_hosts as skh


@pytest.fixture
def skh_state(monkeypatch):
    """Überschreibt get_verified_keys + get_enforcement_mode pro Case."""
    state = {"mode": "warn", "verified_keys": []}

    monkeypatch.setattr(skh, "get_enforcement_mode", lambda: state["mode"])
    monkeypatch.setattr(skh, "get_verified_keys", lambda t, i: list(state["verified_keys"]))
    return state


VERIFIED_KEY = {
    "algorithm": "ssh-ed25519",
    "public_key": "AAAAC3NzaC1lZDI1NTE5AAAAIexample",
    "fingerprint_sha256": "SHA256:aaaa1111bbbb2222",
}


class TestPreparePolicyMatrix:
    def test_warn_unverified_passthrough(self, skh_state):
        """warn + keine verified Keys → SSH läuft ohne Pinning, Flag gesetzt."""
        skh_state["mode"] = "warn"
        skh_state["verified_keys"] = []
        p = skh.prepare_host_key_policy("wks", "alice", "10.0.0.1")
        assert p.blocked is False
        assert p.verified is False
        assert p.host_key_unverified is True
        assert p.enforcement == "warn"
        assert p.known_hosts_path is None
        assert "StrictHostKeyChecking=no" in p.ssh_opts
        assert "UserKnownHostsFile=/dev/null" in " ".join(p.ssh_opts)
        assert "LogLevel=ERROR" in p.ssh_opts

    def test_strict_unverified_blocks(self, skh_state):
        """strict + keine verified Keys → blocked + user-facing reason."""
        skh_state["mode"] = "strict"
        skh_state["verified_keys"] = []
        p = skh.prepare_host_key_policy("wks", "alice", "10.0.0.1")
        assert p.blocked is True
        assert p.blocked_reason  # non-empty
        assert "Admin" in p.blocked_reason
        assert p.known_hosts_path is None
        assert p.ssh_opts == []

    def test_warn_verified_pins(self, skh_state, tmp_path):
        """warn + verified Key → temp known_hosts + StrictHostKeyChecking=yes."""
        skh_state["mode"] = "warn"
        skh_state["verified_keys"] = [VERIFIED_KEY]
        p = skh.prepare_host_key_policy("wks", "alice", "10.0.0.1")
        try:
            assert p.blocked is False
            assert p.verified is True
            assert p.host_key_unverified is False
            assert p.known_hosts_path is not None
            assert Path(p.known_hosts_path).exists()
            content = Path(p.known_hosts_path).read_text()
            assert "10.0.0.1" in content
            assert "ssh-ed25519" in content
            assert "StrictHostKeyChecking=yes" in p.ssh_opts
            assert f"UserKnownHostsFile={p.known_hosts_path}" in p.ssh_opts
        finally:
            skh.cleanup_known_hosts_path(p.known_hosts_path)

    def test_strict_verified_pins(self, skh_state):
        """strict + verified Key → gepinnt, nicht blocked."""
        skh_state["mode"] = "strict"
        skh_state["verified_keys"] = [VERIFIED_KEY]
        p = skh.prepare_host_key_policy("wks", "alice", "10.0.0.1")
        try:
            assert p.blocked is False
            assert p.verified is True
            assert p.enforcement == "strict"
            assert "StrictHostKeyChecking=yes" in p.ssh_opts
        finally:
            skh.cleanup_known_hosts_path(p.known_hosts_path)


class TestTempKnownHostsFormat:
    def test_writes_ssh_known_hosts_line(self, tmp_path):
        keys = [VERIFIED_KEY]
        path = skh._write_temp_known_hosts("10.0.0.1", keys)
        try:
            text = Path(path).read_text()
            # OpenSSH-Format: "<host> <algo> <pubkey>"
            assert text.startswith("10.0.0.1 ssh-ed25519 ")
            assert VERIFIED_KEY["public_key"] in text
        finally:
            os.unlink(path)

    def test_empty_keys_writes_empty_file(self):
        path = skh._write_temp_known_hosts("10.0.0.1", [])
        try:
            assert Path(path).read_text() == ""
        finally:
            os.unlink(path)

    def test_chmod_0600(self):
        path = skh._write_temp_known_hosts("10.0.0.1", [VERIFIED_KEY])
        try:
            mode = os.stat(path).st_mode & 0o777
            assert mode == 0o600
        finally:
            os.unlink(path)


class TestHostKeyChangedDetection:
    @pytest.mark.parametrize("stderr,expected", [
        ("Host key verification failed.", True),
        ("WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!", True),
        ("Offending ED25519 key in /root/.ssh/known_hosts:3", True),
        ("Permission denied (publickey).", False),
        ("Connection timed out", False),
        ("", False),
    ])
    def test_match_keywords(self, stderr, expected):
        assert skh.is_host_key_changed(stderr) is expected


class TestCleanup:
    def test_unlinks_existing_path(self):
        path = skh._write_temp_known_hosts("10.0.0.1", [VERIFIED_KEY])
        assert Path(path).exists()
        skh.cleanup_known_hosts_path(path)
        assert not Path(path).exists()

    def test_none_is_noop(self):
        # Darf nicht werfen
        skh.cleanup_known_hosts_path(None)

    def test_missing_file_is_noop(self, tmp_path):
        missing = str(tmp_path / "does-not-exist")
        skh.cleanup_known_hosts_path(missing)  # swallows FileNotFoundError
