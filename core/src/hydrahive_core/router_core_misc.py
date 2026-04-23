from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .auth_utils import clear_auth_cookie, set_auth_cookie
from .settings import settings

_bearer = HTTPBearer(auto_error=False)


def _sync_admin_credential_console_password(new_password: str, logger: logging.Logger) -> bool:
    """
    #814: console_password in /etc/hydrahive/admin_credentials auf
    `new_password` setzen. admin_credentials ist die Source-of-Truth für
    BL-15 (update.sh / 06_core_service.sh). Ohne diesen Sync überlebt ein
    im Wizard / Passwort-Reset gesetztes Passwort kein update.sh.

    Idempotent: bestehende andere Keys (matrix_admin_password etc.) bleiben
    unberührt. Schreibt atomar über Temp-File + replace. Schlägt niemals
    durch — bei Permission-Fehlern wird gewarnt, der Setup/Change-Flow
    läuft weiter (users.json ist ja bereits aktualisiert).
    """
    cred_path = settings.admin_credentials
    try:
        existing_lines: list[str] = []
        if cred_path.exists():
            for line in cred_path.read_text(encoding="utf-8").splitlines():
                if not line.startswith("console_password="):
                    existing_lines.append(line)
        existing_lines.append(f"console_password={new_password}")
        tmp = cred_path.with_suffix(cred_path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(cred_path)
        logger.info("admin_credentials.console_password synchronisiert (#814)")
        return True
    except PermissionError as e:
        logger.warning(
            "admin_credentials (#814) konnte nicht geschrieben werden (%s). "
            "Fix: chown hydrahive:hydrahive /etc/hydrahive/admin_credentials && chmod 600. "
            "Bis dahin überlebt das neue Passwort kein update.sh.",
            e,
        )
        return False
    except OSError as e:
        logger.warning("admin_credentials (#814) Schreibfehler: %s", e)
        return False


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str
    password: str


class KasConfigRequest(BaseModel):
    login:          str
    password:       str
    default_domain: str = ""
    smtp_host:      str = ""
    smtp_port:      int = 587


class AgentLlmPatchRequest(BaseModel):
    fallback_models: list[str]


class WorkspaceOverride(BaseModel):
    """#662: internal-only Workspace-Override für Sub-Agent-Worktrees.

    Darf NUR von internal-auth Aufrufen (ask_agent via HMAC) gesetzt werden.
    Der Router validiert Feld gegen worktrees_dir und subagent_worktrees-
    Metadaten und lehnt externe Clients mit 400 ab.
    """
    path:              str
    worktree_id:       str
    parent_project_id: str


class IncomingMessage(BaseModel):
    content: str
    sender: str = "user"
    execution_mode: Literal["safe", "elevated", "root", "unrestricted"] | None = None
    images: list[dict] | None = None  # [{"data": "base64...", "media_type": "image/png"}]
    workspace_override: WorkspaceOverride | None = None  # #662, internal-only


class AgentSoulRequest(BaseModel):
    soul: str = ""


class AgentFullConfigRequest(BaseModel):
    config: dict


class CloneAgentRequest(BaseModel):
    new_id: str


class WriteFileRequest(BaseModel):
    path: str
    content: str = ""


class DashboardConfigRequest(BaseModel):
    custom_tabs: list[dict] = []
    overview_widgets: dict = {}


class ShellCommandRequest(BaseModel):
    command: str


class AgentMemoryRequest(BaseModel):
    filename: str = "session"
    content: str
    mode: str = "overwrite"


class SessionImportRequest(BaseModel):
    session_b64: str


_JOURNAL_TIMESTAMP_RE = re.compile(r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)\b")
_JOURNAL_MESSAGE_RE = re.compile(
    r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?\s+\S+\s+\S+(?:\[\d+\])?:\s*(?P<msg>.*)$"
)


def _extract_journal_timestamp(line: str) -> str | None:
    match = _JOURNAL_TIMESTAMP_RE.match(line)
    if not match:
        return None
    return match.group("ts")


def _extract_journal_message(line: str) -> str:
    match = _JOURNAL_MESSAGE_RE.search(line)
    if match:
        return match.group("msg").strip()
    fallback = line.strip()
    if "]: " in fallback:
        return fallback.split("]: ", 1)[-1].strip()
    return fallback


def _normalize_journal_signature(message: str) -> str:
    signature = re.sub(r"^(?:INFO|WARN(?:ING)?|ERROR|DEBUG|TRACE|FATAL)\s+", "", message, flags=re.IGNORECASE)
    signature = re.sub(r"^[a-z0-9_.-]+:\s+", "", signature, flags=re.IGNORECASE)
    signature = re.sub(r"\b[0-9a-f]{7,}\b", "#", signature, flags=re.IGNORECASE)
    signature = re.sub(r"\b\d+\b", "#", signature)
    signature = re.sub(r"\s+", " ", signature).strip()
    return signature[:220]


def summarize_core_journal_lines(lines: list[str], *, source: str = "journalctl -u hydrahive-core") -> dict:
    timestamps: list[str] = []
    signatures: Counter[str] = Counter()
    error_count = 0
    warn_count = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        timestamp = _extract_journal_timestamp(line)
        if timestamp:
            timestamps.append(timestamp)

        message = _extract_journal_message(line)
        lowered = message.lower()
        if any(keyword in lowered for keyword in (" error ", " error:", "error", " fehler", " fehler:", "failed", " failure", " exception", " traceback")):
            error_count += 1
        if any(keyword in lowered for keyword in (" warn ", " warn:", "warn", " warning", " warnung", " warnung:")):
            warn_count += 1

        signature = _normalize_journal_signature(message)
        if signature:
            signatures[signature] += 1

    # Bekannte Noise-Patterns aus Top-Signatures herausfiltern (#153)
    _NOISE_PATTERNS = ("nio.rooms", "snap", "firmware", "handling event of type")
    top_signatures = [
        {"signature": signature, "count": count}
        for signature, count in signatures.most_common(20)
        if not any(p in signature for p in _NOISE_PATTERNS)
    ][:5]
    return {
        "source": source,
        "available": bool(lines),
        "count": len([line for line in lines if line.strip()]),
        "error_count": error_count,
        "warn_count": warn_count,
        "first_timestamp": min(timestamps) if timestamps else None,
        "last_timestamp": max(timestamps) if timestamps else None,
        "top_signatures": top_signatures,
    }


_journal_cache: dict[int, tuple[float, dict]] = {}
_JOURNAL_CACHE_TTL_S: float = 30.0


def collect_core_journal_report(*, lines: int = 200) -> dict:
    import subprocess as _sub

    lines = max(10, min(lines, 1000))
    now = time.monotonic()
    cached_ts, cached_result = _journal_cache.get(lines, (0.0, {}))
    if cached_result and (now - cached_ts) < _JOURNAL_CACHE_TTL_S:
        return cached_result
    try:
        result = _sub.run(
            [
                "journalctl",
                "-u",
                "hydrahive-core",
                "-n",
                str(lines),
                "--no-pager",
                "--output=short-iso",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        journal_lines = result.stdout.splitlines()
        summary = summarize_core_journal_lines(journal_lines)
        report = {
            "available": True,
            "source": "journalctl -u hydrahive-core",
            "count": len(journal_lines),
            "lines": journal_lines,
            "summary": summary,
        }
        _journal_cache[lines] = (time.monotonic(), report)
        return report
    except Exception as e:
        return {
            "available": False,
            "source": "journalctl -u hydrahive-core",
            "count": 0,
            "lines": [str(e)],
            "summary": {
                "source": "journalctl -u hydrahive-core",
                "available": False,
                "count": 0,
                "error_count": 0,
                "warn_count": 0,
                "first_timestamp": None,
                "last_timestamp": None,
                "top_signatures": [],
                "reason": str(e),
            },
            "reason": str(e),
        }


def register_core_misc_routes(
    public_router: APIRouter,
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    require_auth,
    setup_lock: asyncio.Lock,
    load_users,
    save_users,
    read_server_name,
    matrix_register,
    hash_password,
    verify_password,
    make_jwt,
    read_admin_password,
    check_login_rate,
    discovery,
    runtime,
    read_audit_logs,
    logger: logging.Logger,
    group_service=None,
) -> type[IncomingMessage]:
    async def _reload_runtime_agent_config(agent_id: str) -> bool:
        cfg = discovery.get(agent_id)
        if cfg is not None and hasattr(runtime, "reload_agent_config"):
            return await runtime.reload_agent_config(cfg)
        return False

    @public_router.get("/setup/status")
    def setup_status():
        import json
        from pathlib import Path as _Path
        users = load_users()
        kas_ok  = settings.kas_config.exists()
        llm_ok  = settings.llm_config.exists() or settings.llm_env.exists()
        wizard_done = settings.setup_wizard_done.exists()
        return {
            "needs_setup":   len(users) == 0,
            "wizard_done":   wizard_done or (kas_ok and llm_ok),
            "kas_configured": kas_ok,
            "llm_configured": llm_ok,
        }

    _KAS_PATH = settings.kas_config

    @admin_router.get("/admin/kas")
    def get_kas():
        import json
        p = _KAS_PATH
        if not p.exists():
            return {"configured": False}
        try:
            data = json.loads(p.read_text())
            return {"configured": True, **data}
        except Exception as e:
            logger.debug("KAS config parse failed: %s", e)
            return {"configured": False}

    @admin_router.put("/admin/kas")
    def put_kas(req: KasConfigRequest):
        import json
        p = _KAS_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        data = req.model_dump()
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        p.chmod(0o600)
        return {"saved": True}

    @admin_router.post("/admin/wizard/complete")
    def wizard_complete():
        settings.setup_wizard_done.touch()
        return {"done": True}

    @public_router.post("/setup", status_code=201)
    async def run_setup(req: SetupRequest):
        import re as _re

        async with setup_lock:
            users = load_users()
            if users:
                raise HTTPException(403, "Setup bereits abgeschlossen")
            if not _re.match(r"^[a-z0-9_.-]+$", req.username):
                raise HTTPException(400, "Username darf nur a-z, 0-9, _ . - enthalten")
            if len(req.password) < 8:
                raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")

            server_name = read_server_name()
            matrix_ok = await matrix_register(req.username, req.password, server_name)
            users[req.username] = {
                "password_hash": hash_password(req.password),
                "role": "admin",
                "matrix_id": f"@{req.username}:{server_name}",
                "matrix_ok": matrix_ok,
                "created_at": datetime.now().isoformat(),
            }
            save_users(users)
            # #814: console_password in admin_credentials synchron halten.
            # BL-15 (in update.sh / 06_core_service.sh) schreibt bei jedem
            # Update den users.json-Hash aus admin_credentials.console_password
            # neu. Ohne diesen Upsert bliebe dort der Installer-Random-String
            # aus 06_core_service.sh, und der Wizard-Login wird beim ersten
            # update.sh wieder ungültig.
            _sync_admin_credential_console_password(req.password, logger)
            logger.info("Setup abgeschlossen: erster Admin-User '%s' angelegt", req.username)
            return {"created": True, "username": req.username, "role": "admin"}

    @public_router.post(
        "/auth/login",
        summary="Login — Dual-Mode Auth (Cookie + Bearer)",
        description=(
            "Prüft Credentials und stellt ein JWT aus.\n\n"
            "**Response liefert das Token auf ZWEI Wegen:**\n"
            "- `Set-Cookie: hydrahive_token=<jwt>` — httpOnly, SameSite=Lax. "
            "Gedacht für Browser-Clients (Console). Nachfolgende Requests "
            "brauchen kein `Authorization`-Header — `credentials:'include'` reicht.\n"
            "- Response-Body `access_token` — für API-Clients (CLI, MCP, "
            "externe Scripts, CI). Als `Authorization: Bearer <jwt>` an "
            "Folge-Requests anhängen.\n\n"
            "**Produktentscheidung (#767):** Browser nutzt den Cookie-Pfad, "
            "CLI/MCP/API-Clients den Bearer-Pfad. Backend akzeptiert beide "
            "dauerhaft parallel — kein Breaking-Change geplant."
        ),
    )
    def login(req: LoginRequest, request: Request, response: Response):
        check_login_rate(request.client.host if request.client else "unknown")
        # #763: Cookie-Lebensdauer matcht JWT_EXPIRE_H (aus main). Lazy-Import
        # um Zirkel zu vermeiden.
        from .main import JWT_EXPIRE_H as _jwt_expire_h
        _cookie_max_age = _jwt_expire_h * 3600
        _cookie_secure = getattr(settings, "auth_cookie_secure", True)
        users = load_users()
        if users:
            user = users.get(req.username)
            if user and verify_password(req.password, user.get("password_hash", "")):
                # #454: Auto-Rehash von Legacy pbkdf2 → pbkdf2b
                from .auth_utils import needs_rehash, hash_password
                if needs_rehash(user.get("password_hash", "")):
                    user["password_hash"] = hash_password(req.password)
                    save_users(users)
                    logger.info("Legacy-Hash für '%s' auf pbkdf2b migriert", req.username)
                role  = user.get("role", "user")
                group = user.get("group", "standard")
                token = make_jwt(req.username, role, group)
                logger.info("Login erfolgreich (users.json): %s", req.username)
                perms = group_service.get_permissions(req.username) if group_service else {}
                # #763 Phase 1: httpOnly-Cookie zusätzlich zum Body-Token.
                set_auth_cookie(response, token, _cookie_max_age, _cookie_secure)
                return {"access_token": token, "token_type": "bearer", "role": role, "group": group, "username": req.username, "permissions": perms}
            raise HTTPException(401, "Ungültige Zugangsdaten")

        admin_pass = read_admin_password()
        if not admin_pass:
            raise HTTPException(503, "Kein Admin-Passwort konfiguriert — Setup erforderlich")
        if req.username != "admin" or req.password != admin_pass:
            raise HTTPException(401, "Ungültige Zugangsdaten")
        token = make_jwt(req.username, "admin", "admin")
        logger.info("Login erfolgreich (admin_credentials): %s", req.username)
        # #763 Phase 1: httpOnly-Cookie zusätzlich zum Body-Token.
        set_auth_cookie(response, token, _cookie_max_age, _cookie_secure)
        return {"access_token": token, "token_type": "bearer", "role": "admin", "group": "admin", "username": req.username, "permissions": {"pages": ["*"], "tools": ["*"], "plugins": ["*"], "agents": ["*"]}}

    @auth_router.post("/auth/logout")
    def logout(response: Response, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
        """Token revoken — JTI wird in Blacklist eingetragen. #763: Cookie löschen."""
        # #763: Cookie auch bei fehlendem Bearer-Header löschen (z.B. wenn
        # Client nur Cookie-Auth nutzt und Middleware den Header synthetisch
        # gesetzt hat, aber bei expliziter Abmeldung kein Client-Header).
        clear_auth_cookie(response)
        if not creds:
            # Kein Token zum Revoken — Cookie ist aber geclearnt, Logout okay.
            return {"logged_out": True}
        # #809: JWT_SECRET/JWT_ALG/_token_blacklist liegen in main.py — die
        # vorherige Version griff auf Modul-Globals zu, die nie importiert
        # wurden (NameError) und durch das catch-all except silent
        # geschluckt wurden. Folge: Blacklist wurde nie befüllt, Logout
        # hatte gar keine Wirkung. Hier explizit lazy-importieren und nur
        # JWT-Decode-Fehler abfangen.
        from .main import JWT_SECRET as _jwt_secret, JWT_ALG as _jwt_alg
        from .main import _token_blacklist as _bl
        from jose import JWTError, jwt as jose_jwt
        try:
            payload = jose_jwt.decode(creds.credentials, _jwt_secret, algorithms=[_jwt_alg])
        except JWTError:
            # Token nicht mehr decodierbar (abgelaufen / Secret rotiert) —
            # Logout trotzdem als erfolgreich melden, Cookie ist gecleart.
            return {"logged_out": True}
        jti = payload.get("jti")
        exp = payload.get("exp", 0)
        if jti:
            _bl[jti] = float(exp)
            logger.info("Token revokiert: jti=%s", jti)
        return {"logged_out": True}

    @auth_router.get("/auth/me")
    def whoami(auth: tuple[str, str] = Depends(require_auth)):
        username, role = auth
        return {"username": username, "role": role}

    # #770: GET /auth/me — Cookie-authenticated User-State (kein Bearer noetig).
    # Ersetzt localStorage-reads in useAuth.tsx. Liefert username, role, group,
    # permissions — alles was die App nach Reload braucht.
    @auth_router.get("/auth/me")
    def get_me(auth: tuple[str, str] = Depends(require_auth)):
        username, role = auth
        users = load_users()
        user_data = users.get(username, {}) if users else {}
        group = user_data.get("group", "standard")
        perms = group_service.get_permissions(username) if group_service else {}
        return {"username": username, "role": role, "group": group, "permissions": perms}

    # #770: POST /auth/ws-token — Cookie-authenticated, liefert kurzlebiges
    # WS-Ticket mit aud="websocket". Nutzbar fuer Collab-WS-Connect.
    # Ticket-Laeuftzeit: 120s (WS_TOKEN_EXPIRE_S aus main.py).
    @auth_router.post("/auth/ws-token")
    def create_ws_token(auth: tuple[str, str] = Depends(require_auth)):
        from .main import _make_jwt as _mj, WS_TOKEN_EXPIRE_S as _ws_exp
        username, role = auth
        token = _mj(username, role, aud="websocket")
        return {"ticket": token, "expires_in": _ws_exp}

    @public_router.get("/health")
    def health():
        return {"status": "ok", "service": "hydrahive-core"}

    @auth_router.get("/agent-roles")
    def list_agent_roles():
        """v2: Rollen-System entfernt — alle Agents haben 9 Core-Tools."""
        return {"roles": {}, "info": "v2: Rollen-System entfernt. Alle Agents nutzen 9 Core-Tools."}

    @auth_router.get("/agents")
    def list_agents():
        registered = discovery.agents
        running = runtime.status_all()
        return {
            agent_id: {
                "config": {
                    "type": cfg.type,
                    "identity": cfg.identity,
                    "model": cfg.llm.model,
                    "fallback_models": cfg.llm.fallback_models,
                    "tools": cfg.tools,
                    "mcp_servers": cfg.mcp_servers,
                },
                "runtime": running.get(agent_id),
            }
            for agent_id, cfg in registered.items()
        }

    @auth_router.get("/agents/{agent_id}")
    def get_agent(agent_id: str):
        cfg = discovery.get(agent_id)
        if not cfg:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        return {
            "config": cfg.model_dump(exclude={"agent_dir"}),
            "runtime": runtime.status_all().get(agent_id),
        }

    @admin_router.patch("/agents/{agent_id}/llm")
    async def patch_agent_llm(agent_id: str, req: AgentLlmPatchRequest):
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
            discovery._register(cfg.agent_dir)
        except Exception as e:
            logger.error("Fallback-Models speichern fehlgeschlagen für %s: %s", agent_id, e)
            raise HTTPException(500, "Fehler beim Speichern der Konfiguration")
        reloaded = await _reload_runtime_agent_config(agent_id)
        return {"ok": True, "agent_id": agent_id, "fallback_models": req.fallback_models, "runtime_reloaded": reloaded}

    @admin_router.get("/logs/core")
    def get_core_logs(lines: int = 200):
        report = collect_core_journal_report(lines=lines)
        return {
            "source": report["source"],
            "lines": report["lines"],
            "count": report["count"],
            "available": report["available"],
            "summary": report["summary"],
        }

    @admin_router.get("/logs/core/summary")
    def get_core_log_summary(lines: int = 200):
        report = collect_core_journal_report(lines=lines)
        return {
            "source": report["source"],
            "count": report["count"],
            "available": report["available"],
            "summary": report["summary"],
        }

    @admin_router.get("/audit/logs")
    def get_audit_logs(limit: int = 100, project_id: str = "", user: str = "", action: str = ""):
        limit = max(10, min(limit, 1000))
        logs = read_audit_logs(limit, project_id, user, action)
        return {"logs": logs, "count": len(logs)}

    @auth_router.get("/tools")
    def list_tools():
        from .tool_registry import registry

        result = {}
        for tool_id in registry.all_ids():
            tool = registry.get(tool_id)
            if tool:
                result[tool_id] = {
                    "name": tool.name,
                    "description": tool.description,
                    "type": "core",
                    "parameters": tool.parameters,
                }
        return result

    @auth_router.get("/templates")
    def list_templates(_a: tuple[str, str] = Depends(require_auth)):
        """v2 (#601): Listet verfuegbare Projekt-Templates aus installer/templates/."""
        from .settings import settings
        tpl_dir = settings.installer_dir / "templates"
        if not tpl_dir.exists():
            return {"templates": []}
        templates = []
        for d in sorted(tpl_dir.iterdir()):
            if not d.is_dir():
                continue
            agent_md = d / "AGENT.md"
            config = d / "config.yaml"
            templates.append({
                "id": d.name,
                "has_agent_md": agent_md.exists(),
                "has_config": config.exists(),
            })
        return {"templates": templates}

    @auth_router.get("/templates/{template_id}/agent-md", response_class=PlainTextResponse)
    def get_template_agent_md(template_id: str, _a: tuple[str, str] = Depends(require_auth)):
        """v2 (#601): AGENT.md eines Templates als Plain-Text.
        Wird vom Projekt-Wizard zur Preview geladen."""
        import re as _re
        from .settings import settings
        if not _re.match(r"^[a-z0-9_-]+$", template_id):
            raise HTTPException(400, "Ungueltige template_id")
        agent_md = settings.installer_dir / "templates" / template_id / "AGENT.md"
        if not agent_md.exists():
            raise HTTPException(404, f"Template '{template_id}' hat keine AGENT.md")
        try:
            return agent_md.read_text(encoding="utf-8")
        except Exception as e:
            raise HTTPException(500, f"AGENT.md nicht lesbar: {e}")

    @admin_router.get("/secrets/keys")
    def list_secret_keys():
        """v2: Listet verfügbare Key-Namen aus llm_env + agent_secrets.json.
        Gibt NUR die Namen zurück, nie die Werte. Für den Projekt-Wizard."""
        from .settings import settings
        seen: set[str] = set()
        keys: list[dict] = []
        # 1. llm_env lesen
        env_file = settings.llm_env
        if env_file.exists():
            try:
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key_name = line.split("=", 1)[0].strip()
                    key_value = line.split("=", 1)[1].strip()
                    preview = key_value[:4] + "..." if len(key_value) > 4 else "***"
                    keys.append({"name": key_name, "preview": preview, "length": len(key_value)})
                    seen.add(key_name)
            except Exception:
                pass
        # 2. agent_secrets.json lesen (nur Keys die noch nicht in llm_env sind)
        secrets_file = settings.agent_secrets_config
        if secrets_file.exists():
            try:
                import json
                data = json.loads(secrets_file.read_text(encoding="utf-8"))
                for key_name, key_value in data.items():
                    if key_name not in seen:
                        preview = key_value[:4] + "..." if len(key_value) > 4 else "***"
                        keys.append({"name": key_name, "preview": preview, "length": len(key_value)})
            except Exception:
                pass
        return {"keys": keys, "source": str(env_file)}

    @auth_router.get("/tool-groups")
    def list_tool_groups():
        """v2: Gibt die 9 Core-Tools als eine Gruppe zurück."""
        from .tool_registry import registry

        all_ids = registry.all_ids()
        return {
            "groups": [
                {
                    "id": "core",
                    "label": "Core-Tools",
                    "icon": "wrench",
                    "tools": all_ids,
                }
            ]
        }

    # ── Erweiterte Logs & System-Info (#129) ─────────────────────────────

    @admin_router.get("/logs/core/live")
    def get_core_logs_live(
        lines: int = 100,
        since: str = "",
        grep: str = "",
    ):
        """Core-Logs mit optionalem Zeitfilter und Grep."""
        import subprocess
        cmd = ["journalctl", "-u", "hydrahive-core", "--no-pager", "-n", str(min(lines, 2000))]
        if since:
            cmd += ["--since", since]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            log_lines = r.stdout.strip().splitlines()
            if grep:
                grep_lower = grep.lower()
                log_lines = [l for l in log_lines if grep_lower in l.lower()]
            return {"lines": log_lines, "count": len(log_lines)}
        except Exception as e:
            logger.error("Core-Log-Abfrage fehlgeschlagen: %s", e)
            raise HTTPException(500, "Log-Abfrage fehlgeschlagen")

    @admin_router.get("/logs/nginx")
    def get_nginx_logs(lines: int = 100, error: bool = False):
        """Nginx Access- oder Error-Logs."""
        import subprocess
        log_file = "/var/log/nginx/error.log" if error else "/var/log/nginx/access.log"
        try:
            r = subprocess.run(
                ["tail", "-n", str(min(lines, 2000)), log_file],
                capture_output=True, text=True, timeout=10,
            )
            log_lines = r.stdout.strip().splitlines()
            return {"lines": log_lines, "count": len(log_lines), "file": log_file}
        except Exception as e:
            logger.error("Nginx-Log-Abfrage fehlgeschlagen: %s", e)
            raise HTTPException(500, "Log-Abfrage fehlgeschlagen")

    @admin_router.get("/admin/memory/legacy-feedback")
    def get_legacy_feedback_report():
        """#715: Findet Projekt-Memory-Dateien die Core-Policy duplizieren.

        Liefert Report ueber feedback_*.md Dateien in /projects/{id}/memory/
        die in der Legacy-Allowlist stehen und Core-Regeln (#708) duplizieren.
        Loescht nichts — reine Diagnose. Cleanup via scripts/dedupe_legacy_feedback.py.
        """
        from .memory_diagnose import scan_legacy_feedback, summarize_report
        from .settings import settings as _s
        try:
            hits = scan_legacy_feedback(_s.projects_dir)
            return summarize_report(hits)
        except Exception as e:
            logger.error("Legacy-Feedback-Scan fehlgeschlagen: %s", e)
            raise HTTPException(500, f"Scan fehlgeschlagen: {e}")

    @admin_router.get("/agents/{agent_id}/logs")
    def get_agent_logs(agent_id: str, lines: int = 100):
        """Agent-spezifische Logs (gefiltert aus Core-Journal)."""
        import subprocess
        try:
            r = subprocess.run(
                ["journalctl", "-u", "hydrahive-core", "--no-pager", "-n", str(min(lines * 5, 5000))],
                capture_output=True, text=True, timeout=10,
            )
            all_lines = r.stdout.strip().splitlines()
            filtered = [l for l in all_lines if agent_id in l][-lines:]
            return {"agent_id": agent_id, "lines": filtered, "count": len(filtered)}
        except Exception as e:
            logger.error("Agent-Log-Abfrage fehlgeschlagen für %s: %s", agent_id, e)
            raise HTTPException(500, "Log-Abfrage fehlgeschlagen")

    @admin_router.get("/admin/system/info")
    def system_info():
        """CPU, RAM, Disk, Uptime, Load in einem Call."""
        import os
        info: dict = {}

        # Uptime
        try:
            with open("/proc/uptime") as f:
                secs = float(f.read().split()[0])
            days, rem = divmod(int(secs), 86400)
            hours, rem = divmod(rem, 3600)
            mins = rem // 60
            info["uptime"] = f"{days}d {hours}h {mins}m"
            info["uptime_seconds"] = int(secs)
        except Exception as e:
            logger.debug("Failed to read uptime: %s", e)

        # Load
        try:
            load1, load5, load15 = os.getloadavg()
            info["load"] = {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)}
        except Exception as e:
            logger.debug("Failed to read load average: %s", e)

        # CPU
        try:
            with open("/proc/stat") as f:
                cpu = f.readline().split()
            total = sum(int(x) for x in cpu[1:])
            idle = int(cpu[4])
            info["cpu_percent"] = round(100 * (1 - idle / max(total, 1)), 1)
        except Exception as e:
            logger.debug("Failed to read CPU stats: %s", e)

        # RAM
        try:
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem[parts[0].rstrip(":")] = int(parts[1])
            total_mb = mem.get("MemTotal", 0) // 1024
            avail_mb = mem.get("MemAvailable", 0) // 1024
            used_mb = total_mb - avail_mb
            info["ram"] = {
                "total_mb": total_mb, "used_mb": used_mb, "available_mb": avail_mb,
                "percent": round(100 * used_mb / max(total_mb, 1), 1),
            }
        except Exception as e:
            logger.debug("Failed to read RAM info: %s", e)

        # Disk
        try:
            st = os.statvfs("/")
            total_gb = round(st.f_blocks * st.f_frsize / (1024**3), 1)
            free_gb = round(st.f_bavail * st.f_frsize / (1024**3), 1)
            used_gb = round(total_gb - free_gb, 1)
            info["disk"] = {
                "total_gb": total_gb, "used_gb": used_gb, "free_gb": free_gb,
                "percent": round(100 * used_gb / max(total_gb, 1), 1),
            }
        except Exception as e:
            logger.debug("Failed to read disk stats: %s", e)

        # Hostname
        try:
            import socket
            info["hostname"] = socket.gethostname()
        except Exception as e:
            logger.debug("Failed to get hostname: %s", e)

        return info

    @admin_router.get("/admin/system/services")
    def system_services():
        """Status der wichtigsten systemd Services."""
        import subprocess
        services = [
            "hydrahive-core", "nginx", "ollama", "tailscaled",
            "hydrahive-whatsapp-bridge", "code-server",
        ]
        result = []
        for svc in services:
            try:
                r = subprocess.run(
                    ["systemctl", "is-active", svc],
                    capture_output=True, text=True, timeout=5,
                )
                status = r.stdout.strip()
            except Exception as e:
                logger.debug("Failed to check service '%s' status: %s", svc, e)
                status = "unknown"
            result.append({"name": svc, "status": status})
        return {"services": result}

    @admin_router.post("/admin/system/service/{name}/restart")
    def restart_service(name: str, _a: tuple = Depends(require_auth)):
        """Einen Service neustarten (nur erlaubte Services)."""
        import subprocess
        allowed = {"hydrahive-core", "nginx", "ollama", "tailscaled", "hydrahive-whatsapp-bridge"}
        if name not in allowed:
            raise HTTPException(403, f"Service '{name}' darf nicht per API neugestartet werden")
        try:
            r = subprocess.run(
                ["sudo", "systemctl", "restart", name],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                logger.error("Service-Restart fehlgeschlagen für %s: %s", name, r.stderr.strip())
                raise HTTPException(500, "Service-Restart fehlgeschlagen")
            return {"ok": True, "service": name}
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Restart Timeout")

    # ── Phase 2: Agent-Management (#129) ────────────────────────────────

    @auth_router.get("/agents/{agent_id}/soul")
    def get_agent_soul(agent_id: str):
        """Soul.md eines Agents lesen."""
        cfg = discovery.get(agent_id)
        if not cfg or not cfg.agent_dir:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        soul_path = cfg.agent_dir / "soul.md"
        if not soul_path.exists():
            return {"agent_id": agent_id, "soul": ""}
        return {"agent_id": agent_id, "soul": soul_path.read_text(encoding="utf-8")}

    @admin_router.put("/agents/{agent_id}/soul")
    def update_agent_soul(agent_id: str, req: AgentSoulRequest):
        """Soul.md eines Agents schreiben."""
        cfg = discovery.get(agent_id)
        if not cfg or not cfg.agent_dir:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        soul_path = cfg.agent_dir / "soul.md"
        soul_path.write_text(req.soul, encoding="utf-8")
        return {"ok": True, "agent_id": agent_id}

    @admin_router.get("/agents/{agent_id}/config/full")
    def get_agent_full_config(agent_id: str):
        """Komplette agent.yaml als JSON."""
        cfg = discovery.get(agent_id)
        if not cfg or not cfg.agent_dir:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        import yaml as _yaml
        yaml_path = cfg.agent_dir / "agent.yaml"
        try:
            raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            return {"agent_id": agent_id, "config": raw}
        except Exception as e:
            logger.error("Agent-Config Lesefehler für %s: %s", agent_id, e)
            raise HTTPException(500, "Konfiguration konnte nicht geladen werden")

    @admin_router.put("/agents/{agent_id}/config/full")
    async def update_agent_full_config(agent_id: str, req: AgentFullConfigRequest):
        """Komplette agent.yaml überschreiben."""
        cfg = discovery.get(agent_id)
        if not cfg or not cfg.agent_dir:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        import yaml as _yaml
        config = req.config
        if not config:
            raise HTTPException(400, "config fehlt")
        yaml_path = cfg.agent_dir / "agent.yaml"
        yaml_path.write_text(_yaml.dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        # Agent neu registrieren
        try:
            discovery._register(cfg.agent_dir)
        except Exception as e:
            logger.warning("Failed to re-register agent '%s': %s", agent_id, e)
        reloaded = await _reload_runtime_agent_config(agent_id)
        return {"ok": True, "agent_id": agent_id, "runtime_reloaded": reloaded}

    @admin_router.post("/agents/{agent_id}/clone")
    def clone_agent(agent_id: str, req: CloneAgentRequest):
        """Agent duplizieren mit neuer ID."""
        import shutil as _sh
        import re as _re
        cfg = discovery.get(agent_id)
        if not cfg or not cfg.agent_dir:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        new_id = req.new_id.strip()
        if not new_id or not _re.match(r'^[a-z0-9_-]{1,64}$', new_id):
            raise HTTPException(400, "Ungültige new_id")
        new_dir = cfg.agent_dir.parent / new_id
        if new_dir.exists():
            raise HTTPException(409, f"Agent '{new_id}' existiert bereits")
        _sh.copytree(cfg.agent_dir, new_dir)
        # ID in agent.yaml anpassen
        import yaml as _yaml
        yaml_path = new_dir / "agent.yaml"
        raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        raw["id"] = new_id
        if "identity" in raw:
            raw["identity"] = raw["identity"] + " (Kopie)"
        yaml_path.write_text(_yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        try:
            import subprocess
            subprocess.run(["chown", "-R", "hydrahive:hydrahive", str(new_dir)], check=False, capture_output=True)
            discovery._register(new_dir)
        except Exception as e:
            logger.warning("Failed to register cloned agent '%s': %s", new_id, e)
        return {"ok": True, "agent_id": new_id, "cloned_from": agent_id}

    # ── Phase 3: Config-Export (#129) ─────────────────────────────────

    @admin_router.get("/admin/config/export")
    def export_config():
        """Gesamte Server-Konfiguration als JSON exportieren."""
        import json as _json
        config = {}
        config_dir = settings.etc_dir
        if config_dir.exists():
            for f in sorted(config_dir.glob("*.json")):
                try:
                    config[f.stem] = _json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    config[f.stem] = {"_error": "Datei nicht lesbar"}
        return {"config": config}

    # ── Phase 4: Filesystem (eingeschränkt) (#129) ────────────────────

    from pathlib import Path
    ALLOWED_FS_ROOTS = ["/agents", "/plugins", "/etc/hydrahive", "/projects"]

    @admin_router.get("/admin/files")
    def list_files(path: str = "/agents"):
        """Verzeichnis listen (nur erlaubte Pfade)."""
        resolved = Path(path).resolve()
        if not any(str(resolved).startswith(r) for r in ALLOWED_FS_ROOTS):
            raise HTTPException(403, f"Zugriff auf '{path}' nicht erlaubt")
        if not resolved.is_dir():
            raise HTTPException(404, "Verzeichnis nicht gefunden")
        items = []
        try:
            for entry in sorted(resolved.iterdir()):
                items.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else None,
                })
        except PermissionError:
            raise HTTPException(403, "Keine Berechtigung")
        return {"path": str(resolved), "items": items}

    @admin_router.get("/admin/files/read")
    def read_file(path: str):
        """Datei lesen (nur erlaubte Pfade, max 1MB)."""
        resolved = Path(path).resolve()
        if not any(str(resolved).startswith(r) for r in ALLOWED_FS_ROOTS):
            raise HTTPException(403, f"Zugriff auf '{path}' nicht erlaubt")
        if not resolved.is_file():
            raise HTTPException(404, "Datei nicht gefunden")
        if resolved.stat().st_size > 1_048_576:
            raise HTTPException(413, "Datei zu groß (max 1MB)")
        try:
            return {"path": str(resolved), "content": resolved.read_text(encoding="utf-8", errors="replace")}
        except Exception as e:
            logger.error("Datei-Lesefehler %s: %s", resolved, e)
            raise HTTPException(500, "Datei konnte nicht gelesen werden")

    @admin_router.put("/admin/files/write")
    def write_file(req: WriteFileRequest):
        """Datei schreiben (nur erlaubte Pfade)."""
        path = req.path
        content = req.content
        if not path:
            raise HTTPException(400, "path fehlt")
        resolved = Path(path).resolve()
        if not any(str(resolved).startswith(r) for r in ALLOWED_FS_ROOTS):
            raise HTTPException(403, f"Zugriff auf '{path}' nicht erlaubt")
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            import subprocess
            subprocess.run(["chown", "hydrahive:hydrahive", str(resolved)], check=False, capture_output=True)
            return {"ok": True, "path": str(resolved)}
        except Exception as e:
            logger.error("Datei-Schreibfehler %s: %s", resolved, e)
            raise HTTPException(500, "Datei konnte nicht geschrieben werden")

    @admin_router.delete("/admin/files/delete")
    def delete_file(path: str):
        """Datei löschen (nur erlaubte Pfade)."""
        if not path:
            raise HTTPException(400, "path fehlt")
        resolved = Path(path).resolve()
        if not any(str(resolved).startswith(r) for r in ALLOWED_FS_ROOTS):
            raise HTTPException(403, f"Zugriff auf '{path}' nicht erlaubt")
        if not resolved.is_file():
            raise HTTPException(404, "Datei nicht gefunden")
        resolved.unlink()
        return {"ok": True, "path": str(resolved)}

    # ── Dashboard-Config (#274) ──────────────────────────────────────

    _DASHBOARD_DIR = settings.dashboard_dir

    @auth_router.get("/dashboard/config")
    def get_dashboard_config(_a: tuple[str, str] = Depends(require_auth)):
        import json as _json
        username, _ = _a
        cfg_path = _DASHBOARD_DIR / f"{username}.json"
        if not cfg_path.exists():
            return {"custom_tabs": [], "overview_widgets": {"hidden": [], "order": []}}
        try:
            return _json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return {"custom_tabs": [], "overview_widgets": {"hidden": [], "order": []}}

    @auth_router.put("/dashboard/config")
    def save_dashboard_config(req: DashboardConfigRequest, _a: tuple[str, str] = Depends(require_auth)):
        import json as _json
        username, _ = _a
        _DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
        cfg_path = _DASHBOARD_DIR / f"{username}.json"
        # Validierung: nur erlaubte Keys
        safe = {
            "custom_tabs": req.custom_tabs[:20],  # max 20 Tabs
            "overview_widgets": req.overview_widgets,
        }
        # Tabs validieren
        for tab in safe["custom_tabs"]:
            if not isinstance(tab, dict):
                continue
            tab["id"] = str(tab.get("id", ""))[:30]
            tab["label"] = str(tab.get("label", ""))[:50]
            tab["icon"] = str(tab.get("icon", "FileText"))[:30]
            tab["type"] = str(tab.get("type", "markdown"))[:20]
            if tab["type"] == "iframe":
                tab["url"] = str(tab.get("url", ""))[:500]
                tab.pop("content", None)
            else:
                tab["content"] = str(tab.get("content", ""))[:50000]
                tab.pop("url", None)
        cfg_path.write_text(_json.dumps(safe, indent=2, ensure_ascii=False), encoding="utf-8")
        cfg_path.chmod(0o600)
        return {"saved": True}

    # ── Eingeschränkte Shell (#129) ───────────────────────────────────

    ALLOWED_SHELL_COMMANDS = {
        "df", "free", "uptime", "hostname", "whoami", "uname",
        "systemctl", "journalctl", "tailscale", "docker", "podman",
        "ls", "cat", "wc",
        "du", "head", "tail", "grep", "find", "which",
        "python3", "pip",
    }
    BLOCKED_SHELL_PATTERNS = {"rm -rf", "mkfs", "dd if=", ":(){ :|:&", "shutdown", "reboot", "halt", "init 0"}

    # Shell-Metazeichen die Command-Injection ermöglichen (außer |)
    _SHELL_INJECTION_CHARS = {";", "&", "`", "$", "(", ")", "\n", "\\"}

    @admin_router.post("/admin/shell")
    def run_shell(req: ShellCommandRequest):
        """Eingeschränkter Shell-Zugriff (Whitelist)."""
        import subprocess, shlex
        cmd = req.command.strip()
        if not cmd:
            raise HTTPException(400, "command fehlt")
        # Shell-Injection-Zeichen blockieren (;, &, $, `, Backticks etc.)
        for ch in _SHELL_INJECTION_CHARS:
            if ch in cmd:
                raise HTTPException(403, f"Befehl blockiert: Zeichen '{ch}' nicht erlaubt")
        # Blocked patterns
        cmd_lower = cmd.lower()
        for blocked in BLOCKED_SHELL_PATTERNS:
            if blocked in cmd_lower:
                raise HTTPException(403, f"Befehl blockiert: enthält '{blocked}'")
        # Jeden Pipe-Teil einzeln gegen Whitelist prüfen
        pipe_parts = [p.strip() for p in cmd.split("|")]
        for part in pipe_parts:
            if not part:
                raise HTTPException(400, "Leerer Pipe-Teil")
            try:
                first_word = shlex.split(part)[0]
            except ValueError:
                raise HTTPException(400, "Ungültige Befehlssyntax")
            base_cmd = Path(first_word).name
            if base_cmd not in ALLOWED_SHELL_COMMANDS:
                raise HTTPException(403, f"Befehl '{base_cmd}' nicht erlaubt. Erlaubt: {', '.join(sorted(ALLOWED_SHELL_COMMANDS))}")
        try:
            r = subprocess.run(
                shlex.split(pipe_parts[0]) if len(pipe_parts) == 1 else cmd,
                shell=len(pipe_parts) > 1,  # shell=True nur bei Pipes, sonst False
                capture_output=True, text=True, timeout=30,
                cwd="/tmp",
            )
            return {
                "exitcode": r.returncode,
                "stdout": r.stdout[-10000:] if r.stdout else "",
                "stderr": r.stderr[-5000:] if r.stderr else "",
            }
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Timeout (30s)")

    return IncomingMessage
