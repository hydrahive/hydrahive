"""
main.py — OctopOS Core Runtime Einstiegspunkt (#4, #6, #7, #8, #9, #10, #11, #12, #35)

FastAPI-App mit Lifespan-Management:
- AgentDiscovery + AgentRuntime + ProjectLoader + SessionManager + Orchestrator
- REST-Endpoints fuer Agenten, Projekte, Sessions und Nachrichten
"""

import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncio
import time
from collections import defaultdict

from fastapi import Depends, FastAPI, HTTPException, Request
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
from .session_manager import MessageRole, SessionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

AGENTS_DIR   = "/agents"
PROJECTS_DIR = "/projects"

CRED_FILE    = "/etc/octopos/admin_credentials"
JWT_SECRET   = ""    # wird im Lifespan aus Datei geladen oder generiert
JWT_ALG      = "HS256"
JWT_EXPIRE_H = 24    # Token-Gültigkeit in Stunden

# Rate-Limiting für /auth/login (#70)
_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_LOGIN_MAX   = 10   # max. Versuche
_LOGIN_WIN_S = 60   # pro Minute

def _check_login_rate(ip: str) -> None:
    now = time.monotonic()
    _LOGIN_ATTEMPTS[ip] = [t for t in _LOGIN_ATTEMPTS[ip] if now - t < _LOGIN_WIN_S]
    if len(_LOGIN_ATTEMPTS[ip]) >= _LOGIN_MAX:
        raise HTTPException(429, "Zu viele Login-Versuche — bitte eine Minute warten")
    _LOGIN_ATTEMPTS[ip].append(now)


_setup_lock = asyncio.Lock()   # verhindert parallele Setup-Requests (#71)

discovery    = AgentDiscovery(AGENTS_DIR)
runtime      = AgentRuntime()
projects     = ProjectLoader(PROJECTS_DIR)
sessions     = SessionManager(PROJECTS_DIR)
orchestrator = Orchestrator(discovery, runtime, sessions)
provisioner: Provisioner | None = None   # initialisiert im Lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    global provisioner, JWT_SECRET
    logger.info("OctopOS Core startet...")
    discovery.start()
    projects.start()
    sessions.start()
    await runtime.start(list(discovery.agents.values()))

    # JWT-Secret laden oder generieren
    JWT_SECRET = _load_or_create_jwt_secret()
    logger.info("JWT-Secret geladen")

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
        provisioner = Provisioner("", "octopos-devmaster")

    # Matrix-Clients für Boss-Agenten mit konfigurierten Rooms starten
    server_name = _read_server_name()
    await _setup_matrix_clients(server_name)

    logger.info("OctopOS Core bereit")
    yield
    logger.info("OctopOS Core faehrt herunter...")
    await runtime.stop()
    projects.stop()
    discovery.stop()
    logger.info("OctopOS Core gestoppt")


def _read_server_name(toml_path: str = "/etc/conduwuit/conduwuit.toml") -> str:
    try:
        for line in open(toml_path).read().splitlines():
            if line.strip().startswith("server_name"):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "octopos-devmaster"


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


def _make_jwt(username: str) -> str:
    """JWT-Token für den angegebenen User erstellen."""
    from jose import jwt as jose_jwt
    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_H),
        "iat": datetime.now(timezone.utc),
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


@app.get("/setup/status")
def setup_status():
    """Gibt zurück ob der Setup-Wizard noch ausgeführt werden muss."""
    users = _load_users()
    return {"needs_setup": len(users) == 0}


class SetupRequest(BaseModel):
    username: str
    password: str


@app.post("/setup", status_code=201)
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


@app.post("/auth/login")
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
            token = _make_jwt(req.username)
            logger.info("Login erfolgreich (users.json): %s", req.username)
            return {"access_token": token, "token_type": "bearer"}
        raise HTTPException(401, "Ungültige Zugangsdaten")

    # Fallback: admin_credentials (vor Setup oder Legacy-Betrieb)
    admin_pass = _read_admin_password()
    if not admin_pass:
        raise HTTPException(503, "Kein Admin-Passwort konfiguriert — Setup erforderlich")
    if req.username != "admin" or req.password != admin_pass:
        raise HTTPException(401, "Ungültige Zugangsdaten")
    token = _make_jwt(req.username)
    logger.info("Login erfolgreich (admin_credentials): %s", req.username)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/auth/me")
