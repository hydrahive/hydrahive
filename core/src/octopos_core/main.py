"""
main.py — OctopOS Core Runtime Einstiegspunkt (#4, #6, #7, #8, #9, #10, #11, #12, #35)

FastAPI-App mit Lifespan-Management:
- AgentDiscovery + AgentRuntime + ProjectLoader + SessionManager + Orchestrator
- REST-Endpoints fuer Agenten, Projekte, Sessions und Nachrichten
"""

import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import asyncio
import time
from collections import defaultdict

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
from .router_agent_chat import register_agent_chat_routes
from .router_agent_skills import register_agent_skill_routes
from .router_project_integrations import register_project_integration_routes
from .router_projects import register_project_routes
from .router_system import register_system_routes
from .router_user_integrations import register_user_integration_routes, setup_discord_clients
from .router_users import register_user_routes
from .session_manager import MessageRole, SessionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

AGENTS_DIR   = "/agents"
PROJECTS_DIR = "/projects"

CRED_FILE        = "/etc/octopos/admin_credentials"
MCP_SERVERS_FILE = "/etc/octopos/mcp_servers.json"
JWT_SECRET   = ""    # wird im Lifespan aus Datei geladen oder generiert
JWT_ALG      = "HS256"
JWT_EXPIRE_H = 24    # Token-Gültigkeit in Stunden

# Rate-Limiting für /auth/login (#70)
_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_LOGIN_MAX      = 10     # max. Versuche
_LOGIN_WIN_S    = 60     # pro Minute
_LOGIN_MAX_KEYS = 10000  # harte Obergrenze unterschiedlicher IP-Keys

# Rate-Limiting für /message Endpoints (#72)
_MESSAGE_ATTEMPTS: dict[tuple[str, str], list[float]] = defaultdict(list)
_MESSAGE_MAX      = 50     # max. Nachrichten pro Fenster
_MESSAGE_WIN_S    = 60     # pro Minute
_MESSAGE_MAX_KEYS = 50000  # harte Obergrenze unterschiedlicher user+project-Keys

def _prune_attempt_map[K](attempts: dict[K, list[float]], window_s: float) -> int:
    """Entfernt abgelaufene Einträge. Gibt Anzahl entfernter Keys zurück."""
    now = time.monotonic()
    remove_keys: list[K] = [k for k, ts in attempts.items() if not any(now - t < window_s for t in ts)]
    for k in remove_keys:
        attempts.pop(k, None)
    return len(remove_keys)

def _check_login_rate(ip: str) -> None:
    now = time.monotonic()
    if len(_LOGIN_ATTEMPTS) > _LOGIN_MAX_KEYS:
        _prune_attempt_map(_LOGIN_ATTEMPTS, _LOGIN_WIN_S)
        if len(_LOGIN_ATTEMPTS) > _LOGIN_MAX_KEYS:
            for k in list(_LOGIN_ATTEMPTS.keys())[:len(_LOGIN_ATTEMPTS) - _LOGIN_MAX_KEYS]:
                _LOGIN_ATTEMPTS.pop(k, None)
    _LOGIN_ATTEMPTS[ip] = [t for t in _LOGIN_ATTEMPTS[ip] if now - t < _LOGIN_WIN_S]
    if len(_LOGIN_ATTEMPTS[ip]) >= _LOGIN_MAX:
        raise HTTPException(429, "Zu viele Login-Versuche — bitte eine Minute warten")
    _LOGIN_ATTEMPTS[ip].append(now)


def _check_message_rate(user_id: str, project_id: str) -> None:
    """Rate-Limit für Nachrichten pro User+Projekt"""
    key = (user_id, project_id)
    now = time.monotonic()
    if len(_MESSAGE_ATTEMPTS) > _MESSAGE_MAX_KEYS:
        _prune_attempt_map(_MESSAGE_ATTEMPTS, _MESSAGE_WIN_S)
        if len(_MESSAGE_ATTEMPTS) > _MESSAGE_MAX_KEYS:
            for k in list(_MESSAGE_ATTEMPTS.keys())[:len(_MESSAGE_ATTEMPTS) - _MESSAGE_MAX_KEYS]:
                _MESSAGE_ATTEMPTS.pop(k, None)
    _MESSAGE_ATTEMPTS[key] = [t for t in _MESSAGE_ATTEMPTS[key] if now - t < _MESSAGE_WIN_S]
    if len(_MESSAGE_ATTEMPTS[key]) >= _MESSAGE_MAX:
        raise HTTPException(429, f"Zu viele Nachrichten — max. {_MESSAGE_MAX} pro Minute")
    _MESSAGE_ATTEMPTS[key].append(now)


_setup_lock = asyncio.Lock()   # verhindert parallele Setup-Requests (#71)

discovery        = AgentDiscovery(AGENTS_DIR)
runtime          = AgentRuntime()
projects         = ProjectLoader(PROJECTS_DIR)
sessions         = SessionManager(PROJECTS_DIR)
orchestrator     = Orchestrator(discovery, runtime, sessions)
agent_sessions   = SessionManager(AGENTS_DIR)          # Direkte Agenten-Chats
agent_orchestrator = Orchestrator(discovery, runtime, agent_sessions)
provisioner:  Provisioner | None = None              # initialisiert im Lifespan
hb_scheduler: "AgentHeartbeatScheduler | None" = None  # initialisiert im Lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    global provisioner, JWT_SECRET, hb_scheduler
    logger.info("OctopOS Core startet...")
    discovery.start()
    projects.start()
    sessions.start()
    agent_sessions.start()
    await runtime.start(list(discovery.agents.values()))

    # JWT-Secret laden oder generieren
    JWT_SECRET = _load_or_create_jwt_secret()
    logger.info("JWT-Secret geladen")

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
        provisioner = Provisioner("", "your-hostname")

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

    # Heartbeat-Scheduler starten (#77)
    from .heartbeat import AgentHeartbeatScheduler as _HBS
    hb_scheduler = _HBS(discovery, projects, orchestrator, AGENTS_DIR)
    hb_task = asyncio.create_task(hb_scheduler.run(), name="heartbeat-scheduler")

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

    # Rate-Limiter Cleanup-Task (verhindert unbounded key growth)
    async def _rate_limit_cleanup_loop():
        while True:
            try:
                await asyncio.sleep(300)  # alle 5 Minuten
                _prune_attempt_map(_LOGIN_ATTEMPTS, _LOGIN_WIN_S)
                _prune_attempt_map(_MESSAGE_ATTEMPTS, _MESSAGE_WIN_S)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Rate-limit cleanup Fehler: %s", e)

    rate_limit_cleanup_task = asyncio.create_task(
        _rate_limit_cleanup_loop(), name="rate-limit-cleanup"
    )

    # Gitea-Verbindung prüfen (best-effort, blockiert nicht den Start)
    try:
        from .gitea import get_gitea_client
        gitea_ver = await get_gitea_client()._get("/version")
        logger.info("Gitea verbunden: version=%s", gitea_ver.get("version", "?"))
    except Exception as _ge:
        logger.warning("Gitea nicht erreichbar beim Start: %s — Git-Tools nur eingeschränkt verfügbar", _ge)

    logger.info("OctopOS Core bereit")
    yield
    hb_task.cancel()
    cleanup_task.cancel()
    rate_limit_cleanup_task.cancel()
    logger.info("OctopOS Core faehrt herunter...")
    await runtime.stop()
    projects.stop()
    discovery.stop()
    logger.info("OctopOS Core gestoppt")


def _read_server_name(
    toml_path: str = "/etc/conduwuit/conduwuit.toml",
    config_path: str = "/etc/octopos/matrix_server_name",
) -> str:
    env_server_name = os.environ.get("OCTOPOS_MATRIX_SERVER_NAME", "").strip()
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

    fallback = "your-hostname"
    logger.warning(
        "Matrix server_name nicht konfiguriert, verwende Fallback '%s'. "
        "Setze OCTOPOS_MATRIX_SERVER_NAME oder /etc/octopos/matrix_server_name fuer nicht-default Installationen.",
        fallback,
    )
    return fallback


def _load_or_create_jwt_secret(secret_file: str = "/etc/octopos/jwt_secret") -> str:
    """JWT-Secret aus Datei laden oder einmalig generieren und speichern."""
    path = Path(secret_file)
    if path.exists():
        return path.read_text().strip()
    secret = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret)
    path.chmod(0o600)
    logger.info("Neues JWT-Secret generiert: %s", secret_file)
    return secret


def _read_admin_password() -> str:
    """Admin-Passwort aus /etc/octopos/admin_credentials lesen."""
    try:
        for line in Path(CRED_FILE).read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() in ("console_password", "matrix_admin_password"):
                    return v.strip()
    except OSError:
        pass
    return ""


