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
        with pytest.raises(ValueError):
            rui._username_from_auth(("../evil", "token"))

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
