# OctopOS

**Selbst-gehosteter KI-Agent-Server** — Agenten-Swarms, Matrix-Kommunikation, Projekt-Isolation.

> Installiere Linux → Installiere OctopOS → Verwalte alles über die Webkonsole.

---

## Features

- **Multi-Agent Swarms** — Boss-Agent koordiniert Worker-Agenten parallel
- **Projekt-Isolation** — Jedes Projekt bekommt eigenen Linux-User, Samba-Share und Matrix-Room
- **Multi-LLM** — Ollama (lokal), Claude Max (OAuth), OpenAI — pro Agent konfigurierbar
- **Matrix-Integration** — Agenten sind echte Matrix-Bots, du kannst mit Element eingreifen
- **QMD-Skills** — Angelerntes Wissen in Markdown-Dateien mit YAML-Frontmatter
- **Webkonsole** — Vollständige Verwaltung ohne SSH: Agenten, Projekte, Users, Logs, Skills
- **Streaming** — Antworten erscheinen Token für Token
- **Webhook-System** — Externe Trigger für Agenten (`/hooks/{project}/wake`)
- **Audit-Log** — Alle User-Aktionen protokolliert

## Deployment-Profile

| Profil | GPU | LLM | Geeignet für |
|--------|-----|-----|-------------|
| **Lite** | Nein | Cloud-APIs | VPS, Test, Demo |
| **Full** | Ja (PCIe-Passthrough) | Ollama + Cloud | Produktion, volle Kontrolle |

Referenz-Setup: GTX 1080 Ti (11GB VRAM) auf Proxmox VM, Ubuntu 24.04

## Schnellstart

```bash
git clone https://github.com/tilleulenspiegel/octopos.git
cd octopos
sudo bash installer/install.sh
# → https://<IP> öffnen → Setup-Wizard
```

## Dokumentation

| Dokument | Inhalt |
|----------|--------|
| [Handbuch](docs/handbook.md) | Installation, Erste Schritte, alle Features |
| [Technische Doku](docs/technical.md) | Architektur, Module, Datenfluss |
| [API-Referenz](docs/api-reference.md) | Alle REST-Endpoints |
| [Entwickler-Guide](docs/development.md) | Tools, Skills, Endpoints, Console-Seiten hinzufügen |

## Architektur

```
Browser (React) → nginx (HTTPS) → FastAPI Core → Orchestrator
                                              ↓
                                   Boss-Agent → Worker-Agenten
                                              ↓
                                   conduwuit (Matrix) ← Element
```

## Stack

- **Core:** Python 3.12, FastAPI, litellm, matrix-nio, anthropic SDK
- **Console:** React 18, TypeScript, Vite, Tailwind CSS
- **Matrix:** conduwuit (Rust, single binary, RocksDB)
- **LLM:** Ollama + Anthropic OAuth + OpenAI
- **Installer:** Bash + Systemd (kein Docker)

## Status

🚧 Aktive Entwicklung — produktiv nutzbar, API noch nicht stabil

## Lizenz

MIT
