# Entschiedene Punkte

> Nur was wirklich entschieden ist. Kein "vielleicht", kein "offen".  
> Basis für spätere Claude Code Prompts.

---

## Session-Roadmap

| Session | Thema | Status |
|---|---|---|
| 1 | Vision, Produkt, Agenten-Modell | ✓ abgeschlossen |
| 2 | Tech-Stack | ✓ abgeschlossen |
| 3 | Agent-Config-Format, Projekt-Config-Format | ✓ abgeschlossen |
| 4 | User-Flow & Webkonsole | ✓ abgeschlossen |
| 5 | GPU & Installer-Flow | ✓ abgeschlossen |
| 6 | QMD-Format, Tool-Format | ✓ abgeschlossen |
| 7 | Monetarisierung & Editions | offen |

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
| A2 | Tuwunel als Matrix-Homeserver | Conduit-Nachfolger (via conduwuit), aktiv gewartet, sponsored CH, static binary, RocksDB, kein Postgres |
| A3 | AgentLink bleibt State-Layer | Bereits validiert, wird erweitert nicht ersetzt |
| A4 | 5-Schichten-Modell | Client → Management → Bus → Runtime → LLM |
| A5 | Web-Chat-UI ist Matrix-Client (matrix-js-sdk) | Kein Gateway-Service nötig, direkt gegen Conduit |
| A6 | Element-Zugriff out-of-the-box | Admin verbindet sich mit AgentOS-Conduit, joinst Room |

## Agenten-Modell

| # | Entscheidung | Begründung |
|---|---|---|
| AG1 | Skill-Pool-Modell | Agenten skill-gebunden, nicht projekt-gebunden |
| AG2 | Boss-Agent pro Projekt | Koordiniert Swarm, bewährt durch Lilith |
| AG3 | Hybride Persistenz | Core permanent, Task-Agenten ephemeral |
| AG4 | Soul + QMD Skills | Von OpenClaw übernehmen |
| AG5 | Projektverwaltung mit Agenten-Zuweisung | Explizit, kein Magic-Routing |
| AG6 | Task-Agenten sind kein Typ in /agents/ | Ephemeral, on-demand vom Boss gespawnt |

## Agent-Konfigurationsformat

| # | Entscheidung | Begründung |
|---|---|---|
| C1 | Self-describing Agent Packages | Alles in einem Verzeichnis, kein zentrales Registry |
| C2 | Filesystem-Discovery mit Hot-Reload | /agents/ scannen, watchdog, kein Neustart nötig |
| C3 | agent.yaml: id, type, identity, llm, skills, tools, permissions, heartbeat | Vollständige Selbstbeschreibung |
| C4 | Kein matrix.rooms in agent.yaml | Rooms sind Projekt-Sache, nicht Agent-Sache |
| C5 | soul.md als eigene Datei | Persönlichkeit getrennt von Konfiguration |
| C8 | Type: specialist oder boss | Nur zwei Typen im /agents/ Verzeichnis |

## Projekt-Konfigurationsformat

| # | Entscheidung | Begründung |
|---|---|---|
| PR1 | project.yaml: id, identity, agents, matrix, filesystem, system | Vollständige Projekt-Definition |
| PR2 | agents.boss + agents.workers Liste | Explizite Zuweisung, kein Magic-Routing |
| PR3 | matrix.room in project.yaml | AgentOS legt Room beim Erstellen an |
| PR4 | system.user = proj_<name> | Linux-User wird automatisch erstellt |
| PR5 | Webkonsole und direktes Dateisystem-Editieren beide unterstützt | Dual-Interface, Hot-Reload |
| PR6 | chat.show_swarm konfigurierbar | false = nur Boss-Antworten, true = voller Swarm-Dialog |

## QMD-Format (Skills)

| # | Entscheidung | Begründung |
|---|---|---|
| QM1 | QMD-Frontmatter: skill, version, scope, triggers, priority | Vollständige Metadaten pro Skill-Datei |
| QM2 | scope: always \| on-demand | always = immer geladen, on-demand = nur bei Trigger-Match |
| QM3 | on-demand: Keyword-Matching, kein ML | Core entscheidet vor dem LLM-Call, spart Token |
| QM4 | priority: Ladereihenfolge bei mehreren Matches | Niedrigere Zahl = höhere Priorität |

## Tool-System

