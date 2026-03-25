# HydraHive — Architektur-Dokumentation

> Version 0.1.0 · Stand 2026-03-25

---

## Inhaltsverzeichnis

1. [Übersicht](#übersicht)
2. [Komponentendiagramm](#komponentendiagramm)
3. [Datenfluss](#datenfluss)
4. [Verzeichnisstruktur](#verzeichnisstruktur)
5. [Agent-Konfiguration](#agent-konfiguration)
6. [Sicherheitsmodell](#sicherheitsmodell)
7. [Tool-System](#tool-system)
8. [Messenger-Integrationen](#messenger-integrationen)
9. [Session-Management](#session-management)
10. [Memory-System](#memory-system)

---

## 1. Übersicht

HydraHive ist ein selbst gehosteter AI-Agent-Server. Das Backend ist eine FastAPI-
Applikation (Python 3.11) mit dem Kern-Konzept hierarchischer Agenten: ein
Boss-Agent koordiniert Worker-Agenten und delegiert Teilaufgaben. Multi-LLM-Support
wird über litellm abstrahiert, sodass Anthropic Claude, OpenAI, Mistral, Google Gemini
und lokale Ollama-Modelle hinter einer einheitlichen API verwendbar sind. Das Frontend
ist eine React/Vite-SPA mit Tailwind CSS und kommuniziert ausschließlich über eine
REST-API mit dem Backend. Messenger-Bots (Matrix, Discord, Telegram, WhatsApp) erlauben
direkten Agenten-Zugriff ohne die Web-Console. Alle Nutzdaten (Agenten, Projekte,
Sessions, Memory) werden als Dateien auf dem Dateisystem abgelegt — keine externe
Datenbank wird benötigt.

---

## 2. Komponentendiagramm

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            HydraHive VM                                 │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     nginx (Port 443/80)                         │    │
│  │   HTTP→HTTPS Redirect │ SPA-Serving │ /api/ → Proxy :8765      │    │
│  └──────────────────────────────┬──────────────────────────────────┘    │
│                                 │                                       │
│  ┌──────────────────────────────▼──────────────────────────────────┐    │
│  │              hydrahive-core (Port 8765, FastAPI)                │    │
│  │                                                                 │    │
│  │  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐   │    │
│  │  │ Orchestrator│  │ AgentRuntime │  │  SessionManager      │   │    │
│  │  │  Boss-Agent │  │  Heartbeat   │  │  JSON Persistenz     │   │    │
│  │  │  Dispatcher │  │  Lifecycle   │  │  /projects/.sessions │   │    │
│  │  └──────┬──────┘  └──────┬───────┘  └──────────────────────┘   │    │
│  │         │                │                                      │    │
│  │  ┌──────▼────────────────▼──────────────────────────────────┐  │    │
│  │  │                   ToolRegistry (49 Tools)                │  │    │
│  │  │  Filesystem │ Shell │ Web │ Git │ Gitea │ AgentLink       │  │    │
│  │  │  Memory     │ Mail  │ Discord  │ MCP                      │  │    │
│  │  └──────────────────────────────────────────────────────────┘  │    │
│  │                                                                 │    │
│  │  ┌────────────┐  ┌────────────┐  ┌─────────────┐              │    │
│  │  │ litellm    │  │ JWT Auth   │  │ RateLimiter │              │    │
│  │  │ Multi-LLM  │  │ HS256      │  │ Login+API   │              │    │
│  │  └────────────┘  └────────────┘  └─────────────┘              │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │  conduwuit   │  │    Gitea     │  │  AgentLink   │                  │
│  │  Matrix HS   │  │  Git-Server  │  │  Hub         │                  │
│  │  Port 6167   │  │  Port 3001   │  │  Port 8010   │                  │
│  └──────────────┘  └──────────────┘  └──────────────┘                  │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    Dateisystem                                  │    │
│  │  /agents/          /projects/        /etc/hydrahive/            │    │
│  │  (Agent-Konfig)    (Projekte+         (Secrets+Config)          │    │
│  │  /agents/{id}/     Sessions)          jwt_secret, llm_env       │    │
│  │  memory/*.md                          users.json                │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐   │
│  │ Matrix   │  │ Discord  │  │ Telegram │  │  WhatsApp Bridge     │   │
│  │ Bot      │  │ Bot      │  │ Bot      │  │  (mautrix-whatsapp)   │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘

           ▲                    ▲                    ▲
           │                    │                    │
    Browser (Console)    Messenger-Clients    Externe LLM-APIs
    React/Vite SPA       Matrix/Discord/      Anthropic/OpenAI/
                         Telegram/WhatsApp    Mistral/Ollama
```

---

## 3. Datenfluss

### 3.1 Web-Console Chat (typischer Pfad)

```
Browser
  │  POST /api/agents/{id}/message
  │  {"content": "...", "session_id": "...", "execution_mode": "safe"}
  │
nginx (:443)
  │  Proxy-Pass → :8765/agents/{id}/message
  │
router_agent_chat.py
  │  JWT-Validierung (auth_utils.py)
  │  Rate-Limit-Check (rate_limiter.py)
  │  Execution-Mode-Policy (execution_mode_policy.py)
  │
orchestrator.py → Orchestrator.handle_message()
  │
  ├─ SessionManager: Nachricht an Session anhängen
  │  (MessageRole.USER → session.messages)
  │
  ├─ System-Prompt aufbauen:
  │   ├─ soul.md (Persönlichkeit/Anweisungen)
  │   ├─ /agents/{id}/memory/*.md (injiziert)
  │   └─ Skills (skill_loader.py → skills_to_system_prompt())
  │
  ├─ Tool-Schema ermitteln:
  │   └─ ToolRegistry.tools_for_agent(agent.tools, permissions)
  │       → Schnittmenge: agent.yaml ∩ Registry ∩ active permissions
  │
  ├─ litellm.acompletion() → LLM-API
  │   (max 6 Tool-Runden, get_final_message())
  │
  ├─ Tool-Loop (wenn LLM Tool-Calls zurückgibt):
  │   ├─ BaseTool.execute(agent_id, project_id, **kwargs)
  │   │   └─ Path-Safety-Check bei Filesystem-Tools
  │   ├─ Tool-Ergebnis → _truncate_tool_result()
  │   └─ Ergebnis als MessageRole.TOOL in Session
  │
  ├─ Finale Antwort → SessionManager.add_message(ASSISTANT)
  │   (Session-Persistierung: /projects/{id}/.sessions/{session}.json)
  │
  └─ Response: {"response": "...", "session_id": "..."}
```

### 3.2 Boss-Agent → Worker-Agent Delegation

```
Boss-Agent
  │  Tool-Call: dispatch_task(worker_id="coder", task="...")
  │
Orchestrator._execute_dispatch_task()
  │
  ├─ AgentRuntime.get_or_create_worker(worker_id)
  │   └─ Worker-AgentConfig aus /agents/{worker_id}/agent.yaml
  │
  ├─ Worker-Session anlegen (temporär, project_id geerbt)
  │
  ├─ Worker-LLM-Aufruf (eigenes Modell, eigene Tools)
  │   (parallel für mehrere Workers via asyncio.gather)
  │
  └─ DispatchResult(worker_id, task, result, success)
       → als Tool-Ergebnis zurück an Boss
```

### 3.3 Messenger-Eingang (am Beispiel Matrix)

```
conduwuit Matrix-Homeserver (:6167)
  │  Matrix-Event (m.room.message)
  │
matrix_agent.py → BossMatrixAgent.on_message()
  │
  ├─ Raum-ID → Agent-ID mapping
  ├─ Message-Deduplication (event_id)
  │
  └─ orchestrator.handle_message(agent_id, content, session_id)
       → gleicher Pfad wie Web-Console-Chat
       → Antwort zurück als Matrix-Nachricht
```

---

## 4. Verzeichnisstruktur

```
/home/till/hydrahive/                # Entwicklungs-Repository (lokal)
│
├── core/                          # Python-Backend
│   ├── pyproject.toml             # Abhängigkeiten, Entry-Points
│   └── src/hydrahive_core/        # 41 Python-Module
│       ├── main.py                # FastAPI-App, Lifespan, JWT-Auth
│       ├── orchestrator.py        # Boss-Agent, Tool-Loop, Dispatch
│       ├── agent_config.py        # AgentConfig Pydantic-Modell
│       ├── agent_discovery.py     # Scannt /agents/ nach agent.yaml
│       ├── agent_runtime.py       # Worker-Spawning, Heartbeat-Management
│       ├── session_manager.py     # Session-CRUD, JSON-Persistierung
│       ├── tool_registry.py       # BaseTool ABC, ToolRegistry, 49 Tools
│       ├── execution_mode_policy.py  # safe/elevated/root Enforcement
│       ├── skill_loader.py        # Agent-Skills laden und in Prompt injizieren
│       ├── learning_memory.py     # Memory-Snippets für System-Prompt
│       ├── project_config.py      # ProjectConfig Pydantic-Modell
│       ├── project_loader.py      # Scannt /projects/ nach project.yaml
│       ├── provisioner.py         # Erststart: Admin-Account, Matrix-Raum
│       ├── rate_limiter.py        # Login + API Rate-Limiting
│       ├── auth_utils.py          # JWT encode/decode, Dependency-Injection
│       ├── heartbeat.py           # Periodische Agenten-Tasks (Cron-artig)
│       ├── gitea.py               # Gitea REST-Client
│       ├── agentlink.py           # File-basierter Handoff-Store (Fallback)
│       ├── agentlink_client.py    # AgentLink-HTTP-Client
│       ├── agentlink_listener.py  # Polling-Loop für eingehende Handoffs
│       ├── matrix_agent.py        # Matrix-Bot (conduwuit)
│       ├── discord_agent.py       # Discord-Bot (discord.py)
│       ├── telegram_agent.py      # Telegram-Bot (python-telegram-bot)
│       ├── whatsapp_agent.py      # WhatsApp-Brücke (mautrix-whatsapp)
│       ├── whatsapp_transcribe.py # Whisper-basierte Voice-to-Text
│       ├── whatsapp_tts.py        # Text-to-Speech für WhatsApp
│       ├── router_agent_chat.py   # POST /agents/{id}/message
│       ├── router_agent_admin.py  # CRUD /agents/
│       ├── router_agent_skills.py # CRUD /agents/{id}/skills/
│       ├── router_projects.py     # CRUD /projects/
│       ├── router_project_lifecycle.py  # Start/Stop/Archive
│       ├── router_project_integrations.py  # Projekt-Messenger-Config
│       ├── router_users.py        # CRUD /users/, persönliche Agenten
│       ├── router_user_integrations.py   # Discord/Matrix pro User
│       ├── router_llm.py          # LLM-Config, Modell-Liste
│       ├── router_mcp.py          # MCP-Server-Registry
│       ├── router_backup_restore.py  # Backup/Restore-Endpunkte
│       ├── router_vpn.py          # WireGuard-Management
│       ├── router_doctor.py       # Diagnose-Endpunkt /doctor
│       ├── router_system.py       # Systeminfo, Self-Update
│       └── router_core_misc.py    # Diverses (Version, Status)
│
├── console/                       # React-Frontend
│   ├── src/pages/                 # 21 Seiten (SPA-Routing)
│   │   ├── DashboardPage.tsx      # Übersicht, System-Status
│   │   ├── AgentsPage.tsx         # Agent-Verwaltung
│   │   ├── AgentChatPage.tsx      # Chat mit Agent
│   │   ├── ChatPage.tsx           # Direkt-Chat
│   │   ├── MyAgentPage.tsx        # Persönlicher Agent
│   │   ├── ProjectsPage.tsx       # Projekt-Liste
│   │   ├── ProjectCreatePage.tsx  # Neues Projekt
│   │   ├── UsersPage.tsx          # Benutzerverwaltung
│   │   ├── LlmConfigPage.tsx      # LLM-Provider-Einstellungen
│   │   ├── McpConfigPage.tsx      # MCP-Server-Konfiguration
│   │   ├── GiteaConfigPage.tsx    # Gitea-Verbindung
│   │   ├── BackupPage.tsx         # Backup & Restore
│   │   ├── SystemPage.tsx         # Systeminfo, Self-Update
│   │   ├── AuditPage.tsx          # Audit-Log
│   │   ├── ToolsPage.tsx          # Tool-Registry-Übersicht
│   │   ├── VpnPage.tsx            # WireGuard-VPN
│   │   ├── SettingsPage.tsx       # Allgemeine Einstellungen
│   │   ├── SetupPage.tsx          # Ersteinrichtungs-Wizard
│   │   ├── WizardPage.tsx         # Geführte Konfiguration
│   │   ├── KasConfigPage.tsx      # All-Inkl KAS-Config
│   │   └── LoginPage.tsx          # Authentifizierung
│   └── vite.config.ts
│
├── installer/                     # Installer-Scripts
│   ├── install.sh                 # Haupt-Installer
│   └── modules/                   # 13 Installer-Module (01–13)
│
├── scripts/                       # Operations-Scripts (lokal)
│   ├── hydrahive-update.sh        # Deploy von Lilith → VM
│   ├── hydrahive-backup.sh        # Backup VM → Lilith
│   └── hydrahive.conf             # Lokale Konfiguration (nicht in Git)
│
└── docs/                          # Dokumentation

/opt/hydrahive/                    # Installation auf der VM
├── core/                          # Installierter Python-Code
├── venv/                          # Python-Virtualenv
├── console/                       # Gebautes React-Frontend (dist/)
└── docs/                          # Docs (handbook.md → Agent-Memory)

/agents/                           # Agent-Verzeichnisse (VM)
└── {agent-id}/
    ├── agent.yaml
    ├── soul.md
    └── memory/
        └── *.md

/projects/                         # Projekt-Verzeichnisse (VM)
└── {project-id}/
    ├── project.yaml
    ├── .sessions/
    │   └── {session-id}.json
    └── {dateien}/

/etc/hydrahive/                    # Konfiguration und Secrets (VM)
```

---

## 5. Agent-Konfiguration

### 5.1 Vollständiges agent.yaml-Format

```yaml
# Pflichtfelder
id: mein_agent              # Eindeutige ID, entspricht Verzeichnisname
type: boss                  # boss | worker | specialist
identity: "Ich bin Mia, eine freundliche Assistentin."
llm:
  model: claude-opus-4-5           # litellm-Modell-String
  temperature: 0.7                  # 0.0–2.0
  max_tokens: 4096
  fallback_models:                  # Optional: Fallback-Reihenfolge
    - gpt-4o
    - ollama/llama3.1
  ollama_base_url: null             # Für Remote-Ollama: "http://192.168.1.101:11434"

# Optionale Felder
soul: soul.md               # Pfad zur Persönlichkeitsdatei (relativ zu agent_dir)
                            # Alternativ: direkt als Inline-Text

skills:                     # Liste aktiver Skills (aus /agents/{id}/skills/)
  - code_review
  - git_workflow

tools:                      # Tools die dieser Agent nutzen darf
  - file_read               # Tool-IDs aus ToolRegistry
  - file_write
  - web_search
  - shell_exec
  - git_status
  - git_commit
  - dispatch_task           # Nur sinnvoll für Boss-Agenten
  - read_memory
  - write_memory

allowed_agents:             # Worker-Agenten die dieser Boss nutzen darf
  - coder_agent
  - researcher_agent

mcp_servers:                # MCP-Server-IDs aus /etc/hydrahive/mcp_servers.json
  - mcp-godot

max_tool_rounds: 20         # Max. Tool-Call-Iterationen pro Anfrage (Standard: 20)

# Heartbeat — periodische Tasks
heartbeat:
  enabled: true
  interval: 60s             # Intervall für Standard-Heartbeat
  timeout: 90s              # Wie lange warten bevor als tot markiert
  on_failure: restart       # restart | stop | alert

heartbeat_tasks:            # Geplante Aufgaben
  - id: morning_briefing
    message: "Erstelle einen Kurzbericht über offene Tasks."
    schedule: "0 8 * * 1-5"    # Cron: Mo-Fr um 08:00
    project: mein_projekt       # Explizites Projekt (sonst: erstes Boss-Projekt)
    active_hours: "07:00-22:00" # Nur in diesem Zeitfenster ausführen

  - id: health_check
    message: "Prüfe alle Services und melde Anomalien."
    interval: 1800              # Alle 30 Minuten (Sekunden)
    escalate_to: personal_admin # AgentLink-Eskalation bei Fund
    escalate_type: bug_fix
    escalate_priority: 3
    escalate_skills:
      - incident_response

# Execution Modes — technische Tool-Permissions pro Sicherheitsstufe
execution_modes:
  default: safe             # Standard-Modus beim Starten

  safe:
    permissions:
      - filesystem.read
      - web_search

  elevated:
    permissions:
      - filesystem.read
      - filesystem.write
      - shell.exec
      - handoff.read
      - handoff.write

  root:
    permissions:
      - filesystem.read
      - filesystem.write
      - shell.exec
      - shell.system_files
      - handoff.read
      - handoff.write
      - spawn_agents
```

### 5.2 Agent-Typen

| Typ          | Beschreibung                                                     |
|--------------|------------------------------------------------------------------|
| `boss`       | Koordiniert Workers, hat `dispatch_task`-Tool, empfängt User-Anfragen |
| `worker`     | Spezialisierter Sub-Agent, wird vom Boss via `dispatch_task` gerufen |
| `specialist` | Eigenständiger Agent ohne Worker-Hierarchie (persönliche Agenten) |

### 5.3 Memory-Injektion

Alle `.md`-Dateien in `/agents/{id}/memory/` werden beim Aufbau des System-Prompts
automatisch gelesen und als `[Memory: dateiname.md]`-Block eingefügt.
Das `handbook.md` im Support-Agenten-Memory wird bei jedem Deploy aktualisiert
(`hydrahive-update.sh` Schritt 5b).

Agenten können ihr eigenes Memory über die Tools `read_memory` und `write_memory`
lesen und schreiben. Writes sind auf das eigene Memory-Verzeichnis beschränkt.

---

## 6. Sicherheitsmodell

### 6.1 Authentifizierung

Alle API-Endpunkte (außer `/health` und `/login`) erfordern einen JWT Bearer-Token.

```
POST /login {"username": "...", "password": "..."}
→ {"token": "eyJ..."}  (gültig 24 Stunden)
```

- JWT-Algorithmus: HS256
- Secret: zufällig generiert bei Erstinstallation, gespeichert in `/etc/hydrahive/jwt_secret`
- Token-Payload: `{"sub": "username", "role": "admin|user", "exp": ...}`
- Login-Rate-Limit: konfigurierbar via `RATE_LIMIT_LOGIN_*` Umgebungsvariablen

### 6.2 Rollen

| Rolle   | Rechte                                                                   |
|---------|--------------------------------------------------------------------------|
| `admin` | Alle Endpunkte, alle Execution Modes, Nutzerverwaltung, Systemkonfig     |
| `user`  | Eigenen persönlichen Agenten nutzen, `elevated`-Mode für persönl. Agent  |

### 6.3 Execution Modes

Die drei Sicherheitsstufen filtern, welche Tool-Permissions aktiv sind:

```
safe      → nur Lese-Operationen, kein Shell-Exec
elevated  → Lesen + Schreiben + Shell, kein System-File-Zugriff
root      → Alle Permissions inkl. System-Files und spawn_agents
```

`elevated` und `root` erfordern Admin-Rolle. Ausnahme: persönliche Agenten dürfen
vom Eigentümer (user-Rolle) in `elevated` betrieben werden.

Jede Nutzung von `elevated`/`root` wird im Audit-Log protokolliert.

### 6.4 Path Safety

Alle Filesystem-Tools prüfen vor jedem Zugriff, ob der Pfad innerhalb von
`/projects/{project_id}/` liegt. Die Prüfung löst Path-Traversal-Angriffe auf:

```python
# Verhindert:
#   ../../etc/passwd  (resolve + normpath)
#   /etc/passwd       (absolute Pfade außerhalb)
#   symlinks          (resolve() folgt Symlinks)
assert_path_within_project(path, project_id)
# → PathSafetyError wenn Pfad außerhalb
```

Agenten können ausschließlich auf ihr Projekt-Verzeichnis zugreifen. Memory-Writes
sind auf `/agents/{id}/memory/` beschränkt. System-File-Tools (`read_system_file`,
`write_system_file`) erfordern die Permission `shell.system_files`.

### 6.5 Shell-Blocklist

Das `shell_exec`-Tool prüft jeden Befehl gegen eine Regex-Blocklist. Blockierte
Muster (Auszug):

| Muster                              | Grund                            |
|-------------------------------------|----------------------------------|
| `rm -r`, `rm -rf`                   | Rekursives Löschen               |
| `rm .*/opt/`                        | Löschen im Installations-Dir     |
| `dd of=/dev/`                       | Disk-Destruktion                 |
| `mkfs`, `fdisk`, `parted`           | Dateisystem-Operationen          |
| `systemctl stop hydrahive-core`     | Self-Sabotage                    |
| `killall uvicorn`                   | Service-Kill                     |
| `> /etc/`, `> /opt/`, `> /bin/`    | Umleitung in Systempfade         |
| `chmod/chown .*/opt/`               | Permission-Manipulation          |
| `git clone .* /opt/`                | Code-Injection in Install-Dir    |
| `cd /opt/hydrahive && git ...`      | Git-Ops im Install-Dir           |
| `:() {`                             | Fork-Bombe                       |
| `$(...)`, `` ` `` (backticks)       | Command Substitution             |

Hintergrund: Im März 2026 hat ein Test-Agent über `shell_exec git`-Kommandos
das `/opt/hydrahive/`-Verzeichnis gelöscht. Die Blocklist wurde daraufhin eingeführt.

### 6.6 Internes Shared-Secret

Core-interne HTTP-Calls (z.B. `AskAgentTool` ruft `/agents/{id}/message` auf)
verwenden ein separates Shared-Secret (`/etc/hydrahive/internal_secret`), das nicht
den üblichen JWT-Flow durchläuft. Diese Calls sind im Execution-Mode-Check als
`internal` markiert und erhalten den angeforderten Modus ohne Admin-Check.

---

## 7. Tool-System

### 7.1 Architektur

```
BaseTool (ABC)
├── id: str                    (eindeutiger Bezeichner, z.B. "file_read")
├── name: str                  (lesbarer Name für Logs und UI)
├── description: str           (erscheint im LLM-Tool-Schema)
├── permissions_required: list (Permissions die der Agent braucht)
├── parameters: dict           (JSON-Schema für litellm function calling)
└── execute(agent_id, project_id, **kwargs) → Any

ToolRegistry
├── register(tool)
├── get(tool_id)
├── tools_for_agent(tool_ids, permissions)   ← Schnittmenge agent ∩ registry ∩ permissions
└── as_litellm_tools(tools) → list[dict]    ← Format für litellm API
```

Tools die in `agent.yaml` aufgelistet sind aber nicht in der Registry existieren,
werden stillschweigend ignoriert. Das verhindert Fehler bei veralteten Konfigurationen.

### 7.2 Tool-Kategorien

**Orchestrierung**

| Tool ID          | Beschreibung                                        |
|------------------|-----------------------------------------------------|
| `dispatch_task`  | Boss delegiert Task an Worker-Agenten               |
| `spawn_agent`    | Boss spawnt Task-Agenten on-demand                  |
| `ask_agent`      | Synchrone Anfrage an anderen Agenten                |
| `delegate_agent` | Asynchrone Delegation                               |

**Dateisystem** (path-safety-gesichert, `/projects/{id}/`)

| Tool ID       | Permission         | Beschreibung              |
|---------------|--------------------|---------------------------|
| `file_read`   | filesystem.read    | Datei lesen               |
| `file_write`  | filesystem.write   | Datei schreiben/anhängen  |

**Memory** (beschränkt auf `/agents/{id}/memory/`)

| Tool ID        | Beschreibung                                       |
|----------------|----------------------------------------------------|
| `read_memory`  | Memory-Datei lesen                                 |
| `write_memory` | Memory-Datei schreiben (persistiert über Sessions) |

**Shell**

| Tool ID              | Permission           | Beschreibung                        |
|----------------------|----------------------|-------------------------------------|
| `shell_exec`         | shell.exec           | Befehl ausführen (Blocklist aktiv)  |
| `read_system_file`   | shell.system_files   | Systemdatei lesen                   |
| `write_system_file`  | shell.system_files   | Systemdatei schreiben               |
| `wks_shell_exec`     | wks.shell            | Remote-Shell auf Workstation        |
| `wks_file_read`      | wks.filesystem       | Datei auf Workstation lesen         |
| `wks_file_write`     | wks.filesystem       | Datei auf Workstation schreiben     |

**Web & HTTP**

| Tool ID        | Beschreibung                                       |
|----------------|----------------------------------------------------|
| `web_search`   | DuckDuckGo Instant Answer API (kein API-Key)       |
| `http_request` | GET/POST/PUT/DELETE an beliebige URL               |

**Git & Gitea**

| Tool ID                  | Beschreibung                              |
|--------------------------|-------------------------------------------|
| `git_status`             | git status im Projekt-Verzeichnis         |
| `git_diff`               | Diff anzeigen                             |
| `git_commit`             | Commit erstellen                          |
| `git_push`               | Push zu Remote                            |
| `git_create_pr`          | Pull Request erstellen                    |
| `gitea_repo_inspect`     | Gitea-Repository-Metadaten               |
| `gitea_repo_tree`        | Verzeichnisstruktur                       |
| `gitea_repo_file`        | Dateiinhalt aus Gitea                     |
| `gitea_repo_commits`     | Commit-Historie                           |
| `gitea_repo_diff`        | Diff zwischen Commits/Branches            |
| `gitea_create_issue`     | Issue erstellen                           |
| `gitea_comment_issue`    | Issue kommentieren                        |
| `gitea_update_issue`     | Issue aktualisieren                       |

**AgentLink / Handoffs**

| Tool ID          | Permission       | Beschreibung                          |
|------------------|------------------|---------------------------------------|
| `write_handoff`  | handoff.write    | State-Transfer an anderen Agenten     |
| `read_handoff`   | handoff.read     | Eingehenden Handoff lesen             |

**Skills**

| Tool ID         | Beschreibung                                      |
|-----------------|---------------------------------------------------|
| `create_skill`  | Neuen Skill anlegen                               |
| `list_skills`   | Alle Skills eines Agenten auflisten               |
| `delete_skill`  | Skill löschen                                     |

**Kommunikation**

| Tool ID                    | Beschreibung                              |
|----------------------------|-------------------------------------------|
| `send_mail`                | E-Mail senden                             |
| `receive_mail`             | E-Mails empfangen/lesen                   |
| `discord_send`             | Nachricht in Discord-Kanal senden         |
| `discord_read`             | Nachrichten aus Discord-Kanal lesen       |
| `discord_list_channels`    | Kanal-Liste                               |
| `discord_list_all_channels`| Alle Kanäle des Servers                   |
| `discord_create_category`  | Kategorie erstellen                       |
| `discord_create_channel`   | Kanal erstellen                           |
| `discord_delete_channel`   | Kanal löschen                             |
| `discord_set_topic`        | Kanal-Thema setzen                        |
| `discord_rename_channel`   | Kanal umbenennen                          |
| `discord_list_members`     | Mitglieder auflisten                      |
| `discord_list_roles`       | Rollen auflisten                          |
| `discord_delete_message`   | Nachricht löschen                         |
| `discord_pin_message`      | Nachricht pinnen                          |

---

## 8. Messenger-Integrationen

### 8.1 Matrix (conduwuit)

HydraHive betreibt einen eigenen Matrix-Homeserver (conduwuit, früher tuwunel) auf
Port 6167. Jeder Agent kann einen eigenen Matrix-Account und Raum bekommen.

```
Agent-Account:     @{agent_id}:{hostname}
Admin-Account:     @admin:{hostname}
Homeserver-URL:    http://127.0.0.1:6167 (intern)
```

Der `BossMatrixAgent` in `matrix_agent.py` abonniert Matrix-Events per long-polling
und leitet eingehende Nachrichten an den Orchestrator weiter. Die Verbindung wird
beim Core-Start in `main.py` (Lifespan) aufgebaut.

**Konfiguration pro Projekt** (via Console → Projekt → Integrationen):
- Matrix-Raum-ID zuweisen
- Bot-Account aktivieren

### 8.2 Discord

Der Discord-Bot läuft als eigenständiger asyncio-Task. Ein Nutzer kann in den User-
Integrationen (Console → Einstellungen → Mein Account) seinen Discord-Bot-Token
hinterlegen. Der Bot hört auf `@mention`-Nachrichten und leitet sie an den
persönlichen Agenten weiter.

**Module:** `discord_agent.py`, `router_user_integrations.py`

```
Eingehend:  @mention → discord_agent → orchestrator.handle_message()
Ausgehend:  agent response → channel.send()
Tool:       discord_send/discord_read für aktive Nutzung durch Agenten
```

### 8.3 Telegram

Telegram-Bot via `python-telegram-bot`. Konfiguration per Bot-Token in den User-
Integrationen. Unterstützt Text-Nachrichten und Befehle.

**Modul:** `telegram_agent.py`

### 8.4 WhatsApp

WhatsApp-Unterstützung über eine mautrix-whatsapp-Bridge (separater systemd-Service,
optional bei Installation). Die Bridge verbindet sich mit dem internen conduwuit-
Homeserver. Agenten empfangen WhatsApp-Nachrichten als Matrix-Events.

Zusätzlich:
- `whatsapp_transcribe.py`: Sprachnachrichten → Text (Whisper)
- `whatsapp_tts.py`: Antworten als Sprachnachricht zurückschicken

**Wichtig:** WhatsApp-Bridge erfordert einmalige QR-Code-Authentifizierung.
Nach Neustart der Bridge muss der QR-Code erneut gescannt werden.

---

## 9. Session-Management

### 9.1 Datenmodell

```python
class MessageRole(Enum):
    USER      = "user"
    ASSISTANT = "assistant"
    SYSTEM    = "system"
    TOOL      = "tool"

@dataclass
class Message:
    role:      MessageRole
    content:   str
    timestamp: str        # ISO-8601 UTC
    agent_id:  str | None # produzierender Agent
    metadata:  dict       # Tool-Name, Tool-Call-ID etc.

@dataclass
class Session:
    id:         str       # UUID
    project_id: str
    agent_id:   str
    messages:   list[Message]
    created_at: str
    updated_at: str
```

### 9.2 Persistierung

Sessions werden als JSON-Dateien unter `/projects/{project_id}/.sessions/{session_id}.json`
gespeichert. Der `SessionManager` hält aktive Sessions im Speicher und schreibt bei
jeder Änderung asynchron auf Disk.

Eine neue Session entsteht wenn kein `session_id` übergeben wird oder die angegebene
Session nicht existiert. Ein Projekt hat keine feste "aktive Session" — der Client
verwaltet die Session-ID selbst (stateless API).

### 9.3 Tool-Ergebnis-Kürzung

Große Tool-Ergebnisse werden vor dem Einfügen in den LLM-Kontext automatisch
gekürzt, um Token-Limits nicht zu sprengen:

| Ergebnistyp         | Max. Zeichen |
|---------------------|-------------|
| Git-Diffs/Patches   | 6000        |
| Repository-Trees    | 3000        |
| JSON-Blobs / Logs   | 4000        |

---

## 10. Memory-System

### 10.1 Agenten-Memory

Jeder Agent hat ein optionales Memory-Verzeichnis unter `/agents/{id}/memory/`. Alle
`.md`-Dateien darin werden automatisch in den System-Prompt injiziert:

```
[Memory: handbook.md]
<Inhalt der Datei>
[/Memory: handbook.md]
```

Die Injektion erfolgt in `learning_memory.py → build_learning_prompt_snippet()`.
Die Größe ist begrenzt — sehr große Memory-Dateien werden auf relevante Abschnitte
gekürzt.

### 10.2 Skill-System

Skills sind wiederverwendbare Verhaltens-Schnipsel (`.md`-Dateien in
`/agents/{id}/skills/`). Sie werden über `skill_loader.py` geladen und über
`skills_to_system_prompt()` als strukturierter Block in den System-Prompt eingefügt.

Skills können über Tools `create_skill`, `list_skills`, `delete_skill` dynamisch
verwaltet werden. Dadurch können Agenten ihr eigenes Verhalten zur Laufzeit anpassen.

### 10.3 AgentLink Handoffs

AgentLink ist ein Task-Queue / State-Transfer-System für Agenten-zu-Agenten-
Kommunikation über Session-Grenzen hinweg. Ein Agent schreibt einen Handoff
(Kontext + strukturierte Daten), ein anderer Agent liest ihn.

```
Primär:   HTTP-Client gegen AgentLink-Service (:8010)
Fallback: File-basierter Store in /projects/{id}/agentlink/ oder /agents/{id}/agentlink/
```

Handoffs haben eine TTL (Standard: 3600 Sekunden). Nach dem Lesen wird der Handoff
standardmäßig gelöscht (`consume=true`).

Anwendungsfall: Ein Heartbeat-Task findet ein Problem und eskaliert es mit
`write_handoff(to_agent="personal_admin", data={...})` an den Admin-Agenten.

---

## Anhang: Router-Übersicht

| Modul                           | Endpunkte                              |
|---------------------------------|----------------------------------------|
| `router_agent_chat.py`          | `POST /agents/{id}/message`            |
| `router_agent_admin.py`         | `GET/POST/PUT/DELETE /agents/`         |
| `router_agent_skills.py`        | `GET/POST/DELETE /agents/{id}/skills/` |
| `router_projects.py`            | `GET/POST/PUT/DELETE /projects/`       |
| `router_project_lifecycle.py`   | `POST /projects/{id}/start|stop`       |
| `router_project_integrations.py`| `GET/PUT /projects/{id}/integrations`  |
| `router_users.py`               | `GET/POST/PUT/DELETE /users/`          |
| `router_user_integrations.py`   | `GET/PUT /users/{id}/integrations`     |
| `router_llm.py`                 | `GET/PUT /llm/config`, `/llm/models`   |
| `router_mcp.py`                 | `GET/POST/DELETE /mcp/servers`         |
| `router_backup_restore.py`      | `POST /admin/backup`, `/admin/restore` |
| `router_vpn.py`                 | `GET/POST /vpn/peers`                  |
| `router_doctor.py`              | `GET /doctor`                          |
| `router_system.py`              | `GET /system/info`, `POST /system/update` |
| `router_core_misc.py`           | `GET /health`, `GET /version`          |
