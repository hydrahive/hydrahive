# Session 2 — 19. März 2026

## Teilnehmer
- Till (Product Vision & Anwenderschicht)
- Claude Code (Systemebene & Technische Entscheidungen)

## Kollaborationsmodell (festgelegt)

Till übernimmt: Product Vision, UX, Features, Zielgruppe, fachliche Richtung.  
Claude übernimmt: technische Entscheidungen auf Systemebene eigenständig.

> "In der technischen Umsetzung hast du den besseren Überblick, ich arbeite aus der User- und Anwenderschicht — das wäre eine gute Kombi."

## Tech-Stack (entschieden)

### Core Runtime → Python (FastAPI)
- Gleiche Sprache wie AgentLink-Backend → nahtlose Integration
- Bestes LLM-Ökosystem (litellm, matrix-nio, etc.)
- Läuft sauber als Systemd-Service
- Async-fähig für parallele Agent-Tasks

### Webkonsole → TypeScript + React (Vite)
- Bewährt, große UI-Komponentenbibliotheken (shadcn/ui)
- TypeScript bereits in OpenClaw und AgentLink-Client vorhanden
- Vite für schnellen Dev-Cycle

### Matrix-Client für Agenten → Python (matrix-nio)
- Async Matrix-Library, passt zum Python Core
- Kein extra Service — Agenten sind direkte Matrix-Clients

### LLM-Adapter → litellm
- Eine Library, einheitliches Interface für alle Modelle
- Ollama + Claude API + OpenAI + weitere ohne eigenen Adapter-Layer
- Drop-in austauschbar pro Agent

### Repo-Struktur → Monorepo
```
agentOS/
├── core/          # Python — Runtime, Orchestrator, Agent-Lifecycle
├── console/       # TypeScript/React — Webkonsole
├── docs/          # Brainstorming + Architektur (dieser Ordner)
├── installer/     # Shell-Skripte, Systemd-Units
└── prompts/       # Claude Code Prompt-Pakete (später)
```

## Offene Punkte für Session 3
- Agent-Konfigurationsformat (YAML bevorzugt — menschenlesbar, Git-freundlich)
- Konkrete Agent-Definition: was steht in einer Agent-Config-Datei?
- Projekt-Definition: was steht in einer Projekt-Config?

---
*Stand: Session 2 — 19. März 2026*