def _make_jwt(username: str, role: str = "user") -> str:
    """JWT-Token für den angegebenen User erstellen."""
    from jose import jwt as jose_jwt
    payload = {
        "sub":  username,
        "role": role,
        "exp":  datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_H),
        "iat":  datetime.now(timezone.utc),
    }
    return jose_jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _verify_jwt(token: str) -> tuple[str, str]:
    """Token verifizieren — gibt (username, role) zurück oder wirft HTTPException 401."""
    if not JWT_SECRET:
        raise HTTPException(503, "Server noch nicht bereit — JWT-Secret fehlt")
    from jose import JWTError, jwt as jose_jwt
    try:
        payload = jose_jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return payload["sub"], payload.get("role", "user")
    except (JWTError, KeyError):
        raise HTTPException(401, "Ungültiger oder abgelaufener Token")


_bearer = HTTPBearer(auto_error=False)


def require_auth(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> tuple[str, str]:
    """FastAPI-Dependency: JWT aus Bearer-Header prüfen. Gibt (username, role) zurück."""
    if not creds:
        raise HTTPException(401, "Kein Authorization-Header")
    return _verify_jwt(creds.credentials)


def require_admin(auth: tuple[str, str] = Depends(require_auth)) -> tuple[str, str]:
    """Nur Admin-User — wirft 403 wenn role != admin."""
    username, role = auth
    if role != "admin":
        raise HTTPException(403, f"Keine Berechtigung — Admin erforderlich (du bist '{role}')")
    return auth


public_router = APIRouter()
auth_router = APIRouter(dependencies=[Depends(require_auth)])
admin_router = APIRouter(dependencies=[Depends(require_admin)])


def _is_local_request(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    return client.host in {"127.0.0.1", "::1", "localhost"}


def require_auth_or_localhost(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> tuple[str, str]:
    """JWT fuer externe Aufrufe, localhost-Bypass fuer interne Core-Calls."""
    if _is_local_request(request):
        return ("internal", "admin")
    return require_auth(creds)


def require_admin_or_localhost(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> tuple[str, str]:
    """Admin fuer externe Aufrufe, localhost-Bypass fuer interne Core-Calls."""
    if _is_local_request(request):
        return ("internal", "admin")
    return require_admin(require_auth(creds))


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
    title="OctopOS Core",
    version="0.1.0",
    lifespan=lifespan,
)


# ================================================================== Auth (#56)

class LoginRequest(BaseModel):
    username: str
    password: str


@public_router.get("/setup/status")
def setup_status():
    """Gibt zurück ob der Setup-Wizard noch ausgeführt werden muss."""
    users = _load_users()
    return {"needs_setup": len(users) == 0}


class SetupRequest(BaseModel):
    username: str
    password: str


@public_router.post("/setup", status_code=201)
async def run_setup(req: SetupRequest):
    """
    Ersteinrichtung — legt den ersten Admin-User an.
    Nur verfügbar wenn noch keine User existieren.
    Lock verhindert parallele Requests (#71).
    """
    import re as _re
    from datetime import datetime as _dt

    async with _setup_lock:
        users = _load_users()
        if users:
            raise HTTPException(403, "Setup bereits abgeschlossen")
        if not _re.match(r"^[a-z0-9_.-]+$", req.username):
            raise HTTPException(400, "Username darf nur a-z, 0-9, _ . - enthalten")
        if len(req.password) < 8:
            raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")

        server_name = _read_server_name()
        matrix_ok   = await _matrix_register(req.username, req.password, server_name)

        users[req.username] = {
            "password_hash": _hash_password(req.password),
            "role":          "admin",
            "matrix_id":     f"@{req.username}:{server_name}",
            "matrix_ok":     matrix_ok,
            "created_at":    _dt.now().isoformat(),
        }
        _save_users(users)
        logger.info("Setup abgeschlossen: erster Admin-User '%s' angelegt", req.username)
        return {"created": True, "username": req.username, "role": "admin"}


@public_router.post("/auth/login")
def login(req: LoginRequest, request: Request):
    """
    Login: prüft zuerst users.json, dann Fallback auf admin_credentials.
    Gibt JWT-Bearer-Token zurück.
    """
    _check_login_rate(request.client.host if request.client else "unknown")
    # Primär: users.json
    users = _load_users()
    if users:
        user = users.get(req.username)
        if user and _verify_password(req.password, user.get("password_hash", "")):
            role  = user.get("role", "user")
            token = _make_jwt(req.username, role)
            logger.info("Login erfolgreich (users.json): %s", req.username)
            return {"access_token": token, "token_type": "bearer", "role": role, "username": req.username}
        raise HTTPException(401, "Ungültige Zugangsdaten")

    # Fallback: admin_credentials (vor Setup oder Legacy-Betrieb)
    admin_pass = _read_admin_password()
    if not admin_pass:
        raise HTTPException(503, "Kein Admin-Passwort konfiguriert — Setup erforderlich")
    if req.username != "admin" or req.password != admin_pass:
        raise HTTPException(401, "Ungültige Zugangsdaten")
    token = _make_jwt(req.username, "admin")
    logger.info("Login erfolgreich (admin_credentials): %s", req.username)
    return {"access_token": token, "token_type": "bearer", "role": "admin", "username": req.username}


@auth_router.get("/auth/me")
def whoami(auth: tuple[str,str] = Depends(require_auth)):
    """Token-Validierung — gibt aktuellen User + Role zurück."""
    username, role = auth
    return {"username": username, "role": role}


# ================================================================== Agenten

@public_router.get("/health")
def health():
    return {"status": "ok", "service": "octopos-core"}


@auth_router.get("/agents")
def list_agents(_a: tuple[str, str] = Depends(require_auth)):
    registered = discovery.agents
    running    = runtime.status_all()
    return {
        agent_id: {
            "config": {
                "type":            cfg.type,
                "identity":        cfg.identity,
                "model":           cfg.llm.model,
                "fallback_models": cfg.llm.fallback_models,
            },
            "runtime": running.get(agent_id),
        }
        for agent_id, cfg in registered.items()
    }


@auth_router.get("/agents/{agent_id}")
def get_agent(agent_id: str, _a: tuple[str, str] = Depends(require_auth)):
    cfg = discovery.get(agent_id)
    if not cfg:
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
    return {
        "config":  cfg.model_dump(exclude={"agent_dir"}),
        "runtime": runtime.status_all().get(agent_id),
    }


class AgentLlmPatchRequest(BaseModel):
    fallback_models: list[str]


@admin_router.patch("/agents/{agent_id}/llm")
def patch_agent_llm(
    agent_id: str,
    req: AgentLlmPatchRequest,
    _a: tuple = Depends(require_admin),
):
    """Aktualisiert llm.fallback_models in agent.yaml."""
    cfg = discovery.get(agent_id)
    if not cfg or not cfg.agent_dir:
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
    yaml_path = cfg.agent_dir / "agent.yaml"
    try:
        import yaml as _yaml
        raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if "llm" not in raw:
            raw["llm"] = {}
        if req.fallback_models:
            raw["llm"]["fallback_models"] = req.fallback_models
        else:
            raw["llm"].pop("fallback_models", None)
        yaml_path.write_text(_yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        # watchdog lädt agent.yaml automatisch neu (on_modified)
    except Exception as e:
        raise HTTPException(500, f"Fehler beim Speichern: {e}")
    return {"ok": True, "agent_id": agent_id, "fallback_models": req.fallback_models}


class SpawnRequest(BaseModel):
    agent_id: str


@app.post("/agents/spawn")
async def spawn_task_agent(req: SpawnRequest, _a: tuple = Depends(require_admin_or_localhost)):
    cfg = discovery.get(req.agent_id)
    if not cfg:
        raise HTTPException(404, f"Agent '{req.agent_id}' nicht in Discovery")
    if cfg.type != "worker":
        raise HTTPException(400, f"Nur worker koennen gespawnt werden, nicht {cfg.type}")
    await runtime.spawn_task_agent(cfg)
    return {"spawned": req.agent_id}


@app.post("/agents/{agent_id}/heartbeat")
def agent_heartbeat(agent_id: str, _a: tuple = Depends(require_auth_or_localhost)):
    runtime.heartbeat(agent_id)
    return {"ok": True}


# ================================================================== Orchestrator / Agenten-Chat

class IncomingMessage(BaseModel):
    content: str
    sender:  str = "user"
    execution_mode: Literal["safe", "elevated", "root"] | None = None


@admin_router.get("/logs/core")
def get_core_logs(lines: int = 200, _a: tuple[str, str] = Depends(require_admin)):
    """
    Core-Logs gesamt — fuer System-Screen.
    """
    import subprocess as _sub
    lines = max(10, min(lines, 2000))
    try:
        result = _sub.run(
            ["journalctl", "-u", "octopos-core", "-n", str(lines),
             "--no-pager", "--output=short-iso"],
            capture_output=True, text=True, timeout=5
        )
        log_lines = result.stdout.splitlines()
        return {"lines": log_lines, "count": len(log_lines)}
    except Exception as e:
        return {"lines": [str(e)], "count": 1}


# ================================================================== Audit-Log

AUDIT_LOG_FILE = Path("/var/log/octopos/audit.jsonl")


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
        _ensure_audit_log_path()
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
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


@admin_router.get("/audit/logs")
def get_audit_logs(
    limit:      int = 100,
    project_id: str = "",
    user:       str = "",
    action:     str = "",
):
    """Audit-Log lesen mit optionalen Filtern."""
    limit = max(10, min(limit, 1000))
    logs  = _read_audit_logs(limit, project_id, user, action)
    return {"logs": logs, "count": len(logs)}


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
)


# ================================================================== User-Verwaltung

USERS_FILE = Path("/etc/octopos/users.json")


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


def _hash_password(password: str) -> str:
    import hashlib, secrets
    salt = secrets.token_hex(16)
    h    = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"pbkdf2:{salt}:{h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    import hashlib
    try:
        _, salt, h = stored.split(":", 2)
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
        return check.hex() == h
    except Exception:
        return False


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


def _create_personal_agent(username: str) -> str:
    """Persönlichen Agenten für einen User anlegen. Gibt agent_id zurück."""
    import yaml as _yaml

    agent_id = f"personal_{username}"
    agent_dir = Path(AGENTS_DIR) / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "skills").mkdir(exist_ok=True)
    (agent_dir / "memory").mkdir(exist_ok=True)

    model = "claude-haiku-4-5-20251001"
    try:
        llm_raw = json.loads(Path("/etc/octopos/llm_config.json").read_text())
        providers = llm_raw.get("providers", {})
        if providers.get("claude_max", {}).get("enabled"):
            model = "claude-haiku-4-5-20251001"
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
        "tools": ["file_read", "file_write", "shell_exec"],
        "execution_modes": {
            "default": "safe",
            "safe": {
                "permissions": ["filesystem.read", "memory.read", "memory.write"],
            },
            "elevated": {
                "permissions": ["filesystem.read", "filesystem.write", "memory.read", "memory.write"],
            },
            "root": {
                "permissions": ["filesystem.read", "filesystem.write", "memory.read", "memory.write", "shell.exec"],
            },
        },
        "heartbeat": {"interval": "60s", "timeout": "180s", "on_failure": "ignore"},
    }

    (agent_dir / "agent.yaml").write_text(
        _yaml.dump(agent_data, allow_unicode=True, default_flow_style=False), encoding="utf-8"
    )
    (agent_dir / "soul.md").write_text(soul_text, encoding="utf-8")
    discovery._register(agent_dir)
    logger.info("Persönlicher Agent angelegt: %s (model=%s)", agent_id, model)
    audit_log("personal_agent.create", user=username, target=agent_id)
    return agent_id


def _ensure_personal_agent(username: str):
    """Persönlichen Agenten laden oder lazy anlegen."""
    agent_id = f"personal_{username}"
    agent_dir = Path(AGENTS_DIR) / agent_id
    if not agent_dir.exists():
        _create_personal_agent(username)
    cfg = discovery.get(agent_id)
    if cfg is None and agent_dir.exists():
        cfg = load_agent_config_direct(agent_dir)
    return agent_id, cfg


register_user_routes(
    auth_router,
    admin_router,
    require_auth=require_auth,
    require_admin=require_admin,
    load_users=_load_users,
    save_users=_save_users,
    read_server_name=_read_server_name,
    matrix_register=_matrix_register,
    hash_password=_hash_password,
    agents_dir=AGENTS_DIR,
    ensure_personal_agent=_ensure_personal_agent,
    runtime=runtime,
    agent_sessions=agent_sessions,
    agent_orchestrator=agent_orchestrator,
    audit_log=audit_log,
    logger=logger,
    incoming_message_model=IncomingMessage,
)


WKS_KEYS_DIR = Path("/etc/octopos/wks_keys")


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
)


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
)


