from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


class SkillRequest(BaseModel):
    filename: str
    skill: str
    version: str = "1.0"
    scope: str = "on-demand"
    triggers: list[str] = []
    priority: int = 50
    content: str = ""


def _skills_dir(agents_dir: str, agent_id: str) -> Path:
    return Path(agents_dir) / agent_id / "skills"


def _parse_skill_file(path: Path) -> dict:
    import re as _re
    import yaml as _yaml

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    m = _re.match(r"^---\s*\n(.*?)\n---\s*\n", text, _re.DOTALL)
    if not m:
        return {
            "filename": path.stem,
            "skill": path.stem,
            "version": "1.0",
            "scope": "on-demand",
            "triggers": [],
            "priority": 50,
            "content": text.strip(),
        }

    try:
        meta = _yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}

    result: dict = {
        "filename": path.stem,
        "skill": meta.get("skill", path.stem),
        "version": str(meta.get("version", "1.0")),
        "scope": meta.get("scope", "on-demand"),
        "triggers": meta.get("triggers", []) or [],
        "priority": int(meta.get("priority", 50)),
        "content": text[m.end():].strip(),
    }
    if "author" in meta:
        result["author"] = str(meta["author"])
    return result


def _write_skill_file(skills_dir: Path, filename: str, data: dict) -> Path:
    import yaml as _yaml

    skills_dir.mkdir(parents=True, exist_ok=True)
    safe = filename.replace(".md", "").replace("/", "-").replace("..", "")
    if not safe:
        raise ValueError("Ungueltiger Dateiname")
    path = skills_dir / f"{safe}.md"

    frontmatter = {
        "skill": data.get("skill", safe),
        "version": data.get("version", "1.0"),
        "scope": data.get("scope", "on-demand"),
        "priority": int(data.get("priority", 50)),
    }
    triggers = data.get("triggers", [])
    if triggers:
        frontmatter["triggers"] = triggers

    yaml_str = _yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
    content = data.get("content", "").strip()
    path.write_text(f"---\n{yaml_str}---\n\n{content}\n", encoding="utf-8")
    return path


def register_agent_skill_routes(
    auth_router: APIRouter,
    *,
    require_auth,
    check_agent_write,
    agents_dir: str,
    logger,
) -> None:
    @auth_router.get("/agents/{agent_id}/skills")
    def list_agent_skills(agent_id: str, _a: tuple[str, str] = Depends(require_auth)):
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        skills_dir = _skills_dir(agents_dir, agent_id)
        if not skills_dir.exists():
            return {"agent_id": agent_id, "skills": []}

        skills = []
        for path in sorted(skills_dir.glob("*.md")):
            skill = _parse_skill_file(path)
            if skill:
                skills.append(skill)

        skills.sort(key=lambda s: s.get("priority", 50))
        return {"agent_id": agent_id, "skills": skills}

    @auth_router.post("/agents/{agent_id}/skills", status_code=201)
    def create_agent_skill(agent_id: str, req: SkillRequest, auth: tuple = Depends(require_auth)):
        check_agent_write(agent_id, auth)
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        skills_dir = _skills_dir(agents_dir, agent_id)
        target = skills_dir / f"{req.filename.replace('.md', '')}.md"
        if target.exists():
            raise HTTPException(409, f"Skill '{req.filename}' existiert bereits")

        path = _write_skill_file(skills_dir, req.filename, req.model_dump())
        logger.info("Skill angelegt: %s/%s", agent_id, path.name)
        return {"created": True, "agent_id": agent_id, "filename": path.stem}

    @auth_router.put("/agents/{agent_id}/skills/{filename}")
    def update_agent_skill(agent_id: str, filename: str, req: SkillRequest, auth: tuple = Depends(require_auth)):
        check_agent_write(agent_id, auth)
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        path = _write_skill_file(_skills_dir(agents_dir, agent_id), filename, req.model_dump())
        logger.info("Skill aktualisiert: %s/%s", agent_id, path.name)
        return {"updated": True, "agent_id": agent_id, "filename": path.stem}

    @auth_router.delete("/agents/{agent_id}/skills/{filename}")
    def delete_agent_skill(agent_id: str, filename: str, auth: tuple = Depends(require_auth)):
        check_agent_write(agent_id, auth)
        safe = filename.replace(".md", "").replace("/", "-").replace("..", "")
        path = _skills_dir(agents_dir, agent_id) / f"{safe}.md"
        if not path.exists():
            raise HTTPException(404, f"Skill '{filename}' nicht gefunden")
        path.unlink()
        logger.info("Skill geloescht: %s/%s", agent_id, safe)
        return {"deleted": True, "agent_id": agent_id, "filename": safe}
