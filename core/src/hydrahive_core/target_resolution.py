"""
target_resolution.py — Projekt-Target-Auth für Tool-Handler (#584-C)

Einheitlicher Resolver für die vier Target-Tools:
- server_shell, server_file_read, server_file_write  → resolve_server_target()
- wks_shell_exec                                      → resolve_wks_target()

Zentrale Sicherheitsregeln:
- project_id kommt vom Orchestrator (Runtime), NIE aus Tool-Input.
- Legacy-Fallback (agent_servers.json) greift nur, wenn project_targets leer.
- SSH-Keys tauchen NIE in Fehlermeldungen oder Tool-Output auf.
- Inklusive run_ssh_command() für einheitliche SSH-Ausführung mit Timeout,
  Output-Truncation und Key-Redaction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import ssh_known_hosts
from .settings import settings
from .project_targets import get_project_targets

logger = logging.getLogger(__name__)


_SAFE_ID = re.compile(r"^[a-z0-9_-]+$")
_DEFAULT_WKS_SSH_PORT = 22
MAX_SSH_OUTPUT = 32000

# #674-B: Stabile OpenSSH-Fragmente bei Host-Key-Mismatch. ssh läuft mit
# LC_ALL=C, damit die Meldungen englisch und verlässlich sind.
_HOST_KEY_CHANGED_RE = re.compile(
    r"Host key verification failed"
    r"|REMOTE HOST IDENTIFICATION HAS CHANGED"
    r"|Offending .* key",
)


class TargetAccessError(Exception):
    """Tool darf Target nicht nutzen — Message ist user-facing, enthält KEINE
    Keys, Pfade oder Interna. Wird vom Tool in {'error': ...} gerendert."""


@dataclass(frozen=True)
class ResolvedServer:
    server_id: str
    name: str
    ip: str
    ssh_user: str
    ssh_port: int
    ssh_key_path: Path


@dataclass(frozen=True)
class ResolvedWks:
    username: str
    ip: str
    ssh_user: str
    ssh_port: int
    ssh_key_path: Path


# ────────────────────────────────────────────── Server-Resolver

def resolve_server_target(
    agent_id: str,
    server_id: str,
    *,
    project_id: str | None,
) -> ResolvedServer:
    """Prüft Zuweisung + Stammdaten + Key-Existenz. Wirft TargetAccessError.

    Precedence:
      1. Wenn project_targets[project_id] irgendein Target enthält (servers
         ODER wks), ist dieser Projekt-Target-Block autoritativ. server_id
         muss dort in .servers stehen.
      2. Legacy agent_servers[agent_id] greift NUR, wenn kein project_id
         gesetzt ist oder project_targets[project_id] komplett leer ist.
      3. Sonst: TargetAccessError.
    """
    if not server_id or not isinstance(server_id, str):
        raise TargetAccessError("server_id fehlt.")
    if not _SAFE_ID.match(server_id) or len(server_id) > 30:
        raise TargetAccessError(f"server_id '{server_id}' hat unerlaubte Zeichen.")

    # #584-C Precedence: Wenn das Projekt IRGENDWELCHE Targets definiert hat
    # (servers ODER wks), ist der Projekt-Targets-Block autoritativ — Legacy
    # agent_servers.json darf dann NICHT zusätzlich erlauben, sonst würde ein
    # alter Legacy-Eintrag (z.B. "prod-db") die Projekt-Zuweisung erweitern.
    # Konsistent zur Prompt-Precedence in #584-A (orchestrator_context.py).
    if project_id:
        targets = get_project_targets(project_id)
    else:
        targets = {"servers": [], "wks": []}
    project_has_any_targets = bool(targets.get("servers") or targets.get("wks"))
    assigned_via_project = any(
        s.get("server_id") == server_id for s in targets.get("servers") or []
    )

    if project_id and project_has_any_targets:
        if not assigned_via_project:
            raise TargetAccessError(
                f"Server '{server_id}' ist diesem Projekt nicht zugewiesen."
            )
    else:
        # Legacy-Fallback: nur wenn Projekt gar keine Targets hat ODER kein project_id
        try:
            from .router_servers import get_server_for_agent
            legacy = get_server_for_agent(agent_id, server_id)
        except Exception:
            legacy = None
        if not legacy:
            raise TargetAccessError(
                f"Server '{server_id}' ist diesem Projekt/Agent nicht zugewiesen."
            )

    # Stammdaten laden (gleiche Quelle wie router_servers._load_servers)
    srv_path = settings.servers_dir / f"{server_id}.json"
    if not srv_path.exists():
        raise TargetAccessError(f"Server '{server_id}' existiert nicht mehr.")
    try:
        srv = json.loads(srv_path.read_text(encoding="utf-8"))
    except Exception:
        raise TargetAccessError(f"Server '{server_id}' nicht lesbar.")

    key_path = settings.server_keys_dir / server_id
    if not key_path.exists():
        raise TargetAccessError(f"Server '{server_id}' hat keinen SSH-Key hinterlegt.")

    return ResolvedServer(
        server_id=server_id,
        name=srv.get("name", server_id),
        ip=srv.get("ip", ""),
        ssh_user=srv.get("ssh_user", "root"),
        ssh_port=int(srv.get("ssh_port", 22) or 22),
        ssh_key_path=key_path,
    )


# ────────────────────────────────────────────── WKS-Resolver

def resolve_wks_target(
    username: str | None,
    *,
    project_id: str | None,
) -> ResolvedWks:
    """Projekt-Pflicht. Defaulting bei genau 1 zugewiesener WKS.
    Legacy-Fallback gibt es für WKS nicht (es gab bisher keinen Tool-Handler)."""
    if not project_id:
        raise TargetAccessError(
            "wks_shell_exec nur in Projektkontext mit zugewiesener WKS verfügbar."
        )

    targets = get_project_targets(project_id)
    assigned = targets.get("wks") or []
    if not assigned:
        raise TargetAccessError("Keine WKS diesem Projekt zugewiesen.")

    if username is None or str(username).strip() == "":
        if len(assigned) == 1:
            username = assigned[0].get("username", "")
        else:
            names = ", ".join(sorted(str(a.get("username", "")) for a in assigned))
            raise TargetAccessError(
                f"username erforderlich — zugewiesene WKS: {names}"
            )

    username = str(username).strip()
    if not _SAFE_ID.match(username):
        raise TargetAccessError(f"username '{username}' hat unerlaubte Zeichen.")

    if not any(a.get("username") == username for a in assigned):
        raise TargetAccessError(
            f"WKS '{username}' ist diesem Projekt nicht zugewiesen."
        )

    try:
        users = json.loads(settings.users_config.read_text(encoding="utf-8"))
    except Exception:
        users = {}
    user_entry = users.get(username) or {}
    wks_entry = user_entry.get("wks") or {}
    ip = (wks_entry.get("ip") or "").strip()
    if not ip:
        raise TargetAccessError(f"WKS '{username}' ist nicht konfiguriert (keine IP).")

    key_path = settings.wks_keys_dir / username
    if not key_path.exists():
        raise TargetAccessError(f"WKS '{username}' hat keinen SSH-Key hinterlegt.")

    return ResolvedWks(
        username=username,
        ip=ip,
        ssh_user=wks_entry.get("ssh_user") or username,
        ssh_port=_DEFAULT_WKS_SSH_PORT,
        ssh_key_path=key_path,
    )


# ────────────────────────────────────────────── SSH-Runner

def _redact_key(text: str, key_path: Path, known_hosts_path: str | None = None) -> str:
    """Ersetzt Key-Pfad und optionalen temp-known_hosts-Pfad durch Platzhalter."""
    if not text:
        return text
    text = text.replace(str(key_path), "<ssh_key>")
    if known_hosts_path:
        text = text.replace(known_hosts_path, "<known_hosts>")
    return text


def _write_temp_known_hosts(host: str, verified_keys: list[dict]) -> str:
    """Schreibt eine temporäre OpenSSH-known_hosts-Datei mit ausschließlich
    verified Keys für `host`. Caller ist verantwortlich für Cleanup.

    Format pro Zeile: `<host> <algorithm> <public_key_base64>`
    """
    fd, path = tempfile.mkstemp(prefix="hydrahive_known_hosts_", suffix=".tmp")
    try:
        lines = []
        for k in verified_keys:
            algo = k.get("algorithm")
            pub = k.get("public_key")
            if algo and pub:
                lines.append(f"{host} {algo} {pub}\n")
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.writelines(lines)
        os.chmod(path, 0o600)
        return path
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.unlink(path)
        except Exception:
            pass
        raise


async def run_ssh_command(
    host: str,
    ssh_user: str,
    ssh_port: int,
    key_path: Path,
    command: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    timeout: int = 60,
    max_output: int | None = MAX_SSH_OUTPUT,
) -> dict:
    """Führt einen Command via SSH aus und liefert {stdout, stderr, exit_code,
    [host_key_unverified, host_key_changed, host_key_mode]}.

    Host-Key-Enforcement (#674-B):
    - Wenn target_type/target_id gesetzt und der Store verified Keys hat:
      temp known_hosts + StrictHostKeyChecking=yes. SSH wird mit
      UserKnownHostsFile=<temp> ausgeführt; bei Mismatch erkennen wir
      "Host key verification failed" / "REMOTE HOST IDENTIFICATION HAS
      CHANGED" im stderr und setzen host_key_changed=true.
    - Wenn target_type/target_id gesetzt, aber keine verified Keys:
      enforcement=strict → sofort fail-closed (kein SSH-Call),
      enforcement=warn → SSH läuft (StrictHostKeyChecking=no), Result
      bekommt host_key_unverified=true + host_key_mode="warn".
    - Wenn target_type ODER target_id None: kein Enforcement, aber weiterhin
      UserKnownHostsFile=/dev/null — System-known_hosts wird nie mehr
      beschrieben.

    stdout-Truncation wie bisher (max_output=None schaltet ab, #670).
    stderr wird stets auf MAX_SSH_OUTPUT gekürzt. Key-Pfad und temp-
    known_hosts-Pfad werden in stderr/Fehler redacted.
    """
    verified_keys: list[dict] = []
    enforcement = "warn"
    host_key_unverified = False

    if target_type and target_id:
        verified_keys = ssh_known_hosts.get_verified_keys(target_type, target_id)
        enforcement = ssh_known_hosts.get_enforcement_mode()
        if not verified_keys:
            host_key_unverified = True
            if enforcement == "strict":
                logger.info(
                    "run_ssh_command blocked: %s:%s ohne verified Host-Keys (strict)",
                    target_type, target_id,
                )
                return {
                    "error": (
                        "Host-Key nicht vertraut — Admin muss im Admin-Panel "
                        "unter Server → Host-Keys einen Fingerprint genehmigen."
                    ),
                    "exit_code": -1,
                    "host_key_unverified": True,
                    "host_key_mode": "strict",
                }
            logger.debug(
                "run_ssh_command warn-mode: %s:%s ohne verified Host-Keys",
                target_type, target_id,
            )

    known_hosts_path: str | None = None
    try:
        args = ["ssh", "-i", str(key_path), "-o", "BatchMode=yes"]
        if verified_keys:
            known_hosts_path = _write_temp_known_hosts(host, verified_keys)
            args.extend([
                "-o", f"UserKnownHostsFile={known_hosts_path}",
                "-o", "GlobalKnownHostsFile=/dev/null",
                "-o", "StrictHostKeyChecking=yes",
            ])
        else:
            # Kein Enforcement — aber niemals mehr System-known_hosts
            # beschreiben (auch bei target_type=None, User-Entscheidung #674-B).
            # LogLevel=ERROR unterdrückt die "Warning: Permanently added ... to
            # the list of known hosts"-Zeile, die OpenSSH trotz /dev/null
            # weiterhin loggt. Bewusst NICHT im strict/verified-Pfad gesetzt,
            # damit "Host key verification failed" dort durchkommt.
            args.extend([
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "GlobalKnownHostsFile=/dev/null",
                "-o", "StrictHostKeyChecking=no",
                "-o", "LogLevel=ERROR",
            ])
        args.extend([
            "-o", "ConnectTimeout=10",
            "-p", str(ssh_port),
            "-l", ssh_user,
            host,
            command,
        ])

        # LC_ALL=C: OpenSSH-Meldungen englisch und stabil für stderr-Parsing.
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        env["LANG"] = "C"

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            return {"error": "ssh nicht verfügbar", "exit_code": -1}

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"error": f"SSH-Timeout nach {timeout}s", "exit_code": -1}

        out = stdout_b.decode(errors="replace")
        err = stderr_b.decode(errors="replace")

        if max_output is not None and len(out) > max_output:
            out = out[:max_output] + f"\n...[stdout gekürzt: {len(out)} Zeichen total]"
        if len(err) > MAX_SSH_OUTPUT:
            err = err[:MAX_SSH_OUTPUT] + f"\n...[stderr gekürzt: {len(err)} Zeichen total]"

        err = _redact_key(err, key_path, known_hosts_path)
        exit_code = proc.returncode if proc.returncode is not None else -1

        # Host-Key-Mismatch erkennen (nur relevant wenn wir verified Keys haben)
        host_key_changed = False
        if verified_keys and exit_code != 0 and _HOST_KEY_CHANGED_RE.search(err):
            host_key_changed = True
            logger.warning(
                "run_ssh_command host_key_changed: %s:%s (%s:%d)",
                target_type, target_id, host, ssh_port,
            )

        result: dict = {
            "stdout": out,
            "stderr": err,
            "exit_code": exit_code,
        }
        if host_key_changed:
            # Im strict-Modus wird die Nachricht user-facing verständlich.
            result["host_key_changed"] = True
            if enforcement == "strict":
                result["error"] = (
                    "Host-Key hat sich geändert — möglicher MITM. "
                    "Admin muss den neuen Fingerprint genehmigen."
                )
                result["host_key_mode"] = "strict"
        elif host_key_unverified:
            result["host_key_unverified"] = True
            result["host_key_mode"] = "warn"
        return result
    except Exception as e:
        msg = _redact_key(str(e), key_path, known_hosts_path)
        return {"error": msg, "exit_code": -1}
    finally:
        if known_hosts_path:
            try:
                os.unlink(known_hosts_path)
            except Exception:
                pass
