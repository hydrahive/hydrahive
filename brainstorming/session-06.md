# Session 6 — 19. März 2026

## Teilnehmer
- Till (Product Vision & Anwenderschicht)
- Claude Code (Systemebene & Technische Entscheidungen)

## Thema: QMD-Format & Tool-System

---

## QMD-Format (entschieden)

QMD ist Markdown mit YAML-Frontmatter. Wird beim Agent-Start geladen und
als Wissens-Kontext in den System-Prompt injiziert.

```markdown
---
skill: steuerrecht-de
version: 1.2.0
domain: steuerrecht
language: de
tags: [umsatzsteuer, einkommensteuer, gewerbesteuer]
scope: on-demand
triggers:
  - steuer
  - umsatzsteuer
  - finanzamt
priority: 1
---

# Steuerrecht Deutschland

## Umsatzsteuer
...

## Einkommensteuer
...
```

### scope-Feld (Schlüsselentscheidung)

| Wert | Verhalten |
|---|---|
| `always` | Immer im System-Prompt geladen |
| `on-demand` | Nur laden wenn Trigger-Keyword in eingehender Nachricht matcht |

**Warum:** Ein Steuer-Agent der eine Code-Frage bekommt soll nicht seinen
gesamten Steuerrecht-Kontext im Hinterkopf haben. Spart Token, macht den
Agenten fokussierter.

### Trigger-Matching

Einfaches Keyword-Matching auf die eingehende Nachricht — kein ML nötig.
Der Core entscheidet welche Skills geladen werden **bevor** er den LLM-Call
macht.

### priority-Feld

Wenn mehrere Skills gleichzeitig triggern (Nachricht enthält Begriffe aus
mehreren Skill-Dateien), bestimmt `priority` die Ladereihenfolge.
Niedrigere Zahl = höhere Priorität.

---

## Tool-System (entschieden)

### Kern-Prinzip: Zentrales Registry im Core

Alle Tools leben in `core/tools/`. Agenten deklarieren nur welche Tools
sie nutzen *wollen* — was sie tatsächlich bekommen bestimmt der Core.

**Sicherheitsmodell — drei unabhängige Filter:**

```
agent.yaml tools-Liste  ∩  Core-Registry  ∩  permissions  =  was der LLM sieht
```

Kein einzelner Punkt kann das System aushebeln:
- Tool nicht in Registry? Existiert nicht, egal was in agent.yaml steht.
- Tool in Registry aber nicht in agent.yaml? Agent bekommt es nicht.
- Tool in beiden aber Permission fehlt? Core blockiert den Call.

### BaseTool Interface

```python
# core/tools/web_search.py
class WebSearchTool(BaseTool):
    id = "web-search"
    name = "Web Search"
    description = "Sucht aktuelle Informationen im Web"
    permissions_required = ["network.outbound"]

    parameters = {
        "query": {"type": "string", "description": "Suchanfrage"},
        "max_results": {"type": "integer", "default": 5}
    }

    async def execute(self, query: str, max_results: int = 5) -> str:
        ...
```

`parameters` mappt direkt auf das Function-Calling-Schema das litellm an
den LLM schickt — kein extra Übersetzungsschritt.

### Built-in Tools (Core-Registry)

| Tool | Permissions |
|---|---|
| `web-search` | network.outbound |
| `file-read` | filesystem.read |
| `file-write` | filesystem.write |
| `http-request` | network.outbound |
| `spawn-agent` | can-spawn-agents |

### Webkonsole (abgeleitet)

Admin-Bereich zeigt Tool-Übersicht: welche Tools der Core kennt, mit
Beschreibung und benötigten Permissions. Agent anlegen = aus dieser Liste
auswählen. Kein Freitext, keine Überraschungen.

---

## Offene Punkte für Session 7
- Monetarisierung: Community vs. Pro Edition?
- Was ist in Community, was in Pro?
- Lizenzmodell: Open Source, Source Available, oder proprietär?
- Pricing-Ideen

---
*Stand: Session 6 — 19. März 2026*
