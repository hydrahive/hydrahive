# Entschiedene Punkte

> Nur was wirklich entschieden ist. Kein "vielleicht", kein "offen".  
> Basis für spätere Claude Code Prompts.

---

## Produkt

| # | Entscheidung | Begründung |
|---|---|---|
| P1 | Standalone Server-Produkt | Install Linux → AgentOS → fertig |
| P2 | Webkonsole ist das Produkt | Kein SSH, kein Config-Editieren |
| P3 | Zwei Deployment-Profile: Lite + Full | Lite = kein GPU, Full = GPU-Passthrough |
| P4 | Till ist Referenz-Kunde | TrueNAS/Proxmox Setup mit GTX 1080 |

## Infrastruktur

| # | Entscheidung | Begründung |
|---|---|---|
| I1 | Kein Docker | Netzwerkprobleme in VMs zu fehleranfällig |
| I2 | Systemd-Services direkt auf Linux | Transparent, debuggbar, kein virtuelles Netz |
| I3 | Linux-User pro Projekt | Isolation + Netzwerkfreigaben über OS-Berechtigungen |
| I4 | Samba/NFS auf Linux-User-Basis | Kein extra Usermanagement nötig |

## Architektur

| # | Entscheidung | Begründung |
|---|---|---|
| A1 | Matrix als Kommunikations-Bus | Standard-Protokoll, Client-Apps verfügbar |
| A2 | Conduit als Matrix-Homeserver | Single binary, RocksDB, kein Postgres |
| A3 | AgentLink bleibt State-Layer | Bereits validiert, wird erweitert nicht ersetzt |
| A4 | 5-Schichten-Modell | Client → Management → Bus → Runtime → LLM |

## Agenten-Modell

| # | Entscheidung | Begründung |
|---|---|---|
| AG1 | Skill-Pool-Modell | Agenten skill-gebunden, nicht projekt-gebunden |
| AG2 | Boss-Agent pro Projekt | Koordiniert Swarm, bewährt durch Lilith |
| AG3 | Hybride Persistenz | Core permanent, Task-Agenten ephemeral |
| AG4 | Soul + QMD Skills | Von OpenClaw übernehmen |
| AG5 | Projektverwaltung mit Agenten-Zuweisung | Explizit, kein Magic-Routing |
| AG6 | Task-Agenten sind kein Typ in /agents/ | Ephemeral, on-demand vom Boss gespawnt, kein eigenes Verzeichnis |

## Agent-Konfigurationsformat

| # | Entscheidung | Begründung |
|---|---|---|
| C1 | Self-describing Agent Packages | Alles in einem Verzeichnis, kein zentrales Registry |
| C2 | Filesystem-Discovery mit Hot-Reload | /agents/ scannen, watchdog, kein Neustart nötig |
| C3 | agent.yaml: id, type, identity, llm, skills, tools, permissions, heartbeat | Vollständige Selbstbeschreibung |
| C4 | Kein matrix.rooms in agent.yaml | Rooms sind Projekt-Sache, nicht Agent-Sache |
| C5 | soul.md als eigene Datei | Persönlichkeit getrennt von Konfiguration |
| C6 | skills/ als QMD-Dateien | Von OpenClaw übernommen |
| C7 | tools/ als YAML-Definitionen | Was darf der Agent aufrufen |
| C8 | Type: specialist oder boss | Nur zwei Typen im /agents/ Verzeichnis |

## Projekt-Konfigurationsformat

| # | Entscheidung | Begründung |
|---|---|---|
| PR1 | project.yaml: id, identity, agents, matrix, filesystem, system | Vollständige Projekt-Definition |
| PR2 | agents.boss + agents.workers Liste | Explizite Zuweisung, kein Magic-Routing |
| PR3 | matrix.room in project.yaml | AgentOS legt Room beim Erstellen an |
| PR4 | system.user = proj_<name> | Linux-User wird automatisch erstellt |
| PR5 | Webkonsole und direktes Dateisystem-Editieren beide unterstützt | Dual-Interface, Hot-Reload übernimmt Änderungen |

## LLM-Anbindung

| # | Entscheidung | Begründung |
|---|---|---|
| L1 | Multi-LLM pro Agent konfigurierbar | Ollama + Claude API + OpenAI + weitere |
| L2 | Graceful Degradation ohne GPU | Cloud-only Mode wenn kein GPU |
| L3 | litellm als LLM-Adapter | Einheitliches Interface, kein eigener Adapter-Layer |
| L4 | llm.fallback in agent.yaml | Fallback-Provider wenn primärer nicht verfügbar |

## Tech-Stack

| # | Entscheidung | Begründung |
|---|---|---|
| T1 | Core Runtime: Python + FastAPI | Gleich wie AgentLink, bestes LLM-Ökosystem, Systemd-tauglich |
| T2 | Webkonsole: TypeScript + React (Vite) | Bewährt, shadcn/ui, TypeScript bereits vorhanden |
| T3 | Matrix-Client: matrix-nio (Python) | Async, passt zum Core, kein extra Service |
| T4 | Monorepo | core/ + console/ + docs/ + installer/ + prompts/ |
| T5 | Filesystem-Watcher: Python watchdog | Hot-Reload für /agents/ und /projects/ |

## Kollaboration

| # | Entscheidung | Begründung |
|---|---|---|
| K1 | Till = Anwender-/Produktschicht | Vision, UX, Features, Zielgruppe |
| K2 | Claude = Systemebene | Technische Entscheidungen eigenständig |

## Von OpenClaw übernehmen

| # | Komponente | Änderung |
|---|---|---|
| O1 | Heartbeat | Besser konfigurierbar (Interval, Timeout, Fallback) |
| O2 | Sitzungskonzept | Unverändert übernehmen |
| O3 | Kanäle | → Matrix-Rooms |
| O4 | Agenten-Config + Berechtigungen | Übersichtlicher gestalten |
| O5 | QMD-Skills | Übernehmen, Format noch zu klären (Session 4) |

---
*Zuletzt aktualisiert: Session 3 — 19. März 2026*