register_agent_skill_routes(
    auth_router,
    require_auth=require_auth,
    check_agent_write=_check_agent_write,
    agents_dir=AGENTS_DIR,
    logger=logger,
)


# ================================================================== Agent CRUD

class CreateAgentRequest(BaseModel):
    id:          str
    type:        str   # boss | specialist | worker
    identity:    str
    model:       str
    temperature: float = 0.7
    max_tokens:  int   = 4096
    soul:        str   = ""
    tools:       list[str] = []
    fallback_models: list[str] = []
    mcp_servers:     list[str] = []
    heartbeat_interval:  str = "30s"
    heartbeat_timeout:   str = "90s"
    heartbeat_on_failure: str = "restart"


@admin_router.post("/agents", status_code=201)
async def create_agent(req: CreateAgentRequest, _a: tuple = Depends(require_admin)):
    """
    Neuen Agenten anlegen: /agents/<id>/agent.yaml + soul.md schreiben.
    Hot-Reload registriert ihn automatisch.
    """
    import re as _re, asyncio as _asyncio
    import yaml as _yaml

    if not _re.match(r"^[a-z0-9_-]+$", req.id):
        raise HTTPException(400, "Agent-ID darf nur a-z, 0-9, _ und - enthalten")
    if req.type not in {"boss", "specialist", "worker"}:
        raise HTTPException(400, f"Ungültiger Typ: {req.type}")
    if discovery.get(req.id):
        raise HTTPException(409, f"Agent '{req.id}' existiert bereits")

    agent_dir = Path(AGENTS_DIR) / req.id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "skills").mkdir(exist_ok=True)
    (agent_dir / "memory").mkdir(exist_ok=True)

    agent_data = {
        "id":       req.id,
        "type":     req.type,
        "identity": req.identity,
        "llm": {
            "model":       req.model,
            "temperature": req.temperature,
            "max_tokens":  req.max_tokens,
        },
        "soul":     "./soul.md" if req.soul else None,
        "tools":    req.tools,
        "heartbeat": {
            "interval":   req.heartbeat_interval,
            "timeout":    req.heartbeat_timeout,
            "on_failure": req.heartbeat_on_failure,
        },
    }
    if req.fallback_models:
        agent_data["llm"]["fallback_models"] = req.fallback_models
    if req.mcp_servers:
        agent_data["mcp_servers"] = req.mcp_servers
    if not agent_data["soul"]:
        del agent_data["soul"]

    yaml_path = agent_dir / "agent.yaml"
    yaml_path.write_text(
        _yaml.dump(agent_data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8"
    )

    soul_path = agent_dir / "soul.md"
    soul_path.write_text(req.soul or f"# {req.identity}\n\nDu bist {req.identity}, ein KI-Agent.\n",
                         encoding="utf-8")

    logger.info("Agent angelegt: %s (%s)", req.id, req.type)
    audit_log("agent.create", target=req.id, details={"type": req.type, "model": req.model})
    await _asyncio.sleep(0.3)

    cfg = discovery.get(req.id)
    if cfg is None:
        from .agent_discovery import AgentDiscovery as _AD
        cfg = load_agent_config_direct(agent_dir)

    return {
        "created":    True,
        "agent_id":   req.id,
        "agent_dir":  str(agent_dir),
        "yaml_path":  str(yaml_path),
        "registered": cfg is not None,
    }


@admin_router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, req: CreateAgentRequest, _a: tuple = Depends(require_admin)):
    """Agent-Config aktualisieren — überschreibt agent.yaml."""
    import asyncio as _asyncio
    import yaml as _yaml

    agent_dir = Path(AGENTS_DIR) / agent_id
    if not agent_dir.exists():
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

    llm_data: dict = {
        "model":       req.model,
        "temperature": req.temperature,
        "max_tokens":  req.max_tokens,
    }
    if req.fallback_models:
        llm_data["fallback_models"] = req.fallback_models

    agent_data = {
        "id":       req.id or agent_id,
        "type":     req.type,
        "identity": req.identity,
        "llm":      llm_data,
        "tools":    req.tools,
        "heartbeat": {
            "interval":   req.heartbeat_interval,
            "timeout":    req.heartbeat_timeout,
            "on_failure": req.heartbeat_on_failure,
        },
    }
    if req.mcp_servers:
        agent_data["mcp_servers"] = req.mcp_servers
    if req.soul:
        agent_data["soul"] = "./soul.md"
        (agent_dir / "soul.md").write_text(req.soul, encoding="utf-8")

    yaml_path = agent_dir / "agent.yaml"
    yaml_path.write_text(
        _yaml.dump(agent_data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8"
    )
    # Discovery sofort aktualisieren — nicht auf Watchdog warten
    discovery._register(agent_dir)
    logger.info("Agent aktualisiert: %s", agent_id)
    return {"updated": True, "agent_id": agent_id}


@admin_router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, _a: tuple = Depends(require_admin)):
    """Agent deaktivieren — benennt Verzeichnis um (kein Datenverlust)."""
    import shutil as _shutil
    agent_dir = Path(AGENTS_DIR) / agent_id
    if not agent_dir.exists():
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
    disabled_dir = Path(AGENTS_DIR) / f"_{agent_id}_disabled"
    agent_dir.rename(disabled_dir)
    logger.info("Agent deaktiviert: %s → %s", agent_dir, disabled_dir)
    audit_log("agent.delete", target=agent_id)
    return {"disabled": True, "agent_id": agent_id, "moved_to": str(disabled_dir)}


