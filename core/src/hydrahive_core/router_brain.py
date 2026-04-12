"""
router_brain.py — HydraBrain Graph Endpoint (#80)

Liefert alle Knoten und Kanten für die 3D-Force-Graph-Visualisierung:
Agenten, Tools, Memory-Dateien, Projekte, Skills, LLM-Provider.
"""
from __future__ import annotations

import json
from pathlib import Path

from .settings import settings


def register_brain_routes(auth_router, *, discovery, runtime, projects):

    @auth_router.get("/brain-graph")
    def brain_graph():
        nodes: list[dict] = []
        links: list[dict] = []
        seen_tools:     set[str] = set()
        seen_providers: set[str] = set()

        running = runtime.status_all()

        # ── Server-Hauptknoten (Mitte des Graphen) ────────────────────────
        import socket
        server_id = "server:core"
        nodes.append({
            "id":    server_id,
            "label": socket.gethostname(),
            "type":  "server",
            "group": "server",
        })

        # ── LLM-Provider aus llm_config.json ─────────────────────────────
        llm_cfg_path = settings.llm_config
        llm_providers: dict[str, str] = {}  # model → provider label
        if llm_cfg_path.exists():
            try:
                raw = json.loads(llm_cfg_path.read_text(encoding="utf-8"))
                entries = raw if isinstance(raw, list) else [raw]
                for entry in entries:
                    provider = entry.get("provider", entry.get("type", "unknown"))
                    for m in entry.get("models", [entry.get("model", "")]):
                        if m:
                            llm_providers[m] = provider
            except Exception:
                pass

        def _ensure_provider(model: str) -> str:
            provider = llm_providers.get(model, model.split("/")[0] if "/" in model else "llm")
            node_id = f"llm:{provider}"
            if node_id not in seen_providers:
                seen_providers.add(node_id)
                nodes.append({"id": node_id, "label": provider, "type": "llm", "group": "llm"})
            return node_id

        # ── Agenten ────────────────────────────────────────────────────────
        for agent_id, cfg in discovery.agents.items():
            status     = running.get(agent_id, {}) or {}
            is_running = status.get("status") == "running"

            # Gruppe: personal_ prefix → personal, sonst cfg.type
            if agent_id.startswith("personal_"):
                group = "agent_personal"
            elif cfg.type == "boss":
                group = "agent_boss"
            else:
                group = "agent_worker"

            # Zähle Memory + Skills für Metadaten
            mem_count = 0
            skill_count = 0
            if cfg.agent_dir:
                mem_dir = Path(cfg.agent_dir) / "memory"
                if mem_dir.exists():
                    mem_count = len(list(mem_dir.glob("*.md")))
                sk_dir = Path(cfg.agent_dir) / "skills"
                if sk_dir.exists():
                    skill_count = len(list(sk_dir.glob("*.md")))

            nodes.append({
                "id":          agent_id,
                "label":       cfg.identity,
                "type":        "agent",
                "group":       group,
                "running":     is_running,
                "model":       cfg.llm.model,
                "tools_count": len(cfg.tools or []),
                "mem_count":   mem_count,
                "skill_count": skill_count,
            })

            # Server → Agent
            links.append({"source": server_id, "target": agent_id, "type": "hosts_agent"})

            # Agent → LLM-Provider
            if cfg.llm.model:
                prov_id = _ensure_provider(cfg.llm.model)
                links.append({"source": agent_id, "target": prov_id, "type": "uses_llm"})

            # Agent → Tools
            for tool_id in (cfg.tools or []):
                tool_node_id = f"tool:{tool_id}"
                if tool_node_id not in seen_tools:
                    seen_tools.add(tool_node_id)
                    nodes.append({
                        "id":    tool_node_id,
                        "label": tool_id,
                        "type":  "tool",
                        "group": "tool",
                    })
                links.append({"source": agent_id, "target": tool_node_id, "type": "has_tool"})

            # Agent → Memory-Dateien
            if cfg.agent_dir:
                memory_dir = Path(cfg.agent_dir) / "memory"
                if memory_dir.exists():
                    for mem_file in sorted(memory_dir.glob("*.md"))[:8]:
                        mem_id = f"mem:{agent_id}:{mem_file.stem}"
                        nodes.append({
                            "id":    mem_id,
                            "label": mem_file.stem,
                            "type":  "memory",
                            "group": "memory",
                        })
                        links.append({"source": agent_id, "target": mem_id, "type": "has_memory"})

                # Agent → Skills
                skills_dir = Path(cfg.agent_dir) / "skills"
                if skills_dir.exists():
                    for skill_file in sorted(skills_dir.glob("*.md"))[:6]:
                        skill_id = f"skill:{skill_file.stem}"
                        if skill_id not in seen_tools:
                            seen_tools.add(skill_id)
                            nodes.append({
                                "id":    skill_id,
                                "label": skill_file.stem,
                                "type":  "skill",
                                "group": "skill",
                            })
                        links.append({"source": agent_id, "target": skill_id, "type": "has_skill"})

        # ── Projekte (v2: Projekt = Agent) ──────────────────────────────────
        # v2-Projekte bekommen dieselben reichen Verbindungen wie Agenten:
        # LLM-Provider, Core-Tools, Memory, AGENT.md
        V2_CORE_TOOLS = [
            "shell_exec", "file_read", "file_write", "file_patch",
            "file_search", "web_search", "read_memory", "write_memory", "ask_agent",
        ]
        for pid, cfg in projects.projects.items():
            proj_id = f"proj:{pid}"
            status     = running.get(pid, {}) or {}
            is_running = status.get("status") == "running"

            mem_count = 0
            proj_dir = getattr(cfg, "project_dir", None)
            if proj_dir:
                mem_dir = Path(proj_dir) / "memory"
                if mem_dir.exists():
                    mem_count = len(list(mem_dir.glob("*.md")))

            llm_model = getattr(getattr(cfg, "llm", None), "model", "") or ""

            nodes.append({
                "id":          proj_id,
                "label":       cfg.identity.name if hasattr(cfg.identity, "name") else pid,
                "type":        "project",
                "group":       "project",
                "running":     is_running,
                "model":       llm_model,
                "tools_count": 9,
                "mem_count":   mem_count,
            })

            # Server → Projekt
            links.append({"source": server_id, "target": proj_id, "type": "hosts_project"})

            # Projekt → LLM-Provider
            if llm_model:
                prov_id = _ensure_provider(llm_model)
                links.append({"source": proj_id, "target": prov_id, "type": "uses_llm"})

            # Projekt → Core-Tools
            for tool_id in V2_CORE_TOOLS:
                tool_node_id = f"tool:{tool_id}"
                if tool_node_id not in seen_tools:
                    seen_tools.add(tool_node_id)
                    nodes.append({
                        "id":    tool_node_id,
                        "label": tool_id,
                        "type":  "tool",
                        "group": "tool",
                    })
                links.append({"source": proj_id, "target": tool_node_id, "type": "has_tool"})

            # Projekt → Memory-Dateien
            if proj_dir:
                memory_dir = Path(proj_dir) / "memory"
                if memory_dir.exists():
                    for mem_file in sorted(memory_dir.glob("*.md"))[:8]:
                        mem_id = f"mem:{pid}:{mem_file.stem}"
                        nodes.append({
                            "id":    mem_id,
                            "label": mem_file.stem,
                            "type":  "memory",
                            "group": "memory",
                        })
                        links.append({"source": proj_id, "target": mem_id, "type": "has_memory"})

            # Legacy v1: Boss/Worker-Links (falls noch gesetzt)
            boss = cfg.agents.boss if hasattr(cfg, "agents") else None
            if boss:
                links.append({"source": proj_id, "target": boss, "type": "has_boss"})
            workers = cfg.agents.workers if hasattr(cfg, "agents") else []
            for w in (workers or []):
                links.append({"source": proj_id, "target": w, "type": "has_worker"})

        # Nur Links zurückgeben deren source UND target als Node existieren
        node_ids = {n["id"] for n in nodes}
        valid_links = [
            lnk for lnk in links
            if lnk["source"] in node_ids and lnk["target"] in node_ids
        ]

        return {"nodes": nodes, "links": valid_links}
