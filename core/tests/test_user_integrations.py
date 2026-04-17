"""
test_user_integrations.py — Tests für router_user_integrations Guards

Deckt ab:
- agent_id / username Path-Traversal-Schutz (_sanitize_agent_id)
- DiscordConfigRequest Pydantic-Validierung (guild_id, channel_ids, ID-Listen)
- Token-Datei Berechtigungen (chmod 600)
- load_discord_config bei fehlendem Verzeichnis → None
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import hydrahive_core.router_user_integrations as rui
from hydrahive_core import discord_agent


# ============================================================= Agent-ID Sanitizing

class TestSanitizeAgentId:

    VALID = ["alice", "bob123", "my-agent", "agent_v2", "a"]
    INVALID = ["../admin", "../../etc/passwd", "Alice", "foo bar", "", "foo/bar", "foo!bar"]

    @pytest.mark.parametrize("agent_id", VALID)
    def test_gueltige_ids_akzeptiert(self, agent_id):
        assert rui._sanitize_agent_id(agent_id) == agent_id

    @pytest.mark.parametrize("agent_id", INVALID)
    def test_ungueltige_ids_werfen_exception(self, agent_id):
        with pytest.raises(ValueError):
            rui._sanitize_agent_id(agent_id)

    def test_username_from_auth_delegiert_sanitize(self):
        with pytest.raises(ValueError, match="Ungültiger Username"):
            rui._sanitize_username("../evil")

    def test_username_from_auth_valide(self):
        assert rui._username_from_auth(("alice", "token")) == "alice"


# ============================================================= DiscordConfigRequest Validierung

class TestDiscordConfigValidation:

    def test_leere_felder_akzeptiert(self):
        req = rui.DiscordConfigRequest()
        assert req.guild_id == ""
        assert req.channel_ids == []

    def test_gueltige_guild_id(self):
        req = rui.DiscordConfigRequest(guild_id="123456789012345678")
        assert req.guild_id == "123456789012345678"

    def test_ungueltige_guild_id_buchstaben(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            rui.DiscordConfigRequest(guild_id="abc")

    def test_ungueltige_guild_id_zu_lang(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            rui.DiscordConfigRequest(guild_id="1" * 21)

    def test_gueltige_channel_ids(self):
        req = rui.DiscordConfigRequest(channel_ids=["123456789012345", "987654321098765"])
        assert len(req.channel_ids) == 2

    def test_ungueltige_channel_id_text(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            rui.DiscordConfigRequest(channel_ids=["../evil"])

    def test_ungueltige_channel_id_zu_lang(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            rui.DiscordConfigRequest(channel_ids=["1" * 21])

    def test_user_whitelist_nur_numerisch(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            rui.DiscordConfigRequest(user_whitelist=["not-a-number"])

    def test_role_blacklist_nur_numerisch(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            rui.DiscordConfigRequest(role_blacklist=["../../etc"])

    def test_gueltige_listen(self):
        req = rui.DiscordConfigRequest(
            user_whitelist=["111111111111111"],
            role_blacklist=["222222222222222"],
        )
        assert req.user_whitelist == ["111111111111111"]
        assert req.role_blacklist == ["222222222222222"]

    def test_whitespace_wird_gestrippt(self):
        req = rui.DiscordConfigRequest(guild_id="  123456789012345  ")
        assert req.guild_id == "123456789012345"


# ============================================================= Token-Datei Berechtigungen

class TestTokenFilePermissions:

    def test_save_setzt_chmod_600(self, tmp_path, monkeypatch):
        monkeypatch.setattr(discord_agent, "TOKEN_DIR", tmp_path)
        discord_agent.save_discord_config("testagent", {"bot_token": "secret"})
        token_file = tmp_path / "testagent_discord.json"
        assert token_file.exists()
        mode = token_file.stat().st_mode & 0o777
        assert mode == 0o600, f"Erwartet 600, bekommen {oct(mode)}"

    def test_save_erstellt_verzeichnis(self, tmp_path, monkeypatch):
        target = tmp_path / "agent_tokens"
        monkeypatch.setattr(discord_agent, "TOKEN_DIR", target)
        discord_agent.save_discord_config("testagent", {"bot_token": "x"})
        assert target.exists()


# ============================================================= Fehlerfall: fehlendes Verzeichnis

class TestMissingTokenDir:

    def test_load_returns_none_bei_fehlendem_dir(self, tmp_path, monkeypatch):
        missing = tmp_path / "agent_tokens"
        monkeypatch.setattr(discord_agent, "TOKEN_DIR", missing)
        assert missing.exists() is False
        result = discord_agent.load_discord_config("anyagent")
        assert result is None

    def test_load_returns_none_bei_leerer_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(discord_agent, "TOKEN_DIR", tmp_path)
        token_file = tmp_path / "testagent_discord.json"
        token_file.write_text("{}")
        assert discord_agent.load_discord_config("testagent") is None

    def test_load_returns_none_bei_fehlendem_token(self, tmp_path, monkeypatch):
        monkeypatch.setattr(discord_agent, "TOKEN_DIR", tmp_path)
        token_file = tmp_path / "testagent_discord.json"
        token_file.write_text('{"bot_token": ""}')
        assert discord_agent.load_discord_config("testagent") is None


# ============================================================= WKS host-key policy (#685)

class TestWksConnectedHostKeyEnforcement:
    """#685: _wks_connected erkennt host-key-Policy.

    - strict + kein verified Key → fail-closed (kein SSH-Call).
    - warn + kein verified Key → SSH läuft kompatibel (Alt-Verhalten).
    - verified + exit_code!=0 mit Host-Key-Mismatch → False + Log-Warning.
    """

    @pytest.fixture
    def wks_env(self, tmp_path):
        """Legt wks-keys-Dir + dummy Key für user 'till' an."""
        wks_keys_dir = tmp_path / "wks-keys"
        wks_keys_dir.mkdir()
        (wks_keys_dir / "till").write_text("dummy-key-material", encoding="utf-8")
        return {
            "wks": {"ip": "10.0.0.50", "ssh_user": "till", "ssh_port": 22},
            "wks_keys_dir": wks_keys_dir,
        }

    @pytest.fixture
    def skh_state(self, monkeypatch):
        from hydrahive_core import ssh_known_hosts as skh
        state = {"mode": "warn", "verified_keys": []}
        monkeypatch.setattr(skh, "get_enforcement_mode", lambda: state["mode"])
        monkeypatch.setattr(skh, "get_verified_keys", lambda t, i: list(state["verified_keys"]))
        return state

    def test_strict_unverified_blocks_without_ssh_call(self, wks_env, skh_state, monkeypatch):
        skh_state["mode"] = "strict"
        skh_state["verified_keys"] = []
        sp_calls = []
        monkeypatch.setattr(rui._sp, "run", lambda *a, **kw: sp_calls.append((a, kw)) or None)

        assert rui._wks_connected("till", wks_env["wks"], wks_env["wks_keys_dir"]) is False
        # Kein SSH-Call angestoßen, weil policy.blocked=True
        assert sp_calls == []

    def test_warn_unverified_runs_ssh(self, wks_env, skh_state, monkeypatch):
        from types import SimpleNamespace
        skh_state["mode"] = "warn"
        skh_state["verified_keys"] = []
        sp_calls = []

        def fake_run(args, **kw):
            sp_calls.append(args)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(rui._sp, "run", fake_run)

        assert rui._wks_connected("till", wks_env["wks"], wks_env["wks_keys_dir"]) is True
        assert len(sp_calls) == 1
        # warn-Mode: StrictHostKeyChecking=no mitgegeben
        assert "StrictHostKeyChecking=no" in sp_calls[0]

    def test_verified_host_key_changed_returns_false(self, wks_env, skh_state, monkeypatch):
        from types import SimpleNamespace
        skh_state["mode"] = "warn"
        skh_state["verified_keys"] = [{
            "algorithm": "ssh-ed25519",
            "public_key": "AAAAC3NzaC1lZDI1NTE5AAAAIexample",
            "fingerprint_sha256": "SHA256:aaaa1111bbbb2222",
        }]

        def fake_run(args, **kw):
            return SimpleNamespace(
                returncode=255,
                stdout="",
                stderr="@@@@@@@\nHost key verification failed.\n",
            )

        monkeypatch.setattr(rui._sp, "run", fake_run)

        assert rui._wks_connected("till", wks_env["wks"], wks_env["wks_keys_dir"]) is False

    def test_verified_pins_with_temp_known_hosts(self, wks_env, skh_state, monkeypatch):
        from types import SimpleNamespace
        skh_state["mode"] = "warn"
        skh_state["verified_keys"] = [{
            "algorithm": "ssh-ed25519",
            "public_key": "AAAAC3NzaC1lZDI1NTE5AAAAIexample",
            "fingerprint_sha256": "SHA256:aaaa1111bbbb2222",
        }]
        args_seen = []

        def fake_run(args, **kw):
            args_seen.extend(args)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(rui._sp, "run", fake_run)

        assert rui._wks_connected("till", wks_env["wks"], wks_env["wks_keys_dir"]) is True
        assert "StrictHostKeyChecking=yes" in args_seen
        path_entries = [a for a in args_seen if isinstance(a, str) and a.startswith("UserKnownHostsFile=")]
        assert len(path_entries) == 1

    def test_missing_wks_config_returns_false(self, tmp_path, skh_state):
        # Kein IP → False vor Policy-Resolution
        assert rui._wks_connected("till", {"ip": ""}, tmp_path) is False
