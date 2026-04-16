"""
ssh_known_hosts.py — Host-Key-Verzeichnis fuer Target-Tools (#674-A)

Zentrale Verwaltung von SSH-Host-Keys fuer server_shell, server_file_read,
server_file_write, wks_shell_exec. Discovery + Pinning + Admin-Approval.

Datei: /etc/hydrahive/ssh_known_hosts.json (0o600)

Host-Keys haben das Format <target_type>:<target_id>, z.B. "server:prod-web"
oder "wks:till", damit Server-IDs und WKS-Usernames nicht kollidieren.

Enforcement-Modus per Env HYDRAHIVE_REQUIRE_HOST_KEYS:
  "warn"   (Default): nur diagnostizieren, nichts blocken.
  "strict": Target-Tools (#674-B) blocken unverifizierte/geaenderte Keys.

#674-A liefert nur Foundation + Discovery + Admin-API. Das tatsaechliche
Enforcement in run_ssh_command() kommt in #674-B.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .settings import settings

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

ALLOWED_TARGET_TYPES = ("server", "wks")
ALLOWED_ALGORITHMS = (
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
)
KEYSCAN_ALGORITHMS = "ed25519,rsa,ecdsa"
DEFAULT_KEYSCAN_TIMEOUT = 5

_SAFE_ID = re.compile(r"^[a-z0-9_-]+$")
_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{10,}$")

HostStatus = Literal["verified", "unverified", "changed", "unknown"]


# ────────────────────────────────────────────── Validation


def make_host_key(target_type: str, target_id: str) -> str:
    """Liefert den Store-Key <target_type>:<target_id>. Wirft ValueError."""
    if target_type not in ALLOWED_TARGET_TYPES:
        raise ValueError(f"target_type '{target_type}' ungueltig")
    if not target_id or not isinstance(target_id, str):
        raise ValueError("target_id leer")
    if len(target_id) > 64 or not _SAFE_ID.match(target_id):
        raise ValueError(f"target_id '{target_id}' ungueltig")
    return f"{target_type}:{target_id}"


def _validate_fingerprint(fp: str) -> None:
    if not isinstance(fp, str) or not _FINGERPRINT_RE.match(fp):
        raise ValueError("fingerprint_sha256 ungueltig")


# ────────────────────────────────────────────── Store I/O


def _empty_store() -> dict:
    return {"schema_version": SCHEMA_VERSION, "hosts": {}}


def load_known_hosts() -> dict:
    p = settings.ssh_known_hosts_config
    if not p.exists():
        return _empty_store()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("ssh_known_hosts.json nicht lesbar — liefere leeren Store")
        return _empty_store()
    if not isinstance(data, dict) or not isinstance(data.get("hosts"), dict):
        return _empty_store()
    data.setdefault("schema_version", SCHEMA_VERSION)
    return data


def save_known_hosts(data: dict) -> None:
    """Atomarer Write via temp + os.replace."""
    p = settings.ssh_known_hosts_config
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    os.replace(tmp, p)


# ────────────────────────────────────────────── Fingerprint + Scan


def compute_fingerprint(public_key_b64: str) -> str:
    """OpenSSH-Format SHA256-Fingerprint (base64-ohne-padding).

    Input ist der reine Base64-Anteil (zweites Feld einer OpenSSH-Zeile),
    nicht die komplette Zeile mit Algo-Prefix.
    """
    if not public_key_b64 or not isinstance(public_key_b64, str):
        raise ValueError("public_key_b64 leer")
    raw = base64.b64decode(public_key_b64, validate=False)
    digest = hashlib.sha256(raw).digest()
    b64 = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{b64}"


async def scan_host(host: str, port: int = 22, *, timeout: int = DEFAULT_KEYSCAN_TIMEOUT) -> dict:
    """Ruft `ssh-keyscan` gegen host:port und liefert
      {"keys": [{"algorithm", "public_key", "fingerprint_sha256"}, ...],
       "scan_error": Optional[str]}

    Haelt sich bei jedem Fehlerpfad an scan_error — wirft nicht. Das
    ist wichtig, damit /admin/servers/{id}/test bei keyscan-Problemen
    nicht kaputt geht (#674-A Vorgabe).

    public_key ist der reine Base64-Anteil ohne Algo-Prefix und ohne
    Comment — aus dem laesst sich in #674-B ein OpenSSH-known_hosts-Zeile
    als `{host} {algorithm} {public_key}` bauen.
    """
    host = str(host or "").strip()
    if not host:
        return {"keys": [], "scan_error": "Host leer"}
    try:
        port_i = int(port)
    except Exception:
        return {"keys": [], "scan_error": "Port ungueltig"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh-keyscan",
            "-T", str(timeout),
            "-t", KEYSCAN_ALGORITHMS,
            "-p", str(port_i),
            host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"keys": [], "scan_error": "ssh-keyscan nicht installiert"}
    except Exception as exc:
        return {"keys": [], "scan_error": f"ssh-keyscan Start: {type(exc).__name__}"}

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout + 2,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"keys": [], "scan_error": f"ssh-keyscan Timeout nach {timeout + 2}s"}
    except Exception as exc:
        return {"keys": [], "scan_error": f"ssh-keyscan Read: {type(exc).__name__}"}

    out = stdout_b.decode(errors="replace")
    err = stderr_b.decode(errors="replace").strip()

    keys: list[dict] = []
    seen_fps: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        algo = parts[1]
        if algo not in ALLOWED_ALGORITHMS:
            continue
        key_b64 = parts[2]
        try:
            fp = compute_fingerprint(key_b64)
        except Exception:
            continue
        if fp in seen_fps:
            continue
        seen_fps.add(fp)
        keys.append({
            "algorithm": algo,
            "public_key": key_b64,
            "fingerprint_sha256": fp,
        })

    scan_error: str | None = None
    if not keys:
        if err:
            scan_error = err[:200]
        elif proc.returncode not in (0, None):
            scan_error = f"ssh-keyscan exit {proc.returncode}"
        else:
            scan_error = "Keine Host-Keys gefunden"
    return {"keys": keys, "scan_error": scan_error}


# ────────────────────────────────────────────── Enforcement-Modus


def get_enforcement_mode() -> Literal["warn", "strict"]:
    """Liest HYDRAHIVE_REQUIRE_HOST_KEYS; Default 'warn'.

    In #674-A nur diagnostisch — run_ssh_command() nutzt den Modus
    noch nicht. #674-B schaltet ihn scharf.
    """
    v = os.environ.get("HYDRAHIVE_REQUIRE_HOST_KEYS", "warn").strip().lower()
    if v in ("1", "true", "yes", "strict"):
        return "strict"
    return "warn"


# ────────────────────────────────────────────── Status-Ableitung


def _compute_host_status(host_keys: dict) -> HostStatus:
    if not host_keys:
        return "unknown"
    if any(v.get("status") == "verified" for v in host_keys.values()):
        return "verified"
    return "unverified"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_default(target_type: str, target_id: str) -> dict:
    return {
        "target_type": target_type,
        "target_id": target_id,
        "ip": "",
        "ssh_port": 22,
        "ssh_user": "",
        "host_keys": {},
        "status": "unknown",
        "last_checked": None,
    }


# ────────────────────────────────────────────── Public Store-Operationen


def get_host_entry(target_type: str, target_id: str) -> dict | None:
    key = make_host_key(target_type, target_id)
    return load_known_hosts()["hosts"].get(key)


def record_scan_result(
    target_type: str,
    target_id: str,
    *,
    ip: str,
    ssh_port: int,
    ssh_user: str,
    scanned_keys: list[dict],
) -> dict:
    """TOFU-Write: neue Keys landen mit status='unverified' im Store.

    Bereits bekannte Keys (gleicher Fingerprint) bleiben mit ihrem bisherigen
    Status (inkl. 'verified') unveraendert — der Scan darf nicht versehentlich
    Approvals zuruecksetzen.
    """
    key = make_host_key(target_type, target_id)
    store = load_known_hosts()
    host = store["hosts"].get(key) or _entry_default(target_type, target_id)
    host["ip"] = str(ip or "")
    host["ssh_port"] = int(ssh_port or 22)
    host["ssh_user"] = str(ssh_user or "")
    host["last_checked"] = _now_iso()

    existing = host.get("host_keys") or {}
    for k in scanned_keys or []:
        fp = k.get("fingerprint_sha256")
        if not fp or fp in existing:
            continue
        existing[fp] = {
            "algorithm": k.get("algorithm", ""),
            "public_key": k.get("public_key", ""),
            "fingerprint_sha256": fp,
            "verified_at": None,
            "verified_by": None,
            "verified_method": None,
            "status": "unverified",
        }
    host["host_keys"] = existing
    host["status"] = _compute_host_status(existing)
    store["hosts"][key] = host
    save_known_hosts(store)
    return host


def approve_key(
    target_type: str,
    target_id: str,
    fingerprint_sha256: str,
    *,
    approver: str = "admin",
) -> dict | None:
    """Setzt einen Key auf status='verified'. Liefert aktualisierte
    Host-Entry oder None, wenn Host/Fingerprint unbekannt."""
    _validate_fingerprint(fingerprint_sha256)
    key = make_host_key(target_type, target_id)
    store = load_known_hosts()
    host = store["hosts"].get(key)
    if not host:
        return None
    host_keys = host.get("host_keys") or {}
    hk = host_keys.get(fingerprint_sha256)
    if not hk:
        return None
    hk["status"] = "verified"
    hk["verified_at"] = _now_iso()
    hk["verified_by"] = (approver or "admin")[:64]
    hk["verified_method"] = "manual-approve"
    host["host_keys"] = host_keys
    host["status"] = _compute_host_status(host_keys)
    save_known_hosts(store)
    return host


def delete_key(
    target_type: str,
    target_id: str,
    fingerprint_sha256: str,
) -> dict | None:
    """Entfernt einen Key aus dem Store. Liefert aktualisierte Entry
    oder None, wenn Host/Fingerprint unbekannt."""
    _validate_fingerprint(fingerprint_sha256)
    key = make_host_key(target_type, target_id)
    store = load_known_hosts()
    host = store["hosts"].get(key)
    if not host:
        return None
    host_keys = host.get("host_keys") or {}
    if fingerprint_sha256 not in host_keys:
        return None
    del host_keys[fingerprint_sha256]
    host["host_keys"] = host_keys
    host["status"] = _compute_host_status(host_keys)
    save_known_hosts(store)
    return host


def verify_host_key(
    target_type: str,
    target_id: str,
    observed_keys: list[dict] | None = None,
) -> HostStatus:
    """Reiner Lookup fuer #674-B. Ohne observed_keys: abgeleiteter Host-Status
    aus dem Store. Mit observed_keys: Vergleich gegen gespeicherte verified Keys."""
    try:
        entry = get_host_entry(target_type, target_id)
    except ValueError:
        return "unknown"
    if not entry:
        return "unknown"
    stored = entry.get("host_keys") or {}
    if not stored:
        return "unknown"
    if observed_keys:
        observed_fps = {
            k.get("fingerprint_sha256") for k in observed_keys
            if k.get("fingerprint_sha256")
        }
        verified_fps = {
            fp for fp, d in stored.items() if d.get("status") == "verified"
        }
        if verified_fps and (verified_fps & observed_fps):
            return "verified"
        if verified_fps and not (verified_fps & observed_fps):
            return "changed"
    return _compute_host_status(stored)