| # | Entscheidung | Begründung |
|---|---|---|
| TL1 | Zentrales Tool-Registry im Core (core/tools/) | Kein Code im Agent-Verzeichnis, keine Sicherheitsprobleme |
| TL2 | BaseTool Interface: id, name, description, permissions_required, parameters, execute() | Einheitliches Interface für alle Tools |
| TL3 | parameters = Function-Calling-Schema direkt für litellm | Kein Übersetzungsschritt nötig |
| TL4 | Schnittmenge: agent.yaml ∩ Registry ∩ permissions = was LLM sieht | Defense in Depth, drei unabhängige Filter |
| TL5 | Tool nicht in Registry = existiert nicht | Egal was in agent.yaml steht |
| TL6 | Webkonsole zeigt Tool-Übersicht | Agent anlegen = aus Liste wählen, kein Freitext |

## Webkonsole-Struktur

| # | Entscheidung | Begründung |
|---|---|---|
| W1 | Admin-Bereich: REST API gegen AgentOS Core | Agenten, Projekte, System, User, LLM-Config |
| W2 | Chat-Bereich: matrix-js-sdk direkt gegen Conduit | Pro Projekt ein Tab, kein Proxy |
| W3 | AgentOS-User = Matrix-Account auf Conduit | Ein Account, zwei Zugangswege (Web + Element) |

## LLM-Anbindung

| # | Entscheidung | Begründung |
|---|---|---|
| L1 | Multi-LLM pro Agent konfigurierbar | Ollama + Claude API + OpenAI + weitere |
| L2 | Graceful Degradation ohne GPU | Cloud-only Mode wenn kein GPU |
| L3 | litellm als LLM-Adapter | Einheitliches Interface, kein eigener Adapter-Layer |
| L4 | llm.fallback in agent.yaml | Fallback-Provider wenn primärer nicht verfügbar |
| L5 | Default-Modell Full: llama3.1:8b (Q4) | Specialist-Agenten, ~4.7GB VRAM |
| L6 | Task-Agenten: llama3.2:3b (Q4) | ~2GB VRAM, schnell für ephemere Tasks |
| L7 | Boss-Agent lokal: mistral-nemo:12b (Q4) | 11GB VRAM erlaubt ~7GB Modell, stärkeres Reasoning ohne API-Kosten |
| L8 | Boss-Fallback: Claude API | Nur wenn lokales Modell nicht verfügbar oder unzureichend |

## Installer

| # | Entscheidung | Begründung |
|---|---|---|
| IN1 | Einzelner curl-Befehl | Maximale Einfachheit für Onboarding |
| IN2 | Automatische GPU-Erkennung via nvidia-smi | Kein manuelles Profil wählen nötig |
| IN3 | Unterstützte OS: Debian 12, Ubuntu 22.04+, Ubuntu 24.04 LTS | Klare Basis, andere werden abgelehnt mit Hinweis |
| IN4 | Idempotent | Mehrfaches Ausführen ohne Schaden möglich |
| IN5 | Setup-Wizard nach Installation | Browser öffnet auf Port 443, Admin-User anlegen |

## Systemd-Services

| # | Service | Profil | Beschreibung |
|---|---|---|---|
| S1 | agentos-core | Lite + Full | Python FastAPI Core + Orchestrator |
| S2 | agentos-conduit | Lite + Full | Matrix-Homeserver |
| S3 | agentos-console | Lite + Full | Web-Console via nginx |
| S4 | ollama | Full only | Lokale LLM-Inference |

## Tech-Stack

| # | Entscheidung | Begründung |
|---|---|---|
| T1 | Core Runtime: Python + FastAPI | Gleich wie AgentLink, bestes LLM-Ökosystem, Systemd-tauglich |
| T2 | Webkonsole: TypeScript + React (Vite) | Bewährt, shadcn/ui, TypeScript bereits vorhanden |
| T3 | Matrix-Client Agenten: matrix-nio (Python) | Async, passt zum Core, kein extra Service |
| T4 | Monorepo | core/ + console/ + docs/ + installer/ + prompts/ |
| T5 | Filesystem-Watcher: Python watchdog | Hot-Reload für /agents/ und /projects/ |
| T6 | Matrix-Client Web-UI: matrix-js-sdk | Direkte Verbindung zu Conduit aus dem Browser |

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
| O5 | QMD-Skills | Übernommen & erweitert: scope, triggers, priority |

---
*Zuletzt aktualisiert: Session 6 + DevMaster Setup — 19. März 2026*
