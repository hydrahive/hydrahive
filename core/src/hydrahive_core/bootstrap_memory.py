"""
bootstrap_memory.py — P1 Bootstrap-Memory für neue/bestehende Projekte (#614)

Scannt ein Projekt-Verzeichnis und schreibt eine strukturierte Memory-Basis:
  - memory/project_structure.md  (Verzeichnisbaum, wichtige Dateien)
  - memory/INDEX.md              (nur anlegen wenn nicht vorhanden)
  - memory/.bootstrap_done       (Sentinel — verhindert Wiederholung)

Wird aufgerufen via:
  POST /projects/{id}/bootstrap-memory          (on-demand, User-Button)
  create_project_v2 nach Projektanlage          (P4, automatisch)
"""

import asyncio
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_BOOTSTRAP_SENTINEL = ".bootstrap_done"
_MAX_STRUCTURE_CHARS = 3000


def is_bootstrap_done(project_dir: Path) -> bool:
    return (project_dir / "memory" / _BOOTSTRAP_SENTINEL).exists()


def _mark_bootstrap_done(project_dir: Path) -> None:
    sentinel = project_dir / "memory" / _BOOTSTRAP_SENTINEL
    sentinel.touch()
    try:
        os.chmod(sentinel, 0o600)
    except Exception:
        pass


def _write_memory_file(memory_dir: Path, filename: str, content: str, *, overwrite: bool = True) -> bool:
    """Schreibt eine Memory-Datei. Gibt False zurück wenn nicht geschrieben (overwrite=False + existiert)."""
    path = memory_dir / filename
    if not overwrite and path.exists():
        return False
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return True


async def _scan_repo_structure(files_dir: Path, project_dir: Path) -> str:
    """
    Liest Verzeichnisbaum via 'git ls-files' (Timeout 5s) oder os.walk (Fallback).
    Gibt Markdown-String zurück (max ~3000 Zeichen).
    """
    lines: list[str] = []

    # Versuche zuerst git ls-files wenn .git vorhanden
    git_dir = files_dir if (files_dir / ".git").exists() else None
    if not git_dir and (project_dir / "files" / ".git").exists():
        git_dir = project_dir / "files"

    if git_dir and git_dir.exists():
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "ls-files", "--", ".",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(git_dir),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            git_files = stdout.decode(errors="replace").strip().splitlines()
            # Nur Pfade die interessant sind (keine Binaries, keine tiefen Unterordner)
            filtered = [f for f in git_files if not f.endswith((".png", ".jpg", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map"))]
            # Verzeichnisse extrahieren
            dirs: set[str] = set()
            for f in filtered:
                parts = Path(f).parts
                for i in range(1, len(parts)):
                    dirs.add("/".join(parts[:i]))
            lines.append(f"Git-Repo: {git_dir.name}/ ({len(git_files)} Dateien total)")
            lines.append("")
            # Top-Level Struktur
            top_dirs = sorted({p.split("/")[0] for p in filtered if "/" in p} | {Path(f).name for f in filtered if "/" not in f})
            lines.append("**Top-Level:**")
            for d in top_dirs[:30]:
                lines.append(f"  {d}/")
            lines.append("")
            # Wichtige Dateien direkt auflisten
            important = [f for f in filtered if Path(f).name in {
                "README.md", "readme.md", "package.json", "pyproject.toml",
                "setup.py", "Makefile", "docker-compose.yml", "Dockerfile",
                "requirements.txt", ".env.example", "config.yaml", "config.json",
            }]
            if important:
                lines.append("**Wichtige Dateien:**")
                for f in important[:10]:
                    lines.append(f"  {f}")
            return "\n".join(lines)
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug("git ls-files fehlgeschlagen, nutze os.walk: %s", e)

    # Fallback: os.walk mit Tiefenlimit
    scan_root = files_dir if files_dir.exists() else project_dir
    lines.append(f"Verzeichnis: {scan_root.name}/")
    lines.append("")

    entry_count = 0
    for root, dirs, files in os.walk(scan_root):
        # Tiefe berechnen
        rel = Path(root).relative_to(scan_root)
        depth = len(rel.parts)
        if depth > 3:
            dirs.clear()
            continue
        # Versteckte + node_modules überspringen
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".") and d != "node_modules" and d != "__pycache__"]
        indent = "  " * depth
        folder_name = Path(root).name if depth > 0 else scan_root.name
        if depth > 0:
            lines.append(f"{indent}{folder_name}/")
        for f in sorted(files)[:20]:
            if not f.startswith("."):
                lines.append(f"  {indent}{f}")
                entry_count += 1
        if entry_count > 150:
            lines.append("  ... (weitere Dateien gekürzt)")
            break

    return "\n".join(lines)


async def _read_agent_md(project_dir: Path) -> str:
    agent_md = project_dir / "AGENT.md"
    if agent_md.exists():
        try:
            return agent_md.read_text(encoding="utf-8", errors="replace")[:2000]
        except Exception:
            pass
    return ""


async def bootstrap_project_memory(
    project_id: str,
    project_dir: Path,
    *,
    force: bool = False,
) -> dict:
    """
    Scannt ein Projekt und schreibt strukturierte Memory-Basis.
    Gibt {'ok': True, 'files_written': [...], 'skipped': bool} zurück.
    """
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    if not force and is_bootstrap_done(project_dir):
        return {"ok": True, "skipped": True, "reason": "Bootstrap bereits erledigt (force=true zum Wiederholen)"}

    files_written: list[str] = []
    files_dir = project_dir / "files"

    try:
        # 1. Verzeichnisstruktur scannen
        structure = await _scan_repo_structure(files_dir, project_dir)
        agent_md_text = await _read_agent_md(project_dir)

        # 2. project_structure.md schreiben (immer)
        now_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        structure_content = f"""---
name: Projekt-Struktur
description: Verzeichnisbaum und wichtige Dateien — automatisch erstellt beim Bootstrap
type: project
---

# Projekt-Struktur: {project_id}
*Erstellt: {now_str}*

## Verzeichnisbaum

```
{structure[:_MAX_STRUCTURE_CHARS]}
{"..." if len(structure) > _MAX_STRUCTURE_CHARS else ""}
```
"""
        if agent_md_text:
            structure_content += f"\n## Agent-Kontext (aus AGENT.md)\n\n{agent_md_text[:800]}\n"

        _write_memory_file(memory_dir, "project_structure.md", structure_content, overwrite=True)
        files_written.append("project_structure.md")

        # 3. INDEX.md anlegen — NUR wenn nicht vorhanden (Agent pflegt sie selbst)
        index_written = _write_memory_file(
            memory_dir,
            "INDEX.md",
            f"# Memory-Index — {project_id}\n\n- [project_structure.md](project_structure.md) — Verzeichnisbaum, automatisch erstellt\n",
            overwrite=False,
        )
        if index_written:
            files_written.append("INDEX.md")

        # 4. Berechtigungen setzen
        try:
            import subprocess
            subprocess.run(
                ["chown", "-R", "hydrahive:hydrahive", str(memory_dir)],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

        # 5. Sentinel setzen
        _mark_bootstrap_done(project_dir)

        logger.info("bootstrap_memory: %s — %d Dateien geschrieben", project_id, len(files_written))
        return {"ok": True, "skipped": False, "files_written": files_written, "project_id": project_id}

    except Exception as e:
        logger.exception("bootstrap_memory fehlgeschlagen fuer %s: %s", project_id, e)
        return {"ok": False, "skipped": False, "error": str(e), "project_id": project_id}
