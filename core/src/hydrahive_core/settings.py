"""
settings.py — HydraHive Zentrale Konfiguration (#402)

Alle Pfade und Umgebungsvariablen an einem Ort.
Überschreibbar via .env oder Environment-Variables.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class HydraHiveSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HYDRAHIVE_",
        env_file="/etc/hydrahive/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Basis-Verzeichnisse
    etc_dir: Path = Path("/etc/hydrahive")
    opt_dir: Path = Path("/opt/hydrahive")
    agents_dir: Path = Path("/agents")
    projects_dir: Path = Path("/projects")

    # ── Konfig-Dateien (etc_dir) ──────────────────────────────────────────

    @property
    def llm_config(self) -> Path:
        return self.etc_dir / "llm_config.json"

    @property
    def gitea_config(self) -> Path:
        return self.etc_dir / "gitea_config.json"

    @property
    def github_token_file(self) -> Path:
        return self.etc_dir / "github_token"

    @property
    def repos_config(self) -> Path:
        return self.etc_dir / "repos.json"

    @property
    def tailscale_config(self) -> Path:
        return self.etc_dir / "tailscale.json"

    @property
    def mcp_servers_config(self) -> Path:
        return self.etc_dir / "mcp_servers.json"

    @property
    def agentlink_config(self) -> Path:
        return self.etc_dir / "agentlink.json"

    @property
    def users_config(self) -> Path:
        return self.etc_dir / "users.json"

    @property
    def groups_config(self) -> Path:
        return self.etc_dir / "groups.json"

    @property
    def schedules_config(self) -> Path:
        return self.etc_dir / "schedules.json"

    @property
    def notification_routes_config(self) -> Path:
        return self.etc_dir / "notification_routes.json"

    @property
    def agent_secrets_config(self) -> Path:
        return self.etc_dir / "agent_secrets.json"

    @property
    def agent_tokens_dir(self) -> Path:
        return self.etc_dir / "agent_tokens"

    @property
    def alerts_config(self) -> Path:
        return self.etc_dir / "alerts.json"

    @property
    def voice_config(self) -> Path:
        return self.etc_dir / "voice.json"

    @property
    def vpn_config(self) -> Path:
        return self.etc_dir / "vpn.json"

    @property
    def a2a_peers_config(self) -> Path:
        return self.etc_dir / "a2a_peers.json"

    @property
    def user_app_cfg_dir(self) -> Path:
        return self.etc_dir / "user_app_config"

    @property
    def butler_webhooks_config(self) -> Path:
        return self.etc_dir / "butler_webhooks.json"

    @property
    def kas_config(self) -> Path:
        return self.etc_dir / "kas.json"

    @property
    def admin_credentials(self) -> Path:
        return self.etc_dir / "admin_credentials"

    @property
    def claude_oauth_token(self) -> Path:
        return self.etc_dir / "claude_oauth_token"

    @property
    def openai_codex_token(self) -> Path:
        return self.etc_dir / "openai_codex_token.json"

    @property
    def llm_env(self) -> Path:
        return self.etc_dir / "llm_env"

    @property
    def jwt_secret_file(self) -> Path:
        return self.etc_dir / "jwt_secret"

    @property
    def internal_secret_file(self) -> Path:
        return self.etc_dir / "internal_secret"

    @property
    def network_profile_file(self) -> Path:
        return self.etc_dir / "network_profile"

    @property
    def setup_wizard_done(self) -> Path:
        return self.etc_dir / "setup_wizard_done"

    @property
    def dashboard_dir(self) -> Path:
        return self.etc_dir / "dashboard"

    @property
    def servers_dir(self) -> Path:
        return self.etc_dir / "servers"

    @property
    def server_keys_dir(self) -> Path:
        return self.etc_dir / "server_keys"

    @property
    def agent_servers_config(self) -> Path:
        return self.etc_dir / "agent_servers.json"

    @property
    def project_targets_config(self) -> Path:
        """#584-A: Projekt-Target-Zuweisungen (Server + WKS pro Projekt)."""
        return self.etc_dir / "project_targets.json"

    @property
    def wks_keys_dir(self) -> Path:
        return self.etc_dir / "wks_keys"

    @property
    def worktrees_dir(self) -> Path:
        """Basisverzeichnis für Sub-Agent-Worktrees (#651).

        Layout:
          <worktrees_dir>/trees/<id>/    Git-Worktree
          <worktrees_dir>/meta/<id>.json Metadaten

        Override via Env HYDRAHIVE_WORKTREES_DIR (wird von
        subagent_worktrees direkt geprüft).
        """
        import os
        override = os.environ.get("HYDRAHIVE_WORKTREES_DIR")
        if override:
            return Path(override)
        return Path("/var/lib/hydrahive/worktrees")

    @property
    def settings_file(self) -> Path:
        """Pfad zu settings.json (Hook-Konfiguration, #654).

        Override via Env HYDRAHIVE_SETTINGS_FILE (wird von
        hook_settings.load_hook_settings() direkt geprüft).
        """
        import os
        override = os.environ.get("HYDRAHIVE_SETTINGS_FILE")
        if override:
            return Path(override)
        return self.etc_dir / "settings.json"

    @property
    def use_local_gitea_file(self) -> Path:
        return self.etc_dir / "use_local_gitea"

    @property
    def amem_config(self) -> Path:
        return self.etc_dir / "amem_config.json"

    @property
    def cleanup_config(self) -> Path:
        return self.etc_dir / "cleanup.json"

    @property
    def mail_seen_ids(self) -> Path:
        return self.etc_dir / "mail_seen_ids.json"

    @property
    def butler_dir(self) -> Path:
        return self.etc_dir / "butler"

    @property
    def clawhub_config(self) -> Path:
        return self.etc_dir / "clawhub.json"

    @property
    def invites_config(self) -> Path:
        return self.etc_dir / "invites.json"

    @property
    def pipelines_dir(self) -> Path:
        return self.etc_dir / "pipelines"

    @property
    def plugin_state(self) -> Path:
        return self.etc_dir / "plugin_state.json"

    @property
    def samba_credentials(self) -> Path:
        return self.etc_dir / "samba_credentials"

    @property
    def skill_packages_dir(self) -> Path:
        return self.etc_dir / "skill_packages"

    @property
    def skills_catalog_dir(self) -> Path:
        """Curated Quelle für `/skill install` (#658). Read-only, Admin-befüllt."""
        return self.opt_dir / "skills" / "catalog"

    @property
    def users_data_dir(self) -> Path:
        """Basis für per-User mutable Daten (#659). Layout:

          <users_data_dir>/<username>/skills/<name>.md
        """
        return Path("/var/lib/hydrahive/users")

    def user_skills_dir(self, username: str) -> Path:
        """User-globaler Skill-Ordner (#659). Wirft ValueError bei ungültigem
        Username. Verzeichnis wird NICHT automatisch angelegt."""
        from .skill_resolver import validate_username
        validate_username(username)
        return self.users_data_dir / username / "skills"

    @property
    def system_handbook(self) -> Path:
        return self.etc_dir / "system_handbook.md"

    @property
    def matrix_server_name_file(self) -> Path:
        return self.etc_dir / "matrix_server_name"

    @property
    def matrix_registration_token(self) -> Path:
        return self.etc_dir / "matrix_registration_token"

    # ── opt_dir Pfade ─────────────────────────────────────────────────────

    @property
    def scripts_dir(self) -> Path:
        return self.opt_dir / "scripts"

    @property
    def installer_dir(self) -> Path:
        return self.opt_dir / "installer"

    @property
    def searxng_install_script(self) -> Path:
        return self.installer_dir / "modules" / "14_searxng.sh"

    @property
    def backups_dir(self) -> Path:
        return self.opt_dir / "backups"

    @property
    def network_profile_script(self) -> Path:
        return self.opt_dir / "apply-network-profile.sh"

    @property
    def whatsapp_bridge_dir(self) -> Path:
        return self.opt_dir / "whatsapp-bridge"

    @property
    def voice_install_dir(self) -> Path:
        return Path("/opt/hydrahive-voice")

    # ── Sonstige Settings ─────────────────────────────────────────────────

    matrix_server_name: str = ""
    console_url: str = ""
    openai_api_key: str = "hydrahive"
    search_alpha: float = 0.5
    embedding_model: str = ""
    rate_limit_backend: str = "auto"
    rate_limit_redis_url: str = ""
    rate_limit_redis_timeout_s: float = 0.5

    # #520/#522: Feature-Flags für P0-Architektur
    boss_policy_enabled: bool = False      # Auto-Verification nach Mutations
    worktree_isolation: bool = False       # Git-Worktrees für Worker-Tasks


# Singleton
settings = HydraHiveSettings()