def whoami(auth: tuple[str,str] = Depends(require_auth)):
    """Token-Validierung — gibt aktuellen User + Role zurück."""
    username, role = auth
    return {"username": username, "role": role}


# ================================================================== Agenten

@app.get("/health")
def health():
    return {"status": "ok", "service": "octopos-core"}


@app.get("/agents")
def list_agents():
    registered = discovery.agents
    running    = runtime.status_all()
    return {
        agent_id: {
            "config": {
                "type":     cfg.type,
                "identity": cfg.identity,
                "model":    cfg.llm.model,
            },
            "runtime": running.get(agent_id),
        }
        for agent_id, cfg in registered.items()
    }


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    cfg = discovery.get(agent_id)
    if not cfg:
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
    return {
        "config":  cfg.model_dump(exclude={"agent_dir"}),
        "runtime": runtime.status_all().get(agent_id),
    }


class SpawnRequest(BaseModel):
    agent_id: str


@app.post("/agents/spawn")
async def spawn_task_agent(req: SpawnRequest):
    cfg = discovery.get(req.agent_id)
    if not cfg:
        raise HTTPException(404, f"Agent '{req.agent_id}' nicht in Discovery")
    if cfg.type != "worker":
        raise HTTPException(400, f"Nur worker koennen gespawnt werden, nicht {cfg.type}")
    await runtime.spawn_task_agent(cfg)
    return {"spawned": req.agent_id}


@app.post("/agents/{agent_id}/heartbeat")
def agent_heartbeat(agent_id: str):
    runtime.heartbeat(agent_id)
    return {"ok": True}


# ================================================================== Projekte

@app.get("/projects")
def list_projects():
    return {
        pid: {
            "name":        cfg.identity.name,
            "description": cfg.identity.description,
            "boss":        cfg.agents.boss,
            "workers":     cfg.agents.workers,
            "matrix_room": cfg.matrix.room,
            "filesystem":  cfg.effective_filesystem_path(),
            "system_user": cfg.effective_system_user(),
            "show_swarm":  cfg.chat.show_swarm,
        }
        for pid, cfg in projects.projects.items()
    }


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    known = set(discovery.agents.keys())
    missing = [a for a in cfg.all_agents if a not in known]
    return {
        "config":          cfg.model_dump(exclude={"project_dir"}),
        "missing_agents":  missing,
        "system_user":     cfg.effective_system_user(),
        "filesystem_path": cfg.effective_filesystem_path(),
    }


@app.get("/projects/{project_id}/agents")
def project_agents(project_id: str):
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    running = runtime.status_all()
    return {
        agent_id: {
            "role":    "boss" if agent_id == cfg.agents.boss else "worker",
            "found":   discovery.get(agent_id) is not None,
            "runtime": running.get(agent_id),
        }
        for agent_id in cfg.all_agents
    }




class CreateProjectRequest(BaseModel):
    id:          str
    name:        str
    description: str = ""
    boss:        str
    workers:     list[str] = []
    samba:       bool = True
    nfs:         bool = False
    show_swarm:  bool = False


