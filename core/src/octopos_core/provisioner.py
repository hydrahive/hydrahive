"""
provisioner.py — Projekt-Provisioning (#9, #10, #11)

Bei Projekt-Anlage drei Schritte in Reihe:
1. Linux-User anlegen: proj_<id>, Heimverzeichnis /projects/<id>/files (I3, PR4)
2. Samba-Share einrichten: Share für proj_<id>, smbd neu laden (I4)
3. Matrix-Room erstellen: #<id>:server, Boss + Worker einladen (A1, PR3)

Idempotent: Bereits existierende Ressourcen werden übersprungen.
Core läuft als 'octopos'-User — Root-Operationen via sudo (NOPASSWD konfiguriert).
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from .project_config import ProjectConfig

logger = logging.getLogger(__name__)

CONDUWUIT_URL  = "http://127.0.0.1:6167"
SAMBA_CONF     = "/etc/samba/smb.conf"
SAMBA_INCLUDES = "/etc/samba/octopos-shares.conf"
PROJECTS_DIR   = "/projects"


@dataclass
class ProvisionResult:
    project_id:  str
    linux_user:  str
    files_dir:   str
    samba_share: str
    matrix_room: str | None
    warnings:    list[str]

    @property
    def ok(self) -> bool:
        return bool(self.matrix_room is not None or self.linux_user)


class Provisioner:

    def __init__(self, admin_token: str, server_name: str) -> None:
        self._token       = admin_token
        self._server_name = server_name

    # ------------------------------------------------------------------ public

    async def provision(self, cfg: ProjectConfig) -> ProvisionResult:
        """Vollständiger Provisioning-Flow für ein Projekt."""
        warnings: list[str] = []
        project_id = cfg.id
        linux_user = cfg.effective_system_user()           # proj_<id>
        files_dir  = f"{PROJECTS_DIR}/{project_id}/files"

        logger.info("Provisioniere Projekt '%s'", project_id)

        # Schritt 1: Linux-User
        user_warn = self._create_linux_user(linux_user, files_dir)
        if user_warn:
            warnings.append(user_warn)

        # Schritt 2: Samba
        samba_warn = self._setup_samba(project_id, linux_user, files_dir)
        if samba_warn:
            warnings.append(samba_warn)

        # Schritt 3: Matrix-Room
        matrix_room, room_warn = await self._create_matrix_room(cfg)
        if room_warn:
            warnings.append(room_warn)

        if warnings:
            logger.warning("Provisioning '%s' mit Warnungen: %s", project_id, warnings)
        else:
            logger.info("Provisioning '%s' abgeschlossen", project_id)

        return ProvisionResult(
            project_id  = project_id,
            linux_user  = linux_user,
            files_dir   = files_dir,
            samba_share = project_id,
            matrix_room = matrix_room,
            warnings    = warnings,
        )

    async def deprovision(self, cfg: ProjectConfig) -> list[str]:
        """Projekt-Ressourcen entfernen (für Projekt-Löschen)."""
        warnings: list[str] = []
        project_id = cfg.id
        linux_user = cfg.effective_system_user()

        # Samba-Share entfernen
        w = self._remove_samba_share(project_id)
        if w:
            warnings.append(w)

        # Linux-User löschen (Dateien bleiben als Backup)
        w = self._delete_linux_user(linux_user)
        if w:
            warnings.append(w)

        return warnings

    # ----------------------------------------------------------------- Schritt 1: Linux-User (#9)

    def _create_linux_user(self, username: str, files_dir: str) -> str | None:
        """
        Legt Linux-User an, erstellt Heimverzeichnis /projects/<id>/files.
        Gibt None zurück wenn OK, sonst Warnmeldung.
        """
        # Idempotenz: User bereits vorhanden?
        result = subprocess.run(["id", username], capture_output=True)
        if result.returncode == 0:
            logger.debug("Linux-User '%s' bereits vorhanden", username)
            # Verzeichnis trotzdem sicherstellen
            self._ensure_dir(files_dir, username)
            return None

        # useradd: kein Login, kein echtes Home (wir setzen das manuell)
        cmd = ["sudo", "useradd",
               "--system",
               "--no-create-home",
               "--shell", "/usr/sbin/nologin",
               "--comment", f"OctopOS Projekt-User",
               username]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return f"useradd '{username}' fehlgeschlagen: {r.stderr.strip()}"

        # Verzeichnis anlegen und Ownership setzen
        self._ensure_dir(files_dir, username)
        logger.info("Linux-User '%s' angelegt, Verzeichnis: %s", username, files_dir)
        return None

    def _delete_linux_user(self, username: str) -> str | None:
        result = subprocess.run(["id", username], capture_output=True)
        if result.returncode != 0:
            return None   # existiert nicht, nichts zu tun
        r = subprocess.run(
            ["sudo", "userdel", username],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            return f"userdel '{username}' fehlgeschlagen: {r.stderr.strip()}"
        logger.info("Linux-User '%s' gelöscht", username)
        return None

    def _ensure_dir(self, path: str, owner: str) -> None:
        subprocess.run(["sudo", "mkdir", "-p", path], check=True, capture_output=True)
        subprocess.run(["sudo", "chown", f"{owner}:{owner}", path], check=True, capture_output=True)
        subprocess.run(["sudo", "chmod", "750", path], check=True, capture_output=True)

    # ----------------------------------------------------------------- Schritt 2: Samba (#10)

    def _setup_samba(self, project_id: str, username: str, files_dir: str) -> str | None:
        """
        Schreibt einen Include-Block nach /etc/samba/octopos-shares.conf.
        Hängt den Include in smb.conf ein falls noch nicht vorhanden.
        Idempotent: bestehender Share-Block wird überschrieben.
        """
        # Samba installiert?
        r = subprocess.run(["which", "smbd"], capture_output=True)
        if r.returncode != 0:
            return "Samba nicht installiert — Share übersprungen"

        # Include-Datei anlegen / aktualisieren
        try:
            self._write_samba_share(project_id, username, files_dir)
        except Exception as e:
            return f"Samba-Config fehlgeschlagen: {e}"

        # Include in smb.conf einhängen (einmalig)
        self._ensure_samba_include()

        # smbd neu laden
        r = subprocess.run(
            ["sudo", "smbcontrol", "smbd", "reload-config"],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            # Fallback: service reload
            subprocess.run(["sudo", "systemctl", "reload", "smbd"],
                           capture_output=True)
        logger.info("Samba-Share '%s' eingerichtet", project_id)
        return None

    def _write_samba_share(self, project_id: str, username: str, files_dir: str) -> None:
        includes_path = Path(SAMBA_INCLUDES)

        # Bestehende Blocks laden
        existing = includes_path.read_text(encoding="utf-8") if includes_path.exists() else ""

        # Block für dieses Projekt herausschneiden und neu schreiben
        marker_start = f"# BEGIN octopos:{project_id}"
        marker_end   = f"# END octopos:{project_id}"
        new_block = (
            f"{marker_start}\n"
            f"[{project_id}]\n"
            f"   comment = OctopOS Projekt {project_id}\n"
            f"   path = {files_dir}\n"
            f"   valid users = {username}\n"
            f"   read only = no\n"
            f"   browseable = yes\n"
            f"   create mask = 0640\n"
            f"   directory mask = 0750\n"
            f"{marker_end}\n"
        )

        if marker_start in existing:
            # Block ersetzen
            import re
            pattern = rf"{re.escape(marker_start)}.*?{re.escape(marker_end)}\n"
            updated = re.sub(pattern, new_block, existing, flags=re.DOTALL)
        else:
            updated = existing + "\n" + new_block

        tmp = Path(f"/tmp/octopos-samba-{project_id}.conf")
        tmp.write_text(updated, encoding="utf-8")
        subprocess.run(
            ["sudo", "cp", str(tmp), SAMBA_INCLUDES],
            check=True, capture_output=True
        )
        subprocess.run(["sudo", "chmod", "644", SAMBA_INCLUDES], capture_output=True)

    def _remove_samba_share(self, project_id: str) -> str | None:
        includes_path = Path(SAMBA_INCLUDES)
        if not includes_path.exists():
            return None
        import re
        existing = includes_path.read_text(encoding="utf-8")
        marker_start = f"# BEGIN octopos:{project_id}"
        marker_end   = f"# END octopos:{project_id}"
        pattern = rf"{re.escape(marker_start)}.*?{re.escape(marker_end)}\n"
        updated = re.sub(pattern, "", existing, flags=re.DOTALL)
        tmp = Path(f"/tmp/octopos-samba-rm-{project_id}.conf")
        tmp.write_text(updated, encoding="utf-8")
        subprocess.run(["sudo", "cp", str(tmp), SAMBA_INCLUDES], capture_output=True)
        subprocess.run(["sudo", "smbcontrol", "smbd", "reload-config"], capture_output=True)
        return None

    def _ensure_samba_include(self) -> None:
        include_line = f"include = {SAMBA_INCLUDES}"
        smb_conf = Path(SAMBA_CONF)
        if not smb_conf.exists():
            return
        content = smb_conf.read_text(encoding="utf-8")
        if SAMBA_INCLUDES in content:
            return
        # Ans Ende der [global]-Sektion anhängen
        updated = content.rstrip() + f"\n\n{include_line}\n"
        tmp = Path("/tmp/octopos-smb.conf")
        tmp.write_text(updated, encoding="utf-8")
        subprocess.run(["sudo", "cp", str(tmp), SAMBA_CONF], capture_output=True)

    # ----------------------------------------------------------------- Schritt 3: Matrix-Room (#11)

    async def _create_matrix_room(
        self, cfg: ProjectConfig
    ) -> tuple[str | None, str | None]:
        """
        Erstellt Matrix-Room, lädt Boss + Worker als Moderatoren ein.
        Gibt (room_id, warning_oder_None) zurück.
        """
        project_id = cfg.id
        room_alias = project_id.lower().replace(" ", "_")
        room_name  = cfg.identity.name

        # Alle Agenten die eingeladen werden sollen
        agent_ids = [cfg.agents.boss] + list(cfg.agents.workers)
        invite_mxids = [f"@{aid}:{self._server_name}" for aid in agent_ids]

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

        async with aiohttp.ClientSession() as session:
            # Room anlegen
            payload = {
                "name":             room_name,
                "room_alias_name":  room_alias,
                "topic":            f"OctopOS Projekt: {room_name}",
                "preset":           "private_chat",
                "invite":           invite_mxids,
                "power_level_content_override": {
                    "users": {
                        f"@admin:{self._server_name}": 100,
                        **{mxid: 50 for mxid in invite_mxids},
                    }
                },
            }
            try:
                async with session.post(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/createRoom",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json()
            except Exception as e:
                return None, f"Matrix-Room konnte nicht erstellt werden: {e}"

            if "errcode" in data:
                if data["errcode"] == "M_ROOM_IN_USE":
                    # Room existiert bereits — Alias auflösen
                    room_id = await self._resolve_alias(session, headers, room_alias)
                    if room_id:
                        logger.debug("Matrix-Room '%s' bereits vorhanden: %s", room_alias, room_id)
                        return room_id, None
                return None, f"Matrix createRoom Fehler: {data.get('error', data)}"

            room_id = data.get("room_id")
            logger.info("Matrix-Room erstellt: %s (%s)", room_alias, room_id)
            return room_id, None

    async def _resolve_alias(
        self, session: aiohttp.ClientSession, headers: dict, alias: str
    ) -> str | None:
        from urllib.parse import quote
        full_alias = f"#{alias}:{self._server_name}"
        encoded    = quote(full_alias, safe="")
        try:
            async with session.get(
                f"{CONDUWUIT_URL}/_matrix/client/v3/directory/room/{encoded}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                return data.get("room_id")
        except Exception:
            return None


def load_admin_token(cred_file: str = "/etc/octopos/admin_credentials") -> str:
    """Admin-Token aus /etc/octopos/admin_credentials lesen."""
    try:
        for line in Path(cred_file).read_text().splitlines():
            if line.startswith("matrix_admin_password="):
                # Wir brauchen den Access-Token, nicht das Passwort
                # Token wird beim Start via Login geholt
                pass
        # Fallback: Token direkt aus Datei wenn vorhanden
        for line in Path(cred_file).read_text().splitlines():
            if line.startswith("matrix_access_token="):
                return line.split("=", 1)[1]
    except OSError:
        pass
    return ""


async def get_admin_access_token(password: str, server_name: str) -> str:
    """
    Loggt Admin ein und gibt Access-Token zurück.
    Wird beim Core-Start aufgerufen und gecacht.
    """
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{CONDUWUIT_URL}/_matrix/client/v3/login",
            json={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": f"@admin:{server_name}"},
                "password": password,
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
            return data.get("access_token", "")
