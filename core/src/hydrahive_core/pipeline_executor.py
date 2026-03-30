"""pipeline_executor.py — Datei-Pipeline Ausführungs-Engine (#60)

Führt eine Pipeline node-weise aus wenn eine neue Datei erkannt wurde.
Unterstützte Node-Typen:
  folder_watch  — Trigger (kein Execute, nur Konfiguration für den Watcher)
  type_filter   — Dateiendung prüfen, Pipeline abbrechen wenn kein Match
  move          — Datei verschieben
  copy          — Datei kopieren
  rename        — Umbenennen nach Muster ({name}, {ext}, {date}, {year}, {month})
  agent_task    — Agenten mit Datei beauftragen (async)
  notify        — Benachrichtigung senden
"""
from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _expand_pattern(pattern: str, file_path: Path) -> str:
    """Ersetzt Platzhalter im Muster durch Dateiinfos."""
    now = datetime.now()
    return (
        pattern
        .replace("{name}",  file_path.stem)
        .replace("{ext}",   file_path.suffix.lstrip("."))
        .replace("{date}",  now.strftime("%Y-%m-%d"))
        .replace("{year}",  now.strftime("%Y"))
        .replace("{month}", now.strftime("%m"))
        .replace("{day}",   now.strftime("%d"))
        .replace("{time}",  now.strftime("%H%M%S"))
    )


