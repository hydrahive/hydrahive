"""
provisioner.py — Projekt-Provisioning (#9, #10, #11)

Bei Projekt-Anlage drei Schritte in Reihe:
1. Linux-User anlegen: proj_<id>, Heimverzeichnis /projects/<id>/ (I3, PR4)
2. Samba-Share einrichten: Share für proj_<id>, smbd neu laden (I4)
3. Matrix-Room erstellen: #<id>:server, Boss + Worker einladen (A1, PR3)

Idempotent: Bereits existierende Ressourcen werden übersprungen.
Core läuft als 'hydrahive'-User — Root-Operationen via sudo (NOPASSWD konfiguriert).
"""

import asyncio
import json
import logging
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from .project_config import ProjectConfig

logger = logging.getLogger(__name__)

CONDUWUIT_URL    = "http://127.0.0.1:6167"
SAMBA_CONF       = "/etc/samba/smb.conf"
SAMBA_INCLUDES   = "/etc/samba/hydrahive-shares.conf"
SAMBA_CREDS_FILE = "/etc/hydrahive/samba_credentials"
PROJECTS_DIR     = "/projects"


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
        files_dir  = f"{PROJECTS_DIR}/{project_id}"

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

        # Matrix-Room entfernen
        room_id = cfg.matrix.room if cfg.matrix else None
        if room_id:
            w = await self._delete_matrix_room(room_id)
            if w:
                warnings.append(w)

        return warnings

    # ----------------------------------------------------------------- Schritt 1: Linux-User (#9)

    def _create_linux_user(self, username: str, files_dir: str) -> str | None:
        """
        Legt Linux-User an, setzt Heimverzeichnis auf /projects/<id>/.
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
               "--comment", f"HydraHive Projekt-User",
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
        # Owner: proj_<id> (Samba-User), Gruppe: hydrahive (Core-Prozess)
        # 770: Owner + hydrahive können lesen/schreiben, Others nichts
        subprocess.run(["sudo", "mkdir", "-p", path], check=True, capture_output=True)
        subprocess.run(["sudo", "chown", f"{owner}:hydrahive", path], check=True, capture_output=True)
        subprocess.run(["sudo", "chmod", "770", path], check=True, capture_output=True)

    # ----------------------------------------------------------------- Schritt 2: Samba (#10)

    def _setup_samba(self, project_id: str, username: str, files_dir: str) -> str | None:
        """
        Schreibt einen Include-Block nach /etc/samba/hydrahive-shares.conf.
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

        # Samba-User anlegen / Passwort setzen
        pw_warn = self._set_samba_password(username)
        if pw_warn:
            logger.warning(pw_warn)

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
        return pw_warn

    def _write_samba_share(self, project_id: str, username: str, files_dir: str) -> None:
        includes_path = Path(SAMBA_INCLUDES)

        # Bestehende Blocks laden
        existing = includes_path.read_text(encoding="utf-8") if includes_path.exists() else ""

        # Block für dieses Projekt herausschneiden und neu schreiben
        marker_start = f"# BEGIN hydrahive:{project_id}"
        marker_end   = f"# END hydrahive:{project_id}"
        new_block = (
            f"{marker_start}\n"
            f"[{project_id}]\n"
            f"   comment = HydraHive Projekt {project_id}\n"
            f"   path = {files_dir}\n"
            f"   valid users = {username}\n"
            f"   read only = no\n"
            f"   browseable = yes\n"
            f"   create mask = 0660\n"
            f"   directory mask = 0770\n"
            f"{marker_end}\n"
        )

        if marker_start in existing:
            # Block ersetzen
            import re
            pattern = rf"{re.escape(marker_start)}.*?{re.escape(marker_end)}\n"
            updated = re.sub(pattern, new_block, existing, flags=re.DOTALL)
        else:
            updated = existing + "\n" + new_block

        tmp = Path(f"/tmp/hydrahive-samba-{project_id}.conf")
        tmp.write_text(updated, encoding="utf-8")
        subprocess.run(
            ["sudo", "cp", str(tmp), SAMBA_INCLUDES],
            check=True, capture_output=True
        )
        subprocess.run(["sudo", "chmod", "644", SAMBA_INCLUDES], capture_output=True)

    def reset_samba_password(self, username: str) -> tuple[str | None, str | None]:
        """
        Öffentliche Methode: Setzt Samba-Passwort zurück.
        Gibt (error_msg, new_password) zurück.
        """
        warn = self._set_samba_password(username)
        if warn:
            return warn, None
        password = self._read_samba_password(username)
        return None, password

    def _read_samba_password(self, username: str) -> str | None:
        """Liest aktuelles Samba-Passwort aus SAMBA_CREDS_FILE (via sudo grep)."""
        try:
            r = subprocess.run(
                ["sudo", "grep", f"^{username}:", SAMBA_CREDS_FILE],
                capture_output=True, text=True
            )
            if r.returncode == 0 and ":" in r.stdout.strip():
                return r.stdout.strip().split(":", 1)[1]
        except Exception:
            pass
        return None

    def _set_samba_password(self, username: str) -> str | None:
        """
        Legt Samba-User an und setzt ein zufälliges Passwort.
        Idempotent: existierender User bekommt ein neues Passwort (smbpasswd ohne -a).
        Speichert Klartext-Passwort in SAMBA_CREDS_FILE (chmod 600, nur root).
        Gibt None zurück wenn OK, sonst Warnmeldung.
        """
        password = secrets.token_urlsafe(16)

        # Prüfen ob User bereits in Samba-Datenbank
        r = subprocess.run(
            ["sudo", "pdbedit", "-L", "-u", username],
            capture_output=True, text=True
        )
        samba_user_exists = r.returncode == 0

        flag = "" if samba_user_exists else "-a"
        flags = ["-s", flag] if flag else ["-s"]
        cmd = ["sudo", "smbpasswd"] + flags + [username]

        proc = subprocess.run(
            cmd,
            input=f"{password}\n{password}\n",
            capture_output=True, text=True
        )
        if proc.returncode != 0:
            return f"smbpasswd für '{username}' fehlgeschlagen: {proc.stderr.strip()}"

        # Passwort in Credentials-Datei speichern
        try:
            creds_path = Path(SAMBA_CREDS_FILE)
            # Datei anlegen falls nicht vorhanden (sudo, da /etc/hydrahive root-owned)
            subprocess.run(
                ["sudo", "bash", "-c",
                 f"touch {SAMBA_CREDS_FILE} && chmod 600 {SAMBA_CREDS_FILE}"],
                capture_output=True
            )
            # Bestehenden Eintrag für diesen User entfernen, neuen anhängen
            # grep -v gibt exit 1 wenn kein Output → || true verhindert Chain-Break
            subprocess.run(
                ["sudo", "bash", "-c",
                 f"grep -v '^{username}:' {SAMBA_CREDS_FILE} > /tmp/hh-sambacreds.tmp || true"
                 f" && echo '{username}:{password}' >> /tmp/hh-sambacreds.tmp"
                 f" && cp /tmp/hh-sambacreds.tmp {SAMBA_CREDS_FILE}"
                 f" && chmod 600 {SAMBA_CREDS_FILE}"],
                capture_output=True
            )
        except Exception as e:
            return f"Samba-Passwort gesetzt, aber Credentials-Datei konnte nicht geschrieben werden: {e}"

        logger.info("Samba-Passwort für '%s' gesetzt", username)
        return None

    def _remove_samba_share(self, project_id: str) -> str | None:
        includes_path = Path(SAMBA_INCLUDES)
        if not includes_path.exists():
            return None
        import re
        existing = includes_path.read_text(encoding="utf-8")
        marker_start = f"# BEGIN hydrahive:{project_id}"
        marker_end   = f"# END hydrahive:{project_id}"
        pattern = rf"{re.escape(marker_start)}.*?{re.escape(marker_end)}\n"
        updated = re.sub(pattern, "", existing, flags=re.DOTALL)
        tmp = Path(f"/tmp/hydrahive-samba-rm-{project_id}.conf")
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
        tmp = Path("/tmp/hydrahive-smb.conf")
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
                "topic":            f"HydraHive Projekt: {room_name}",
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

    async def _delete_matrix_room(self, room_id: str) -> str | None:
        """Matrix-Room verlassen und via Admin-API löschen. Gibt Warning-String oder None zurück."""
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                # Erst verlassen (falls der Bot-User Mitglied ist)
                async with session.post(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/rooms/{room_id}/leave",
                    headers=headers,
                    json={},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    await resp.read()

                # Conduwuit/Synapse Admin-API: Room löschen
                async with session.delete(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/admin/rooms/{room_id}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status not in (200, 404):
                        return f"Matrix-Room {room_id} konnte nicht gelöscht werden: {data.get('error', resp.status)}"

            logger.info("Matrix-Room gelöscht: %s", room_id)
            return None
        except Exception as e:
            return f"Matrix-Room {room_id} Fehler beim Löschen: {e}"


def load_admin_token(cred_file: str = "/etc/hydrahive/admin_credentials") -> str:
    """Admin-Token aus /etc/hydrahive/admin_credentials lesen."""
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