@auth_router.get("/agents/{agent_id}/soul")
def get_agent_soul(agent_id: str, _a: tuple[str, str] = Depends(require_auth)):
    """soul.md eines Agenten lesen."""
    soul_path = Path(AGENTS_DIR) / agent_id / "soul.md"
    if not soul_path.exists():
        return {"soul": "", "exists": False}
    return {"soul": soul_path.read_text(encoding="utf-8"), "exists": True}


def load_agent_config_direct(agent_dir: Path):
    """Fallback falls Hot-Reload noch nicht gegriffen hat."""
    from .agent_config import load_agent_config
    cfg = load_agent_config(agent_dir)
    if cfg:
        with discovery._lock:
            discovery._agents[cfg.id] = cfg
    return cfg


# ================================================================== Provisioning


@admin_router.delete("/projects/{project_id}")
async def delete_project(project_id: str, _a: tuple = Depends(require_admin)):
    """
    Projekt deprovisionieren und deaktivieren.
    - Agenten stoppen
    - Samba-Share entfernen
    - Verzeichnis umbenennen (_deleted_)
    - project.yaml als disabled markieren
    Kein Datenverlust: Verzeichnis bleibt als _deleted_ erhalten.
    """
    import shutil as _shutil, time as _time

    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

    project_dir = Path(PROJECTS_DIR) / project_id
    if not project_dir.exists():
        raise HTTPException(404, f"Projektverzeichnis nicht gefunden")

    # 1. Laufende Agenten stoppen
    stopped_agents = []
    boss_id = cfg.agents.boss
    handle = runtime.get_handle(boss_id)
    if handle:
        await runtime.stop_agent(boss_id)
        stopped_agents.append(boss_id)

    # 2. Samba-Share entfernen
    smb_conf = Path("/etc/samba/smb.conf")
    if smb_conf.exists():
        try:
            smb_content = smb_conf.read_text(encoding="utf-8")
            # Sektion [project_id] entfernen
            import re as _re
            smb_content = _re.sub(
                rf"\[{_re.escape(project_id)}\][^\[]*",
                "", smb_content, flags=_re.DOTALL
            )
            smb_conf.write_text(smb_content, encoding="utf-8")
            import subprocess as _sub
            _sub.run(["systemctl", "reload", "smbd"], check=False, timeout=5)
        except Exception as e:
            logger.warning("Samba-Share Entfernung fehlgeschlagen: %s", e)

    # 3. Verzeichnis umbenennen
    timestamp = int(_time.time())
    deleted_dir = Path(PROJECTS_DIR) / f"_deleted_{project_id}_{timestamp}"
    project_dir.rename(deleted_dir)

    audit_log("project.delete", target=project_id, project_id=project_id,
              details={"moved_to": str(deleted_dir), "stopped_agents": stopped_agents})
    logger.info("Projekt gelöscht: %s → %s", project_id, deleted_dir)

    return {
        "deleted":      True,
        "project_id":   project_id,
        "moved_to":     str(deleted_dir),
        "stopped_agents": stopped_agents,
    }


@admin_router.post("/projects/{project_id}/provision")
async def provision_project(project_id: str, _a: tuple = Depends(require_admin)):
    """
    Projekt provisionieren: Linux-User + Samba-Share + Matrix-Room.
    Idempotent — kann mehrfach aufgerufen werden.
    """
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    if provisioner is None:
        raise HTTPException(503, "Provisioner nicht initialisiert")

    result = await provisioner.provision(cfg)

    # project.yaml mit Matrix-Room-ID aktualisieren wenn neu angelegt
    if result.matrix_room and not cfg.matrix.room:
        _update_project_matrix_room(project_id, result.matrix_room)

    # Matrix-Client für Boss starten wenn Room vorhanden (neu oder bereits konfiguriert)
    room_id = result.matrix_room or cfg.matrix.room
    if room_id:
        boss_cfg = discovery.get(cfg.agents.boss)
        if boss_cfg:
            matrix_client = BossMatrixAgent(
                config       = boss_cfg,
                server_name  = _read_server_name(),
                rooms        = [room_id],
                orchestrator = orchestrator,
                project_cfg  = cfg,
            )
            await runtime.attach_matrix_client(boss_cfg.id, matrix_client)
            logger.info("Matrix-Client nach Provisioning gestartet: %s → %s", boss_cfg.id, room_id)

    return {
        "project_id":  result.project_id,
        "linux_user":  result.linux_user,
        "files_dir":   result.files_dir,
        "samba_share": result.samba_share,
        "matrix_room": result.matrix_room,
        "warnings":    result.warnings,
        "ok":          result.ok,
    }


@admin_router.delete("/projects/{project_id}/provision")
async def deprovision_project(project_id: str, _a: tuple = Depends(require_admin)):
    """Projekt-Ressourcen entfernen (User, Samba-Share)."""
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    if provisioner is None:
        raise HTTPException(503, "Provisioner nicht initialisiert")

    warnings = await provisioner.deprovision(cfg)
    return {"project_id": project_id, "deprovisioned": True, "warnings": warnings}


def _update_project_matrix_room(project_id: str, room_id: str) -> None:
    """Matrix-Room-ID in project.yaml zurückschreiben."""
    import re
    project_yaml = Path(PROJECTS_DIR) / project_id / "project.yaml"
    if not project_yaml.exists():
        return
    try:
        content = project_yaml.read_text(encoding="utf-8")
        # Fall 1: room: "" vorhanden → ersetzen
        updated = re.sub(r'(room:\s*)""', f'\\1"{room_id}"', content)
        if updated != content:
            project_yaml.write_text(updated, encoding="utf-8")
            return
        # Fall 2: matrix: Sektion vorhanden aber ohne room → room: einfügen
        updated = re.sub(r"(matrix:\s*\n)", f'\\1  room: "{room_id}"\n', content)
        if updated != content:
            project_yaml.write_text(updated, encoding="utf-8")
            return
        # Fall 3: gar keine matrix: Sektion → ans Ende anhängen
        updated = content.rstrip() + f'\nmatrix:\n  room: "{room_id}"\n'
        project_yaml.write_text(updated, encoding="utf-8")
    except OSError as e:
        logger.warning("project.yaml konnte nicht aktualisiert werden: %s", e)


# ================================================================== Projekte / Sessions / Projekt-Chat

register_project_routes(
    auth_router,
    admin_router,
    projects=projects,
    discovery=discovery,
    runtime=runtime,
    sessions=sessions,
    orchestrator=orchestrator,
    projects_dir=PROJECTS_DIR,
    get_provisioner=lambda: provisioner,
    update_project_matrix_room=_update_project_matrix_room,
    audit_log=audit_log,
    check_message_rate=_check_message_rate,
    logger=logger,
)


# ================================================================== Tools

@auth_router.get("/tools")
def list_tools():
    """Alle registrierten Tools mit Schema — fuer die Webkonsole."""
    from .tool_registry import registry
    result = {}
    for tool_id in registry.all_ids():
        tool = registry.get(tool_id)
        if tool:
            result[tool_id] = {
                "name":                 tool.name,
                "description":          tool.description,
                "permissions_required": tool.permissions_required,
                "parameters":           tool.parameters,
            }
    return result



# ================================================================== LLM-Config

LLM_CONFIG_FILE = "/etc/octopos/llm_config.json"


