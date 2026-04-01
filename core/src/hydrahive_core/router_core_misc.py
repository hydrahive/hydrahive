from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel


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


class IncomingMessage(BaseModel):
    content: str
    sender: str = "user"
    execution_mode: Literal["safe", "elevated", "root"] | None = None


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
) -> type[IncomingMessage]:
    @public_router.get("/setup/status")
    def setup_status():
        import json
        from pathlib import Path as _Path
        users = load_users()
        kas_ok  = _Path("/etc/hydrahive/kas.json").exists()
        llm_ok  = _Path("/etc/hydrahive/llm_config.json").exists() or _Path("/etc/hydrahive/llm_config.json").exists()
        wizard_done = _Path("/etc/hydrahive/setup_wizard_done").exists()
        return {
            "needs_setup":   len(users) == 0,
            "wizard_done":   wizard_done or (kas_ok and llm_ok),
            "kas_configured": kas_ok,
            "llm_configured": llm_ok,
        }

    _KAS_PATH = "/etc/hydrahive/kas.json"

    @admin_router.get("/admin/kas")
    def get_kas():
        import json
        from pathlib import Path as _Path
        p = _Path(_KAS_PATH)
        if not p.exists():
            return {"configured": False}
        try:
            data = json.loads(p.read_text())
            return {"configured": True, **data}
        except Exception:
            return {"configured": False}

    @admin_router.put("/admin/kas")
    def put_kas(req: KasConfigRequest):
        import json
        from pathlib import Path as _Path
        p = _Path(_KAS_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = req.model_dump()
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        p.chmod(0o600)
        return {"saved": True}

    @admin_router.post("/admin/wizard/complete")
    def wizard_complete():
        from pathlib import Path as _Path
        _Path("/etc/hydrahive/setup_wizard_done").touch()
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
            logger.info("Setup abgeschlossen: erster Admin-User '%s' angelegt", req.username)
            return {"created": True, "username": req.username, "role": "admin"}

    @public_router.post("/auth/login")
    def login(req: LoginRequest, request: Request):
        check_login_rate(request.client.host if request.client else "unknown")
        users = load_users()
        if users:
            user = users.get(req.username)
            if user and verify_password(req.password, user.get("password_hash", "")):
                role  = user.get("role", "user")
                group = user.get("group", "standard")
                token = make_jwt(req.username, role)
                logger.info("Login erfolgreich (users.json): %s", req.username)
                return {"access_token": token, "token_type": "bearer", "role": role, "group": group, "username": req.username}
            raise HTTPException(401, "Ungültige Zugangsdaten")

        admin_pass = read_admin_password()
        if not admin_pass:
            raise HTTPException(503, "Kein Admin-Passwort konfiguriert — Setup erforderlich")
        if req.username != "admin" or req.password != admin_pass:
            raise HTTPException(401, "Ungültige Zugangsdaten")
        token = make_jwt(req.username, "admin")
        logger.info("Login erfolgreich (admin_credentials): %s", req.username)
        return {"access_token": token, "token_type": "bearer", "role": "admin", "username": req.username}

    @auth_router.get("/auth/me")
    def whoami(auth: tuple[str, str] = Depends(require_auth)):
        username, role = auth
        return {"username": username, "role": role}

    @public_router.get("/health")
    def health():
        return {"status": "ok", "service": "hydrahive-core"}

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
                    "tool_selection": getattr(cfg, "tool_selection", "auto"),
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
    def patch_agent_llm(agent_id: str, req: AgentLlmPatchRequest):
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
        except Exception as e:
            raise HTTPException(500, f"Fehler beim Speichern: {e}")
        return {"ok": True, "agent_id": agent_id, "fallback_models": req.fallback_models}

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
                    "permissions_required": tool.permissions_required,
                    "parameters": tool.parameters,
                }
        return result

    return IncomingMessage
