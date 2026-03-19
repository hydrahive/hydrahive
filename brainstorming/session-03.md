# Session 3 — 19. März 2026

## Teilnehmer
- Till (Product Vision & Anwenderschicht)
- Claude Code (Systemebene & Technische Entscheidungen)

## Thema: Agent-Konfigurationsformat & Projekt-Definition

## Kernentscheidung: Self-Describing Agent Packages

Ein Agent ist ein selbstbeschreibendes Paket. Alles was er braucht um zu
existieren und zu arbeiten steckt in seiner eigenen Config. Kein zentrales
Registry, kein hardcodiertes Wissen über andere Agenten.

## Filesystem-Struktur (entschieden)

```
/agents/
├── steuer-agent/
│   ├── agent.yaml        # Identität, LLM, Berechtigungen
│   ├── soul.md           # Persönlichkeit, Kommunikationsstil
│   ├── skills/           # QMD-Dateien — angelerntes Wissen
│   │   ├── steuerrecht-de.qmd
│   │   └── umsatzsteuer.qmd
│   └── tools/            # Tool-Definitionen
│       └── web-search.yaml
├── medizin-agent/
│   ├── agent.yaml
│   ├── soul.md
│   └── skills/
│       └── diagnose.qmd
└── lilith/               # Boss-Agent
    ├── agent.yaml
    ├── soul.md
    └── skills/

/projects/
└── buchhaltung/
    ├── project.yaml      # Projekt-Definition, Agenten-Zuweisung
    └── files/            # Projektdateien (Samba/NFS-Share)
```

AgentOS scannt `/agents/` beim Start — jeder Ordner mit `agent.yaml` wird
automatisch registriert. Hot-Reload via Filesystem-Watcher (Python watchdog):
neuen Ordner anlegen = Agent ist sofort verfügbar, kein Neustart nötig.

## agent.yaml Format (entschieden)

```yaml
id: steuer-agent
version: 1.0.0
type: specialist          # specialist | boss
# "task" ist kein eigener Typ — Task-Agenten sind ephemeral,
# werden on-demand vom Boss gespawnt, haben kein eigenes Verzeichnis

identity:
  name: Steuerbert
  soul: ./soul.md

llm:
  provider: ollama
  model: llama3.1:8b
  fallback: claude        # wenn Ollama nicht verfügbar

skills:
  - ./skills/steuerrecht-de.qmd
  - ./skills/umsatzsteuer.qmd

tools:
  - web-search
  - file-read

permissions:
  filesystem: read-only
  network: outbound-only
  can-spawn-agents: false

heartbeat:
  interval: 30s
  timeout: 10s
  on-failure: restart
```

**Kein `matrix.rooms` in agent.yaml** — Rooms sind Projekt-Sache, nicht
Agent-Sache. Der Agent ist skill-gebunden, nicht projekt-gebunden. Welche
Rooms er joined entscheidet die project.yaml.

## project.yaml Format (entschieden)

```yaml
id: buchhaltung
version: 1.0.0

identity:
  name: Buchhaltung 2026
  description: Steuer- und Buchhaltungsarbeiten

# Agenten-Zuweisung — AgentOS joined diese Agenten beim Projekt-Start
# automatisch in den Projekt-Matrix-Room
agents:
  boss: lilith
  workers:
    - steuer-agent
    - research-agent

matrix:
  room: "#buchhaltung:agentOS.local"   # wird beim Erstellen angelegt

filesystem:
  path: /projects/buchhaltung/files
  share:
    samba: true
    nfs: false

# Linux-User für dieses Projekt (OS-Isolation)
system:
  user: proj_buchhaltung
  group: proj_buchhaltung
```

## Task-Agenten (Klarstellung)

Task-Agenten sind **kein eigener Typ in /agents/**. Sie sind:
- Ephemeral — existieren nur während einer Aufgabe
- On-demand gespawnt vom Boss-Agenten
- Erben Context vom rufenden Boss
- Schreiben State via AgentLink zurück
- Sterben nach Task-Completion

Konkrete Umsetzung: Boss-Agent spawnt einen temporären Sub-Prozess mit
eigenem LLM-Call, übergibt Context, wartet auf Ergebnis.

## Webkonsole (abgeleitet)

"Agent anlegen" im Web-Formular = AgentOS legt intern diese Dateistruktur an.
"Projekt anlegen" = project.yaml wird geschrieben + Linux-User erstellt +
Matrix-Room angelegt + Samba-Share eingerichtet.

Direktes Editieren im Dateisystem funktioniert parallel — Hot-Reload
übernimmt die Änderungen automatisch.

## Offene Punkte für Session 4
- Client & User-Support Flow: Was passiert wenn ein User über den Web-Client
  schreibt? Landet das direkt im Matrix-Room oder gibt es einen Dispatcher?
- Tool-Format: Wie sieht eine tool.yaml konkret aus?
- QMD-Format: Ist das 1:1 von OpenClaw übernommen oder angepasst?

---
*Stand: Session 3 — 19. März 2026*