def _load_llm_config() -> dict:
    import json as _json
    try:
        return _json.loads(Path(LLM_CONFIG_FILE).read_text())
    except (OSError, ValueError):
        return {"providers": {}}


def _save_llm_config(config: dict) -> None:
    import json as _json
    Path(LLM_CONFIG_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(LLM_CONFIG_FILE).write_text(
        _json.dumps(config, indent=2), encoding="utf-8"
    )


@admin_router.get("/llm/config")
def get_llm_config():
    """LLM-Provider-Config lesen (API-Keys maskiert)."""
    config = _load_llm_config()
    providers = config.get("providers", {})
    # API-Keys maskieren
    masked = {}
    for name, cfg in providers.items():
        masked[name] = {
            "enabled":  cfg.get("enabled", True),
            "api_key":  "***" + cfg.get("api_key","")[-4:] if cfg.get("api_key") else "",
            "has_key":  bool(cfg.get("api_key")),
        }
    return {"providers": masked}


class LlmProviderConfig(BaseModel):
    provider: str   # ollama | anthropic | openai
    api_key:  str = ""
    enabled:  bool = True



@admin_router.put("/llm/config/claude_max")
async def set_claude_oauth_token(body: dict):
    """
    Claude Max OAuth Token speichern.
    Token kommt von: claude setup-token (sk-ant-oat01-...)
    Wird vom Claude OAuth Proxy auf Port 3456 genutzt.
    """
    token = body.get("api_key", "").strip()
    if not token:
        raise HTTPException(400, "api_key fehlt")
    if not token.startswith("sk-ant-oat01-"):
        raise HTTPException(400, "Ungültiger Claude OAuth Token — erwartet sk-ant-oat01-...")

    token_file = Path("/etc/octopos/claude_oauth_token")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)
    logger.info("Claude OAuth Token gespeichert")
    audit_log("llm.token_set", details={"provider": "claude_max"})
    return {"updated": True, "provider": "claude_max"}


