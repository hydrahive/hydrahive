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
import os
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from .project_config import ProjectConfig
from .settings import settings

logger = logging.getLogger(__name__)

CONDUWUIT_URL    = "http://127.0.0.1:6167"
SAMBA_CONF       = "/etc/samba/smb.conf"
SAMBA_INCLUDES   = "/etc/samba/hydrahive-shares.conf"
SAMBA_CREDS_FILE = str(settings.samba_credentials)

# Registration-Token Pfade (in Reihenfolge: hydrahive-eigene Datei zuerst)
_REG_TOKEN_PATHS = [
    str(settings.matrix_registration_token),  # hydrahive-lesbar
    "/etc/conduwuit/conduwuit.toml",          # Fallback (octopos-Gruppe)
]


def _read_matrix_reg_token() -> str:
    """Liest den Matrix-Registration-Token aus der ersten lesbaren Quelle."""
    for path in _REG_TOKEN_PATHS:
        try:
            for line in Path(path).read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("registration_token"):
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""
PROJECTS_DIR     = str(settings.projects_dir)


@dataclass
class ProvisionResult:
    project_id:  str
    linux_user:  str
    files_dir:   str
    samba_share: str
    matrix_room:  str | None
    matrix_space: str | None
    warnings:     list[str]

    @property
    def ok(self) -> bool:
        return bool(self.matrix_room is not None or self.linux_user)


