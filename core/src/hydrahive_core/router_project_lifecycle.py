from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from .matrix_agent import BossMatrixAgent


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
        import time as _time

        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

        project_dir = Path(projects_dir) / project_id
        if not project_dir.exists():
            raise HTTPException(404, "Projektverzeichnis nicht gefunden")

        stopped_agents = []
        boss_id = cfg.agents.boss
        handle = runtime.get_handle(boss_id)
        if handle:
            await runtime.stop_agent(boss_id)
            stopped_agents.append(boss_id)

        _provisioner = get_provisioner()
        if _provisioner:
            deprov_warnings = await _provisioner.deprovision(cfg)
            for w in deprov_warnings:
                logger.warning("deprovision warning: %s", w)

        timestamp = int(_time.time())
        deleted_dir = Path(projects_dir) / f'_deleted_{project_id}_{timestamp}'
        project_dir.rename(deleted_dir)

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
            raise HTTPException(500, error)
        return {'project_id': project_id, 'username': username, 'password': new_password}
