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

    # Konfig-Dateien
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
    def scripts_dir(self) -> Path:
        return self.opt_dir / "scripts"

    @property
    def installer_dir(self) -> Path:
        return self.opt_dir / "installer"

    @property
    def searxng_install_script(self) -> Path:
        return self.installer_dir / "modules" / "14_searxng.sh"

    # Sonstige Settings
    matrix_server_name: str = ""
    console_url: str = "http://192.168.178.181"
    openai_api_key: str = "hydrahive"
    search_alpha: float = 0.5
    embedding_model: str = ""
    rate_limit_backend: str = "auto"
    rate_limit_redis_url: str = ""
    rate_limit_redis_timeout_s: float = 0.5


# Singleton
settings = HydraHiveSettings()