def _build_execution_order(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Topologisch sortiert: Start beim folder_watch-Node, dann entlang der Edges."""
    node_map = {n["id"]: n for n in nodes}
    # Adjacency: source → [target, ...]
    adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        src = e.get("source", "")
        tgt = e.get("target", "")
        if src in adj:
            adj[src].append(tgt)

    # Startknoten = folder_watch ohne eingehende Kante
    incoming = {e.get("target") for e in edges}
    starts = [n for n in nodes if n.get("type") == "folder_watch" and n["id"] not in incoming]
    if not starts:
        starts = [n for n in nodes if n["id"] not in incoming]

    visited: list[str] = []
    queue = [s["id"] for s in starts]
    seen: set[str] = set()
    while queue:
        nid = queue.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        visited.append(nid)
        queue.extend(adj.get(nid, []))

    return [node_map[nid] for nid in visited if nid in node_map]


async def execute_pipeline(
    pipeline: dict[str, Any],
    file_path: str,
    notify_fn: Callable | None = None,
) -> list[dict]:
    """Führt eine Pipeline für eine gegebene Datei aus.
    Gibt eine Liste von Schritt-Ergebnissen zurück."""
    nodes = pipeline.get("nodes", [])
    edges = pipeline.get("edges", [])
    order = _build_execution_order(nodes, edges)
    results: list[dict] = []
    current_path = Path(file_path)

    for node in order:
        ntype = node.get("type", "")
        ndata = node.get("data", {})
        nid   = node["id"]
        label = ndata.get("label", ntype)

        if ntype == "folder_watch":
            results.append({"node": nid, "type": ntype, "label": label, "status": "trigger", "file": str(current_path)})
            continue

        if ntype == "type_filter":
            extensions = [e.lstrip(".").lower() for e in (ndata.get("extensions") or [])]
            if extensions and current_path.suffix.lstrip(".").lower() not in extensions:
                results.append({"node": nid, "type": ntype, "label": label, "status": "filtered_out", "reason": f"Endung {current_path.suffix!r} nicht in {extensions}"})
                logger.info("Pipeline %s: Datei %s herausgefiltert (Endung)", pipeline.get("id"), current_path.name)
                break
            results.append({"node": nid, "type": ntype, "label": label, "status": "passed", "file": str(current_path)})

        elif ntype == "move":
            dest_pattern = ndata.get("destination", "")
            if not dest_pattern:
                results.append({"node": nid, "type": ntype, "label": label, "status": "skipped", "reason": "Kein Ziel angegeben"})
                continue
            dest_dir = Path(_expand_pattern(dest_pattern, current_path))
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / current_path.name
            try:
                shutil.move(str(current_path), str(dest_file))
                results.append({"node": nid, "type": ntype, "label": label, "status": "ok", "from": str(current_path), "to": str(dest_file)})
                current_path = dest_file
            except Exception as e:
                results.append({"node": nid, "type": ntype, "label": label, "status": "error", "error": str(e)})
                break

        elif ntype == "copy":
            dest_pattern = ndata.get("destination", "")
            if not dest_pattern:
                results.append({"node": nid, "type": ntype, "label": label, "status": "skipped", "reason": "Kein Ziel angegeben"})
                continue
            dest_dir = Path(_expand_pattern(dest_pattern, current_path))
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / current_path.name
            try:
                shutil.copy2(str(current_path), str(dest_file))
                results.append({"node": nid, "type": ntype, "label": label, "status": "ok", "from": str(current_path), "to": str(dest_file)})
            except Exception as e:
                results.append({"node": nid, "type": ntype, "label": label, "status": "error", "error": str(e)})

        elif ntype == "rename":
            pattern = ndata.get("pattern", "")
            if not pattern:
                results.append({"node": nid, "type": ntype, "label": label, "status": "skipped", "reason": "Kein Muster angegeben"})
                continue
            new_name = _expand_pattern(pattern, current_path)
            # Endung anhängen wenn im Muster nicht enthalten
            if not Path(new_name).suffix and current_path.suffix:
                new_name += current_path.suffix
            new_path = current_path.parent / new_name
            try:
                current_path.rename(new_path)
                results.append({"node": nid, "type": ntype, "label": label, "status": "ok", "from": current_path.name, "to": new_name})
                current_path = new_path
            except Exception as e:
                results.append({"node": nid, "type": ntype, "label": label, "status": "error", "error": str(e)})

        elif ntype == "agent_task":
            agent_id = ndata.get("agent_id", "")
            prompt   = ndata.get("prompt", "Verarbeite diese Datei: {file}")
            message  = _expand_pattern(prompt.replace("{file}", str(current_path)), current_path)
            if not agent_id:
                results.append({"node": nid, "type": ntype, "label": label, "status": "skipped", "reason": "Kein Agent angegeben"})
                continue
            try:
                from .router_agent_chat import _send_agent_message
                response = await _send_agent_message(agent_id, message)
                results.append({"node": nid, "type": ntype, "label": label, "status": "ok", "agent": agent_id, "response_preview": str(response)[:200]})
            except Exception as e:
                # Agent-Task-Fehler stoppen die Pipeline nicht
                results.append({"node": nid, "type": ntype, "label": label, "status": "error", "error": str(e)})

        elif ntype == "notify":
            msg_pattern = ndata.get("message", "Neue Datei: {file}")
            message = _expand_pattern(msg_pattern.replace("{file}", str(current_path)), current_path)
            if notify_fn:
                try:
                    await notify_fn("pipeline", message)
                    results.append({"node": nid, "type": ntype, "label": label, "status": "ok", "message": message})
                except Exception as e:
                    results.append({"node": nid, "type": ntype, "label": label, "status": "error", "error": str(e)})
            else:
                logger.info("Pipeline-Notify (kein notify_fn): %s", message)
                results.append({"node": nid, "type": ntype, "label": label, "status": "ok", "message": message})

        else:
            results.append({"node": nid, "type": ntype, "label": label, "status": "unknown_type"})

    return results


def get_watch_folders(pipelines: list[dict]) -> list[dict]:
    """Gibt alle konfigurierten Ordner-Watch-Einstellungen zurück."""
    watches = []
    for pl in pipelines:
        if not pl.get("enabled"):
            continue
        for node in pl.get("nodes", []):
            if node.get("type") == "folder_watch":
                path = node.get("data", {}).get("path", "")
                if path:
                    watches.append({
                        "pipeline_id": pl["id"],
                        "pipeline_name": pl["name"],
                        "node_id": node["id"],
                        "path": path,
                        "recursive": node.get("data", {}).get("recursive", False),
                    })
    return watches