@app.post("/projects", status_code=201)
async def create_project(req: CreateProjectRequest, _a: tuple = Depends(require_admin)):
    """
    Neues Projekt anlegen — alles in einem Schritt:
    1. Verzeichnis + project.yaml schreiben
    2. Provisionieren (Linux-User, Samba-Share, Matrix-Room)
    Das ist der Endpoint den die Webkonsole aufruft.
    """
    import re
    import asyncio as _asyncio
    import yaml as _yaml

    if not re.match(r"^[a-z0-9_-]+$", req.id):
        raise HTTPException(400, "Projekt-ID darf nur a-z, 0-9, _ und - enthalten")

    if projects.get(req.id):
        raise HTTPException(409, f"Projekt '{req.id}' existiert bereits")

    if not discovery.get(req.boss):
        raise HTTPException(422, f"Boss-Agent '{req.boss}' nicht in Discovery")

    project_dir = Path(PROJECTS_DIR) / req.id
    project_dir.mkdir(parents=True, exist_ok=True)

    project_data = {
        "id": req.id, "version": "1.0.0",
        "identity": {"name": req.name, "description": req.description},
        "agents":   {"boss": req.boss, "workers": req.workers},
        "matrix":   {"room": ""},
        "filesystem": {"path": f"/projects/{req.id}", "samba": req.samba, "nfs": req.nfs},
        "system":   {"user": f"proj_{req.id}", "group": f"proj_{req.id}"},
        "chat":     {"show_swarm": req.show_swarm},
    }
    yaml_path = project_dir / "project.yaml"
    yaml_path.write_text(_yaml.dump(project_data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    logger.info("project.yaml geschrieben: %s", yaml_path)
    audit_log("project.create", target=req.id, project_id=req.id, details={"boss": req.boss})

    await _asyncio.sleep(0.3)

    cfg = projects.get(req.id) or projects.register(project_dir)
    if cfg is None:
        raise HTTPException(500, "Projekt konnte nach Anlage nicht geladen werden")

    if provisioner is None:
        raise HTTPException(503, "Provisioner nicht initialisiert")

    result = await provisioner.provision(cfg)
    audit_log("project.provision", target=project_id, project_id=project_id)
    if result.matrix_room and not cfg.matrix.room:
        _update_project_matrix_room(req.id, result.matrix_room)

    return {
        "created": True, "project_id": req.id,
        "linux_user": result.linux_user, "files_dir": result.files_dir,
        "samba_share": result.samba_share, "matrix_room": result.matrix_room,
        "warnings": result.warnings, "ok": result.ok,
    }


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str, remove_files: bool = False):
    """Projekt loeschen: Deprovisionieren + project.yaml entfernen."""
    import shutil as _shutil
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    if provisioner is None:
        raise HTTPException(503, "Provisioner nicht initialisiert")
    warnings = await provisioner.deprovision(cfg)
    project_dir = Path(PROJECTS_DIR) / project_id
    if remove_files and project_dir.exists():
        _shutil.rmtree(project_dir)
    else:
        yaml_path = project_dir / "project.yaml"
        if yaml_path.exists():
            yaml_path.unlink()
    return {"deleted": project_id, "files_removed": remove_files, "warnings": warnings}

# ================================================================== Sessions

@app.get("/projects/{project_id}/session")
def get_session(project_id: str):
    """Aktive Session eines Projekts."""
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    session = sessions.get_active(project_id)
    if not session:
        return {"active": False, "session": None}
    return {
        "active":      True,
        "session_id":  session.id,
        "started_at":  session.started_at,
        "message_count": len(session.messages),
    }


@app.post("/projects/{project_id}/session/start")
def start_session(project_id: str):
    """Neue Session starten (beendet ggf. vorherige)."""
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    session = sessions.new_session(project_id)
    return {"session_id": session.id, "started_at": session.started_at}


@app.post("/projects/{project_id}/session/end")
def end_session(project_id: str):
    """Aktive Session beenden."""
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    session = sessions.end_session(project_id)
    if not session:
        return {"ended": False}
    return {"ended": True, "session_id": session.id, "message_count": len(session.messages)}


class MessageRequest(BaseModel):
    role:     str
    content:  str
    agent_id: str | None = None


@app.post("/projects/{project_id}/session/message")
def append_message(project_id: str, req: MessageRequest):
    """Nachricht an aktive Session anhängen (Session wird ggf. angelegt)."""
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    try:
        role = MessageRole(req.role)
    except ValueError:
        raise HTTPException(400, f"Ungültige Rolle: {req.role}. Erlaubt: user, assistant, system, tool")
    msg = sessions.append(project_id, role, req.content, agent_id=req.agent_id)
    session = sessions.get_active(project_id)
    return {
        "appended":      True,
        "session_id":    session.id if session else None,
        "message_count": len(session.messages) if session else 1,
        "timestamp":     msg.timestamp,
    }


@app.get("/projects/{project_id}/session/history")
def session_history(project_id: str, limit: int = 50):
    """Nachrichten-History der aktiven Session."""
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    context = sessions.get_context(project_id, max_messages=limit)
    session = sessions.get_active(project_id)
    return {
        "session_id": session.id if session else None,
        "messages":   context,
        "count":      len(context),
    }


# ================================================================== Orchestrator

class IncomingMessage(BaseModel):
    content: str
    sender:  str = "user"



@app.post("/projects/{project_id}/message/stream")
async def send_message_stream(project_id: str, req: IncomingMessage):
    """
    Streaming-Version: SSE Token-für-Token.
    Client: fetch + ReadableStream oder EventSource.
    Format: data: {"text": "..."} / data: {"done": true} / data: {"error": "..."}
    """
    from fastapi.responses import StreamingResponse as _SR
    import asyncio as _asyncio

    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt nicht gefunden")
    if not discovery.get(cfg.agents.boss):
        raise HTTPException(503, f"Boss-Agent nicht verfügbar")

    async def event_stream():
        async for chunk in orchestrator.handle_message_stream(
            project_id=project_id,
            project_cfg=cfg,
            content=req.content,
            sender=req.sender,
        ):
            yield chunk

    return _SR(event_stream(), media_type="text/event-stream",
               headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/projects/{project_id}/message")
async def send_message(project_id: str, req: IncomingMessage):
    """
    User-Nachricht an Projekt senden — Boss-Agent verarbeitet und antwortet.
    Das ist der Haupt-Einstiegspunkt für die Web-Chat-UI und Matrix-Integration.
    """
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

    boss_id = cfg.agents.boss
    if not discovery.get(boss_id):
        raise HTTPException(503, f"Boss-Agent '{boss_id}' nicht in Discovery")

    response, workers = await orchestrator.handle_message(
        project_id=project_id,
        project_cfg=cfg,
        content=req.content,
        sender=req.sender,
    )
    session = sessions.get_active(project_id)
    return {
        "response":      response,
        "workers":       workers,
        "session_id":    session.id if session else None,
        "message_count": len(session.messages) if session else 0,
    }





@app.get("/agents/{agent_id}/logs")
def get_agent_logs(agent_id: str, lines: int = 100):
    """
    Agent-Logs aus journalctl — gefiltert auf octopos-core + Agent-ID.
    Gibt die letzten N Zeilen zurueck.
    Interface: { agent_id, lines: [...], count }
    """
    import subprocess as _sub

    # Agent existiert?
    if not discovery.get(agent_id) and not (Path(AGENTS_DIR) / agent_id).exists():
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

    lines = max(10, min(lines, 1000))  # 10-1000

    try:
        # journalctl fuer octopos-core, letzten N Zeilen, kein Pager
        result = _sub.run(
            [
                "journalctl",
                "-u", "octopos-core",
                "-n", str(lines),
                "--no-pager",
                "--output=short-iso",
            ],
            capture_output=True, text=True, timeout=5
        )
        all_lines = result.stdout.splitlines()

        # Auf Agent-ID filtern (case-insensitive)
        agent_lower = agent_id.lower().replace("-", "_")
        filtered = [
            l for l in all_lines
            if agent_lower in l.lower()
            or agent_id.lower() in l.lower()
            or "octopos_core" in l.lower()  # Core-eigene Meldungen immer drin
        ]

        # Wenn zu wenig agent-spezifisch → alle Core-Logs
        if len(filtered) < 5:
            filtered = all_lines

        return {
            "agent_id": agent_id,
            "lines":    filtered[-lines:],
            "count":    len(filtered),
            "source":   "journalctl -u octopos-core",
        }

    except FileNotFoundError:
        # journalctl nicht verfuegbar (z.B. in Tests)
        return {
            "agent_id": agent_id,
            "lines":    ["[journalctl nicht verfuegbar]"],
            "count":    1,
            "source":   "unavailable",
        }
    except _sub.TimeoutExpired:
        raise HTTPException(504, "Timeout beim Lesen der Logs")


@app.get("/logs/core")
def get_core_logs(lines: int = 200):
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
        AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
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


@app.get("/audit/logs")
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


@app.get("/users")
def list_users():
    """Alle OctopOS-User auflisten."""
    users = _load_users()
    return {
        username: {
            "username":    username,
            "role":        data.get("role", "user"),
            "matrix_id":  f"@{username}:{_read_server_name()}",
            "created_at": data.get("created_at", ""),
        }
        for username, data in users.items()
    }


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role:     str = "user"   # user | admin


@app.post("/users", status_code=201)
async def create_user(req: CreateUserRequest, _a: tuple = Depends(require_admin)):
    """Neuen User anlegen — Console-Login + Matrix-Account."""
    import re as _re
    from datetime import datetime as _dt

    if not _re.match(r"^[a-z0-9_.-]+$", req.username):
        raise HTTPException(400, "Username darf nur a-z, 0-9, _ . - enthalten")
    if len(req.password) < 8:
        raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")

    users = _load_users()
    if req.username in users:
        raise HTTPException(409, f"User '{req.username}' existiert bereits")

    # Matrix-Account anlegen
    server_name = _read_server_name()
    matrix_ok   = await _matrix_register(req.username, req.password, server_name)

    # Console-Credentials speichern
    users[req.username] = {
        "password_hash": _hash_password(req.password),
        "role":          req.role,
        "matrix_id":     f"@{req.username}:{server_name}",
        "matrix_ok":     matrix_ok,
        "created_at":    _dt.now().isoformat(),
    }
    _save_users(users)
    logger.info("User angelegt: %s (role=%s, matrix=%s)", req.username, req.role, matrix_ok)
    audit_log("user.create", target=req.username, details={"role": req.role})

    return {
        "created":    True,
        "username":   req.username,
        "matrix_id":  f"@{req.username}:{server_name}",
        "matrix_ok":  matrix_ok,
    }


@app.delete("/users/{username}")
async def delete_user(username: str, _a: tuple = Depends(require_admin)):
    """User löschen (Console-Login entfernen)."""
    users = _load_users()
    if username not in users:
        raise HTTPException(404, f"User '{username}' nicht gefunden")
    if username == "admin":
        raise HTTPException(403, "Admin-User kann nicht gelöscht werden")
    del users[username]
    _save_users(users)
    logger.info("User gelöscht: %s", username)
    audit_log("user.delete", target=username)
    return {"deleted": True, "username": username}


@app.put("/users/{username}/password")
async def change_password(username: str, body: dict, _a: tuple = Depends(require_admin)):
    """Passwort ändern."""
    new_password = body.get("password", "").strip()
    if len(new_password) < 8:
        raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")
    users = _load_users()
    if username not in users:
        raise HTTPException(404, f"User '{username}' nicht gefunden")
    users[username]["password_hash"] = _hash_password(new_password)
    _save_users(users)
    return {"updated": True, "username": username}





# ================================================================== AgentLink

@app.get("/projects/{project_id}/agentlink")
def list_agentlink(project_id: str):
    """Aktive Handoffs eines Projekts auflisten."""
    from .agentlink import list_handoffs as _lh
    project_dir = Path(PROJECTS_DIR) / project_id
    if not project_dir.exists():
        raise HTTPException(404, f"Projekt nicht gefunden")
    handoffs = _lh(project_dir)
    return {"project_id": project_id, "handoffs": handoffs, "count": len(handoffs)}


@app.delete("/projects/{project_id}/agentlink/{handoff_id}")
def delete_agentlink(project_id: str, handoff_id: str):
    """Handoff manuell loeschen."""
    from .agentlink import delete_handoff as _dh
    project_dir = Path(PROJECTS_DIR) / project_id
    if not project_dir.exists():
        raise HTTPException(404, f"Projekt nicht gefunden")
    deleted = _dh(project_dir, handoff_id)
    if not deleted:
        raise HTTPException(404, f"Handoff '{handoff_id}' nicht gefunden")
    return {"deleted": True, "handoff_id": handoff_id}


@app.post("/projects/{project_id}/agentlink")
def create_agentlink(project_id: str, body: dict):
    """Handoff manuell anlegen (fuer Tests)."""
    from .agentlink import write_handoff as _wh
    project_dir = Path(PROJECTS_DIR) / project_id
    if not project_dir.exists():
        raise HTTPException(404, f"Projekt nicht gefunden")
    entry = _wh(
        project_dir,
        from_agent=body.get("from_agent", "manual"),
        to_agent=body.get("to_agent", ""),
        context=body.get("context", ""),
        data=body.get("data", {}),
        ttl_seconds=int(body.get("ttl_seconds", 3600)),
    )
    return entry


# ================================================================== Webhook-System

VALID_EVENTS = {"message", "agent_error", "provision", "agent_start", "agent_stop"}


def _webhooks_file(project_id: str) -> Path:
    return Path(PROJECTS_DIR) / project_id / "webhooks.json"


def _load_webhooks(project_id: str) -> list[dict]:
    import json as _j
    f = _webhooks_file(project_id)
    try:
        return _j.loads(f.read_text())
    except (OSError, ValueError):
        return []


def _save_webhooks(project_id: str, webhooks: list[dict]) -> None:
    import json as _j
    f = _webhooks_file(project_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_j.dumps(webhooks, indent=2), encoding="utf-8")


async def _fire_webhook(webhook: dict, event: str, data: dict) -> None:
    """Webhook asynchron abfeuern — Fehler werden geloggt, nicht propagiert."""
    import hashlib as _hl, hmac as _hm, time as _time
    import json as _j

    payload = _j.dumps({
        "event":      event,
        "project_id": data.get("project_id", ""),
        "timestamp":  _time.time(),
        "data":       data,
    })

    headers = {
        "Content-Type": "application/json",
        "User-Agent":   "OctopOS-Webhook/1.0",
        "X-OctopOS-Event": event,
    }

    secret = webhook.get("secret", "")
    if secret:
        sig = _hm.new(secret.encode(), payload.encode(), _hl.sha256).hexdigest()
        headers["X-OctopOS-Signature"] = f"sha256={sig}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook["url"], content=payload, headers=headers)
            logger.info("Webhook %s → %s: HTTP %d", event, webhook["url"], resp.status_code)
    except Exception as e:
        logger.warning("Webhook fehlgeschlagen (%s → %s): %s", event, webhook["url"], e)


async def fire_project_webhooks(project_id: str, event: str, data: dict) -> None:
    """Alle Webhooks eines Projekts fuer ein Event abfeuern."""
    webhooks = _load_webhooks(project_id)
    tasks = [
        _fire_webhook(wh, event, data)
        for wh in webhooks
        if event in wh.get("events", [])
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class WebhookRequest(BaseModel):
    name:   str
    url:    str
    secret: str = ""
    events: list[str] = ["message"]


@app.get("/projects/{project_id}/webhooks")
def list_webhooks(project_id: str):
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt nicht gefunden")
    webhooks = _load_webhooks(project_id)
    # Secrets maskieren
    masked = [{**w, "secret": "***" if w.get("secret") else ""} for w in webhooks]
    return {"project_id": project_id, "webhooks": masked}


@app.post("/projects/{project_id}/webhooks", status_code=201)
def create_webhook(project_id: str, req: WebhookRequest, _a: tuple = Depends(require_admin)):
    import secrets as _sec, time as _time
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt nicht gefunden")

    invalid = [e for e in req.events if e not in VALID_EVENTS]
    if invalid:
        raise HTTPException(400, f"Unbekannte Events: {invalid}. Gueltig: {sorted(VALID_EVENTS)}")

    webhooks = _load_webhooks(project_id)
    wh = {
        "id":         _sec.token_hex(8),
        "name":       req.name,
        "url":        req.url,
        "secret":     req.secret,
        "events":     req.events,
        "created_at": _time.time(),
    }
    webhooks.append(wh)
    _save_webhooks(project_id, webhooks)
    logger.info("Webhook angelegt: %s → %s (%s)", project_id, req.url, req.events)
    audit_log("webhook.create", target=req.url, project_id=project_id, details={"events": req.events})
    return {**wh, "secret": "***" if wh["secret"] else ""}


@app.delete("/projects/{project_id}/webhooks/{webhook_id}")
def delete_webhook(project_id: str, webhook_id: str, _a: tuple = Depends(require_admin)):
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt nicht gefunden")
    webhooks = _load_webhooks(project_id)
    updated  = [w for w in webhooks if w["id"] != webhook_id]
    if len(updated) == len(webhooks):
        raise HTTPException(404, f"Webhook '{webhook_id}' nicht gefunden")
    _save_webhooks(project_id, updated)
    return {"deleted": True, "webhook_id": webhook_id}


@app.post("/projects/{project_id}/webhooks/test")
async def test_webhook(project_id: str, body: dict):
    """Test-Ping an einen Webhook senden."""
    url    = body.get("url", "")
    secret = body.get("secret", "")
    if not url:
        raise HTTPException(400, "url fehlt")
    wh = {"url": url, "secret": secret}
    await _fire_webhook(wh, "ping", {"project_id": project_id, "message": "OctopOS Webhook Test"})
    return {"sent": True, "url": url}


@app.post("/hooks/{project_id}/wake")
async def webhook_wake(project_id: str, request: Request):
    """
    Externer Trigger — startet Boss-Agent mit einer Wake-Nachricht.
    Optional: X-OctopOS-Signature Header fuer Verifikation.
    Body: { "message": "...", "sender": "..." }
    """
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt nicht gefunden")

    # Signatur prüfen falls Webhook mit Secret konfiguriert
    webhooks = _load_webhooks(project_id)
    wake_hooks = [w for w in webhooks if "agent_start" in w.get("events", [])]

    body_bytes = await request.body()
    sig_header = request.headers.get("X-OctopOS-Signature", "")

    if wake_hooks and sig_header:
        import hashlib as _hl, hmac as _hm
        for wh in wake_hooks:
            secret = wh.get("secret", "")
            if secret:
                expected = "sha256=" + _hm.new(secret.encode(), body_bytes, _hl.sha256).hexdigest()
                if not _hm.compare_digest(sig_header, expected):
                    raise HTTPException(401, "Ungültige Signatur")
                break

    try:
        data = json.loads(body_bytes) if body_bytes else {}
    except Exception:
        data = {}

    message = data.get("message", "Wake-up call")
    sender  = data.get("sender", "webhook")

    boss_id = cfg.agents.boss
    if not discovery.get(boss_id):
        raise HTTPException(503, f"Boss-Agent '{boss_id}' nicht verfügbar")

    # Asynchron starten — nicht warten
    asyncio.create_task(
        orchestrator.handle_message(project_id, cfg, message, sender),
        name=f"webhook-wake-{project_id}"
    )

    return {"triggered": True, "project_id": project_id, "message": message}


# ================================================================== QMD-Skills CRUD

def _skills_dir(agent_id: str) -> Path:
    return Path(AGENTS_DIR) / agent_id / "skills"


def _parse_skill_file(path: Path) -> dict:
    """QMD-Datei parsen → dict mit Frontmatter + Content."""
    import re as _re
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    m = _re.match(r"^---\s*\n(.*?)\n---\s*\n", text, _re.DOTALL)
    if not m:
        return {
            "filename": path.stem,
            "skill":    path.stem,
            "version":  "1.0",
            "scope":    "on-demand",
            "triggers": [],
            "priority": 50,
            "content":  text.strip(),
        }

    import yaml as _yaml
    try:
        meta = _yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}

    return {
        "filename": path.stem,
        "skill":    meta.get("skill", path.stem),
        "version":  str(meta.get("version", "1.0")),
        "scope":    meta.get("scope", "on-demand"),
        "triggers": meta.get("triggers", []) or [],
        "priority": int(meta.get("priority", 50)),
        "content":  text[m.end():].strip(),
    }


def _write_skill_file(skills_dir: Path, filename: str, data: dict) -> Path:
    """Skill-Dict als QMD-Datei schreiben."""
    import yaml as _yaml

    skills_dir.mkdir(parents=True, exist_ok=True)

    # Sicherer Dateiname
    safe = filename.replace(".md", "").replace("/", "-").replace("..", "")
    if not safe:
        raise ValueError("Ungültiger Dateiname")
    path = skills_dir / f"{safe}.md"

    frontmatter = {
        "skill":    data.get("skill", safe),
        "version":  data.get("version", "1.0"),
        "scope":    data.get("scope", "on-demand"),
        "priority": int(data.get("priority", 50)),
    }
    triggers = data.get("triggers", [])
    if triggers:
        frontmatter["triggers"] = triggers

    yaml_str  = _yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
    content   = data.get("content", "").strip()
    full_text = f"---\n{yaml_str}---\n\n{content}\n"
    path.write_text(full_text, encoding="utf-8")
    return path


@app.get("/agents/{agent_id}/skills")
def list_agent_skills(agent_id: str):
    """Alle QMD-Skills eines Agenten."""
    agent_dir = Path(AGENTS_DIR) / agent_id
    if not agent_dir.exists():
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

    skills_dir = _skills_dir(agent_id)
    if not skills_dir.exists():
        return {"agent_id": agent_id, "skills": []}

    skills = []
    for path in sorted(skills_dir.glob("*.md")):
        skill = _parse_skill_file(path)
        if skill:
            skills.append(skill)

    skills.sort(key=lambda s: s.get("priority", 50))
    return {"agent_id": agent_id, "skills": skills}


class SkillRequest(BaseModel):
    filename: str
    skill:    str
    version:  str  = "1.0"
    scope:    str  = "on-demand"
    triggers: list[str] = []
    priority: int  = 50
    content:  str  = ""


@app.post("/agents/{agent_id}/skills", status_code=201)
def create_agent_skill(agent_id: str, req: SkillRequest, _a: tuple = Depends(require_admin)):
    """Neuen QMD-Skill anlegen."""
    agent_dir = Path(AGENTS_DIR) / agent_id
    if not agent_dir.exists():
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

    skills_dir = _skills_dir(agent_id)
    target = skills_dir / f"{req.filename.replace('.md','')}.md"
    if target.exists():
        raise HTTPException(409, f"Skill '{req.filename}' existiert bereits")

    path = _write_skill_file(skills_dir, req.filename, req.model_dump())
    logger.info("Skill angelegt: %s/%s", agent_id, path.name)
    return {"created": True, "agent_id": agent_id, "filename": path.stem}


@app.put("/agents/{agent_id}/skills/{filename}")
def update_agent_skill(agent_id: str, filename: str, req: SkillRequest, _a: tuple = Depends(require_admin)):
    """Bestehenden QMD-Skill aktualisieren."""
    agent_dir = Path(AGENTS_DIR) / agent_id
    if not agent_dir.exists():
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

    path = _write_skill_file(_skills_dir(agent_id), filename, req.model_dump())
    logger.info("Skill aktualisiert: %s/%s", agent_id, path.name)
    return {"updated": True, "agent_id": agent_id, "filename": path.stem}


@app.delete("/agents/{agent_id}/skills/{filename}")
def delete_agent_skill(agent_id: str, filename: str, _a: tuple = Depends(require_admin)):
    """QMD-Skill löschen."""
    safe = filename.replace(".md", "").replace("/", "-").replace("..", "")
    path = _skills_dir(agent_id) / f"{safe}.md"
    if not path.exists():
        raise HTTPException(404, f"Skill '{filename}' nicht gefunden")
    path.unlink()
    logger.info("Skill gelöscht: %s/%s", agent_id, safe)
    return {"deleted": True, "agent_id": agent_id, "filename": safe}


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
    heartbeat_interval:  str = "30s"
    heartbeat_timeout:   str = "90s"
    heartbeat_on_failure: str = "restart"


@app.post("/agents", status_code=201)
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


@app.put("/agents/{agent_id}")
async def update_agent(agent_id: str, req: CreateAgentRequest, _a: tuple = Depends(require_admin)):
    """Agent-Config aktualisieren — überschreibt agent.yaml."""
    import asyncio as _asyncio
    import yaml as _yaml

    agent_dir = Path(AGENTS_DIR) / agent_id
    if not agent_dir.exists():
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

    agent_data = {
        "id":       req.id or agent_id,
        "type":     req.type,
        "identity": req.identity,
        "llm": {
            "model":       req.model,
            "temperature": req.temperature,
            "max_tokens":  req.max_tokens,
        },
        "tools":    req.tools,
        "heartbeat": {
            "interval":   req.heartbeat_interval,
            "timeout":    req.heartbeat_timeout,
            "on_failure": req.heartbeat_on_failure,
        },
    }
    if req.soul:
        agent_data["soul"] = "./soul.md"
        (agent_dir / "soul.md").write_text(req.soul, encoding="utf-8")

    yaml_path = agent_dir / "agent.yaml"
    yaml_path.write_text(
        _yaml.dump(agent_data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8"
    )
    logger.info("Agent aktualisiert: %s", agent_id)
    await _asyncio.sleep(0.3)
    return {"updated": True, "agent_id": agent_id}


@app.delete("/agents/{agent_id}")
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


@app.get("/agents/{agent_id}/soul")
def get_agent_soul(agent_id: str):
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

@app.post("/projects/{project_id}/provision")
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


@app.delete("/projects/{project_id}/provision")
async def deprovision_project(project_id: str):
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



# ================================================================== Tools

@app.get("/tools")
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


@app.get("/llm/config")
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



@app.put("/llm/config/claude_max")
async def set_claude_oauth_token(body: dict, _a: tuple = Depends(require_admin)):
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


@app.put("/llm/config/{provider}")
def set_llm_provider(provider: str, req: LlmProviderConfig, _a: tuple = Depends(require_admin)):
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



@app.get("/llm/claude_token_status")
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


@app.get("/llm/ollama/models")
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



@app.post("/llm/ollama/pull")
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


# ================================================================== Status

@app.get("/status")
def system_status():
    return {
        "discovery": {
            "agents_dir": AGENTS_DIR,
            "count":      len(discovery.agents),
        },
        "projects": {
            "projects_dir": PROJECTS_DIR,
            "count":        len(projects.projects),
        },
        "sessions": {
            "active_projects": sessions.active_projects(),
        },
        "runtime": runtime.status_all(),
    }