@admin_router.put("/llm/config/{provider}")
def set_llm_provider(provider: str, req: LlmProviderConfig):
    """API-Key fuer einen Provider setzen."""
    config = _load_llm_config()
    if "providers" not in config:
        config["providers"] = {}
    config["providers"][provider] = {
        "enabled": req.enabled,
        "api_key":  req.api_key,
    }
    # API-Key als Umgebungsvariable schreiben fuer litellm
    # claude_max → ANTHROPIC_API_KEY, openai → OPENAI_API_KEY, etc.
    ENV_KEY_MAP = {
        "claude_max": "ANTHROPIC_API_KEY",
        "anthropic":  "ANTHROPIC_API_KEY",
        "openai":     "OPENAI_API_KEY",
    }
    env_var = ENV_KEY_MAP.get(provider, f"{provider.upper()}_API_KEY")
    env_file = Path("/etc/octopos/llm_env")
    lines = []
    if env_file.exists():
        lines = [l for l in env_file.read_text().splitlines()
                 if not l.startswith(f"{env_var}=")]
    if req.api_key:
        lines.append(f"{env_var}={req.api_key}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _save_llm_config(config)
    logger.info("LLM-Provider konfiguriert: %s", provider)
    return {"updated": True, "provider": provider}



@auth_router.get("/llm/available-models")
async def get_available_models(auth: tuple = Depends(require_auth)):
    """Gibt verfügbare LLM-Modelle zurück: Anthropic + OpenAI + Server-Ollama + WKS-Ollama."""
    import httpx as _httpx
    username, _ = auth
    models: list[dict] = []

    config = _load_llm_config()
    providers = config.get("providers", {})

    # Anthropic / Claude
    if providers.get("claude_max", {}).get("enabled") or providers.get("anthropic", {}).get("enabled"):
        for m in ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"]:
            models.append({"id": m, "label": m, "provider": "anthropic"})

    # OpenAI
    if providers.get("openai", {}).get("enabled"):
        for m in ["gpt-4o-mini", "gpt-4o"]:
            models.append({"id": m, "label": m, "provider": "openai"})

    # Google Antigravity (Gemini via Cloud Code Assist OAuth)
    ga_file = Path("/etc/octopos/google_antigravity_token.json")
    if ga_file.exists():
        try:
            import json as _json
            ga_data = _json.loads(ga_file.read_text(encoding="utf-8"))
            if ga_data.get("access_token"):
                for m in ["gemini-3.0-flash", "gemini-3.0-pro", "gemini-3.5-pro"]:
                    models.append({"id": f"google-antigravity/{m}", "label": f"Antigravity: {m}", "provider": "google_antigravity"})
        except Exception:
            pass

    # OpenAI Codex (ChatGPT Plus/Pro OAuth)
    codex_file = Path("/etc/octopos/openai_codex_token.json")
    if codex_file.exists():
        try:
            import json as _json
            codex_data = _json.loads(codex_file.read_text(encoding="utf-8"))
            if codex_data.get("access_token") and codex_data.get("account_id"):
                for m in ["gpt-5.2", "gpt-5.1", "gpt-5.1-codex-max", "gpt-5.1-codex-mini",
                          "gpt-5.2-codex", "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.4"]:
                    models.append({"id": f"openai-codex/{m}", "label": f"Codex: {m}", "provider": "openai_codex"})
        except Exception:
            pass

    # Server-Ollama — live Tags abfragen
    ollama_cfg = providers.get("ollama", {})
    ollama_base = ollama_cfg.get("base_url", "http://localhost:11434")
    try:
        async with _httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{ollama_base}/api/tags")
            if resp.status_code == 200:
                tags = resp.json().get("models", [])
                for t in tags:
                    name = t.get("name", "")
                    if name:
                        models.append({"id": f"ollama/{name}", "label": f"ollama/{name}", "provider": "ollama"})
    except Exception:
        pass  # Ollama nicht erreichbar — kein Fehler

    # WKS-Ollama — falls der User eine Workstation konfiguriert hat
    wks = _load_users().get(username, {}).get("wks", {})
    if wks.get("ip"):
        wks_url = f"http://{wks['ip']}:{wks.get('ollama_port', 11434)}"
        try:
            async with _httpx.AsyncClient(timeout=2) as client:
                resp = await client.get(f"{wks_url}/api/tags")
                if resp.status_code == 200:
                    tags = resp.json().get("models", [])
                    for t in tags:
                        name = t.get("name", "")
                        if name:
                            models.append({"id": f"ollama/{name}", "label": f"WKS: {name}", "provider": "wks_ollama", "wks_base_url": wks_url})
        except Exception:
            pass  # WKS nicht erreichbar

    return {"models": models}


@admin_router.put("/llm/config/openai_codex")
async def set_openai_codex_token(body: dict):
    """
    OpenAI Codex OAuth Token speichern (ChatGPT Plus/Pro).
    Erwartet: {access_token, account_id, refresh_token?}
    """
    import json as _json
    access_token = body.get("access_token", "").strip()
    account_id   = body.get("account_id", "").strip()
    if not access_token:
        raise HTTPException(400, "access_token fehlt")
    if not account_id:
        raise HTTPException(400, "account_id fehlt")

    data = {
        "access_token":  access_token,
        "refresh_token": body.get("refresh_token", ""),
        "account_id":    account_id,
    }
    token_file = Path("/etc/octopos/openai_codex_token.json")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    token_file.chmod(0o600)
    logger.info("OpenAI Codex OAuth Token gespeichert")
    audit_log("llm.token_set", details={"provider": "openai_codex"})
    return {"updated": True, "provider": "openai_codex"}


@admin_router.get("/llm/openai_codex_status")
def get_openai_codex_status():
    """OpenAI Codex Token Status — konfiguriert, Account-ID."""
    import json as _json
    token_file = Path("/etc/octopos/openai_codex_token.json")
    if not token_file.exists() or token_file.stat().st_size == 0:
        return {"configured": False, "account_id": None}
    try:
        data = _json.loads(token_file.read_text(encoding="utf-8"))
        if data.get("access_token") and data.get("account_id"):
            return {
                "configured": True,
                "account_id": data["account_id"],
                "models": ["gpt-5.1", "gpt-5.1-codex-max", "gpt-5.1-codex-mini",
                           "gpt-5.2", "gpt-5.2-codex", "gpt-5.3-codex",
                           "gpt-5.3-codex-spark", "gpt-5.4"],
            }
    except Exception:
        pass
    return {"configured": False, "account_id": None}


# ---------------------------------------------------------------------------
# OAuth PKCE Flow — Anthropic, OpenAI Codex, Google Antigravity
# ---------------------------------------------------------------------------

# In-Memory State Store: state → {verifier, provider, expires}
_oauth_pending: dict[str, dict] = {}

def _pkce_pair() -> tuple[str, str]:
    """Generiert PKCE code_verifier und code_challenge (S256)."""
    import hashlib, secrets, base64 as _b64
    verifier  = _b64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = _b64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


@admin_router.post("/llm/oauth/anthropic/start")
async def start_anthropic_oauth():
    """
    Startet Anthropic Claude Max PKCE OAuth Flow.
    Gibt Authorization-URL zurück — User oeffnet sie im Browser.
    Nach Login: console.anthropic.com/oauth/code/callback zeigt code#state — User kopiert es.
    """
    import secrets, time, urllib.parse
    verifier, challenge = _pkce_pair()
    state = verifier  # Anthropic nutzt verifier als state

    _oauth_pending[state] = {"verifier": verifier, "provider": "anthropic", "expires": time.time() + 600}

    params = {
        "code":                   "true",
        "client_id":              "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        "response_type":          "code",
        "redirect_uri":           "https://console.anthropic.com/oauth/code/callback",
        "scope":                  "org:create_api_key user:profile user:inference",
        "code_challenge":         challenge,
        "code_challenge_method":  "S256",
        "state":                  state,
    }
    auth_url = "https://claude.ai/oauth/authorize?" + urllib.parse.urlencode(params)
    return {"auth_url": auth_url, "state": state}


@admin_router.post("/llm/oauth/anthropic/exchange")
async def exchange_anthropic_code(body: dict):
    """
    Tauscht Anthropic Authorization Code gegen Token.
    body: {code_and_state: "code#state"} oder {code, state} getrennt.
    Speichert sk-ant-oat01-... Token in /etc/octopos/claude_oauth_token.
    """
    import time
    import httpx as _httpx

    # Akzeptiere "code#state" als kombiniertes Feld (wie console.anthropic.com es anzeigt)
    code_and_state = body.get("code_and_state", "").strip()
    if code_and_state and "#" in code_and_state:
        code, state = code_and_state.split("#", 1)
    else:
        code  = body.get("code", "").strip()
        state = body.get("state", "").strip()

    if not code or not state:
        raise HTTPException(400, "code und state erforderlich")

    pending = _oauth_pending.pop(state, None)
    if not pending:
        raise HTTPException(400, "Ungültiger oder abgelaufener State — bitte OAuth neu starten")
    if pending["expires"] < time.time():
        raise HTTPException(400, "OAuth-Session abgelaufen — bitte neu starten")

    async with _httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://console.anthropic.com/v1/oauth/token",
            headers={"Content-Type": "application/json"},
            json={
                "grant_type":    "authorization_code",
                "client_id":     "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
                "code":          code,
                "state":         state,
                "redirect_uri":  "https://console.anthropic.com/oauth/code/callback",
                "code_verifier": pending["verifier"],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(400, f"Token-Exchange fehlgeschlagen: {resp.text[:300]}")

    token_data = resp.json()
    access_token = token_data.get("access_token", "")
    if not access_token or not access_token.startswith("sk-ant-oat01-"):
        raise HTTPException(400, f"Kein gültiger Anthropic Token in Response: {str(token_data)[:200]}")

    token_file = Path("/etc/octopos/claude_oauth_token")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(access_token, encoding="utf-8")
    token_file.chmod(0o600)
    logger.info("Claude OAuth Token via PKCE gespeichert")
    audit_log("llm.token_set", details={"provider": "anthropic", "via": "pkce_oauth"})
    return {"updated": True, "provider": "anthropic"}


@admin_router.post("/llm/oauth/openai_codex/start")
async def start_openai_codex_oauth():
    """
    Startet OpenAI Codex PKCE OAuth Flow (ChatGPT Plus/Pro).
    Redirect geht auf localhost:1455 (schlägt im Browser fehl — das ist normal).
    User kopiert die code= URL-Parameter und gibt sie hier ein.
    """
    import secrets, time, urllib.parse
    verifier, challenge = _pkce_pair()
    state = secrets.token_hex(16)

    _oauth_pending[state] = {"verifier": verifier, "provider": "openai_codex", "expires": time.time() + 600}

    params = {
        "response_type":               "code",
        "client_id":                   "app_EMoamEEZ73f0CkXaXp7hrann",
        "redirect_uri":                "http://localhost:1455/auth/callback",
        "scope":                       "openid profile email offline_access",
        "code_challenge":              challenge,
        "code_challenge_method":       "S256",
        "state":                       state,
        "id_token_add_organizations":  "true",
        "codex_cli_simplified_flow":   "true",
        "originator":                  "pi",
    }
    auth_url = "https://auth.openai.com/oauth/authorize?" + urllib.parse.urlencode(params)
    return {"auth_url": auth_url, "state": state}


@admin_router.post("/llm/oauth/openai_codex/exchange")
async def exchange_openai_codex_code(body: dict):
    """
    Tauscht OpenAI Codex Authorization Code gegen Token.
    body: {redirect_url: "http://localhost:1455/auth/callback?code=...&state=..."}
    oder  {code, state} getrennt.
    """
    import time, base64 as _b64, json as _json
    import httpx as _httpx
    from urllib.parse import urlparse, parse_qs, unquote

    # Redirect-URL automatisch parsen wenn übergeben
    redirect_url = body.get("redirect_url", "").strip()
    if redirect_url:
        parsed = urlparse(redirect_url)
        qs     = parse_qs(parsed.query)
        code  = qs.get("code",  [""])[0]
        state = qs.get("state", [""])[0]
    else:
        code  = body.get("code",  "").strip()
        state = body.get("state", "").strip()

    if not code or not state:
        raise HTTPException(400, "code und state erforderlich (oder redirect_url mit beiden)")

    pending = _oauth_pending.pop(state, None)
    if not pending:
        raise HTTPException(400, "Ungültiger oder abgelaufener State — bitte OAuth neu starten")
    if pending["expires"] < time.time():
        raise HTTPException(400, "OAuth-Session abgelaufen")

    async with _httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://auth.openai.com/oauth/token",
            data={
                "grant_type":    "authorization_code",
                "client_id":     "app_EMoamEEZ73f0CkXaXp7hrann",
                "code":          code,
                "code_verifier": pending["verifier"],
                "redirect_uri":  "http://localhost:1455/auth/callback",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code != 200:
        raise HTTPException(400, f"Token-Exchange fehlgeschlagen: {resp.text[:300]}")

    token_data = resp.json()
    access_token  = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    if not access_token:
        raise HTTPException(400, "Kein access_token in Response")

    # Account-ID aus JWT-Payload extrahieren
    account_id = ""
    try:
        payload_b64 = access_token.split(".")[1]
        payload = _json.loads(_b64.urlsafe_b64decode(payload_b64 + "=="))
        account_id = payload.get("https://api.openai.com/auth", {}).get("chatgpt_account_id", "")
    except Exception:
        pass
    if not account_id:
        raise HTTPException(400, "account_id konnte nicht aus Token extrahiert werden")

    token_file = Path("/etc/octopos/openai_codex_token.json")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(_json.dumps({
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "account_id":    account_id,
        "expires":       token_data.get("expires_in", 3600) + int(time.time()),
    }, indent=2), encoding="utf-8")
    token_file.chmod(0o600)
    logger.info("OpenAI Codex Token via PKCE gespeichert (account: %s…)", account_id[:12])
    audit_log("llm.token_set", details={"provider": "openai_codex", "via": "pkce_oauth"})
    return {"updated": True, "account_id": account_id}


@admin_router.post("/llm/oauth/google_antigravity/start")
async def start_google_antigravity_oauth():
    """
    Startet Google Antigravity PKCE OAuth Flow (Gemini 3 Flash/Pro).
    Redirect geht auf localhost:51121 (schlägt im Browser fehl — normal).
    User kopiert die redirect-URL und gibt sie hier ein.
    """
    import time, urllib.parse
    verifier, challenge = _pkce_pair()
    state = verifier  # Google nutzt verifier als state

    _oauth_pending[state] = {"verifier": verifier, "provider": "google_antigravity", "expires": time.time() + 600}

    scopes = " ".join([
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/cclog",
        "https://www.googleapis.com/auth/experimentsandconfigs",
    ])
    params = {
        "client_id":             "REDACTED_GOOGLE_CLIENT_ID",
        "response_type":         "code",
        "redirect_uri":          "http://localhost:51121/oauth-callback",
        "scope":                 scopes,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
        "state":                 state,
        "access_type":           "offline",
        "prompt":                "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return {"auth_url": auth_url, "state": state}


@admin_router.post("/llm/oauth/google_antigravity/exchange")
async def exchange_google_antigravity_code(body: dict):
    """
    Tauscht Google Antigravity Authorization Code gegen Token.
    body: {redirect_url: "http://localhost:51121/oauth-callback?code=...&state=..."}
    oder  {code, state} getrennt.
    """
    import time, json as _json
    import httpx as _httpx
    from urllib.parse import urlparse, parse_qs

    redirect_url = body.get("redirect_url", "").strip()
    if redirect_url:
        parsed = urlparse(redirect_url)
        qs     = parse_qs(parsed.query)
        code  = qs.get("code",  [""])[0]
        state = qs.get("state", [""])[0]
    else:
        code  = body.get("code",  "").strip()
        state = body.get("state", "").strip()

    if not code or not state:
        raise HTTPException(400, "code und state erforderlich")

    pending = _oauth_pending.pop(state, None)
    if not pending:
        raise HTTPException(400, "Ungültiger oder abgelaufener State — bitte OAuth neu starten")
    if pending["expires"] < time.time():
        raise HTTPException(400, "OAuth-Session abgelaufen")

    async with _httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id":     "REDACTED_GOOGLE_CLIENT_ID",
                "client_secret": "REDACTED_GOOGLE_SECRET",
                "code":          code,
                "grant_type":    "authorization_code",
                "redirect_uri":  "http://localhost:51121/oauth-callback",
                "code_verifier": pending["verifier"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code != 200:
        raise HTTPException(400, f"Token-Exchange fehlgeschlagen: {resp.text[:300]}")

    token_data    = resp.json()
    access_token  = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    if not access_token:
        raise HTTPException(400, "Kein access_token in Response")

    # Email aus User-Info
    email = ""
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            ui = await client.get(
                "https://www.googleapis.com/oauth2/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if ui.status_code == 200:
                email = ui.json().get("email", "")
    except Exception:
        pass

    # Projekt-Discovery (Fallback: REDACTED_GOOGLE_PROJECT)
    project_id = "REDACTED_GOOGLE_PROJECT"
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            ca = await client.post(
                "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"cloudaiCompanionClient": {"clientVersion": ""}},
            )
            if ca.status_code == 200:
                project_id = ca.json().get("currentProject", {}).get("projectId", project_id)
    except Exception:
        pass

    token_file = Path("/etc/octopos/google_antigravity_token.json")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(_json.dumps({
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "email":         email,
        "project_id":    project_id,
        "expires":       token_data.get("expires_in", 3600) + int(time.time()),
    }, indent=2), encoding="utf-8")
    token_file.chmod(0o600)
    logger.info("Google Antigravity Token via PKCE gespeichert (email: %s, project: %s)", email, project_id)
    audit_log("llm.token_set", details={"provider": "google_antigravity", "via": "pkce_oauth"})
    return {"updated": True, "email": email, "project_id": project_id}


@admin_router.get("/llm/google_antigravity_status")
def get_google_antigravity_status():
    """Google Antigravity Token Status."""
    import json as _json
    token_file = Path("/etc/octopos/google_antigravity_token.json")
    if not token_file.exists():
        return {"configured": False}
    try:
        data = _json.loads(token_file.read_text(encoding="utf-8"))
        if data.get("access_token"):
            return {
                "configured": True,
                "email":      data.get("email", ""),
                "project_id": data.get("project_id", ""),
                "models":     ["gemini-3.0-flash", "gemini-3.0-pro", "gemini-3.5-pro"],
            }
    except Exception:
        pass
    return {"configured": False}


@admin_router.get("/llm/claude_token_status")
def get_claude_token_status():
    """
    Claude OAuth Token Status — Alter und Gueltigkeit.
    Liest sk-ant-oat01- Token aus /etc/octopos/claude_oauth_token.
    Token-Ablauf: oat01 Tokens gelten typisch 30 Tage ab Erstellung.
    """
    import time as _time
    token_file = Path("/etc/octopos/claude_oauth_token")

    if not token_file.exists() or token_file.stat().st_size == 0:
        return {"configured": False, "token_age_days": None, "warning": None}

    # Alter aus mtime
    mtime        = token_file.stat().st_mtime
    age_seconds  = _time.time() - mtime
    age_days     = age_seconds / 86400

    # oat01 Tokens gelten ca. 30 Tage
    TOKEN_TTL_DAYS = 30
    remaining_days = TOKEN_TTL_DAYS - age_days

    warning = None
    if remaining_days <= 0:
        warning = "expired"
    elif remaining_days <= 3:
        warning = f"expires_soon_{int(remaining_days)}d"
    elif remaining_days <= 7:
        warning = f"expires_in_{int(remaining_days)}d"

    return {
        "configured":     True,
        "token_age_days": round(age_days, 1),
        "remaining_days": round(remaining_days, 1),
        "warning":        warning,
        "ttl_days":       TOKEN_TTL_DAYS,
    }


@auth_router.get("/llm/ollama/models")
async def get_ollama_models():
    """Verfuegbare Ollama-Modelle von lokalem Server abrufen."""
    import asyncio as _asyncio
    try:
        import urllib.request as _ur
        import json as _json
        req = _ur.Request("http://127.0.0.1:11434/api/tags")
        with _ur.urlopen(req, timeout=3) as r:
            data = _json.loads(r.read())
        models = [
            {
                "name":     m["name"],
                "size":     m.get("size", 0),
                "size_gb":  round(m.get("size", 0) / 1e9, 1),
                "modified": m.get("modified_at",""),
            }
            for m in data.get("models", [])
        ]
        return {"available": True, "models": models, "count": len(models)}
    except Exception as e:
        return {"available": False, "models": [], "error": str(e)}



@admin_router.post("/llm/ollama/pull")
async def pull_ollama_model(body: dict):
    """Ollama-Modell herunterladen (blockiert bis fertig)."""
    model = body.get("model","").strip()
    if not model:
        raise HTTPException(400, "model fehlt")
    import subprocess as _sub
    try:
        result = _sub.run(
            ["ollama", "pull", model],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            raise HTTPException(500, f"ollama pull fehlgeschlagen: {result.stderr[:200]}")
        logger.info("Ollama-Modell geladen: %s", model)
        return {"pulled": True, "model": model}
    except FileNotFoundError:
        raise HTTPException(503, "ollama nicht installiert")
    except _sub.TimeoutExpired:
        raise HTTPException(504, "Timeout beim Laden des Modells")


# ================================================================== MCP-Server

class McpServerEntry(BaseModel):
    id:        str
    name:      str
    transport: str = "streamableHttp"   # streamableHttp | sse | stdio
    url:       str
    headers:   dict = {}


def _load_mcp_servers() -> list[dict]:
    import json as _json
    try:
        data = _json.loads(Path(MCP_SERVERS_FILE).read_text())
        return data.get("servers", [])
    except (OSError, ValueError):
        return []


def _save_mcp_servers(servers: list[dict]) -> None:
    import json as _json
    Path(MCP_SERVERS_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(MCP_SERVERS_FILE).write_text(
        _json.dumps({"servers": servers}, indent=2), encoding="utf-8"
    )


@auth_router.get("/mcp/servers")
def list_mcp_servers():
    """Alle konfigurierten MCP-Server auflisten (alle eingeloggten User)."""
    return {"servers": _load_mcp_servers()}


@admin_router.post("/mcp/servers", status_code=201)
def create_mcp_server(req: McpServerEntry):
    """Neuen MCP-Server anlegen."""
    import re as _re
    if not _re.match(r"^[a-z0-9_-]+$", req.id):
        raise HTTPException(400, "ID darf nur a-z, 0-9, _ und - enthalten")
    servers = _load_mcp_servers()
    if any(s["id"] == req.id for s in servers):
        raise HTTPException(409, f"MCP-Server '{req.id}' existiert bereits")
    servers.append(req.model_dump())
    _save_mcp_servers(servers)
    audit_log("mcp.create", target=req.id, details={"url": req.url})
    return {"created": True, "server": req.model_dump()}


@admin_router.put("/mcp/servers/{server_id}")
def update_mcp_server(server_id: str, req: McpServerEntry):
    """MCP-Server aktualisieren."""
    servers = _load_mcp_servers()
    idx = next((i for i, s in enumerate(servers) if s["id"] == server_id), None)
    if idx is None:
        raise HTTPException(404, f"MCP-Server '{server_id}' nicht gefunden")
    servers[idx] = req.model_dump()
    _save_mcp_servers(servers)
    return {"updated": True, "server": req.model_dump()}


@admin_router.delete("/mcp/servers/{server_id}")
def delete_mcp_server(server_id: str):
    """MCP-Server löschen."""
    servers = _load_mcp_servers()
    new_servers = [s for s in servers if s["id"] != server_id]
    if len(new_servers) == len(servers):
        raise HTTPException(404, f"MCP-Server '{server_id}' nicht gefunden")
    _save_mcp_servers(new_servers)
    audit_log("mcp.delete", target=server_id)
    return {"deleted": True, "server_id": server_id}


# ================================================================== Backup & Restore

BACKUP_DIR = Path("/opt/octopos/backups")
_BACKUP_SOURCES = [
    ("/etc/octopos",  "etc-octopos"),
    ("/agents",       "agents"),
    ("/projects",     "projects"),
]


def _list_backups() -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for f in sorted(BACKUP_DIR.glob("octopos-backup-*.tar.gz"), reverse=True):
        stat = f.stat()
        result.append({
            "name":       f.name,
            "size":       stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return result


@admin_router.get("/admin/backups")
def list_backups():
    """Alle vorhandenen Backups auflisten."""
    return {"backups": _list_backups()}


@admin_router.post("/admin/backup", status_code=201)
def create_backup():
    """Backup von /etc/octopos, /agents, /projects erstellen."""
    import tarfile as _tar
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"octopos-backup-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.tar.gz"
    dest = BACKUP_DIR / name
    with _tar.open(dest, "w:gz") as tf:
        for src, arcname in _BACKUP_SOURCES:
            p = Path(src)
            if p.exists():
                tf.add(p, arcname=arcname, filter=lambda ti: (
                    None if any(
                        part.startswith("proj_") or part == "files"
                        for part in Path(ti.name).parts
                    ) else ti
                ))
    size = dest.stat().st_size
    audit_log("backup.create", target=name, details={"size": size})
    logger.info("Backup erstellt: %s (%d bytes)", name, size)
    return {"created": True, "name": name, "size": size,
            "created_at": datetime.now().isoformat()}


@admin_router.get("/admin/backups/{name}/download")
def download_backup(name: str):
    """Backup-Datei herunterladen."""
    import re as _re
    from fastapi.responses import FileResponse
    if not _re.match(r"^octopos-backup-[\w\-]+\.tar\.gz$", name):
        raise HTTPException(400, "Ungültiger Backup-Name")
    path = BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(404, "Backup nicht gefunden")
    return FileResponse(path, media_type="application/gzip", filename=name)


@admin_router.delete("/admin/backups/{name}")
def delete_backup(name: str):
    """Backup löschen."""
    import re as _re
    if not _re.match(r"^octopos-backup-[\w\-]+\.tar\.gz$", name):
        raise HTTPException(400, "Ungültiger Backup-Name")
    path = BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(404, "Backup nicht gefunden")
    path.unlink()
    audit_log("backup.delete", target=name)
    return {"deleted": True, "name": name}


@admin_router.post("/admin/restore/{name}")
def restore_backup(name: str, _a: tuple = Depends(require_admin)):
    """Backup einspielen — überschreibt /etc/octopos und /agents, startet Service neu."""
    import re as _re, tarfile as _tar, subprocess as _sub, shutil as _sh
    if not _re.match(r"^octopos-backup-[\w\-]+\.tar\.gz$", name):
        raise HTTPException(400, "Ungültiger Backup-Name")
    path = BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(404, "Backup nicht gefunden")

    import tempfile as _tmp
    with _tmp.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with _tar.open(path, "r:gz") as tf:
            tf.extractall(tmp_path)

        # etc-octopos → /etc/octopos
        src_etc = tmp_path / "etc-octopos"
        if src_etc.exists():
            for f in src_etc.iterdir():
                _sh.copy2(f, Path("/etc/octopos") / f.name)

        # agents → /agents
        src_agents = tmp_path / "agents"
        if src_agents.exists():
            _sh.copytree(src_agents, Path("/agents"), dirs_exist_ok=True)

        # projects (nur project.yaml + sessions) → /projects
        src_projects = tmp_path / "projects"
        if src_projects.exists():
            for proj_dir in src_projects.iterdir():
                if proj_dir.is_dir():
                    dest_proj = Path("/projects") / proj_dir.name
                    dest_proj.mkdir(exist_ok=True)
                    for item in proj_dir.iterdir():
                        if item.name != "files":
                            dst = dest_proj / item.name
                            if item.is_dir():
                                _sh.copytree(item, dst, dirs_exist_ok=True)
                            else:
                                _sh.copy2(item, dst)

    audit_log("backup.restore", target=name)
    logger.info("Restore abgeschlossen: %s — starte Service neu", name)

    # Service-Neustart nach kurzer Verzögerung (Response noch senden)
    import threading as _thr
    def _restart():
        import time as _time
        _time.sleep(1)
        _sub.run(["systemctl", "restart", "octopos-core"], check=False)
    _thr.Thread(target=_restart, daemon=True).start()

    return {"restored": True, "name": name, "restarting": True}


# ================================================================== Status

NETWORK_PROFILE_FILE = Path("/etc/octopos/network_profile")
NETWORK_PROFILE_SCRIPT = "/opt/octopos/apply-network-profile.sh"
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


# ================================================================== Gitea Webhook + Config

@app.post("/webhooks/gitea/{project_id}")
async def gitea_webhook(project_id: str, request: Request):
    """
    Gitea Push-Webhook: bei Push auf main wird der OctopOS-Core-Service neu gestartet
    wenn es sich um das octopos-core Repo handelt, sonst nur geloggt.
    Kein Auth — Gitea-Secret wird in der Gitea-Config gesetzt.
    """
    import hmac
    import hashlib

    body = await request.body()

    # Optional: Webhook-Secret prüfen
    from .gitea import _load_config as _gitea_cfg
    cfg = _gitea_cfg()
    webhook_secret = cfg.get("webhook_secret", "")
    if webhook_secret:
        sig = request.headers.get("X-Gitea-Signature", "")
        expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()  # type: ignore[attr-defined]
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(403, "Webhook-Signatur ungültig")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    ref    = payload.get("ref", "")
    pusher = payload.get("pusher", {}).get("login", "unknown")
    commits = len(payload.get("commits", []))

    logger.info("Gitea Webhook: project=%s ref=%s pusher=%s commits=%d",
                project_id, ref, pusher, commits)

    if ref != "refs/heads/main":
        return {"status": "ignored", "reason": "not main branch", "ref": ref}

    audit_log("gitea.webhook.push", target=project_id, project_id=project_id,
              details={"ref": ref, "pusher": pusher, "commits": commits})

    # Workspace-Cache löschen damit nächste Tool-Nutzung frisch clont
    import shutil as _shutil
    ws = Path(f"/tmp/octopos-git/{project_id}")
    if ws.exists():
        _shutil.rmtree(ws)
        logger.info("Gitea Webhook: Workspace-Cache %s geleert", ws)

    # OctopOS-Core selbst deployen wenn das octopos-Core-Repo gepusht wurde
    if project_id == "octopos-core":
        asyncio.create_task(_run_self_update(pusher, commits))
        return {"status": "deploying", "project": project_id, "ref": ref}

    return {"status": "ok", "project": project_id, "ref": ref}


async def _run_self_update(pusher: str, commits: int) -> None:
    """Übergibt den Self-Update-Job an eine dedizierte systemd-Service-Unit."""
    import asyncio as _asyncio
    STATUS_FILE   = "/var/run/octopos-update.json"

    logger.info("Self-Update gestartet (pusher=%s commits=%d)", pusher, commits)

    # Status: running
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
            "sudo", "systemctl", "is-active", "--quiet", "octopos-selfupdate.service",
            stdout=_asyncio.subprocess.DEVNULL,
            stderr=_asyncio.subprocess.DEVNULL,
        )
        await check.wait()
        if check.returncode == 0:
            logger.info("Self-Update läuft bereits")
            return

        proc = await _asyncio.create_subprocess_exec(
            "sudo", "systemctl", "start", "--no-block", "octopos-selfupdate.service",
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


GITEA_CONFIG_FILE = "/etc/octopos/gitea_config.json"


def get_gitea_config():
    """Helper fuer Tests und lokale Call-Sites: maskierte Gitea-Konfiguration lesen."""
    p = Path(GITEA_CONFIG_FILE)
    if not p.exists():
        return {"url": "http://127.0.0.1:3001", "org": "octopos", "webhook_secret": "", "has_token": False, "token_masked": ""}
    cfg = json.loads(p.read_text(encoding="utf-8"))
    token = cfg.get("token", "")
    return {
        "url": cfg.get("url", "http://127.0.0.1:3001"),
        "org": cfg.get("org", "octopos"),
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
    logger=logger,
)

app.include_router(public_router)
app.include_router(auth_router)
app.include_router(admin_router)
