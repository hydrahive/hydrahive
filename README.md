# HydraHive

**Selbst-gehosteter KI-Agent-Server** — Agenten-Swarms, Matrix-Kommunikation, Projekt-Isolation.

> Installiere Linux → Installiere HydraHive → Verwalte alles über die Webkonsole.

---

## Features

- **Multi-Agent Swarms** — Boss-Agent koordiniert Worker-Agenten parallel
- **Projekt-Isolation** — Jedes Projekt bekommt eigenen Linux-User, Samba-Share und Matrix-Room
- **Multi-LLM** — Ollama (lokal), Claude Max (OAuth), OpenAI — pro Agent konfigurierbar; Fallback-Ketten
- **Matrix-Integration** — Agenten sind echte Matrix-Bots, du kannst mit Element eingreifen
- **Discord-Integration** — Persönliche Agenten können auf Discord lesen und antworten
- **QMD-Skills** — Angelerntes Wissen in Markdown-Dateien mit YAML-Frontmatter
- **Persönlicher Agent** — Jeder User bekommt einen eigenen privaten Agenten (`personal_<username>`)
- **Memory-System** — Agenten speichern Wissen persistent in Markdown-Dateien, auto-injiziert in System-Prompt
- **A-MEM Shared Memory** — Zentrale agentenuebergreifende Wissensdatenbank fuer Fehler, Loesungen und Learnings
- **WKS-Zugang** — Persönliche Agenten verbinden sich per SSH/SFTP mit der eigenen Workstation
- **WKS-Ollama** — Ollama auf der Workstation wird automatisch im Modell-Dropdown angeboten
- **Git-Tools** — Agenten können committen, pushen und Pull Requests auf Gitea erstellen
- **Gitea-Issues** — Agenten können Issues anlegen, kommentieren, updaten und schliessen
- **MCP-Server** — Externe Tool-Server per streamableHttp einbinden (z.B. QMD Memory Search)
- **Execution Modes** — safe/elevated/root fuer kontrollierte Eskalation von Agentenrechten
- **Webkonsole** — Vollständige Verwaltung ohne SSH: Agenten, Projekte, Users, Logs, Skills, MCP
- **Streaming** — Antworten erscheinen Token für Token
- **Webhook-System** — Externe Trigger für Agenten (`/hooks/{project}/wake`)
- **Audit-Log** — Alle User-Aktionen protokolliert
- **System-Update** — Ein-Klick-Update aus der Webkonsole (git pull + Build + Restart)

## Deployment-Profile

| Profil | GPU | LLM | Geeignet für |
|--------|-----|-----|-------------|
| **Lite** | Nein | Cloud-APIs | VPS, Test, Demo |
| **Full** | Ja (PCIe-Passthrough) | Ollama + Cloud | Produktion, volle Kontrolle |

Referenz-Setup: GTX 1080 Ti (11GB VRAM) auf Proxmox VM, Ubuntu 24.04

## Schnellstart

```bash
git clone https://github.com/tilleulenspiegel/hydrahive.git
cd hydrahive
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

## Aktueller Stand

- Produktiv nutzbar und aktiv weiterentwickelt
- Router- und Auth-Architektur ist modularisiert statt monolithisch
- Persönliche Agenten arbeiten mit klaren Sicherheitsstufen und A-MEM als Shared Memory
- Die Webkonsole deckt Agenten, Projekte, MCP, Gitea, WKS, Discord, Audit und Updates ab

## Status

🚧 Aktive Entwicklung — produktiv nutzbar

## Lizenz

MIT
