"""router_hub.py — HydraHub API

GET  /hub/index                    → Index vom GitHub-Repo (gecacht 5 Min)
POST /hub/install                  → Agent aus Hub installieren
GET  /hub/installed                → Liste aller installierten Hub-Pakete
GET  /hub/clawhub/skills?q=...     → ClawhHub Skills suchen / browsen
GET  /hub/clawhub/packages?family= → ClawhHub Plugins browsen
POST /hub/clawhub/skill/install    → ClawhHub Skill in Agent-Skills importieren
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

# ClawhHub Binary-Cache (Modulebene damit nonlocal in nested functions funktioniert)
_CLAWHUB_BIN: str | None = None

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

HUB_INDEX_URL = "https://raw.githubusercontent.com/hydrahive/hub/main/index.json"
HUB_RAW_BASE  = "https://raw.githubusercontent.com/hydrahive/hub/main"
CACHE_TTL     = 300  # 5 Minuten

_index_cache: dict[str, Any] = {}
_cache_ts: float = 0.0


def _fetch_text(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "HydraHive/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _get_index() -> dict[str, Any]:
    global _index_cache, _cache_ts
    if time.time() - _cache_ts < CACHE_TTL and _index_cache:
        return _index_cache
    try:
        raw = _fetch_text(HUB_INDEX_URL)
        _index_cache = json.loads(raw)
        _cache_ts = time.time()
    except Exception as e:
        logger.warning("Hub-Index konnte nicht geladen werden: %s", e)
        if not _index_cache:
            raise HTTPException(503, f"Hub nicht erreichbar: {e}")
    return _index_cache


class InstallRequest(BaseModel):
    id: str
    agent_id_override: str | None = None   # optionale eigene Agent-ID
    model_override: str | None = None


class ClawhubInstallRequest(BaseModel):
    slug:     str
    agent_id: str
    force:    bool = False


def register_hub_routes(router: APIRouter, require_admin, agents_dir: str, discovery=None) -> None:

    @router.get("/hub/index")
    async def hub_index(_auth=Depends(require_admin)):
        return _get_index()

    @router.get("/hub/installed")
    async def hub_installed(_auth=Depends(require_admin)):
        """Gibt zurück welche Hub-Pakete bereits installiert sind (Agents + Plugins)."""
        installed = []
        # Agents
        base = Path(agents_dir)
        if base.exists():
            for agent_dir in base.iterdir():
                meta_path = agent_dir / ".hub_meta.json"
                if meta_path.exists():
                    try:
                        installed.append(json.loads(meta_path.read_text()))
                    except Exception:
                        pass
        # Plugins
        plugins_base = Path("/plugins")
        if plugins_base.exists():
            for plugin_dir in plugins_base.iterdir():
                meta_path = plugin_dir / ".hub_meta.json"
                if meta_path.exists():
                    try:
                        installed.append(json.loads(meta_path.read_text()))
                    except Exception:
                        pass
        return installed

    async def _hub_install_plugin(pkg: dict, pkg_path: str):
        """Installiert ein Plugin vom Hub nach /plugins/<id>/."""
        plugin_id = pkg["id"]
        target = Path("/plugins") / plugin_id
        if target.exists():
            raise HTTPException(409, f"Plugin '{plugin_id}' existiert bereits")

        # Dateien vom Hub laden
        try:
            plugin_yaml = _fetch_text(f"{HUB_RAW_BASE}/{pkg_path}/plugin.yaml")
            plugin_py = _fetch_text(f"{HUB_RAW_BASE}/{pkg_path}/plugin.py")
        except Exception as e:
            raise HTTPException(502, f"Plugin-Dateien konnten nicht geladen werden: {e}")

        # Verzeichnis anlegen + Dateien schreiben
        target.mkdir(parents=True, exist_ok=True)
        (target / "plugin.yaml").write_text(plugin_yaml, encoding="utf-8")
        (target / "plugin.py").write_text(plugin_py, encoding="utf-8")
        (target / ".hub_meta.json").write_text(
            json.dumps({**pkg, "installed_plugin_id": plugin_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Berechtigungen
        try:
            subprocess.run(["chown", "-R", "hydrahive:hydrahive", str(target)], check=False, capture_output=True)
        except Exception:
            pass

        # Plugin sofort laden — erst discover, dann enable
        from .plugin_manager import plugin_manager, PluginManifest, LoadedPlugin
        try:
            manifest = PluginManifest.from_yaml(target / "plugin.yaml")
            lp = LoadedPlugin(manifest=manifest, path=target, enabled=False)
            plugin_manager._plugins[manifest.id] = lp
            if not plugin_manager._tool_registry:
                from .tool_registry import registry
                plugin_manager._tool_registry = registry
            plugin_manager.enable_plugin(plugin_id)
        except Exception as e:
            logger.warning("Plugin '%s' konnte nicht direkt geladen werden: %s", plugin_id, e)

        logger.info("Hub-Plugin '%s' installiert", plugin_id)
        return {
            "installed": True,
            "type": "plugin",
            "plugin_id": plugin_id,
            "name": pkg.get("name"),
        }

    @router.post("/hub/install")
    async def hub_install(req: InstallRequest, _auth=Depends(require_admin)):
        index = _get_index()
        pkg = next((p for p in index.get("packages", []) if p["id"] == req.id), None)
        if not pkg:
            raise HTTPException(404, f"Paket '{req.id}' nicht im Hub gefunden")

        pkg_path = pkg.get("_path", "")
        if not pkg_path:
            raise HTTPException(500, "Paket hat keinen _path — Index beschädigt?")

        # ── Plugin-Install (#110) ────────────────────────────────────────
        if pkg.get("type") == "plugin":
            return await _hub_install_plugin(pkg, pkg_path)

        # Dateien vom Hub laden
        try:
            soul_md   = _fetch_text(f"{HUB_RAW_BASE}/{pkg_path}/soul.md")
            agent_yaml_raw = _fetch_text(f"{HUB_RAW_BASE}/{pkg_path}/agent.yaml")
        except Exception as e:
            raise HTTPException(502, f"Hub-Dateien konnten nicht geladen werden: {e}")

        # agent.yaml parsen — Hub-Format → AgentConfig-Format konvertieren
        try:
            hub_cfg = yaml.safe_load(agent_yaml_raw) or {}
        except Exception as e:
            raise HTTPException(500, f"agent.yaml ungültig: {e}")

        agent_id = req.agent_id_override or hub_cfg.get("agent_id") or req.id
        import re
        if not re.match(r'^[a-z0-9_-]{1,64}$', agent_id):
            raise HTTPException(400, "agent_id enthält unerlaubte Zeichen")

        model = req.model_override or hub_cfg.get("model", "claude-sonnet-4-6")
        identity = hub_cfg.get("display_name") or hub_cfg.get("identity") or agent_id

        # Kanonisches AgentConfig-Format aufbauen
        agent_cfg: dict = {
            "id":       agent_id,
            "type":     hub_cfg.get("type", "specialist"),
            "identity": identity,
            "llm": {
                "model":       model,
                "max_tokens":  4096,
                "temperature": 0.7,
            },
            "execution_mode": hub_cfg.get("execution_mode", "safe"),
            "tools": hub_cfg.get("tools", []),
        }
        if hub_cfg.get("mcp_servers"):
            agent_cfg["mcp_servers"] = hub_cfg["mcp_servers"]

        # Zielverzeichnis anlegen
        target = Path(agents_dir) / agent_id
        if target.exists():
            raise HTTPException(409, f"Agent '{agent_id}' existiert bereits")

        target.mkdir(parents=True, exist_ok=True)
        (target / "memory").mkdir(exist_ok=True)

        # Dateien schreiben
        (target / "soul.md").write_text(soul_md, encoding="utf-8")
        (target / "agent.yaml").write_text(
            yaml.dump(agent_cfg, allow_unicode=True, default_flow_style=False),
            encoding="utf-8"
        )
        # Hub-Metadaten für "installed"-Tracking speichern
        hub_meta = {**pkg, "installed_agent_id": agent_id}
        (target / ".hub_meta.json").write_text(
            json.dumps(hub_meta, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # Berechtigungen setzen (hydrahive user)
        try:
            import subprocess
            subprocess.run(
                ["chown", "-R", "hydrahive:hydrahive", str(target)],
                check=False, capture_output=True
            )
        except Exception:
            pass

        # Agent sofort in discovery registrieren (kein Neustart nötig)
        if discovery is not None:
            try:
                discovery._register(target)
            except Exception as e:
                logger.warning("discovery._register fehlgeschlagen: %s", e)

        logger.info("Hub-Agent '%s' installiert (Paket: %s)", agent_id, req.id)
        return {
            "installed": True,
            "agent_id": agent_id,
            "name": pkg.get("name"),
            "category": pkg.get("category"),
        }

    @router.delete("/hub/installed/{agent_id}")
    async def hub_uninstall(agent_id: str, _auth=Depends(require_admin)):
        """Entfernt einen Hub-Agenten (nur wenn via Hub installiert)."""
        import re
        if not re.match(r'^[a-z0-9_-]{1,64}$', agent_id):
            raise HTTPException(400, "Ungültige agent_id")

        target = Path(agents_dir) / agent_id
        if not target.exists():
            raise HTTPException(404, "Agent nicht gefunden")
        if not (target / ".hub_meta.json").exists():
            raise HTTPException(403, "Dieser Agent wurde nicht über den Hub installiert")

        shutil.rmtree(target)
        if discovery is not None:
            try:
                discovery._unregister_dir(target)
            except Exception as e:
                logger.warning("discovery._unregister_dir fehlgeschlagen: %s", e)
        logger.info("Hub-Agent '%s' deinstalliert", agent_id)
        return {"uninstalled": True, "agent_id": agent_id}

    # ── ClawhHub ────────────────────────────────────────────────────────────────

    def _find_clawhub() -> str:
        global _CLAWHUB_BIN
        if _CLAWHUB_BIN:
            return _CLAWHUB_BIN
        for candidate in ["/usr/bin/clawhub", "/usr/local/bin/clawhub"]:
            if Path(candidate).exists():
                _CLAWHUB_BIN = candidate
                return candidate
        # Fallback: shutil.which mit erweitertem PATH
        import shutil
        found = shutil.which("clawhub", path="/usr/bin:/usr/local/bin:/usr/sbin:/opt/node/bin")
        if found:
            _CLAWHUB_BIN = found
            return found
        raise FileNotFoundError("clawhub nicht gefunden")

    _CLAWHUB_ENV = {
        "PATH": "/usr/bin:/usr/local/bin:/bin:/sbin:/usr/sbin",
        "HOME": "/home/hydrahive",
        "USER": "hydrahive",
        "NODE_PATH": "/usr/lib/node_modules",
    }

    def _run_clawhub(*args: str, timeout: int = 30) -> tuple[int, str]:
        """Führt 'node clawhub <args>' aus — direkt via node um Shebang-Problem zu umgehen."""
        try:
            bin_path = _find_clawhub()
        except FileNotFoundError:
            raise HTTPException(503, "clawhub_not_installed")
        try:
            # Node direkt aufrufen statt Shebang (#!/usr/bin/env node)
            result = subprocess.run(
                ["/usr/bin/node", bin_path, "--no-input", *args],
                capture_output=True, text=True, timeout=timeout,
                env=_CLAWHUB_ENV,
            )
            return result.returncode, result.stdout
        except FileNotFoundError:
            raise HTTPException(503, "node nicht gefunden unter /usr/bin/node")
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "ClawhHub-Anfrage Timeout")

    def _parse_search_output(raw: str) -> list[dict]:
        """Parst 'clawhub search' Textausgabe → Liste von {slug, name, score}."""
        items = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("-"):
                continue
            # Format: "slug  Display Name  (score)" oder "slug  Display Name"
            m = re.match(r'^(\S+)\s{2,}(.+?)(?:\s+\(([0-9.]+)\))?\s*$', line)
            if m:
                items.append({
                    "slug": m.group(1),
                    "name": m.group(2).strip(),
                    "score": float(m.group(3)) if m.group(3) else None,
                })
        return items

    @router.get("/hub/clawhub/status")
    async def clawhub_status(_auth=Depends(require_admin)):
        """Prüft ob clawhub installiert ist."""
        try:
            bin_path = _find_clawhub()
            return {"installed": True, "path": bin_path}
        except FileNotFoundError:
            return {"installed": False, "path": None}

    @router.post("/hub/clawhub/install-cli")
    async def clawhub_install_cli(_auth=Depends(require_admin)):
        """Installiert clawhub global via sudo npm."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["sudo", "/usr/bin/npm", "install", "-g", "clawhub@latest"],
                capture_output=True, text=True, timeout=120,
                env={**_CLAWHUB_ENV, "PATH": "/usr/bin:/usr/local/bin:/bin:/usr/sbin"},
            )
            combined = (result.stdout + result.stderr)
            if result.returncode != 0:
                # npm schreibt Nutzinfos auf stderr → echten Fehler suchen
                error_lines = [l for l in combined.splitlines() if "npm error" in l and "notice" not in l]
                err_msg = " | ".join(error_lines[-3:]) or combined[-300:]
                raise HTTPException(500, f"npm install fehlgeschlagen: {err_msg}")
            global _CLAWHUB_BIN
            _CLAWHUB_BIN = None  # Cache zurücksetzen damit _find_clawhub neu sucht
            return {"ok": True, "output": combined[-400:]}
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "npm install Timeout")
        except FileNotFoundError:
            raise HTTPException(503, "npm/sudo nicht gefunden")

    @router.get("/hub/clawhub/skills")
    async def clawhub_skills(q: str = "", _auth=Depends(require_admin)):
        """Sucht oder listet ClawhHub Skills."""
        if q.strip():
            rc, out = _run_clawhub("search", q.strip(), "--limit", "30")
            items = _parse_search_output(out)
        else:
            # explore liefert leere Liste ohne Auth-Kontext → Fallback auf Suche
            rc, out = _run_clawhub("search", "agent", "--limit", "30")
            items = _parse_search_output(out)
        return {"items": items}

    @router.get("/hub/clawhub/packages")
    async def clawhub_packages(family: str = "", _auth=Depends(require_admin)):
        """Listet ClawhHub Packages / Plugins."""
        args = ["package", "explore", "--json", "--limit", "50"]
        if family in ("code-plugin", "bundle-plugin", "skill"):
            args += ["--family", family]
        rc, out = _run_clawhub(*args)
        try:
            data = json.loads(out)
            return {"items": data.get("items", [])}
        except json.JSONDecodeError:
            return {"items": []}

    @router.post("/hub/clawhub/skill/install")
    async def clawhub_skill_install(req: ClawhubInstallRequest, _auth=Depends(require_admin)):
        """Installiert einen ClawhHub Skill in den Skills-Ordner eines Agenten."""
        slug = req.slug.strip()
        agent_id = req.agent_id.strip()
        if not slug or not re.match(r'^[@a-z0-9_/-]{1,128}$', slug):
            raise HTTPException(400, "Ungültiger slug")
        if not re.match(r'^[a-z0-9_-]{1,64}$', agent_id):
            raise HTTPException(400, "Ungültige agent_id")

        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        skills_dir = agent_dir / "skills"
        skills_dir.mkdir(exist_ok=True)

        # ClawhHub skill in temp-Verzeichnis herunterladen
        with tempfile.TemporaryDirectory() as tmpdir:
            args = ["install", slug, "--dir", "clawhub_tmp", "--workdir", tmpdir]
            if req.force:
                args.append("--force")
            rc, out = _run_clawhub(*args, timeout=60)

            tmp_skill_dir = Path(tmpdir) / "clawhub_tmp"
            # clawhub legt einen Unterordner mit dem Slug-Namen an:
            #   clawhub_tmp/<slug>/SKILL.md  (bei scoped: clawhub_tmp/<name>/SKILL.md)
            # → SKILL.md rekursiv suchen
            skill_md_candidates = list(tmp_skill_dir.rglob("SKILL.md"))
            skill_md_path = skill_md_candidates[0] if skill_md_candidates else None
            if not skill_md_path or not skill_md_path.exists():
                # stderr könnte Hinweise enthalten (clawhub schreibt auf stderr)
                if "suspicious" in out.lower() or "flag" in out.lower():
                    raise HTTPException(422, f"Skill als verdächtig markiert. Mit force=true erneut versuchen.")
                raise HTTPException(502, f"SKILL.md nicht gefunden. clawhub: {out.strip()[-300:]}")

            raw_md = skill_md_path.read_text(encoding="utf-8")

        # ClawhHub-Frontmatter → HydraHive-Skill-Frontmatter konvertieren
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw_md, re.DOTALL)
        if fm_match:
            try:
                claw_fm = yaml.safe_load(fm_match.group(1)) or {}
            except Exception:
                claw_fm = {}
            body = raw_md[fm_match.end():]
        else:
            claw_fm = {}
            body = raw_md

        skill_name = claw_fm.get("name") or slug.replace("/", "-").replace("@", "")
        description = claw_fm.get("description", "")
        # Trigger-Keywords aus Beschreibung ableiten (erste 8 Wörter, Kleinbuchstaben, stopwords raus)
        _stop = {"and", "or", "the", "a", "an", "to", "of", "in", "for", "with", "when", "use", "is", "it"}
        triggers = [w.lower().strip(".,;") for w in description.split()[:20]
                    if len(w) > 3 and w.lower() not in _stop][:8]

        hh_frontmatter = {
            "skill":    skill_name,
            "version":  claw_fm.get("version", "1.0"),
            "scope":    "on-demand",
            "triggers": triggers,
            "priority": 50,
            "source":   f"clawhub:{slug}",
        }
        converted_md = f"---\n{yaml.dump(hh_frontmatter, allow_unicode=True, default_flow_style=False)}---\n{body}"

        # Dateiname: slug-slug.md (Schrägstriche/@ → Bindestrich)
        safe_name = re.sub(r'[^a-z0-9_-]', '-', slug.lower().lstrip('@'))
        out_path = skills_dir / f"{safe_name}.md"
        out_path.write_text(converted_md, encoding="utf-8")

        # Berechtigungen setzen
        try:
            subprocess.run(
                ["chown", "hydrahive:hydrahive", str(out_path)],
                check=False, capture_output=True
            )
        except Exception:
            pass

        logger.info("ClawhHub-Skill '%s' → Agent '%s' installiert", slug, agent_id)
        return {
            "installed": True,
            "skill_name": skill_name,
            "agent_id": agent_id,
            "file": out_path.name,
        }
