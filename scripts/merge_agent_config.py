#!/usr/bin/env python3
"""
merge_agent_config.py — Intelligenter Merge von Agent-Konfigurationen (#310)

Mergt ein Repo-Template (source) in eine bestehende Runtime-Config (target).
Regeln:
  - Neue Tools aus Template werden ADDIERT (nicht ersetzt)
  - execution_modes: Runtime gewinnt (Admin hat die konfiguriert)
  - soul, identity, type: Template gewinnt (gehören zum Repo)
  - llm: Template gewinnt für model + fallback_models, Runtime für temperature/max_tokens
  - heartbeat: Template gewinnt (Systemwerte)
  - max_tool_rounds: Runtime gewinnt wenn gesetzt
  - Memory-Verzeichnis wird nie angefasst

Aufruf: python3 merge_agent_config.py <template.yaml> <runtime.yaml>
        Ergebnis wird nach <runtime.yaml> geschrieben.
"""
import sys
import yaml
from pathlib import Path


def merge_tools(template_tools: list, runtime_tools: list) -> list:
    """Addiert neue Tools aus Template ohne bestehende zu entfernen."""
    seen = set(runtime_tools)
    merged = list(runtime_tools)
    for t in template_tools:
        if t not in seen:
            merged.append(t)
            seen.add(t)
    return merged


def merge_config(template: dict, runtime: dict) -> dict:
    """Merged Template in Runtime — Runtime-Einstellungen haben Vorrang."""
    result = dict(runtime)

    # Template gewinnt: identity, type, soul (gehören zum Repo/Charakter)
    for key in ("identity", "type", "soul"):
        if key in template:
            result[key] = template[key]

    # Tools: addieren, nicht ersetzen
    if "tools" in template:
        result["tools"] = merge_tools(
            template.get("tools", []),
            runtime.get("tools", []),
        )

    # LLM: Runtime gewinnt komplett (Admin hat Model + Temperature konfiguriert)
    # Nur neue fallback_models aus Template addieren
    if "llm" in template and "llm" in runtime:
        r_llm = result.get("llm", {})
        t_llm = template["llm"]
        # fallback_models: neue aus Template addieren
        if "fallback_models" in t_llm:
            existing = set(r_llm.get("fallback_models", []))
            for fm in t_llm["fallback_models"]:
                if fm not in existing:
                    r_llm.setdefault("fallback_models", []).append(fm)
        result["llm"] = r_llm
    elif "llm" in template and "llm" not in runtime:
        result["llm"] = template["llm"]

    # execution_modes: Runtime gewinnt komplett (Admin hat die konfiguriert)
    # → nicht anfassen wenn Runtime sie hat
    if "execution_modes" not in runtime and "execution_modes" in template:
        result["execution_modes"] = template["execution_modes"]

    # heartbeat: Template gewinnt (Systemwerte)
    if "heartbeat" in template:
        result["heartbeat"] = template["heartbeat"]

    # max_tool_rounds: Runtime gewinnt wenn gesetzt
    if "max_tool_rounds" not in runtime and "max_tool_rounds" in template:
        result["max_tool_rounds"] = template["max_tool_rounds"]

    # allowed_agents, mcp_servers, sources: Template addiert neue
    for key in ("allowed_agents", "mcp_servers", "sources"):
        if key in template:
            t_list = template.get(key, [])
            r_list = runtime.get(key, [])
            if isinstance(t_list, list) and isinstance(r_list, list):
                seen = set(str(x) for x in r_list)
                merged = list(r_list)
                for item in t_list:
                    if str(item) not in seen:
                        merged.append(item)
                result[key] = merged

    return result


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <template.yaml> <runtime.yaml>", file=sys.stderr)
        sys.exit(1)

    template_path = Path(sys.argv[1])
    runtime_path = Path(sys.argv[2])

    if not template_path.exists():
        print(f"Template nicht gefunden: {template_path}", file=sys.stderr)
        sys.exit(1)

    template = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}

    if not runtime_path.exists():
        # Kein Runtime-File → Template direkt kopieren
        runtime_path.write_text(
            yaml.dump(template, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        print(f"NEW: {runtime_path} (aus Template erstellt)")
        sys.exit(0)

    runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8")) or {}
    merged = merge_config(template, runtime)

    runtime_path.write_text(
        yaml.dump(merged, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print(f"MERGED: {runtime_path} (Template → Runtime)")


if __name__ == "__main__":
    main()
