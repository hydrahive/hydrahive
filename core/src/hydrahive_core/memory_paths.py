"""Canonical memory paths for project-backed agents (#706).

The v2 runtime treats a project as the agent workspace. Persistent memory is
therefore stored under ``/projects/<id>/memory``. Legacy ``/agents/<id>/memory``
is only used by the migration path.
"""

from __future__ import annotations

from pathlib import Path

from .settings import settings

MEMORY_INDEX_FILENAME = "MEMORY.md"
LEGACY_MEMORY_INDEX_FILENAME = "INDEX.md"


def _root(path: str | Path | None, fallback: Path) -> Path:
    return Path(path) if path is not None else fallback


def canonical_memory_owner(agent_id: str, project_id: str | None = None) -> str:
    """Return the project id whose directory owns this agent's memory."""
    return (project_id or agent_id).strip()


def agent_memory_dir(
    agent_id: str,
    project_id: str | None = None,
    *,
    projects_root: str | Path | None = None,
) -> Path:
    """Canonical memory dir for an agent/tool call."""
    owner = canonical_memory_owner(agent_id, project_id)
    return _root(projects_root, settings.projects_dir) / owner / "memory"


def memory_index_db_path(
    agent_id: str,
    project_id: str | None = None,
    *,
    projects_root: str | Path | None = None,
) -> Path:
    """BM25/decay metadata DB path colocated with canonical project memory."""
    owner = canonical_memory_owner(agent_id, project_id)
    return _root(projects_root, settings.projects_dir) / owner / "memory_index.db"


def legacy_agent_memory_dir(
    agent_id: str,
    *,
    agents_root: str | Path | None = None,
) -> Path:
    """Legacy v1 memory dir. Use only for migration/diagnostics."""
    return _root(agents_root, settings.agents_dir) / agent_id / "memory"
