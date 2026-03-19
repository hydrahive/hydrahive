# OctopOS

**AI-Agent Server Platform** — Standalone server solution for managing AI agent swarms.

> Brainstorming started: 19. März 2026

## Vision

Install Linux → Install OctopOS → Done.  
Manage everything via web console. No SSH, no manual config files.

"Proxmox für AI-Agenten."

## Name & Metapher

Der Oktopus: zentrales Hirn (Boss-Agent), viele Arme die parallel arbeiten (Worker-Swarm),  
jeder Arm halbautonorm — mehrere Projekte und Clients gleichzeitig.

## Produkt-Linie

- **OctopOS** — die Plattform / das Betriebssystem-Aufsatz
- **OctopOS AI** — fertiger Server mit GPU, vorinstalliert, plug & play

## Deployment Profiles

- **Lite** — VM ohne GPU, Cloud APIs only (Claude, ChatGPT)
- **Full** — VM mit PCIe GPU passthrough, Ollama local models

## Status

🧠 Brainstorming & Architecture Phase (Sessions 1-6 abgeschlossen)

## Structure

```
OctopOS/
├── brainstorming/     # Session notes and decisions
├── architecture/      # Architecture overview
├── core/              # Python runtime (Phase 1+)
├── console/           # TypeScript/React webconsole (Phase 4+)
├── installer/         # Bash installer script (Phase 1+)
└── prompts/           # Claude Code prompt packages (later)
```
