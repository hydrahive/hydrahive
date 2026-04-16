"""
project_targets.py — Zentrale Projekt-Target-Verwaltung (#584)

Weist einem Projekt konkrete Zielsysteme zu (Root-/Remote-Server + WKS).
Ergänzt, ersetzt NICHT die Agent-Ebene aus router_servers.py (agent_servers.json).

Datenformat (settings.project_targets_config, 0o600):
    {
      "project-a": {
        "servers": [
          {"server_id": "prod-web", "role": "web", "note": "Frontend + API"}
        ],
        "wks": [
          {"username": "till", "role": "local-dev", "note": "Lokale Testmaschine"}
        ]
      }
    }

V1 (Phase #584-A):
- reine Zuweisungs-/Prompt-Sicht.
- KEINE Tool-Handler-Auth hier (kommt in #584-B).
- WKS ssh_port hardcoded 22 (bestehendes WKS-Schema hat kein Feld).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from .settings import settings

logger = logging.getLogger(__name__)

# Validierungs-Limits
MAX_ROLE_LEN    = 40
MAX_NOTE_LEN    = 300
MAX_SERVERS     = 20
MAX_WKS         = 20
_ROLE_RE        = re.compile(r"^[a-z0-9_-]+$")
_DEFAULT_WKS_SSH_PORT = 22


class TargetValidationError(ValueError):
    """Eingabe-Validierung fehlgeschlagen. Wird im Router zu HTTP 400 übersetzt."""


# ──────────────────────────────────────────────────────────── Storage

def _load_project_targets() -> dict:
    """Lädt das vollständige Target-Dict. Fehlende Datei → leeres Dict."""
    path = settings.project_targets_config
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("project_targets.json nicht lesbar: %s", e)
        return {}


def _save_project_targets(data: dict) -> None:
    path = settings.project_targets_config
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)


def get_project_targets(project_id: str) -> dict:
    """Liefert rohes Target-Dict für ein Projekt ({'servers': [...], 'wks': [...]})."""
    data = _load_project_targets()
    entry = data.get(project_id) or {}
    return {
        "servers": list(entry.get("servers") or []),
        "wks":     list(entry.get("wks") or []),
    }


def compute_project_targets_etag(project_id: str) -> str:
    """#676: Deterministischer ETag pro Projekt aus normalisierten Targets.

    Nicht Datei-Stat, weil project_targets.json global ist — Änderungen in
    Projekt B dürfen das ETag von Projekt A nicht invalidieren.

    Leeres/fehlendes Projekt → stabiler etag über leere Listen, damit der
    erste PUT (strict If-Match) mit diesem GET-etag sauber klappt.
    """
    entry = get_project_targets(project_id)
    # sort_keys stabilisiert die Reihenfolge; die Listen behalten ihre
    # Eingabe-/Speicher-Reihenfolge, was für den ETag ausreicht — Reordering
    # ist eine semantische Änderung und soll eine neue etag bedeuten.
    raw = json.dumps(
        {"servers": entry.get("servers") or [], "wks": entry.get("wks") or []},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def set_project_targets(project_id: str, targets: dict) -> dict:
    """Persistiert validierte Targets. Wirft TargetValidationError bei Verletzung.

    Gibt die normalisierte Form zurück (gleich wie gespeichert).
    """
    normalized = _validate_and_normalize(targets)
    data = _load_project_targets()
    data[project_id] = normalized
    _save_project_targets(data)
    return normalized


# ──────────────────────────────────────────────────────────── Validierung

def _validate_and_normalize(targets: dict) -> dict:
    """Validiert Eingabe + gibt kanonische Form mit nur den erlaubten Feldern zurück.

    Prüft Struktur, Längen, Duplikate, Role-Format. Prüft NICHT, ob server_id oder
    username tatsächlich existieren — das macht der Router-Handler, weil er die
    Stammdaten (servers/users) aus dem Request-Scope hat.
    """
    if not isinstance(targets, dict):
        raise TargetValidationError("Body muss ein Objekt mit 'servers' und 'wks' sein.")

    servers_in = targets.get("servers") or []
    wks_in     = targets.get("wks") or []
    if not isinstance(servers_in, list):
        raise TargetValidationError("'servers' muss eine Liste sein.")
    if not isinstance(wks_in, list):
        raise TargetValidationError("'wks' muss eine Liste sein.")
    if len(servers_in) > MAX_SERVERS:
        raise TargetValidationError(f"Maximal {MAX_SERVERS} Server pro Projekt.")
    if len(wks_in) > MAX_WKS:
        raise TargetValidationError(f"Maximal {MAX_WKS} WKS pro Projekt.")

    out_servers: list[dict] = []
    seen_ids: set[str] = set()
    for idx, raw in enumerate(servers_in):
        if not isinstance(raw, dict):
            raise TargetValidationError(f"servers[{idx}] muss ein Objekt sein.")
        sid = str(raw.get("server_id") or "").strip()
        if not sid:
            raise TargetValidationError(f"servers[{idx}].server_id fehlt.")
        if sid in seen_ids:
            raise TargetValidationError(f"servers[{idx}]: Duplikat server_id '{sid}'.")
        seen_ids.add(sid)
        out_servers.append({
            "server_id": sid,
            "role":      _validate_role(raw.get("role", ""), f"servers[{idx}]"),
            "note":      _validate_note(raw.get("note", ""), f"servers[{idx}]"),
        })

    out_wks: list[dict] = []
    seen_users: set[str] = set()
    for idx, raw in enumerate(wks_in):
        if not isinstance(raw, dict):
            raise TargetValidationError(f"wks[{idx}] muss ein Objekt sein.")
        username = str(raw.get("username") or "").strip()
        if not username:
            raise TargetValidationError(f"wks[{idx}].username fehlt.")
        if username in seen_users:
            raise TargetValidationError(f"wks[{idx}]: Duplikat username '{username}'.")
        seen_users.add(username)
        out_wks.append({
            "username": username,
            "role":     _validate_role(raw.get("role", ""), f"wks[{idx}]"),
            "note":     _validate_note(raw.get("note", ""), f"wks[{idx}]"),
        })

    return {"servers": out_servers, "wks": out_wks}


def _validate_role(value, where: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if len(s) > MAX_ROLE_LEN:
        raise TargetValidationError(f"{where}.role zu lang (max {MAX_ROLE_LEN} Zeichen).")
    if not _ROLE_RE.match(s):
        raise TargetValidationError(
            f"{where}.role enthält ungültige Zeichen — erlaubt: a-z, 0-9, _, -"
        )
    return s


def _validate_note(value, where: str) -> str:
    s = str(value or "")
    if len(s) > MAX_NOTE_LEN:
        raise TargetValidationError(f"{where}.note zu lang (max {MAX_NOTE_LEN} Zeichen).")
    return s.strip()


# ──────────────────────────────────────────────────────────── Prompt-Rendering

def render_project_targets_for_prompt(
    project_id: str,
    *,
    server_lookup: dict[str, dict] | None = None,
    users: dict | None = None,
) -> str | None:
    """Baut den 'Zugewiesene Zielsysteme'-Block für den System-Prompt.

    Liefert None, wenn weder Server noch WKS zugewiesen sind. Leere Sections
    werden weggelassen. Keine SSH-Keys, keine ssh_key_path im Output.

    Parameter:
      server_lookup — optional, {server_id: server_dict}. Wenn None, werden die
        Server-Stammdaten aus router_servers._load_servers() gelesen.
      users — optional, users.json-Dict. Wenn None, wird settings.users_config
        gelesen.
    """
    targets = get_project_targets(project_id)
    servers = targets.get("servers") or []
    wks     = targets.get("wks") or []
    if not servers and not wks:
        return None

    if server_lookup is None:
        try:
            from .router_servers import _load_servers
            server_lookup = {s["id"]: s for s in _load_servers()}
        except Exception:
            server_lookup = {}

    if users is None:
        try:
            users = json.loads(settings.users_config.read_text(encoding="utf-8"))
        except Exception:
            users = {}

    lines = [
        "## Zugewiesene Zielsysteme",
        "",
        "Diese Zielsysteme gehören zum aktuellen Projekt. Nutze nur diese Targets.",
        "Bei unklarer Zielauswahl erst fragen.",
    ]

    server_lines: list[str] = []
    for t in servers:
        srv = server_lookup.get(t["server_id"])
        if not srv:
            continue
        name = srv.get("name") or t["server_id"]
        ip   = srv.get("ip", "?")
        port = srv.get("ssh_port", 22)
        user = srv.get("ssh_user", "?")
        role = t.get("role", "")
        note = t.get("note", "")
        meta = [f"ID: `{t['server_id']}`"]
        if role:
            meta.append(f"role: `{role}`")
        line = f"- **{name}** ({', '.join(meta)}): `{user}@{ip}:{port}`"
        if note:
            line += f" — {note}"
        server_lines.append(line)

    wks_lines: list[str] = []
    for t in wks:
        user_entry = (users.get(t["username"]) or {}).get("wks") or {}
        ip = user_entry.get("ip", "").strip()
        if not ip:
            continue
        ssh_user = user_entry.get("ssh_user", t["username"])
        role = t.get("role", "")
        note = t.get("note", "")
        meta = [f"role: `{role}`"] if role else []
        head = f"**{t['username']}**"
        if meta:
            head += f" ({', '.join(meta)})"
        line = f"- {head}: `{ssh_user}@{ip}:{_DEFAULT_WKS_SSH_PORT}`"
        if note:
            line += f" — {note}"
        wks_lines.append(line)

    if server_lines:
        lines.extend(["", "### Root-/Remote-Server", *server_lines, "",
                      "Nutze `server_shell`, `server_file_read`, `server_file_write` mit `server_id`."])
    if wks_lines:
        lines.extend(["", "### WKS", *wks_lines, "",
                      "Nutze `wks_shell_exec` mit `username`, falls mehrere WKS zugewiesen sind."])

    if not server_lines and not wks_lines:
        return None
    return "\n".join(lines)
