"""
repo_config.py — Zentrale Repo-Verwaltung

Speichert Git-Repos mit Credentials und Agent/Projekt-Zuweisungen.
Datei: /etc/hydrahive/repos.json (chmod 600)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

REPOS_FILE = Path("/etc/hydrahive/repos.json")


class RepoConfig(BaseModel):
    id: str
    name: str
    url: str                                          # https://github.com/org/repo
    token: str = ""                                   # PAT oder leer für public
    branch: str = "main"
    provider: Literal["github", "gitea", "gitlab", "other"] = "github"
    agents: list[str] = Field(default_factory=list)   # Agent-IDs
    projects: list[str] = Field(default_factory=list) # Projekt-IDs


def _load_raw() -> list[dict]:
    if not REPOS_FILE.exists():
        return []
    try:
        return json.loads(REPOS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("repos.json Lesefehler: %s", e)
        return []


def _save_raw(data: list[dict]) -> None:
    REPOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPOS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    REPOS_FILE.chmod(0o600)


def load_repos() -> list[RepoConfig]:
    return [RepoConfig(**r) for r in _load_raw()]


def save_repos(repos: list[RepoConfig]) -> None:
    _save_raw([r.model_dump() for r in repos])


def get_repo(repo_id: str) -> RepoConfig | None:
    for r in load_repos():
        if r.id == repo_id:
            return r
    return None


def upsert_repo(repo: RepoConfig) -> None:
    repos = load_repos()
    for i, r in enumerate(repos):
        if r.id == repo.id:
            repos[i] = repo
            save_repos(repos)
            return
    repos.append(repo)
    save_repos(repos)


def delete_repo(repo_id: str) -> bool:
    repos = load_repos()
    filtered = [r for r in repos if r.id != repo_id]
    if len(filtered) == len(repos):
        return False
    save_repos(filtered)
    return True


def repos_for_agent(agent_id: str) -> list[RepoConfig]:
    """Alle Repos die einem Agent zugewiesen sind."""
    return [r for r in load_repos() if agent_id in r.agents]


def repos_for_project(project_id: str) -> list[RepoConfig]:
    """Alle Repos die einem Projekt zugewiesen sind."""
    return [r for r in load_repos() if project_id in r.projects]
