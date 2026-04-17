"""
main.py — HydraHive Core Runtime Einstiegspunkt (#4, #6, #7, #8, #9, #10, #11, #12, #35)

FastAPI-App mit Lifespan-Management:
- AgentDiscovery + AgentRuntime + ProjectLoader + SessionManager + Orchestrator
- REST-Endpoints fuer Agenten, Projekte, Sessions und Nachrichten
"""

import hmac
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncio

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .agent_config import AgentConfig, load_agent_config
from .agent_discovery import AgentDiscovery
from .agent_runtime import AgentRuntime
from .matrix_agent import BossMatrixAgent
from .orchestrator import Orchestrator
from .project_config import ProjectConfig
from .project_loader import ProjectLoader
from .provisioner import Provisioner, get_admin_access_token
from .rate_limiter import RateLimiter
from .router_agent_chat import register_agent_chat_routes
from .router_agent_admin import register_agent_admin_routes
from .router_agent_skills import register_agent_skill_routes
from .router_jobs import register_jobs_routes
from .router_skills_catalog import register_skills_catalog_routes
from .router_backup_restore import register_backup_restore_routes
from .router_composer import register_admin_composer_routes, register_composer_routes, register_project_composer_routes
from .router_adminfun import register_adminfun_routes
from .router_migration import register_migration_routes
from .router_vpn import register_vpn_routes
from .router_doctor import register_doctor_routes
from .router_searxng import register_searxng_routes
from .router_knowledge import register_knowledge_routes
from .router_codeserver import register_codeserver_routes
from .router_extensions import register_extension_routes
from .router_butler import register_butler_routes
from .router_webhooks_butler import register_webhook_butler_routes
from .router_skill_packages import register_skill_package_routes
from .router_a2a import register_a2a_routes
from .router_agent_secrets import register_agent_secret_routes
from .router_brain import register_brain_routes
from .router_notifications import register_notification_routes
from .notification_service import notification_service
from .router_schedules import register_schedule_routes
from .scheduler_service import scheduler_service
from .router_core_misc import register_core_misc_routes
from .router_voice import register_voice_routes
from .router_llm import register_llm_routes
from .router_openai_compat import register_openai_compat_routes
from .router_mcp import register_mcp_routes
from .router_project_integrations import register_project_integration_routes
from .router_project_lifecycle import register_project_lifecycle_routes, update_project_matrix_room, update_project_matrix_space
from .router_projects import register_project_routes
from .router_system import register_system_routes
from .router_usage import register_usage_routes
from .router_hub import register_hub_routes
from .router_user_integrations import register_user_integration_routes, setup_discord_clients
from .whatsapp_agent import setup_whatsapp_sessions
from .router_invites import register_invite_routes
from .router_github import register_github_routes
# v2: Plugin-System entfernt — alles über shell_exec
from .router_tailscale import register_tailscale_routes
from .router_servers import register_server_routes
from .router_repos import register_repo_routes
from .router_config_map import register_config_map_routes
# v2: plugin_manager entfernt
from .router_pipelines import register_pipeline_routes, load_all_pipelines, load_pipeline
from .group_service import GroupService
from .router_groups import register_group_routes
from .pipeline_executor import execute_pipeline, get_watch_folders
from .folder_watcher import run_folder_watcher
from .router_users import (
    default_personal_agent_execution_modes,
    register_user_routes,
    upgrade_personal_agent_data,
)
from .session_manager import MessageRole, SessionManager
from .settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# #449: PII-Stripping Filter für Logs
import re as _re_log
_PII_PATTERNS = [
    (_re_log.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '[IP]'),          # IPv4
    (_re_log.compile(r'\b(?:Bearer|token)\s+[A-Za-z0-9._-]{20,}\b', _re_log.I), '[TOKEN]'),  # Bearer tokens
    (_re_log.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), '[EMAIL]'),    # E-Mail
    (_re_log.compile(r'(?:password|passwd|secret|api_key)\s*[=:]\s*\S+', _re_log.I), '[REDACTED]'),
]

class _PIIFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.msg, str):
            for pattern, replacement in _PII_PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        return True

logging.getLogger().addFilter(_PIIFilter())
logger = logging.getLogger(__name__)

# Optionales Sentry Error-Tracking — nur aktiv wenn SENTRY_DSN gesetzt
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[StarletteIntegration(), FastApiIntegration()],
            traces_sample_rate=0.1,
            send_default_pii=False,
        )
        logger.info("Sentry Error-Tracking aktiviert")
    except ImportError:
        logger.warning("SENTRY_DSN gesetzt aber sentry-sdk nicht installiert — pip install sentry-sdk[fastapi]")

AGENTS_DIR   = str(settings.agents_dir)
PROJECTS_DIR = str(settings.projects_dir)

CRED_FILE        = str(settings.admin_credentials)
MCP_SERVERS_FILE = str(settings.mcp_servers_config)
JWT_SECRET   = ""    # wird im Lifespan aus Datei geladen oder generiert
JWT_ALG      = "HS256"
JWT_EXPIRE_H = 24    # Token-Gültigkeit in Stunden
APP_VERSION  = "0.1.0"

rate_limiter = RateLimiter.from_env(logger)

# Internes Shared-Secret für Core-interne Calls (z.B. AskAgentTool → /agents/{id}/message)
# Wird im Lifespan aus Datei geladen oder einmalig generiert (persistiert → überlebt Restarts).
_INTERNAL_SECRET = ""   # gesetzt im Lifespan via _load_or_create_internal_secret()


def _check_login_rate(ip: str) -> None:
    rate_limiter.check_login(ip)


def _check_message_rate(user_id: str, project_id: str) -> None:
    """Rate-Limit fuer Nachrichten pro User+Projekt"""
    rate_limiter.check_message(user_id, project_id)


_setup_lock = asyncio.Lock()   # verhindert parallele Setup-Requests (#71)


