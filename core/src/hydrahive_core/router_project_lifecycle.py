from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .matrix_agent import BossMatrixAgent
from .settings import settings


class TokenBudgetRequest(BaseModel):
    """#820: Body für PUT /admin/projects/{id}/token-budget.
    None / fehlt = globaler Default greift; 0 = Limit deaktiviert."""
    model_config = {"extra": "ignore"}
    hard_per_hour: int | None = None
    warn_per_hour: int | None = None


def update_project_matrix_space(projects_dir: str, project_id: str, space_id: str, *, logger) -> None:
    import yaml as _yaml
    project_yaml = Path(projects_dir) / project_id / "project.yaml"
    if not project_yaml.exists():
        return
    try:
        data = _yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
        data.setdefault("matrix", {})["space"] = space_id
        project_yaml.write_text(_yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    except OSError as e:
        logger.warning("project.yaml (space) konnte nicht aktualisiert werden: %s", e)


def update_project_matrix_room(projects_dir: str, project_id: str, room_id: str, *, logger) -> None:
    import re

    project_yaml = Path(projects_dir) / project_id / "project.yaml"
    if not project_yaml.exists():
        return
    try:
        content = project_yaml.read_text(encoding="utf-8")
        # Beide Quote-Varianten matchen: room: "" und room: ''
        updated = re.sub(r"""(room:\s*)(''|"")""", f'\\1"{room_id}"', content)
        if updated != content:
            project_yaml.write_text(updated, encoding="utf-8")
            return
        updated = re.sub(r"(matrix:\s*\n)", f'\\1  room: "{room_id}"\n', content)
        if updated != content:
            project_yaml.write_text(updated, encoding="utf-8")
            return
        updated = content.rstrip() + f'\nmatrix:\n  room: "{room_id}"\n'
        project_yaml.write_text(updated, encoding="utf-8")
    except OSError as e:
        logger.warning("project.yaml konnte nicht aktualisiert werden: %s", e)


def register_project_lifecycle_routes(
    admin_router: APIRouter,
    *,
    require_admin,
    projects,
    runtime,
    discovery,
    orchestrator,
    projects_dir: str,
    get_provisioner,
    read_server_name,
    audit_log,
    logger,
) -> None:
    @admin_router.delete("/projects/{project_id}")
    async def delete_project(project_id: str, _a: tuple = Depends(require_admin)):
        import shutil as _shutil
        import time as _time

        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

        project_dir = Path(projects_dir) / project_id
        if not project_dir.exists():
            raise HTTPException(404, "Projektverzeichnis nicht gefunden")

        stopped_agents = []
        boss_id = cfg.agents.boss
        if await runtime.stop_agent_task(boss_id):
            stopped_agents.append(boss_id)

        _provisioner = get_provisioner()
        if _provisioner:
            deprov_warnings = await _provisioner.deprovision(cfg)
            for w in deprov_warnings:
                logger.warning("deprovision warning: %s", w)

        deleted_root = settings.deleted_projects_dir
        try:
            deleted_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(500, f"Deleted-projects-Verzeichnis nicht nutzbar: {e}") from e

        timestamp = int(_time.time())
        deleted_dir = deleted_root / f'{project_id}.{timestamp}'
        suffix = 0
        while deleted_dir.exists():
            suffix += 1
            deleted_dir = deleted_root / f'{project_id}.{timestamp}.{suffix}'
        try:
            _shutil.move(str(project_dir), str(deleted_dir))
        except OSError as e:
            raise HTTPException(500, f"Projekt konnte nicht verschoben werden: {e}") from e

        # Aus In-Memory-Registry entfernen damit create_project danach wieder funktioniert
        projects._unregister_dir(project_dir)

        audit_log('project.delete', target=project_id, project_id=project_id, details={'moved_to': str(deleted_dir), 'stopped_agents': stopped_agents})
        logger.info('Projekt geloescht: %s -> %s', project_id, deleted_dir)
        return {
            'deleted': True,
            'project_id': project_id,
            'moved_to': str(deleted_dir),
            'stopped_agents': stopped_agents,
        }

    @admin_router.post("/projects/{project_id}/provision")
    async def provision_project(project_id: str, _a: tuple = Depends(require_admin)):
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        provisioner = get_provisioner()
        if provisioner is None:
            raise HTTPException(503, 'Provisioner nicht initialisiert')

        result = await provisioner.provision(cfg)
        if result.matrix_room and not cfg.matrix.room:
            update_project_matrix_room(projects_dir, project_id, result.matrix_room, logger=logger)
        if result.matrix_space and not cfg.matrix.space:
            update_project_matrix_space(projects_dir, project_id, result.matrix_space, logger=logger)

        room_id = result.matrix_room or cfg.matrix.room
        if room_id:
            boss_cfg = discovery.get(cfg.agents.boss)
            if boss_cfg:
                matrix_client = BossMatrixAgent(
                    config=boss_cfg,
                    server_name=read_server_name(),
                    rooms=[room_id],
                    orchestrator=orchestrator,
                    project_cfg=cfg,
                )
                await runtime.attach_matrix_client(boss_cfg.id, matrix_client)
                logger.info('Matrix-Client nach Provisioning gestartet: %s -> %s', boss_cfg.id, room_id)

        return {
            'project_id': result.project_id,
            'linux_user': result.linux_user,
            'files_dir': result.files_dir,
            'samba_share': result.samba_share,
            'matrix_room': result.matrix_room,
            'matrix_space': result.matrix_space,
            'warnings': result.warnings,
            'ok': result.ok,
        }

    @admin_router.delete("/projects/{project_id}/provision")
    async def deprovision_project(project_id: str, _a: tuple = Depends(require_admin)):
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        provisioner = get_provisioner()
        if provisioner is None:
            raise HTTPException(503, 'Provisioner nicht initialisiert')

        warnings = await provisioner.deprovision(cfg)
        return {'project_id': project_id, 'deprovisioned': True, 'warnings': warnings}

    @admin_router.post("/projects/{project_id}/matrix-invite")
    async def matrix_invite_members(project_id: str, _a: tuple = Depends(require_admin)):
        """Lädt alle konfigurierten members in den Matrix-Room und Space ein."""
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        room_id = cfg.matrix.room if cfg.matrix else None
        if not room_id:
            raise HTTPException(409, "Kein Matrix-Room konfiguriert — erst provisionieren")
        provisioner = get_provisioner()
        if provisioner is None:
            raise HTTPException(503, 'Provisioner nicht initialisiert')

        server_name = read_server_name()
        space_id = cfg.matrix.space if cfg.matrix else None
        invited, warnings = [], []
        import aiohttp as _aio
        headers = {
            "Authorization": f"Bearer {provisioner._token}",
            "Content-Type": "application/json",
        }
        members = list(getattr(cfg, "members", []))
        async with _aio.ClientSession() as session:
            for username in members:
                mxid = f"@{username}:{server_name}"
                for target_room in filter(None, [room_id, space_id]):
                    try:
                        async with session.post(
                            f"http://localhost:8008/_matrix/client/v3/rooms/{target_room}/invite",
                            headers=headers,
                            json={"user_id": mxid},
                            timeout=_aio.ClientTimeout(total=10),
                        ) as resp:
                            data = await resp.json(content_type=None)
                            if resp.status in (200, 403) or data.get("errcode") in ("M_FORBIDDEN", "M_LIMIT_EXCEEDED"):
                                if target_room == room_id:
                                    invited.append(mxid)
                            else:
                                warnings.append(f"{mxid}@{target_room}: {data.get('error', resp.status)}")
                    except Exception as e:
                        warnings.append(f"{mxid}@{target_room}: {e}")

        return {'project_id': project_id, 'room_id': room_id, 'space_id': space_id, 'invited': invited, 'warnings': warnings}

    @admin_router.get("/projects/{project_id}/samba-credentials")
    async def get_samba_credentials(project_id: str, _a: tuple = Depends(require_admin)):
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        provisioner = get_provisioner()
        if provisioner is None:
            raise HTTPException(503, 'Provisioner nicht initialisiert')
        username = cfg.effective_system_user()
        password = provisioner._read_samba_password(username)
        if password is None:
            raise HTTPException(404, 'Keine Samba-Credentials gefunden — Provisioning erforderlich')
        return {'project_id': project_id, 'username': username, 'password': password}

    @admin_router.post("/projects/{project_id}/samba-reset-password")
    async def reset_samba_password(project_id: str, _a: tuple = Depends(require_admin)):
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        provisioner = get_provisioner()
        if provisioner is None:
            raise HTTPException(503, 'Provisioner nicht initialisiert')
        username = cfg.effective_system_user()
        error, new_password = provisioner.reset_samba_password(username)
        if error:
            logger.error("Samba-Passwort-Reset fehlgeschlagen für %s: %s", username, error)
            raise HTTPException(500, "Samba-Passwort konnte nicht zurückgesetzt werden")
        return {'project_id': project_id, 'username': username, 'password': new_password}

    @admin_router.post("/projects/{project_id}/fix-permissions")
    async def fix_project_permissions(project_id: str, _a: tuple = Depends(require_admin)):
        """Setzt Dateiberechtigungen für ein Projekt (nach Samba-Upload etc.)."""
        import asyncio
        import subprocess
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        proj_dir = Path(f"/projects/{project_id}")
        if not proj_dir.is_dir():
            raise HTTPException(404, "Projektverzeichnis nicht gefunden")
        try:
            await asyncio.to_thread(lambda: subprocess.run(
                ["sudo", "chgrp", "-R", "hydrahive", str(proj_dir)],
                capture_output=True, check=True, timeout=60))
            await asyncio.to_thread(lambda: subprocess.run(
                ["sudo", "chmod", "-R", "g+rw", str(proj_dir)],
                capture_output=True, check=True, timeout=60))
            return {"ok": True, "project_id": project_id}
        except Exception as e:
            logger.error("Berechtigungen setzen fehlgeschlagen für %s: %s", project_id, e)
            raise HTTPException(500, "Berechtigungen konnten nicht gesetzt werden")

    # ────────────────────────────────────────────────────────────────────
    # #820: Pro-Projekt Token-Budget Override (überschreibt globalen Default)
    # ────────────────────────────────────────────────────────────────────

    @admin_router.get("/projects/{project_id}/token-budget")
    async def get_project_token_budget(project_id: str, _a: tuple = Depends(require_admin)):
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        tb = getattr(cfg, "token_budget", None)
        return {
            "project_id": project_id,
            "hard_per_hour": getattr(tb, "hard_per_hour", None),
            "warn_per_hour": getattr(tb, "warn_per_hour", None),
        }

    @admin_router.put("/projects/{project_id}/token-budget")
    async def set_project_token_budget(
        project_id: str,
        body: TokenBudgetRequest,
        _a: tuple = Depends(require_admin),
    ):
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        # Validation: negative Werte → 400 (None und 0 sind ok).
        for label, val in (("hard_per_hour", body.hard_per_hour), ("warn_per_hour", body.warn_per_hour)):
            if val is not None and val < 0:
                raise HTTPException(400, f"{label} muss >= 0 sein (0 = deaktiviert, leer = globaler Default)")

        # config.yaml laden, token_budget patchen, atomar speichern.
        config_path = Path(projects_dir) / project_id / "config.yaml"
        if not config_path.exists():
            raise HTTPException(404, "config.yaml nicht gefunden — kein v2-Projekt")
        import yaml as _yaml
        try:
            data = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            tb_section: dict = {}
            if body.hard_per_hour is not None:
                tb_section["hard_per_hour"] = int(body.hard_per_hour)
            if body.warn_per_hour is not None:
                tb_section["warn_per_hour"] = int(body.warn_per_hour)
            if tb_section:
                data["token_budget"] = tb_section
            else:
                # Beide None → Eintrag entfernen (back to global default).
                data.pop("token_budget", None)
            tmp = config_path.with_suffix(".yaml.tmp")
            tmp.write_text(_yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
            tmp.replace(config_path)
        except Exception as e:
            logger.error("token_budget für '%s' konnte nicht geschrieben werden: %s", project_id, e)
            raise HTTPException(500, f"Schreiben fehlgeschlagen: {e}")

        # ProjectLoader cache invalidieren — neu registrieren
        try:
            projects._register(config_path.parent)
        except Exception as e:
            logger.warning("ProjectLoader-Reload für '%s' nach token_budget-Update fehlgeschlagen: %s", project_id, e)

        audit_log("project.token_budget_set", target=project_id, details={
            "hard_per_hour": body.hard_per_hour,
            "warn_per_hour": body.warn_per_hour,
        })
        return {
            "updated": True,
            "project_id": project_id,
            "hard_per_hour": body.hard_per_hour,
            "warn_per_hour": body.warn_per_hour,
        }

    @admin_router.post("/projects/reprovision-all")
    async def reprovision_all_projects(_a: tuple = Depends(require_admin)):
        """
        #813: Self-healing Reconcile. Stellt Linux-User + Samba-Share für
        jedes bekannte Projekt sicher. Heilt Migrations-Lücken nach
        Server-Neuinstallation + Daten-Restore.
        """
        import asyncio
        provisioner = get_provisioner()
        if provisioner is None:
            raise HTTPException(503, 'Provisioner nicht initialisiert')
        report = await asyncio.to_thread(provisioner.reconcile_all_projects, projects)
        logger.info(
            "Reprovision-All: %d reconciled, %d skipped, %d errors",
            len(report.get("reconciled", [])),
            len(report.get("skipped", [])),
            len(report.get("errors", [])),
        )
        return {"ok": True, "report": report}