class Provisioner:

    def __init__(self, admin_token: str, server_name: str) -> None:
        self._token       = admin_token
        self._server_name = server_name

    async def register_matrix_user(self, username: str, password: str) -> bool:
        """Registriert einen menschlichen User-Account in conduwuit."""
        reg_token = _read_matrix_reg_token()
        if not reg_token:
            logger.warning("Matrix-Registrierung: Kein registration_token gefunden")
            return False

        async with aiohttp.ClientSession() as session:
            try:
                # UIAA Schritt 1: Session-ID holen
                async with session.post(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/register",
                    json={"username": username, "password": password, "inhibit_login": True},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r1:
                    d1 = await r1.json(content_type=None)

                if d1.get("errcode") == "M_USER_IN_USE":
                    logger.debug("Matrix-User '%s' bereits vorhanden", username)
                    return True
                if r1.status in (200, 201) and "errcode" not in d1:
                    logger.info("Matrix-User '%s' registriert (direkt)", username)
                    return True

                # UIAA Schritt 2: Mit auth + session
                uiaa_session = d1.get("session", "")
                auth_block = {"type": "m.login.registration_token", "token": reg_token}
                if uiaa_session:
                    auth_block["session"] = uiaa_session

                async with session.post(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/register",
                    json={"username": username, "password": password, "auth": auth_block, "inhibit_login": True},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r2:
                    data = await r2.json(content_type=None)
                    if r2.status in (200, 201):
                        logger.info("Matrix-User '%s' registriert", username)
                        return True
                    if data.get("errcode") == "M_USER_IN_USE":
                        logger.debug("Matrix-User '%s' bereits vorhanden", username)
                        return True
                    logger.warning("Matrix-User-Registrierung '%s' fehlgeschlagen: %s %s", username, r2.status, data)
                    return False
            except Exception as e:
                logger.warning("Matrix-User-Registrierung '%s' Exception: %s", username, e)
                return False

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

        # Schritt 2: Samba (nur wenn User existiert)
        from subprocess import run as _run
        user_exists = _run(["id", linux_user], capture_output=True).returncode == 0
        if user_exists:
            samba_warn = self._setup_samba(project_id, linux_user, files_dir)
            if samba_warn:
                warnings.append(samba_warn)
        else:
            warnings.append(f"Samba-Setup übersprungen: Linux-User '{linux_user}' nicht vorhanden")

        # Schritt 3: Matrix-Room
        matrix_room, room_warn = await self._create_matrix_room(cfg)
        if room_warn:
            warnings.append(room_warn)

        # Schritt 4: Matrix-Space (#82)
        matrix_space = None
        if matrix_room:
            matrix_space, space_warn = await self._create_matrix_space(cfg, matrix_room)
            if space_warn:
                warnings.append(space_warn)

        if warnings:
            logger.warning("Provisioning '%s' mit Warnungen: %s", project_id, warnings)
        else:
            logger.info("Provisioning '%s' abgeschlossen", project_id)

        return ProvisionResult(
            project_id   = project_id,
            linux_user   = linux_user,
            files_dir    = files_dir,
            samba_share  = project_id,
            matrix_room  = matrix_room,
            matrix_space = matrix_space,
            warnings     = warnings,
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

        # Matrix-Space entfernen (vor dem Room)
        space_id = cfg.matrix.space if cfg.matrix else None
        if space_id:
            w = await self._delete_matrix_room(space_id)
            if w:
                warnings.append(w)

        # Matrix-Room entfernen
        room_id = cfg.matrix.room if cfg.matrix else None
        if room_id:
            w = await self._delete_matrix_room(room_id)
            if w:
                warnings.append(w)

        return warnings

    # ----------------------------------------------------------------- Reconcile (#813)

    def reprovision(self, cfg: ProjectConfig) -> list[str]:
        """
        Synchroner Teil-Reprovisioner: stellt Linux-User + Samba-Share für
        ein bestehendes Projekt sicher. Matrix wird nicht angefasst.

        Idempotent: existierende Ressourcen werden nicht überschrieben, nur
        fehlende ergänzt. Lazy-tolerant: fehlendes smbd oder fehlende sudoers
        erzeugen nur Warnungen, keine Exceptions.
        """
        warnings: list[str] = []
        project_id = cfg.id
        linux_user = cfg.effective_system_user()
        files_dir  = f"{PROJECTS_DIR}/{project_id}"

        user_warn = self._create_linux_user(linux_user, files_dir)
        if user_warn:
            warnings.append(user_warn)

        user_exists = subprocess.run(["id", linux_user], capture_output=True).returncode == 0
        if not user_exists:
            warnings.append(
                f"Reconcile '{project_id}': Linux-User '{linux_user}' fehlt weiterhin — Samba übersprungen"
            )
            return warnings

        samba_warn = self._setup_samba(project_id, linux_user, files_dir)
        if samba_warn:
            warnings.append(samba_warn)

        return warnings

    def reconcile_all_projects(self, project_loader) -> dict:
        """
        Iteriert alle geladenen Projekte und ruft reprovision() pro Projekt.
        Gibt einen Report mit reconciled/skipped/errors zurück.

        Darf nie eine Exception werfen — self-healing im Boot-Pfad.
        """
        report: dict = {"reconciled": [], "skipped": [], "errors": []}
        try:
            projects = project_loader.projects
        except Exception as e:
            logger.warning("Reconcile: ProjectLoader nicht verfügbar: %s", e)
            report["errors"].append(f"project_loader: {e}")
            return report

        for project_id, cfg in projects.items():
            try:
                warnings = self.reprovision(cfg)
                if warnings:
                    report["reconciled"].append({"id": project_id, "warnings": warnings})
                else:
                    report["skipped"].append(project_id)
            except Exception as e:
                logger.warning("Reconcile '%s' fehlgeschlagen: %s", project_id, e)
                report["errors"].append({"id": project_id, "error": str(e)})

        logger.info(
            "Reconcile abgeschlossen: %d reconciled, %d skipped, %d errors",
            len(report["reconciled"]), len(report["skipped"]), len(report["errors"]),
        )
        return report

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
        # #813: --comment ohne Leerzeichen, damit sudoers-Rule ohne
        # Escape-Fallen matched.
        cmd = ["sudo", "useradd",
               "--system",
               "--no-create-home",
               "--shell", "/usr/sbin/nologin",
               "--comment", "HydraHive-Projekt-User",
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
            f"   force group = hydrahive\n"
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
        try:
            tmp.write_text(updated, encoding="utf-8")
            subprocess.run(
                ["sudo", "cp", str(tmp), SAMBA_INCLUDES],
                check=True, capture_output=True
            )
            subprocess.run(["sudo", "chmod", "644", SAMBA_INCLUDES], capture_output=True)
        finally:
            tmp.unlink(missing_ok=True)

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
        """
        Liest aktuelles Samba-Passwort aus SAMBA_CREDS_FILE.
        #813: `sudo cat` + Python-Filter statt `sudo grep`, weil die grep-
        sudoers-Regel mit `^proj_*\\:` eine "unterminated regular expression"-
        Warnung wirft und von sudo ignoriert wird. `cat` ist eh erlaubt.
        """
        try:
            r = subprocess.run(
                ["sudo", "cat", SAMBA_CREDS_FILE],
                capture_output=True, text=True
            )
            if r.returncode != 0:
                return None
            prefix = f"{username}:"
            for line in r.stdout.splitlines():
                if line.startswith(prefix):
                    return line.split(":", 1)[1]
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

        # Passwort in Credentials-Datei speichern (injection-safe: kein bash -c)
        tmp_creds = Path(f"/tmp/hh-sambacreds-{os.getpid()}.tmp")
        try:
            # Bestehenden Inhalt lesen (sudo cat), User-Zeile herausfiltern, neu schreiben
            r_read = subprocess.run(
                ["sudo", "cat", SAMBA_CREDS_FILE],
                capture_output=True, text=True
            )
            existing_lines = [
                line for line in r_read.stdout.splitlines()
                if line and not line.startswith(f"{username}:")
            ]
            existing_lines.append(f"{username}:{password}")
            tmp_creds.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
            subprocess.run(["sudo", "cp", str(tmp_creds), SAMBA_CREDS_FILE], capture_output=True, check=True)
            subprocess.run(["sudo", "chmod", "600", SAMBA_CREDS_FILE], capture_output=True)
        except Exception as e:
            return f"Samba-Passwort gesetzt, aber Credentials-Datei konnte nicht geschrieben werden: {e}"
        finally:
            tmp_creds.unlink(missing_ok=True)

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
        try:
            tmp.write_text(updated, encoding="utf-8")
            subprocess.run(["sudo", "cp", str(tmp), SAMBA_INCLUDES], capture_output=True)
        finally:
            tmp.unlink(missing_ok=True)
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

    async def _ensure_matrix_account(
        self, session: "aiohttp.ClientSession", agent_id: str
    ) -> None:
        """
        Stellt sicher dass ein Matrix-Account für den Agenten existiert.
        Falls nicht: registriert ihn mit dem Registration-Token aus conduwuit.toml.
        Fehler werden ignoriert — der Account existiert evtl. bereits.
        """
        from pathlib import Path as _Path
        reg_token = _read_matrix_reg_token()
        if not reg_token:
            return

        # Deterministic Password (wie in matrix_agent.py)
        import hashlib as _hashlib
        secret_file = settings.internal_secret_file
        secret = secret_file.read_text().strip() if secret_file.exists() else "hydrahive"
        password = _hashlib.sha256(f"{agent_id}:{secret}".encode()).hexdigest()[:32]

        try:
            # UIAA Schritt 1: Session-ID holen
            async with session.post(
                f"{CONDUWUIT_URL}/_matrix/client/v3/register",
                json={"username": agent_id, "password": password, "inhibit_login": True},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r1:
                d1 = await r1.json(content_type=None)

            if d1.get("errcode") == "M_USER_IN_USE":
                logger.debug("Matrix-Account '%s' bereits vorhanden", agent_id)
                return
            if r1.status in (200, 201) and "errcode" not in d1:
                logger.info("Matrix-Account für Agent '%s' registriert (direkt)", agent_id)
                return

            # UIAA Schritt 2: Mit auth + session
            uiaa_session = d1.get("session", "")
            auth_block = {"type": "m.login.registration_token", "token": reg_token}
            if uiaa_session:
                auth_block["session"] = uiaa_session

            async with session.post(
                f"{CONDUWUIT_URL}/_matrix/client/v3/register",
                json={"username": agent_id, "password": password, "auth": auth_block, "inhibit_login": True},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r2:
                data = await r2.json(content_type=None)
                if r2.status in (200, 201):
                    logger.info("Matrix-Account für Agent '%s' registriert", agent_id)
                elif data.get("errcode") == "M_USER_IN_USE":
                    logger.debug("Matrix-Account '%s' bereits vorhanden", agent_id)
                else:
                    logger.warning("Matrix-Account '%s' Registration: %s", agent_id, data)
        except Exception as e:
            logger.warning("Matrix-Account '%s' konnte nicht registriert werden: %s", agent_id, e)

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

        # Projekt-Member (HydraHive-User) ebenfalls einladen
        for username in getattr(cfg, "members", []):
            mxid = f"@{username}:{self._server_name}"
            if mxid not in invite_mxids:
                invite_mxids.append(mxid)

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

        async with aiohttp.ClientSession() as session:
            # Agent-Accounts vorab registrieren damit Einladungen ankommen
            for aid in agent_ids:
                await self._ensure_matrix_account(session, aid)

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

    async def _create_matrix_space(
        self, cfg: ProjectConfig, room_id: str
    ) -> tuple[str | None, str | None]:
        """
        Erstellt einen Matrix-Space für das Projekt und fügt den Room als Child ein.
        Gibt (space_id, warning_oder_None) zurück.
        """
        # Bereits vorhanden?
        if cfg.matrix and cfg.matrix.space:
            logger.debug("Matrix-Space für '%s' bereits vorhanden: %s", cfg.id, cfg.matrix.space)
            return cfg.matrix.space, None

        project_id = cfg.id
        space_alias = f"{project_id}_space"

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                # Space erstellen
                async with session.post(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/createRoom",
                    headers=headers,
                    json={
                        "name":             cfg.identity.name,
                        "room_alias_name":  space_alias,
                        "preset":           "private_chat",
                        "visibility":       "private",
                        "creation_content": {"type": "m.space"},
                        "topic":            f"HydraHive Projekt: {cfg.identity.name}",
                        "initial_state": [
                            {
                                "type":      "m.room.history_visibility",
                                "state_key": "",
                                "content":   {"history_visibility": "shared"},
                            }
                        ],
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json(content_type=None)

                if "room_id" not in data:
                    # Alias bereits vergeben → Space existiert schon
                    if data.get("errcode") == "M_ROOM_IN_USE":
                        # Alias auflösen
                        async with session.get(
                            f"{CONDUWUIT_URL}/_matrix/client/v3/directory/room/%23{space_alias}%3A{self._server_name}",
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as r2:
                            d2 = await r2.json(content_type=None)
                            space_id = d2.get("room_id")
                        if space_id:
                            logger.debug("Matrix-Space '%s' bereits vorhanden: %s", space_alias, space_id)
                            return space_id, None
                    return None, f"Space-Erstellung fehlgeschlagen: {data}"

                space_id = data["room_id"]
                logger.info("Matrix-Space für Projekt '%s' angelegt: %s", project_id, space_id)

                server_name = self._server_name
                from urllib.parse import quote as _quote

                room_id_enc  = _quote(room_id,  safe="")
                space_id_enc = _quote(space_id, safe="")

                # Child-Link: Space → Room  (m.space.child)
                async with session.put(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/rooms/{space_id}/state/m.space.child/{room_id_enc}",
                    headers=headers,
                    json={"via": [server_name], "suggested": True},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as _r:
                    _rd = await _r.json(content_type=None)
                    if "errcode" in _rd:
                        logger.warning("m.space.child fehlgeschlagen: %s", _rd)

                # Parent-Link: Room → Space  (m.room.parent)
                async with session.put(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/rooms/{room_id}/state/m.room.parent/{space_id_enc}",
                    headers=headers,
                    json={"via": [server_name], "canonical": True},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as _r:
                    _rd = await _r.json(content_type=None)
                    if "errcode" in _rd:
                        logger.warning("m.room.parent fehlgeschlagen: %s", _rd)

                # Mitglieder in den Space einladen:
                # (1) bereits im Room joined, (2) cfg.members die noch nicht drin sind
                async with session.get(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/rooms/{room_id}/joined_members",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r_mem:
                    members_data = await r_mem.json(content_type=None)

                space_invite_mxids: set[str] = set(members_data.get("joined", {}).keys())
                for uname in getattr(cfg, "members", []):
                    space_invite_mxids.add(f"@{uname}:{server_name}")
                space_invite_mxids.discard(f"@admin:{server_name}")

                for mxid in space_invite_mxids:
                    try:
                        async with session.post(
                            f"{CONDUWUIT_URL}/_matrix/client/v3/rooms/{space_id}/invite",
                            headers=headers,
                            json={"user_id": mxid},
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as _r:
                            pass
                    except Exception:
                        pass

                return space_id, None

        except Exception as e:
            return None, f"Space-Erstellung fehlgeschlagen: {e}"

    async def _delete_matrix_room(self, room_id: str) -> str | None:
        """
        Matrix-Room schließen: alle Member kicken, dann leave + forget.
        Conduit 0.4.x hat keine Admin-API für Room-Deletion — daher dieser Weg.
        """
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }
        admin_mxid = f"@admin:{self._server_name}"
        try:
            async with aiohttp.ClientSession() as session:
                # Alle aktuellen Member holen
                async with session.get(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/rooms/{room_id}/joined_members",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json(content_type=None)
                    members = list(data.get("joined", {}).keys())

                # Alle außer Admin kicken
                for member in members:
                    if member == admin_mxid:
                        continue
                    try:
                        async with session.post(
                            f"{CONDUWUIT_URL}/_matrix/client/v3/rooms/{room_id}/kick",
                            headers=headers,
                            json={"user_id": member, "reason": "Projekt gelöscht"},
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as kick_resp:
                            await kick_resp.read()
                    except Exception:
                        pass

                # Admin verlässt den Room
                async with session.post(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/rooms/{room_id}/leave",
                    headers=headers,
                    json={},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    await resp.read()

                # Admin vergisst den Room (aus der lokalen History entfernen)
                async with session.post(
                    f"{CONDUWUIT_URL}/_matrix/client/v3/rooms/{room_id}/forget",
                    headers=headers,
                    json={},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    await resp.read()

            logger.info("Matrix-Room geschlossen: %s", room_id)
            return None
        except Exception as e:
            return f"Matrix-Room {room_id} Fehler beim Schließen: {e}"


def load_admin_token(cred_file: str = "") -> str:
    if not cred_file:
        cred_file = str(settings.admin_credentials)
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