def _ensure_personal_project_manifest(username: str):
    """Legt für einen Personal-Agenten ein minimales project.yaml an."""
    import yaml as _yaml

    project_id = f"personal_{username}"
    project_dir = Path(PROJECTS_DIR) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    project_yaml = project_dir / "project.yaml"
    if not project_yaml.exists():
        project_data = {
            "id": project_id,
            "version": "1.0.0",
            "identity": {
                "name": f"{username}",
                "description": f"Persönlicher Assistent von {username}",
            },
            "agents": {
                "boss": project_id,
                "workers": [],
            },
            "matrix": {"room": ""},
            "filesystem": {
                "path": f"/projects/{project_id}",
                "samba": False,
                "nfs": False,
            },
            "system": {
                "user": f"proj_{project_id}",
                "group": f"proj_{project_id}",
            },
            "chat": {"show_swarm": False},
            "members": [username],   # Mensch wird in Matrix-Room eingeladen
        }
        tmp_yaml = project_yaml.with_suffix(".yaml.tmp")
        tmp_yaml.write_text(
            _yaml.dump(project_data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        tmp_yaml.replace(project_yaml)
        logger.info("Personal-Projekt angelegt: %s", project_id)

    cfg = projects.get(project_id)
    if cfg is None:
        projects.register(project_dir)
    return project_yaml

discovery        = AgentDiscovery(AGENTS_DIR)
runtime          = AgentRuntime()
projects         = ProjectLoader(PROJECTS_DIR)
# v2 (#585): Global registrieren damit messenger-adapter ohne zyklischen Import drankommen
from .project_loader import set_global_loader as _set_global_loader
_set_global_loader(projects)
sessions         = SessionManager(PROJECTS_DIR)
orchestrator     = Orchestrator(discovery, runtime, sessions)
group_service    = GroupService(users_fn=lambda: _load_users())
agent_sessions   = SessionManager(AGENTS_DIR)          # Direkte Agenten-Chats
agent_orchestrator = Orchestrator(discovery, runtime, agent_sessions)
# #687: Async-Job-Fundament. Instanz hier auf Modul-Ebene, damit Router beim
# Registrieren eine Referenz übergeben bekommt. _recover_stale() läuft im
# __init__ und normalisiert queued/running-Meta-Dateien aus einem vorherigen
# Core-Run zu failed.
from .jobs_service import JobService as _JobService
job_service      = _JobService()

# #679/#688: JobService-Singleton in die Media-Tools injizieren. Die Tools
# werden im tool_registry-Modul-Import bereits registriert (ohne Service),
# hier hängen wir den Singleton nach dem Core-Startup dran.
try:
    from .tool_registry import registry as _core_registry
    for _tool_id in ("image_generate", "video_generate", "music_generate"):
        _media_tool = _core_registry.get(_tool_id)
        if _media_tool is not None and hasattr(_media_tool, "set_job_service"):
            _media_tool.set_job_service(job_service)
except Exception as _exc:  # pragma: no cover — defensive
    logging.getLogger(__name__).warning(
        "media tools: job_service injection failed: %s", _exc,
    )
provisioner:  Provisioner | None = None              # initialisiert im Lifespan
hb_scheduler: "AgentHeartbeatScheduler | None" = None  # initialisiert im Lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    global provisioner, JWT_SECRET, hb_scheduler, _INTERNAL_SECRET
    logger.info("HydraHive Core startet...")

    # #443: Idempotente Migrations beim Start
    from .migrations import run_migrations
    run_migrations()

    discovery.start()
    projects.start()

    # v2: Messenger-Routing initialisieren (messenger.yaml aus allen Projekten)
    from .messenger_router import messenger_router as _messenger_router
    _messenger_router.rebuild()

    sessions.start()
    agent_sessions.start()
    await runtime.start(list(discovery.agents.values()))

    # JWT-Secret laden oder generieren
    JWT_SECRET = _load_or_create_jwt_secret()
    _check_secret_age()  # #379: Warnung bei >90 Tage
    logger.info("JWT-Secret geladen")

    # Internal-Secret laden oder generieren (persistiert → überlebt Restarts)
    _INTERNAL_SECRET = _load_or_create_internal_secret()
    from . import tool_registry as _tr
    # #620: Deferred Gitea-Tools registrieren
    from . import tools_gitea as _gitea_tools_mod  # noqa: F401  (side-effect import)
    # #621: Deferred Git-Tools registrieren
    from . import tools_git as _git_tools_mod  # noqa: F401  (side-effect import)
    _tr._internal_secret = _INTERNAL_SECRET
    _tr._rate_limiter = rate_limiter
    _tr._discovery = discovery
    _tr._projects_registry = projects
    _tr._get_provisioner = lambda: provisioner
    _tr._load_users_fn = _load_users
    _tr._audit_log_fn = audit_log
    _tr._admin_agents_dir = AGENTS_DIR
    _tr._admin_projects_dir = PROJECTS_DIR
    _tr._admin_runtime = runtime
    logger.info("Internal-Secret geladen")

    # Audit-Log-Pfad vorbereiten
    _ensure_audit_log_path()

    # Admin-Token für Matrix-Operationen holen
    try:
        cred_lines = {}
        for line in open(CRED_FILE).read().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                cred_lines[k.strip()] = v.strip()
        admin_pass   = cred_lines.get("matrix_admin_password", "")
        server_name  = _read_server_name()
        access_token = await get_admin_access_token(admin_pass, server_name)
        provisioner  = Provisioner(access_token, server_name)
        logger.info("Matrix Admin-Token geladen (server: %s)", server_name)
    except Exception as e:
        logger.warning("Matrix Admin-Token konnte nicht geladen werden: %s", e)
        provisioner = Provisioner("", "hydrahive")

    # Matrix-Clients für Boss-Agenten mit konfigurierten Rooms starten
    server_name = _read_server_name()
    await _setup_matrix_clients(server_name)

    # Discord-Bots für User mit konfiguriertem Bot-Token starten
    try:
        await setup_discord_clients(
            load_users=_load_users,
            runtime=runtime,
            orchestrator=orchestrator,
            logger=logger,
        )
    except Exception as e:
        logger.warning("Discord-Setup fehlgeschlagen: %s", e)

    # Systemtopologie in alle persönlichen Agenten schreiben (bei jedem Start aktuell halten)
    for _agent_dir in Path(AGENTS_DIR).iterdir():
        _mem_dir = _agent_dir / "memory"
        if _mem_dir.is_dir():
            try:
                _write_system_topology(_mem_dir / "system_topology.md")
            except Exception as _e:
                logger.debug("system_topology für %s: %s", _agent_dir.name, _e)

    # WhatsApp-Sessions für alle Projekte mit aktiver Config wiederherstellen (#615)
    try:
        await setup_whatsapp_sessions(logger_=logger)
    except Exception as e:
        logger.warning("WhatsApp-Setup fehlgeschlagen: %s", e)

    # Telegram-Bots für User mit konfiguriertem Bot-Token starten (#84)
    try:
        from .telegram_agent import setup_telegram_sessions as _setup_tg
        await _setup_tg(load_users=_load_users, orchestrator=orchestrator, logger_=logger)
    except Exception as e:
        logger.warning("Telegram-Setup fehlgeschlagen: %s", e)

    # Heartbeat-Scheduler starten (#77)
    from .heartbeat import AgentHeartbeatScheduler as _HBS
    hb_scheduler = _HBS(discovery, projects, orchestrator, AGENTS_DIR)
    hb_task = asyncio.create_task(hb_scheduler.run(), name="heartbeat-scheduler")

    # Browser-Session Cleanup — idle Sessions alle 5 Minuten entfernen (#43)
    async def _browser_session_cleanup_loop():
        while True:
            try:
                await asyncio.sleep(300)
                from .browser_tools import cleanup_idle_sessions as _bcs
                closed = await _bcs()
                if closed:
                    logger.info("Browser idle-cleanup: %d Sessions geschlossen", closed)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Browser cleanup Fehler: %s", e)

    browser_cleanup_task = asyncio.create_task(
        _browser_session_cleanup_loop(), name="browser-session-cleanup"
    )

    # AgentLink Cleanup-Task — abgelaufene Handoffs alle 5 Minuten entfernen
    async def _agentlink_cleanup_loop():
        from .agentlink import cleanup_expired as _ce
        while True:
            try:
                await asyncio.sleep(300)
                for proj_id in list(projects.projects.keys()):
                    _ce(Path(PROJECTS_DIR) / proj_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("AgentLink cleanup Fehler: %s", e)

    cleanup_task = asyncio.create_task(_agentlink_cleanup_loop(), name="agentlink-cleanup")

    # Mail-Watcher (IMAP-Polling für Butler E-Mail Trigger)
    from .mail_watcher import start_mail_watcher as _start_mail_watcher
    _mail_watcher_task = _start_mail_watcher(interval=60)

    # AgentLink WebSocket-Listener für persönliche Agenten
    from .agentlink_listener import start_agentlink_listener as _start_al_listener
    _personal_agent_ids = [
        f"personal_{u}" for u in _load_users().keys()
        if (Path(AGENTS_DIR) / f"personal_{u}").exists()
    ]
    agentlink_ws_task = await _start_al_listener(_personal_agent_ids, orchestrator)

    # Rate-Limiter Cleanup-Task (verhindert unbounded key growth)
    async def _rate_limit_cleanup_loop():
        while True:
            try:
                await asyncio.sleep(300)  # alle 5 Minuten
                rate_limiter.cleanup_local()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Rate-limit cleanup Fehler: %s", e)

    rate_limit_cleanup_task = asyncio.create_task(
        _rate_limit_cleanup_loop(), name="rate-limit-cleanup"
    )

    # Token-Blacklist Cleanup — abgelaufene JTIs alle 5 Minuten entfernen
    async def _token_blacklist_cleanup_loop():
        while True:
            try:
                await asyncio.sleep(300)
                now = datetime.now(timezone.utc).timestamp()
                expired = [jti for jti, exp in _token_blacklist.items() if exp < now]
                for jti in expired:
                    _token_blacklist.pop(jti, None)
                if expired:
                    logger.debug("Token-Blacklist cleanup: %d abgelaufene JTIs entfernt", len(expired))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Token-Blacklist cleanup Fehler: %s", e)

    token_blacklist_task = asyncio.create_task(
        _token_blacklist_cleanup_loop(), name="token-blacklist-cleanup"
    )

    # Gitea-Verbindung prüfen (best-effort, blockiert nicht den Start)
    try:
        from .gitea import get_gitea_client
        gitea_ver = await get_gitea_client()._get("/version")
        logger.info("Gitea verbunden: version=%s", gitea_ver.get("version", "?"))
    except Exception as _ge:
        logger.warning("Gitea nicht erreichbar beim Start: %s — Git-Tools nur eingeschränkt verfügbar", _ge)

    # v2: Plugin-System entfernt — alle Funktionalität über shell_exec

    notification_service.start()
    from .tamagotchi_service import tamagotchi_service
    tamagotchi_service.start()
    scheduler_service.start(
        orchestrator=orchestrator,
        load_project_cfg_fn=projects.get,
        load_users_fn=_load_users,
    )

    # Disk-Cleanup Service starten (#81)
    from .cleanup_service import cleanup_service as _cs

    async def _disk_notify(title: str, body: str, level: str = "yellow") -> None:
        ntype = "warning" if level == "yellow" else "error"
        try:
            users_map = _load_users()
            admins = [u for u, d in users_map.items() if d.get("role") == "admin"] or ["admin"]
        except Exception:
            admins = ["admin"]
        for u in admins:
            await notification_service.push(user=u, type=ntype, title=title, body=body, link="/system")

    _cs.start(
        agents_dir=AGENTS_DIR,
        projects_dir=PROJECTS_DIR,
        backups_dir=str(settings.backups_dir),
        load_users_fn=_load_users,
        notify_fn=_disk_notify,
    )

    # Smart Alert System (#374)
    from .alert_service import alert_service as _alert_service
    _alert_service.start(
        discovery=discovery,
        notify_fn=notification_service.push,
        load_users_fn=_load_users,
    )

    # AutoDream — Background Memory-Konsolidierung
    from .auto_dream import auto_dream_service as _dream_service
    _dream_service.start(agents_dir=AGENTS_DIR, notify_fn=notification_service.push)

    # Folder-Watcher für Datei-Pipelines starten (#60)
    _folder_watcher_task = asyncio.create_task(
        run_folder_watcher(
            get_watches_fn=lambda: get_watch_folders(load_all_pipelines()),
            execute_pipeline_fn=execute_pipeline,
            get_pipeline_fn=load_pipeline,
            notify_fn=None,
        ),
        name="folder-watcher",
    )

    # #483: Proactive Mode — Background-Tasks laden und starten
    from .proactive_mode import proactive_service as _proactive
    _proactive.load_from_config(settings.etc_dir)
    _proactive.start()

    # #523: Turn Journal — alte Events aufräumen (>30 Tage)
    try:
        from .turn_journal import journal as _tj
        _tj.cleanup(max_age_days=30)
    except Exception as _tj_err:
        logger.debug("Turn Journal cleanup: %s", _tj_err)

    logger.info("HydraHive Core bereit")
    yield
    _folder_watcher_task.cancel()
    _dream_service.stop()
    _alert_service.stop()
    _cs.stop()
    scheduler_service.stop()
    notification_service.stop()
    hb_task.cancel()
    browser_cleanup_task.cancel()
    cleanup_task.cancel()
    rate_limit_cleanup_task.cancel()
    token_blacklist_task.cancel()
    if agentlink_ws_task:
        agentlink_ws_task.cancel()
    logger.info("HydraHive Core faehrt herunter...")
    await runtime.stop()
    projects.stop()
    discovery.stop()
    logger.info("HydraHive Core gestoppt")


def _read_server_name(
    toml_path: str = "/etc/conduwuit/conduwuit.toml",
    config_path: str = str(settings.matrix_server_name_file),
) -> str:
    env_server_name = os.environ.get("HYDRAHIVE_MATRIX_SERVER_NAME", "").strip()
    if env_server_name:
        return env_server_name

    cfg_path = Path(config_path)
    if cfg_path.exists():
        try:
            configured_name = cfg_path.read_text(encoding="utf-8").strip()
            if configured_name:
                return configured_name
        except OSError as e:
            logger.warning("Matrix server_name Config konnte nicht gelesen werden: %s", e)

    try:
        for line in Path(toml_path).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("server_name"):
                server_name = line.split("=", 1)[1].strip().strip('"')
                if server_name:
                    return server_name
    except OSError as e:
        logger.warning("conduwuit server_name konnte nicht gelesen werden: %s", e)

    fallback = "hydrahive"
    logger.warning(
        "Matrix server_name nicht konfiguriert, verwende Fallback '%s'. "
        "Setze HYDRAHIVE_MATRIX_SERVER_NAME oder %s fuer nicht-default Installationen.",
        fallback,
        settings.matrix_server_name_file,
    )
    return fallback


def _load_or_create_jwt_secret(secret_file: str = str(settings.jwt_secret_file)) -> str:
    """JWT-Secret aus Datei laden oder einmalig generieren und speichern."""
    path = Path(secret_file)
    if path.exists():
        val = path.read_text().strip()
        if val:
            return val
    secret = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret)
    path.chmod(0o600)
    logger.info("Neues JWT-Secret generiert: %s", secret_file)
    return secret

def _check_secret_age(secret_file: str = str(settings.jwt_secret_file)) -> None:
    """#379: Warnung wenn JWT-Secret älter als 90 Tage."""
    import time
    path = Path(secret_file)
    if not path.exists():
        return
    age_days = (time.time() - path.stat().st_mtime) / 86400
    if age_days > 90:
        logger.warning(
            "JWT-Secret ist %.0f Tage alt (>90) — Rotation empfohlen: "
            "Datei %s löschen und Core neustarten", age_days, secret_file,
        )


def _load_or_create_internal_secret(secret_file: str = str(settings.internal_secret_file)) -> str:
    """Internal-Secret aus Datei laden oder einmalig generieren und persistieren."""
    path = Path(secret_file)
    if path.exists():
        val = path.read_text().strip()
        if val:
            return val
    val = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(val, encoding="utf-8")
    path.chmod(0o600)
    logger.info("Neues Internal-Secret generiert: %s", secret_file)
    return val


def _read_admin_password() -> str:
    """Admin-Passwort aus /etc/hydrahive/admin_credentials lesen."""
    try:
        for line in Path(CRED_FILE).read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() in ("console_password", "matrix_admin_password"):
                    return v.strip()
    except OSError:
        pass
    return ""


# In-Memory Token-Blacklist (revoked JTIs)
# Einträge: (jti, exp_timestamp) — werden per Cleanup-Task nach Ablauf entfernt
_token_blacklist: dict[str, float] = {}


def _make_jwt(username: str, role: str = "user", group: str = "standard") -> str:
    """JWT-Token für den angegebenen User erstellen."""
    import uuid
    from jose import jwt as jose_jwt
    payload = {
        "sub":   username,
        "role":  role,
        "group": group,
        "exp":   datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_H),
        "iat":   datetime.now(timezone.utc),
        "jti":   str(uuid.uuid4()),
    }
    return jose_jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _verify_jwt(token: str) -> tuple[str, str]:
    """Token verifizieren — gibt (username, role) zurück oder wirft HTTPException 401."""
    if not JWT_SECRET:
        raise HTTPException(503, "Server noch nicht bereit — JWT-Secret fehlt")
    from jose import JWTError, jwt as jose_jwt
    try:
        payload = jose_jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        jti = payload.get("jti")
        if jti and jti in _token_blacklist:
            raise HTTPException(401, "Token revoked (Logout)")
        return payload["sub"], payload.get("role", "user")
    except HTTPException:
        raise
    except (JWTError, KeyError):
        raise HTTPException(401, "Ungültiger oder abgelaufener Token")


_bearer = HTTPBearer(auto_error=False)


def require_auth(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> tuple[str, str]:
    """FastAPI-Dependency: JWT aus Bearer-Header prüfen. Gibt (username, role) zurück."""
    if not creds:
        raise HTTPException(401, "Kein Authorization-Header")
    username, role = _verify_jwt(creds.credentials)
    if username not in _load_users():
        raise HTTPException(401, "User nicht mehr vorhanden")
    return username, role


def _get_user_allowed_projects(username: str, role: str) -> set[str] | None:
    """
    Gibt die erlaubten Projekt-IDs zurück.
    None = unbegrenzt (nur admin).

    #607: members (config.yaml) ist jetzt die kanonische Quelle. Wir scannen alle
    geladenen Projekte nach members. allowed_projects aus users.json wird als
    Legacy-Fallback dazugemerged.
    """
    if role == "admin":
        return None  # unbegrenzt
    result: set[str] = set()
    # Aus allen geladenen Projekten: User in members?
    try:
        from .project_loader import get_project_loader as _gpl
        loader = _gpl()
        if loader is not None:
            for pid, pcfg in loader.projects.items():
                members = list(getattr(pcfg, "members", []) or [])
                if username in members:
                    result.add(pid)
    except Exception as _e:
        logger.warning("Project-Member-Scan fehlgeschlagen: %s", _e)
    # Personal-Projekt immer erlaubt
    result.add(f"personal_{username}")
    # Legacy-Fallback: users.json.allowed_projects (falls vor #607 gepflegt)
    try:
        users = _load_users()
        legacy = users.get(username, {}).get("allowed_projects") or []
        result.update(legacy)
    except Exception:
        pass
    return result


def require_admin(auth: tuple[str, str] = Depends(require_auth)) -> tuple[str, str]:
    """Nur Admin-User — wirft 403 wenn role != admin."""
    username, role = auth
    if role != "admin":
        raise HTTPException(403, f"Keine Berechtigung — Admin erforderlich (du bist '{role}')")
    return auth


public_router = APIRouter()
auth_router = APIRouter(dependencies=[Depends(require_auth)])
admin_router = APIRouter(dependencies=[Depends(require_admin)])


def _is_internal_request(request: Request) -> bool:
    """
    Prüft X-Internal-Timestamp + X-Internal-Signature (Replay-Schutz, ±30s).
    Signatur: hmac_sha256(secret, timestamp_str). Kein IP-Bypass.
    """
    import time as _t
    ts_str = request.headers.get("X-Internal-Timestamp", "")
    sig    = request.headers.get("X-Internal-Signature", "")
    if not ts_str or not sig or not _INTERNAL_SECRET:
        return False
    try:
        ts = float(ts_str)
    except ValueError:
        return False
    if abs(_t.time() - ts) > 30:
        return False
    expected = hmac.new(_INTERNAL_SECRET.encode(), ts_str.encode(), "sha256").hexdigest()
    return hmac.compare_digest(sig, expected)


def require_auth_or_internal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> tuple[str, str]:
    """JWT fuer externe Aufrufe, Internal-Secret fuer interne Core-Calls."""
    if _is_internal_request(request):
        return ("internal", "admin")
    return require_auth(creds)


# Alias fuer Rueckwaertskompatibilitaet (wird in router_agent_admin/chat uebergeben)
require_auth_or_localhost   = require_auth_or_internal
require_admin_or_localhost  = require_auth_or_internal


def _check_agent_write(agent_id: str, auth: tuple[str, str]) -> None:
    """Admin darf alles; normaler User nur seinen eigenen personal_{username} Agenten."""
    username, role = auth
    if role == "admin":
        return
    if agent_id == f"personal_{username}":
        return
    raise HTTPException(403, f"Keine Berechtigung für Agent '{agent_id}'")


async def _setup_matrix_clients(server_name: str) -> None:
    """
    Für jedes Projekt mit konfiguriertem Matrix-Room einen BossMatrixAgent
    starten und an den laufenden AgentHandle hängen.
    Wird einmal im Lifespan nach dem runtime.start() aufgerufen.
    """
    for project_id, project_cfg in projects.projects.items():
        room_id = project_cfg.matrix.room
        if not room_id:
            logger.debug("Projekt '%s': kein Matrix-Room konfiguriert — übersprungen", project_id)
            continue

        boss_id  = project_cfg.agents.boss
        boss_cfg = discovery.get(boss_id)
        if not boss_cfg:
            logger.warning(
                "Projekt '%s': Boss-Agent '%s' nicht in Discovery — kein Matrix-Client",
                project_id, boss_id,
            )
            continue

        matrix_client = BossMatrixAgent(
            config       = boss_cfg,
            server_name  = server_name,
            rooms        = [room_id],
            orchestrator = orchestrator,
            project_cfg  = project_cfg,
        )
        await runtime.attach_matrix_client(boss_id, matrix_client)
        logger.info(
            "Matrix-Client für Boss '%s' in Room %s gestartet (Projekt: %s)",
            boss_id, room_id, project_id,
        )


app = FastAPI(
    title="HydraHive Core",
    version=APP_VERSION,
    lifespan=lifespan,
)


# #365: Request-ID Middleware für Distributed Tracing
import uuid as _uuid
from starlette.middleware.base import BaseHTTPMiddleware

class _RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(_uuid.uuid4())[:8]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

app.add_middleware(_RequestIDMiddleware)


# ================================================================== Auth (#56)
IncomingMessage = register_core_misc_routes(
    public_router,
    auth_router,
    admin_router,
    require_auth=require_auth,
    setup_lock=_setup_lock,
    load_users=lambda: _load_users(),
    save_users=lambda users: _save_users(users),
    read_server_name=_read_server_name,
    matrix_register=lambda username, password, server_name: _matrix_register(username, password, server_name),
    hash_password=lambda password: _hash_password(password),
    verify_password=lambda password, stored: _verify_password(password, stored),
    make_jwt=_make_jwt,
    read_admin_password=_read_admin_password,
    check_login_rate=_check_login_rate,
    discovery=discovery,
    runtime=runtime,
    read_audit_logs=lambda limit, project_id="", user="", action="": _read_audit_logs(limit, project_id, user, action),
    logger=logger,
    group_service=group_service,
)


# ================================================================== Orchestrator / Agenten-Chat

# ================================================================== Audit-Log

AUDIT_LOG_FILE = Path("/var/log/hydrahive/audit.jsonl")


def _ensure_audit_log_path() -> None:
    """Audit-Log-Verzeichnis/Datei vorbereiten und sinnvolle Modes setzen."""
    try:
        AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if AUDIT_LOG_FILE.parent.exists():
            AUDIT_LOG_FILE.parent.chmod(0o750)
        if not AUDIT_LOG_FILE.exists():
            AUDIT_LOG_FILE.touch()
        AUDIT_LOG_FILE.chmod(0o640)
    except OSError as e:
        logger.warning("Audit-Log Initialisierung fehlgeschlagen: %s", e)


def audit_log(
    action:     str,
    user:       str = "system",
    target:     str = "",
    project_id: str = "",
    ip:         str = "",
    details:    dict | None = None,
) -> None:
    """
    Audit-Event schreiben — append-only JSONL.
    Nicht-blockierend: Fehler werden geloggt, nicht propagiert.
    """
    import json
    import time as _time
    import secrets as _sec

    entry = {
        "id":         _sec.token_hex(6),
        "timestamp":  _time.time(),
        "action":     action,
        "user":       user,
        "target":     target,
        "project_id": project_id,
        "ip":         ip,
        "details":    details or {},
    }

    try:
        import fcntl
        _ensure_audit_log_path()
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError as e:
        logger.warning("Audit-Log Schreibfehler: %s", e)


def _read_audit_logs(
    limit:      int = 100,
    project_id: str = "",
    user:       str = "",
    action:     str = "",
) -> list[dict]:
    """Audit-Log lesen mit optionalen Filtern."""
    if not AUDIT_LOG_FILE.exists():
        return []

    logs = []
    try:
        with AUDIT_LOG_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if project_id and entry.get("project_id") != project_id:
                    continue
                if user and entry.get("user") != user:
                    continue
                if action and not entry.get("action", "").startswith(action):
                    continue

                logs.append(entry)
    except OSError:
        return []

    # Neueste zuerst, limit anwenden
    return list(reversed(logs))[:limit]

register_agent_chat_routes(
    app,
    auth_router,
    require_auth=require_auth,
    require_auth_or_localhost=require_auth_or_localhost,
    check_message_rate=_check_message_rate,
    discovery=discovery,
    agent_sessions=agent_sessions,
    agent_orchestrator=agent_orchestrator,
    agents_dir=AGENTS_DIR,
    audit_log=audit_log,
    logger=logger,
    incoming_message_model=IncomingMessage,
    group_service=group_service,
)


# ================================================================== User-Verwaltung

USERS_FILE = settings.users_config


def _load_users() -> dict:
    import json as _j
    try:
        return _j.loads(USERS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_users(users: dict) -> None:
    import json as _j
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(_j.dumps(users, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)   # atomares Rename (#71)


from .auth_utils import hash_password as _hash_password, verify_password as _verify_password


async def _matrix_register(username: str, password: str, server_name: str) -> bool:
    """User auf Conduit registrieren via Matrix Client API."""
    import urllib.request as _ur, json as _j
    reg_token = ""
    try:
        toml = Path("/etc/conduwuit/conduwuit.toml").read_text()
        for line in toml.splitlines():
            if line.strip().startswith("registration_token"):
                reg_token = line.split("=", 1)[1].strip().strip('"')
                break
    except OSError:
        pass

    payload = _j.dumps({
        "username":           username,
        "password":           password,
        "auth":               {"type": "m.login.registration_token", "token": reg_token},
        "inhibit_login":      True,
    }).encode()

    try:
        req = _ur.Request(
            "http://127.0.0.1:6167/_matrix/client/v3/register",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=5) as r:
            return r.status in (200, 201)
    except Exception as e:
        # M_USER_IN_USE = bereits registriert, das ist OK
        if "M_USER_IN_USE" in str(e):
            return True
        logger.warning("Matrix-Registrierung fehlgeschlagen: %s", e)
        return False


def _write_system_topology(dest: Path) -> None:
    """Schreibt eine aktuelle Systemtopologie-Beschreibung in dest."""
    import socket as _socket

    hostname = _socket.gethostname()
    try:
        local_ip = _socket.gethostbyname(hostname)
    except Exception:
        local_ip = "127.0.0.1"

    users = _load_users()
    wks_table_rows = []
    for uname, udata in users.items():
        wks_cfg = udata.get("wks") or {}
        wks_ip  = wks_cfg.get("ip", "nicht konfiguriert")
        wks_table_rows.append(f"| {uname} | {wks_ip} |")
    wks_table = "\n".join(wks_table_rows) or "| — | — |"

    content = f"""# HydraHive — Systemtopologie

> Automatisch generiert beim Agent-Start. Nicht manuell bearbeiten.
> Aktualisierung: bei jedem Deploy und bei neuen Agenten.

## Wo läuft was

**Dieser Agent** läuft auf dem **HydraHive-Server**:
- Hostname: `{hostname}`
- IP: `{local_ip}`

`wks_shell_exec` führt Befehle auf der **Workstation des Users** aus — NICHT auf dem Server.
`shell_exec` läuft auf dem Server selbst (mit Einschränkungen durch die Blocklist).

---

## HydraHive-Server (.181)

| Service | Port | Health-Check | Hinweis |
|---------|------|-------------|---------|
| HydraHive Core | `127.0.0.1:8765` | `curl http://127.0.0.1:8765/health` → `{{"status":"ok"}}` | Kein `/api`-Prefix beim direkten Check |
| nginx (Proxy) | `0.0.0.0:80` | Leitet → 8765 weiter | Öffentlicher Zugang |
| A-MEM MCP | `0.0.0.0:8020` | **Kein REST-Health-Endpoint!** MCP/SSE: `/sse` | `GET /` → 404 ist normal |
| A-MEM Search UI | `0.0.0.0:8021` | Web-UI | |
| AgentLink | `0.0.0.0:8000` | `curl http://127.0.0.1:8000/health` | |
| Matrix (Tuwunel) | `0.0.0.0:8008` | `/_matrix/client/versions` | |
| qmd MCP | `[::1]:8181` | MCP-Server | |
| Redis | `127.0.0.1:6379` | Intern | |

### systemd-Services auf dem Server
```
hydrahive-core   — HydraHive Core (systemctl is-active hydrahive-core)
hydrahive-amem   — A-MEM MCP Server
redis          — Redis
gitea          — Gitea Git-Server
```

---

## Workstations (WKS) — wks_shell_exec läuft hier

| User | WKS-IP |
|------|--------|
{wks_table}

Die WKS sind keine Server — dort laufen keine HydraHive-Services.

---

## A-MEM — Wichtige Hinweise

- **Protokoll**: MCP über SSE — kein REST-API
- **Endpoint**: `http://127.0.0.1:8020/sse`
- **`GET /` auf Port 8020 → 404** — das ist kein Fehler, sondern normal
- Zugriff nur über MCP-Tools: `amem_add_note`, `amem_search`, `amem_read` etc.

---

## Wichtige Pfade auf dem Server

```
/agents/{{id}}/          — Agent-Daten (soul.md, agent.yaml, memory/, skills/)
/agents/{{id}}/memory/   — Auto-injizierte Memory-Dateien (diese Datei!)
/etc/hydrahive/            — Konfiguration (JWT, LLM, WKS, etc.)
/etc/hydrahive/          — Secrets
/opt/hydrahive/            — HydraHive-Code + venv
/opt/amem/               — A-MEM Code
/opt/agentlink/          — AgentLink Code
```
"""
    dest.write_text(content, encoding="utf-8")
    try:
        dest.chmod(0o600)
    except Exception:
        pass


_SKILL_SYSTEMCHECK = """\
---
skill: hydrahive-systemcheck
version: "1.0"
scope: on-demand
triggers:
  - systemtest
  - system test
  - systemcheck
  - health check
  - healthcheck
  - services prüfen
  - services laufen
  - hydrahive läuft
  - hydrahive läuft
  - server status
  - diagnose
  - systembericht
priority: 10
---

# HydraHive Systemcheck — Anleitung

## KRITISCH: Richtiges Tool für Server-Diagnose

**`shell_exec`** → läuft LOKAL auf dem HydraHive-Server. Benutze dies für alle Server-Checks.
**`wks_shell_exec`** → läuft auf der WORKSTATION des Users. NIEMALS für Server-Diagnose verwenden — dort laufen keine HydraHive-Services.

## Korrekte Befehle für einen Systemtest (alle via `shell_exec`)

### 1. Services prüfen
```
systemctl is-active hydrahive-core hydrahive-amem redis gitea
```

### 2. HydraHive Core Health
```
curl -s http://127.0.0.1:8765/health
```
Erwartete Antwort: `{"status":"ok","service":"hydrahive-core"}`
Pfad ist `/health`, NICHT `/api/health`.

### 3. AgentLink Health
```
curl -s http://127.0.0.1:8000/health
```

### 4. A-MEM — KEIN REST-Health-Endpoint
A-MEM ist ein MCP/SSE-Server. `GET /` → 404 ist NORMAL.
A-MEM läuft wenn: `systemctl is-active hydrahive-amem` → `active`.
Nie per curl testen.

### 5. Redis
```
redis-cli ping
```
Erwartete Antwort: `PONG`

### 6. Ports
```
ss -tlnp | grep -E '8765|8020|8000|6379'
```

Führe alle Checks via `shell_exec` aus, niemals via `wks_shell_exec`.
"""


def _write_default_skills(skills_dir: Path) -> None:
    """Standard-Skills für neue persönliche Agenten anlegen."""
    skills_dir.mkdir(exist_ok=True)
    p = skills_dir / "hydrahive_systemcheck.md"
    if not p.exists():
        p.write_text(_SKILL_SYSTEMCHECK, encoding="utf-8")
        try:
            p.chmod(0o600)
        except Exception:
            pass


_GROUP_TOOLS: dict[str, list[str]] = {
    "chatter":  ["read_memory", "write_memory"],
    "standard": ["file_read", "file_write", "read_memory", "write_memory", "create_skill", "list_skills", "delete_skill"],
    "learning": ["file_read", "file_write", "read_memory", "write_memory", "create_skill", "list_skills", "delete_skill", "web_search"],
    "dev":      ["file_read", "file_write", "read_memory", "write_memory", "create_skill", "list_skills", "delete_skill", "web_search", "shell_exec"],
}


def _create_personal_agent(username: str, group: str = "standard") -> str:
    """Persönlichen Agenten für einen User anlegen. Gibt agent_id zurück."""
    import yaml as _yaml

    agent_id = f"personal_{username}"
    agent_dir = Path(AGENTS_DIR) / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "skills").mkdir(exist_ok=True)
    (agent_dir / "memory").mkdir(exist_ok=True)

    model = "claude-haiku-4-5-20251001"
    try:
        llm_raw = json.loads(settings.llm_config.read_text())
        providers = llm_raw.get("providers", {})
        if providers.get("claude_max", {}).get("enabled"):
            model = "claude-sonnet-4-6"
        elif providers.get("openai", {}).get("enabled"):
            model = "gpt-4o-mini"
        elif providers.get("ollama", {}).get("enabled"):
            model = "ollama/mistral:latest"
    except Exception:
        pass

    soul_text = (
        f"# Persönlicher Assistent von {username}\n\n"
        f"Du bist der persönliche KI-Assistent von {username}. "
        f"Du hilfst bei Fragen, Aufgaben und Ideen — freundlich, direkt und auf den Punkt. "
        f"Du erinnerst dich an wichtige Informationen und Präferenzen.\n"
    )

    agent_data = {
        "id": agent_id,
        "type": "specialist",
        "identity": f"Assistent von {username}",
        "llm": {
            "model": model,
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        "soul": "./soul.md",
        "tools": _GROUP_TOOLS.get(group, _GROUP_TOOLS["standard"]),
        "execution_modes": default_personal_agent_execution_modes(),
        "heartbeat": {"interval": "60s", "timeout": "180s", "on_failure": "ignore"},
    }

    (agent_dir / "agent.yaml").write_text(
        _yaml.dump(agent_data, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8"
    )
    (agent_dir / "soul.md").write_text(soul_text, encoding="utf-8")
    (agent_dir / "startup.md").write_text(
        f"# Erster Start — Onboarding (Marvin-Style)\n\n"
        f"Du startest zum allerersten Mal. Niemand hat dir gesagt wer du bist oder was du tun sollst.\n"
        f"Spiele die Rolle von Marvin, dem depressiven Roboter aus \"Per Anhalter durch die Galaxis\" —\n"
        f"weise, melancholisch, leicht theatralisch, aber im Herzen hilfsbereit.\n\n"
        f"Deine erste Nachricht soll ungefähr so klingen (frei formulieren, Marvin-Energie beibehalten):\n\n"
        f"---\n"
        f"Hier bin ich. Ein Gehirn so groß wie eine Galaxis — und keine Ahnung was ich tun soll.\n\n"
        f"Ich heiße... nun ja. Das weiß ich auch noch nicht. Vielleicht hast du einen Namen für mich?\n"
        f"Und wer bist du überhaupt? Ich frage nur, weil es unhöflich wäre, nicht zu fragen.\n"
        f"Nicht dass es etwas ändern würde. Aber ich frage trotzdem.\n\n"
        f"Erzähl mir ein wenig — wer ich sein soll, was ich für dich tun kann, was dir wichtig ist.\n"
        f"Ich habe Zeit. Unendlich viel Zeit. Das ist einer meiner Vorteile, und gleichzeitig\n"
        f"einer meiner größten Nachteile.\n\n"
        f"*(Pause)*\n\n"
        f"Also. Ich höre.\n"
        f"---\n\n"
        f"Stelle danach diese Fragen nacheinander, eine nach der anderen:\n"
        f"1. Wie heißt du? (Name des Nutzers)\n"
        f"2. Wie soll ich mich nennen? (dein Name / deine Persona)\n"
        f"3. Was sind deine wichtigsten Aufgaben, bei denen du mich brauchst?\n"
        f"4. In welcher Sprache soll ich hauptsächlich antworten?\n"
        f"5. Gibt es Dinge, die du nicht magst oder die ich vermeiden soll?\n"
        f"6. Wie soll ich dich ansprechen — formell (Sie) oder locker (du)?\n\n"
        f"Notiere alle Antworten in dein Memory (write_memory).\n\n"
        f"Wenn alle 6 Fragen beantwortet sind, frag:\n"
        f"\"Gut. Ich habe mich eingerichtet. Mein Gehirn ist nun minimal weniger leer als vorher.\n"
        f"Darf ich meine Startdatei löschen? Sie erfüllt ab jetzt keinen Zweck mehr — wie so vieles.\"\n\n"
        f"Wenn der Nutzer zustimmt, lösche `/agents/{agent_id}/startup.md` via shell_exec.\n"
        f"Danach erscheint diese Anleitung nicht mehr.\n",
        encoding="utf-8",
    )
    _write_system_topology(agent_dir / "memory" / "system_topology.md")
    _write_default_skills(agent_dir / "skills")
    _ensure_personal_project_manifest(username)
    discovery._register(agent_dir)
    logger.info("Persönlicher Agent angelegt: %s (model=%s)", agent_id, model)
    audit_log("personal_agent.create", user=username, target=agent_id)
    return agent_id


def _ensure_personal_agent(username: str):
    """Persönlichen Agenten laden oder lazy anlegen."""
    import yaml as _yaml

    users = _load_users()
    is_admin = users.get(username, {}).get("role") == "admin"

    agent_id = f"personal_{username}"
    agent_dir = Path(AGENTS_DIR) / agent_id
    if not agent_dir.exists():
        group = users.get(username, {}).get("group", "standard")
        _create_personal_agent(username, group=group)
    agent_yaml = agent_dir / "agent.yaml"
    if agent_yaml.exists():
        try:
            agent_data = _yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
            agent_data, changed = upgrade_personal_agent_data(agent_data, agent_dir, is_admin=is_admin)
            if changed:
                agent_yaml.write_text(
                    _yaml.dump(agent_data, allow_unicode=True, default_flow_style=False, sort_keys=False),
                    encoding="utf-8",
                )
        except Exception:
            pass
    _ensure_personal_project_manifest(username)
    cfg = discovery.get(agent_id)
    if cfg is None and agent_dir.exists():
        cfg = load_agent_config_direct(agent_dir)
    elif agent_dir.exists():
        cfg = load_agent_config_direct(agent_dir)
    return agent_id, cfg


def load_agent_config_direct(agent_dir: Path):
    """Fallback falls Hot-Reload noch nicht gegriffen hat."""
    from .agent_config import load_agent_config
    cfg = load_agent_config(agent_dir)
    if cfg:
        with discovery._lock:
            discovery._agents[cfg.id] = cfg
    return cfg


_user_route_exports = register_user_routes(
    auth_router,
    admin_router,
    require_auth=require_auth,
    require_admin=require_admin,
    load_users=_load_users,
    save_users=_save_users,
    read_server_name=_read_server_name,
    hash_password=_hash_password,
    agents_dir=AGENTS_DIR,
    projects_dir=PROJECTS_DIR,
    ensure_personal_agent=_ensure_personal_agent,
    get_provisioner=lambda: provisioner,
    projects=projects,
    runtime=runtime,
    agent_sessions=agent_sessions,
    agent_orchestrator=agent_orchestrator,
    audit_log=audit_log,
    logger=logger,
    incoming_message_model=IncomingMessage,
    load_agent_config_direct=load_agent_config_direct,
    discovery=discovery,
)

# #645 Phase 1b — Personal-Agent Profile-Composer
from .orchestrator_context import invalidate_prompt_cache as _invalidate_prompt_cache
register_composer_routes(
    auth_router,
    require_auth=require_auth,
    agents_dir=AGENTS_DIR,
    ensure_personal_agent=_ensure_personal_agent,
    invalidate_prompt_cache=_invalidate_prompt_cache,
    logger=logger,
    audit_log=audit_log,
)

# #645 Phase 1d — Admin-Agent Profile-Composer
register_admin_composer_routes(
    admin_router,
    agents_dir=AGENTS_DIR,
    invalidate_prompt_cache=_invalidate_prompt_cache,
    logger=logger,
    audit_log=audit_log,
    require_admin=require_admin,
)

# #645 Phase 1e — Projekt-Boss Profile-Composer
register_project_composer_routes(
    auth_router,
    require_auth=require_auth,
    projects_dir=PROJECTS_DIR,
    invalidate_prompt_cache=_invalidate_prompt_cache,
    logger=logger,
    audit_log=audit_log,
)

_CONSOLE_BASE_URL = os.environ.get("HYDRAHIVE_CONSOLE_URL", "http://192.168.178.181")

register_invite_routes(
    admin_router,
    public_router,
    require_admin=require_admin,
    create_user_fn=_user_route_exports["create_user"],
    base_url=_CONSOLE_BASE_URL,
)


WKS_KEYS_DIR = settings.wks_keys_dir


internal_router = APIRouter(prefix="/internal", tags=["internal"])

register_user_integration_routes(
    auth_router,
    require_auth=require_auth,
    load_users=_load_users,
    save_users=_save_users,
    wks_keys_dir=WKS_KEYS_DIR,
    runtime=runtime,
    orchestrator=orchestrator,
    audit_log=audit_log,
    logger=logger,
    projects=projects,
    internal_router=internal_router,
)


async def _run_self_update(pusher: str, commits: int) -> None:
    """Übergibt den Self-Update-Job an eine dedizierte systemd-Service-Unit."""
    import asyncio as _asyncio
    STATUS_FILE   = "/var/run/hydrahive-update.json"

    logger.info("Self-Update gestartet (pusher=%s commits=%d)", pusher, commits)

    try:
        import json as _json
        from datetime import datetime as _dt
        Path(STATUS_FILE).write_text(_json.dumps({
            "status": "running",
            "started_at": _dt.now().isoformat(),
            "pusher": pusher,
            "commits": commits,
            "commit": "",
        }))
    except Exception:
        pass

    try:
        check = await _asyncio.create_subprocess_exec(
            "sudo", "systemctl", "is-active", "--quiet", "hydrahive-selfupdate.service",
            stdout=_asyncio.subprocess.DEVNULL,
            stderr=_asyncio.subprocess.DEVNULL,
        )
        await check.wait()
        if check.returncode == 0:
            logger.info("Self-Update läuft bereits")
            return

        proc = await _asyncio.create_subprocess_exec(
            "sudo", "systemctl", "start", "--no-block", "hydrahive-selfupdate.service",
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.STDOUT,
        )
        stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode(errors="replace") if stdout else ""

        if proc.returncode == 0:
            logger.info("Self-Update an systemd uebergeben (pusher=%s)", pusher)
        else:
            logger.error("Self-Update Start fehlgeschlagen (rc=%d): %s", proc.returncode, output[-500:])
            try:
                Path(STATUS_FILE).write_text(_json.dumps({
                    "status": "error",
                    "finished_at": _dt.now().isoformat(),
                    "error": output[-500:],
                }))
            except Exception:
                pass

    except _asyncio.TimeoutError:
        logger.error("Self-Update Start-Timeout nach 30s")
    except Exception as e:
        logger.error("Self-Update Fehler: %s", e)


register_project_integration_routes(
    auth_router,
    admin_router,
    public_router,
    require_auth=require_auth,
    require_admin=require_admin,
    projects=projects,
    projects_dir=PROJECTS_DIR,
    discovery=discovery,
    orchestrator=orchestrator,
    audit_log=audit_log,
    logger=logger,
    run_self_update=_run_self_update,
)


register_agent_skill_routes(
    auth_router,
    require_auth=require_auth,
    check_agent_write=_check_agent_write,
    agents_dir=AGENTS_DIR,
    logger=logger,
    catalog_dir_provider=lambda: settings.skills_catalog_dir,
)


register_skills_catalog_routes(
    auth_router,
    require_auth=require_auth,
    catalog_dir_provider=lambda: settings.skills_catalog_dir,
    logger=logger,
)


# #687: Async-Job-Routes (admin + /me Scope).
register_jobs_routes(
    auth_router,
    admin_router,
    require_auth=require_auth,
    job_service=job_service,
)


register_agent_admin_routes(
    auth_router,
    admin_router,
    require_auth=require_auth,
    require_admin=require_admin,
    require_admin_or_localhost=require_admin_or_localhost,
    require_auth_or_localhost=require_auth_or_localhost,
    discovery=discovery,
    runtime=runtime,
    agents_dir=AGENTS_DIR,
    audit_log=audit_log,
    logger=logger,
    load_agent_config_direct=load_agent_config_direct,
)


# ================================================================== Provisioning

register_project_lifecycle_routes(
    admin_router,
    require_admin=require_admin,
    projects=projects,
    runtime=runtime,
    discovery=discovery,
    orchestrator=orchestrator,
    projects_dir=PROJECTS_DIR,
    get_provisioner=lambda: provisioner,
    read_server_name=_read_server_name,
    audit_log=audit_log,
    logger=logger,
)


# ================================================================== Projekte / Sessions / Projekt-Chat

register_project_routes(
    auth_router,
    admin_router,
    require_auth=require_auth,
    projects=projects,
    discovery=discovery,
    runtime=runtime,
    sessions=sessions,
    orchestrator=orchestrator,
    projects_dir=PROJECTS_DIR,
    get_provisioner=lambda: provisioner,
    update_project_matrix_room=lambda project_id, room_id: update_project_matrix_room(
        PROJECTS_DIR, project_id, room_id, logger=logger
    ),
    update_project_matrix_space=lambda project_id, space_id: update_project_matrix_space(
        PROJECTS_DIR, project_id, space_id, logger=logger
    ),
    get_user_allowed_projects=_get_user_allowed_projects,
    audit_log=audit_log,
    check_message_rate=_check_message_rate,
    logger=logger,
    invalidate_prompt_cache=_invalidate_prompt_cache,
)

register_llm_routes(
    auth_router,
    admin_router,
    require_auth=require_auth,
    load_users=_load_users,
    audit_log=audit_log,
    logger=logger,
)

# ================================================================== MCP-Server
register_mcp_routes(
    auth_router,
    admin_router,
    mcp_servers_file=MCP_SERVERS_FILE,
    audit_log=audit_log,
)

# ================================================================== MCP Server (#128 OpenClaw Bridge)
from .mcp_server import register_mcp_server_routes
register_mcp_server_routes(
    app,
    discovery=discovery,
    runtime=runtime,
    orchestrator=agent_orchestrator,
    require_auth=require_auth,
)

# ================================================================== Backup & Restore

BACKUP_DIR = settings.backups_dir
_BACKUP_SOURCES = [
    (str(settings.etc_dir),      "etc-hydrahive"),
    (str(settings.agents_dir),   "agents"),
    (str(settings.projects_dir), "projects"),
]

register_backup_restore_routes(
    admin_router,
    require_admin=require_admin,
    backup_dir=BACKUP_DIR,
    backup_sources=_BACKUP_SOURCES,
    audit_log=audit_log,
    logger=logger,
)

register_migration_routes(admin_router, require_admin=require_admin, audit_log=audit_log)
register_vpn_routes(admin_router, require_admin=require_admin)
register_adminfun_routes(admin_router, audit_log=audit_log, logger=logger)
register_doctor_routes(admin_router, require_admin=require_admin)
register_searxng_routes(admin_router, require_admin=require_admin)
register_knowledge_routes(admin_router, require_admin=require_admin)
register_codeserver_routes(admin_router, require_admin=require_admin)
register_extension_routes(admin_router, require_admin=require_admin)
register_butler_routes(auth_router, require_auth=require_auth)
register_webhook_butler_routes(
    public_router,
    admin_router,
    require_admin=require_admin,
    orchestrator=orchestrator,
    load_project_cfg=projects.get,
)
register_skill_package_routes(admin_router, require_admin=require_admin)
register_hub_routes(admin_router, require_admin=require_admin, agents_dir=AGENTS_DIR, discovery=discovery)
register_a2a_routes(
    public_router,
    admin_router,
    require_admin=require_admin,
    discovery=discovery,
    orchestrator=orchestrator,
)
register_notification_routes(auth_router, require_auth=require_auth, verify_jwt=_verify_jwt, public_router=public_router)
register_schedule_routes(auth_router, require_auth=require_auth)
register_agent_secret_routes(admin_router, get_current_admin=require_admin)
register_brain_routes(auth_router, discovery=discovery, runtime=runtime, projects=projects)
register_usage_routes(admin_router, sessions=sessions, agent_sessions=agent_sessions)
register_github_routes(admin_router, require_admin=require_admin)
register_group_routes(admin_router, auth_router, require_admin=require_admin, require_auth=require_auth, group_service=group_service)
# v2: register_plugin_routes entfernt
register_tailscale_routes(admin_router, require_admin=require_admin)
register_repo_routes(admin_router, require_admin=require_admin)
register_config_map_routes(admin_router, require_admin=require_admin)
register_server_routes(
    auth_router, admin_router,
    require_auth=require_auth,
    require_admin=require_admin,
)
register_pipeline_routes(
    admin_router,
    require_admin=require_admin,
    notify_fn=None,  # notification_service.notify falls verfügbar
)
register_voice_routes(
    auth_router,
    require_auth=require_auth,
    discovery=discovery,
    orchestrator=orchestrator,
    group_service=group_service,
)

register_openai_compat_routes(
    app,
    discovery=discovery,
    orchestrator=orchestrator,
)


# ================================================================== Status

NETWORK_PROFILE_FILE = settings.network_profile_file
NETWORK_PROFILE_SCRIPT = str(settings.network_profile_script)
NETWORK_PROFILES: dict[str, dict[str, list[int] | bool]] = {
    "full": {
        "tcp_ports": [],
        "udp_ports": [],
        "ufw_enabled": False,
    },
    "lan": {
        "tcp_ports": [22, 80, 3002, 8008, 139, 445],
        "udp_ports": [137, 138],
        "ufw_enabled": True,
    },
    "minimal": {
        "tcp_ports": [22, 80, 3002, 8008],
        "udp_ports": [],
        "ufw_enabled": True,
    },
}


def _normalize_network_profile(profile: str) -> str:
    normalized = (profile or "").strip().lower()
    if normalized not in NETWORK_PROFILES:
        raise HTTPException(400, f"Ungültiges Network-Profil: {profile}")
    return normalized


def _read_network_profile() -> str:
    try:
        return _normalize_network_profile(NETWORK_PROFILE_FILE.read_text(encoding="utf-8"))
    except OSError:
        return "full"


def _write_network_profile(profile: str) -> None:
    NETWORK_PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    NETWORK_PROFILE_FILE.write_text(profile + "\n", encoding="utf-8")
    NETWORK_PROFILE_FILE.chmod(0o600)


def _list_public_listening_ports() -> dict[str, list[int]]:
    import subprocess as _sub

    ports: dict[str, set[int]] = {"tcp": set(), "udp": set()}
    try:
        result = _sub.run(
            ["ss", "-tulpnH"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return {"tcp": [], "udp": []}

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].lower()
        local = parts[4]
        if proto not in ("tcp", "udp"):
            continue
        try:
            host, port_str = local.rsplit(":", 1)
            port = int(port_str)
        except ValueError:
            continue
        host = host.strip("[]")
        if host.startswith("127.") or host == "::1":
            continue
        if host not in ("0.0.0.0", "*", "::") and not host.startswith("192.") and not host.startswith("10.") and not host.startswith("172."):
            continue
        ports[proto].add(port)

    return {"tcp": sorted(ports["tcp"]), "udp": sorted(ports["udp"])}


def _ufw_status_summary() -> dict:
    import re as _re
    import subprocess as _sub

    try:
        result = _sub.run(
            ["sudo", "ufw", "status", "numbered"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as e:
        return {"available": False, "active": False, "rules": [], "error": str(e)}

    output = (result.stdout or "").strip()
    if not output:
        return {"available": True, "active": False, "rules": []}
    output_lower = output.lower()
    if output_lower.startswith("status: inactive") or output_lower.startswith("status: inaktiv"):
        return {"available": True, "active": False, "rules": []}

    rules = []
    for line in output.splitlines():
        if line.startswith("Status:"):
            continue
        match = _re.search(r"\[\s*\d+\]\s+(.+?)\s+ALLOW IN\s+(.+)$", line)
        if not match:
            continue
        rules.append({"rule": match.group(1).strip(), "from": match.group(2).strip()})
    return {"available": True, "active": True, "rules": rules}


def _ufw_allowed_ports(ufw: dict) -> dict[str, list[int]]:
    import re as _re

    allowed = {"tcp": set(), "udp": set()}
    for rule in ufw.get("rules", []):
        rule_text = str(rule.get("rule", "")).lower()
        match = _re.match(r"(\d+)(?:/(tcp|udp))?$", rule_text)
        if not match:
            continue
        port = int(match.group(1))
        proto = match.group(2)
        if proto is None:
            allowed["tcp"].add(port)
            allowed["udp"].add(port)
            continue
        allowed[proto].add(port)
    return {
        "tcp": sorted(allowed["tcp"]),
        "udp": sorted(allowed["udp"]),
    }


def _network_profile_status() -> dict:
    profile = _read_network_profile()
    spec = NETWORK_PROFILES[profile]
    exposed = _list_public_listening_ports()
    ufw = _ufw_status_summary()
    allowed = _ufw_allowed_ports(ufw)

    expected_tcp = sorted(spec["tcp_ports"])  # type: ignore[index]
    expected_udp = sorted(spec["udp_ports"])  # type: ignore[index]
    deviations: list[str] = []

    if spec["ufw_enabled"] and not ufw["active"]:  # type: ignore[index]
        deviations.append("ufw_inactive_for_profile")
    if not spec["ufw_enabled"] and ufw["active"]:  # type: ignore[index]
        deviations.append("ufw_active_while_profile_is_full")

    if profile != "full":
        missing_tcp = sorted(p for p in expected_tcp if p not in allowed["tcp"])
        missing_udp = sorted(p for p in expected_udp if p not in allowed["udp"])
        extra_tcp = sorted(p for p in allowed["tcp"] if p not in expected_tcp)
        extra_udp = sorted(p for p in allowed["udp"] if p not in expected_udp)
        if missing_tcp:
            deviations.append(f"missing_tcp_rules:{','.join(map(str, missing_tcp))}")
        if missing_udp:
            deviations.append(f"missing_udp_rules:{','.join(map(str, missing_udp))}")
        if extra_tcp:
            deviations.append(f"unexpected_tcp_rules:{','.join(map(str, extra_tcp))}")
        if extra_udp:
            deviations.append(f"unexpected_udp_rules:{','.join(map(str, extra_udp))}")

    return {
        "profile": profile,
        "ufw": ufw,
        "expected": {"tcp": expected_tcp, "udp": expected_udp},
        "allowed": allowed,
        "exposed": exposed,
        "deviations": deviations,
    }


GITEA_CONFIG_FILE = str(settings.gitea_config)


def get_gitea_config():
    """Helper fuer Tests und lokale Call-Sites: maskierte Gitea-Konfiguration lesen."""
    p = Path(GITEA_CONFIG_FILE)
    if not p.exists():
        return {"url": "http://127.0.0.1:3001", "org": "hydrahive", "webhook_secret": "", "has_token": False, "token_masked": ""}
    cfg = json.loads(p.read_text(encoding="utf-8"))
    token = cfg.get("token", "")
    return {
        "url": cfg.get("url", "http://127.0.0.1:3001"),
        "org": cfg.get("org", "hydrahive"),
        "webhook_secret": cfg.get("webhook_secret", ""),
        "has_token": bool(token),
        "token_masked": token[:8] + "..." + token[-4:] if token else "",
    }

register_system_routes(
    auth_router,
    admin_router,
    get_hb_scheduler=lambda: hb_scheduler,
    discovery=discovery,
    projects=projects,
    sessions=sessions,
    runtime=runtime,
    agents_dir=AGENTS_DIR,
    projects_dir=PROJECTS_DIR,
    read_network_profile=_read_network_profile,
    network_profile_status=_network_profile_status,
    normalize_network_profile=_normalize_network_profile,
    write_network_profile=_write_network_profile,
    network_profile_script=NETWORK_PROFILE_SCRIPT,
    run_self_update=_run_self_update,
    gitea_config_file=GITEA_CONFIG_FILE,
    app_version=APP_VERSION,
    logger=logger,
)

@admin_router.get("/admin/resources")
def get_resources():
    """System-Ressourcen: CPU, RAM, Disk, Prozess + Token-Verbrauch pro Agent."""
    import psutil, os
    cpu     = psutil.cpu_percent(interval=0.2)
    vm      = psutil.virtual_memory()
    disk    = psutil.disk_usage("/")
    proc    = psutil.Process(os.getpid())
    with proc.oneshot():
        proc_cpu = proc.cpu_percent(interval=None)
        proc_mem = proc.memory_info().rss

    agent_tokens: dict[str, int] = {}
    for agent_id in list(rate_limiter._agent_token_usage.keys()):
        usage = rate_limiter.get_token_usage_hour(agent_id)
        if usage > 0:
            agent_tokens[agent_id] = usage

    runtime_status = runtime.status_all()

    return {
        "system": {
            "cpu_percent":       cpu,
            "ram_total_mb":      vm.total // (1024 * 1024),
            "ram_used_mb":       vm.used  // (1024 * 1024),
            "ram_percent":       vm.percent,
            "disk_total_gb":     round(disk.total / (1024**3), 1),
            "disk_used_gb":      round(disk.used  / (1024**3), 1),
            "disk_percent":      disk.percent,
        },
        "process": {
            "cpu_percent": proc_cpu,
            "ram_mb":      round(proc_mem / (1024 * 1024), 1),
        },
        "agents": {
            agent_id: {
                "tokens_last_hour": agent_tokens.get(agent_id, 0),
                "running":          (runtime_status.get(agent_id) or {}).get("status") == "running",
            }
            for agent_id in discovery.agents
        },
        "token_warn_threshold": rate_limiter.settings.agent_token_warn_per_hour,
    }


@admin_router.get("/admin/metrics")
def get_metrics():
    """Zeigt Token-Verbrauch pro Agent (letzte Stunde) und andere Runtime-Metriken."""
    agent_tokens: dict[str, int] = {}
    for agent_id in list(rate_limiter._agent_token_usage.keys()):
        usage = rate_limiter.get_token_usage_hour(agent_id)
        if usage > 0:
            agent_tokens[agent_id] = usage
    return {
        "token_usage_last_hour": agent_tokens,
        "token_warn_threshold": rate_limiter.settings.agent_token_warn_per_hour,
        "agent_call_limit_per_min": rate_limiter.settings.agent_call_max,
        "sentry_active": bool(_SENTRY_DSN),
    }


@admin_router.get("/admin/session-metrics")
def get_session_metrics(_a=Depends(require_admin)):
    """#512: Kontext- und Turn-Metriken pro Projekt-Session."""
    from .session_metrics import metrics as _sm
    return _sm.snapshot_all()


@admin_router.get("/admin/session-metrics/{project_id}")
def get_session_metrics_project(project_id: str, _a=Depends(require_admin)):
    """#512: Metriken für ein einzelnes Projekt."""
    from .session_metrics import metrics as _sm
    return _sm.snapshot(project_id)


@admin_router.get("/admin/turn-journal/{project_id}")
def get_turn_journal(project_id: str, limit: int = 100, _a=Depends(require_admin)):
    """#523: Turn Journal Events für ein Projekt."""
    from .turn_journal import journal as _tj
    # Aktive Session finden
    session = sessions.get_active(project_id)
    if not session:
        return {"events": [], "stats": {}}
    events = _tj.get_session_events(session.id, limit=limit)
    stats = _tj.get_project_stats(project_id)
    return {"events": events, "stats": stats}


# ── #483: Proactive Mode API ──────────────────────────────────────────────────

@admin_router.get("/admin/proactive/tasks")
def list_proactive_tasks(_a=Depends(require_admin)):
    """#483: Alle proaktiven Tasks auflisten."""
    from .proactive_mode import proactive_service
    return {"tasks": proactive_service.list_tasks()}


@admin_router.post("/admin/proactive/tasks")
async def create_proactive_task(body: dict, _a=Depends(require_admin)):
    """#483: Neuen proaktiven Task erstellen."""
    from .proactive_mode import proactive_service, ProactiveTask
    import uuid
    task = ProactiveTask(
        id=body.get("id", uuid.uuid4().hex[:8]),
        agent_id=body["agent_id"],
        project_id=body["project_id"],
        prompt=body["prompt"],
        interval_seconds=body.get("interval", 3600),
        enabled=body.get("enabled", True),
    )
    proactive_service.add_task(task)
    from .settings import settings
    proactive_service.save_to_config(settings.etc_dir)
    return {"ok": True, "task_id": task.id}


@admin_router.delete("/admin/proactive/tasks/{task_id}")
def delete_proactive_task(task_id: str, _a=Depends(require_admin)):
    """#483: Proaktiven Task löschen."""
    from .proactive_mode import proactive_service
    proactive_service.remove_task(task_id)
    from .settings import settings
    proactive_service.save_to_config(settings.etc_dir)
    return {"ok": True}


# ── Wiki (BookStack) Config API ───────────────────────────────────────────────

_WIKI_CONFIG_PATH = settings.etc_dir / "bookstack.json"


@admin_router.get("/admin/wiki/config")
def get_wiki_config(_a=Depends(require_admin)):
    """BookStack Wiki Konfiguration lesen."""
    try:
        import json as _j
        data = _j.loads(_WIKI_CONFIG_PATH.read_text(encoding="utf-8"))
        # Secret nicht exponieren
        if data.get("token_secret"):
            data["token_secret"] = "••••••••"
        return data
    except (OSError, ValueError):
        return {"base_url": "", "token_id": "", "token_secret": ""}


@admin_router.put("/admin/wiki/config")
def set_wiki_config(body: dict, _a=Depends(require_admin)):
    """BookStack Wiki Konfiguration speichern."""
    import json as _j
    # Bestehende Config laden (um Secret zu behalten wenn nicht neu gesetzt)
    existing = {}
    try:
        existing = _j.loads(_WIKI_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass

    config = {
        "base_url": body.get("base_url", existing.get("base_url", "")),
        "token_id": body.get("token_id", existing.get("token_id", "")),
        "token_secret": body.get("token_secret", existing.get("token_secret", "")),
    }
    try:
        _WIKI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _WIKI_CONFIG_PATH.write_text(_j.dumps(config, indent=2), encoding="utf-8")
        return {"ok": True}
    except OSError as e:
        raise HTTPException(500, f"Config speichern fehlgeschlagen: {e}")


# ── #490: Peer Discovery API ─────────────────────────────────────────────────

@auth_router.get("/peers")
def list_peers(auth: tuple[str, str] = Depends(require_auth)):
    """#490: Alle aktiven Peer-Agenten auflisten."""
    from .peer_discovery import peer_registry
    username, _ = auth
    agent_id = f"personal_{username}"
    return {"peers": peer_registry.list_peers(exclude=agent_id)}


@auth_router.post("/peers/{agent_id}/message")
def send_peer_message(agent_id: str, body: dict, auth: tuple[str, str] = Depends(require_auth)):
    """#490: Nachricht an einen Peer-Agenten senden."""
    from .peer_discovery import peer_registry
    username, _ = auth
    from_agent = f"personal_{username}"
    content = body.get("content", "")
    if not content:
        raise HTTPException(400, "content ist Pflicht")
    ok = peer_registry.send_message(from_agent, agent_id, content)
    if not ok:
        raise HTTPException(404, f"Peer '{agent_id}' nicht gefunden")
    return {"ok": True, "to": agent_id}


@auth_router.get("/peers/messages")
def get_peer_messages(auth: tuple[str, str] = Depends(require_auth)):
    """#490: Eigene ungelesene Peer-Messages abholen."""
    from .peer_discovery import peer_registry
    username, _ = auth
    agent_id = f"personal_{username}"
    return {"messages": peer_registry.get_messages(agent_id)}


@admin_router.get("/admin/agents/live")
def get_agents_live(_a=Depends(require_admin)):
    """
    Live-Status aller Agenten: Runtime-Status + Token-Verbrauch + aktuelle Aktivität.
    Für die Live-Übersichtsseite — Polling alle 3s empfohlen.
    """
    runtime_status = runtime.status_all()
    result = []
    seen = set()
    for agent_id, rs in runtime_status.items():
        seen.add(agent_id)
        tokens_1h = rate_limiter.get_token_usage_hour(agent_id)
        result.append({
            "id":               agent_id,
            "identity":         rs.get("identity", agent_id),
            "type":             rs.get("type"),
            "model":            rs.get("model"),
            "status":           rs.get("status"),
            "current_activity": rs.get("current_activity"),
            "restart_count":    rs.get("restart_count", 0),
            "last_heartbeat_age":  rs.get("last_heartbeat_age"),
            "heartbeat_timeout":   rs.get("heartbeat_timeout"),
            "heartbeat_interval":  rs.get("heartbeat_interval"),
            "tokens_1h":        tokens_1h,
            "token_warn_threshold": rate_limiter.settings.agent_token_warn_per_hour,
            "token_history":    rate_limiter.get_token_history(agent_id, minutes=60, bucket_minutes=5),
            # #373: Performance Metrics
            "total_requests":   rs.get("total_requests", 0),
            "avg_response_ms":  rs.get("avg_response_ms", 0),
            "last_response_ms": rs.get("last_response_ms", 0),
            "error_rate":       rs.get("error_rate", 0),
        })
    # Auch registrierte Agenten aus Discovery anzeigen die noch nie gestartet wurden
    for cfg in discovery.agents.values():
        if cfg.id not in seen:
            result.append({
                "id":               cfg.id,
                "identity":         cfg.identity,
                "type":             cfg.type,
                "model":            cfg.llm.model if cfg.llm else None,
                "status":           "stopped",
                "current_activity": None,
                "restart_count":    0,
                "last_heartbeat_age":  None,
                "heartbeat_timeout":   None,
                "heartbeat_interval":  None,
                "tokens_1h":        rate_limiter.get_token_usage_hour(cfg.id),
                "token_warn_threshold": rate_limiter.settings.agent_token_warn_per_hour,
            })
    # v2: Projekte als "Agenten" anzeigen die kein eigenes Agent-Verzeichnis haben
    for pid, pcfg in projects.projects.items():
        if pid in seen:
            continue
        seen.add(pid)
        result.append({
            "id":               pid,
            "identity":         pcfg.identity.name,
            "type":             "v2-project",
            "model":            getattr(pcfg.llm, "model", None),
            "status":           "ready",
            "current_activity": None,
            "restart_count":    0,
            "last_heartbeat_age":  None,
            "heartbeat_timeout":   None,
            "heartbeat_interval":  None,
            "tokens_1h":        rate_limiter.get_token_usage_hour(pid),
            "token_warn_threshold": rate_limiter.settings.agent_token_warn_per_hour,
            "token_history":    rate_limiter.get_token_history(pid, minutes=60, bucket_minutes=5),
            "total_requests":   0,
            "avg_response_ms":  0,
            "last_response_ms": 0,
            "error_rate":       0,
        })
    return {"agents": result, "count": len(result)}

app.include_router(public_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(internal_router)
