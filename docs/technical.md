# HydraHive — Technische Dokumentation

Diese Dokumentation richtet sich an Entwickler die HydraHive verstehen, erweitern oder deployen wollen.

---

## Inhaltsverzeichnis

1. [Architektur-Übersicht](#1-architektur-übersicht)
2. [Verzeichnisstruktur](#2-verzeichnisstruktur)
3. [Core-Module](#3-core-module)
4. [Agent-Lebenszyklus](#4-agent-lebenszyklus)
5. [Orchestrator und Message-Flow](#5-orchestrator-und-message-flow)
6. [Konfigurationsformate](#6-konfigurationsformate)
7. [Tool-System](#7-tool-system)
8. [Skill-System (QMD)](#8-skill-system-qmd)
9. [Matrix-Integration](#9-matrix-integration)
10. [Session-Management](#10-session-management)
11. [Sicherheitsmodell](#11-sicherheitsmodell)
12. [Installer-Architektur](#12-installer-architektur)

---

## 1. Architektur-Übersicht

```
┌─────────────────────────────────────────────────────┐
│                   Browser (React)                    │
│              console/src/  (Vite + Tailwind)         │
└────────────────────────┬────────────────────────────┘
                         │ HTTPS (nginx)
                         ▼
┌─────────────────────────────────────────────────────┐
│              nginx (Port 443 / 80→443)               │
│   Static: /opt/hydrahive/console/                      │
│   Proxy:  /api/ → localhost:8765                     │
└────────────────────────┬────────────────────────────┘
                         │ HTTP
                         ▼
┌─────────────────────────────────────────────────────┐
│           HydraHive Core (FastAPI, Port 8765)          │
│                                                      │
│  ┌──────────┐  ┌─────────────┐  ┌───────────────┐  │
│  │  Agent   │  │  Project    │  │   Session     │  │
│  │Discovery │  │  Loader     │  │   Manager     │  │
│  └────┬─────┘  └──────┬──────┘  └───────┬───────┘  │
│       │               │                  │          │
│  ┌────▼──────────────────────────────────▼───────┐  │
│  │              Orchestrator                      │  │
│  │  Boss-Agent → dispatch_task → Worker-Agenten  │  │
│  └────────────────────┬──────────────────────────┘  │
│                       │                              │
│  ┌────────────────────▼──────────────────────────┐  │
│  │            AgentRuntime + Watchdog             │  │
│  │   Heartbeat-Loop, Matrix-Client, TTL           │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │  conduwuit (Matrix) │
              │  Port 6167          │
              └─────────────────────┘
```

### Komponenten

| Komponente | Technologie | Aufgabe |
|---|---|---|
| **Console** | React 18, Vite, Tailwind, shadcn | Web-UI |
| **Core** | FastAPI, Python 3.12, litellm | REST-API, Orchestration |
| **Matrix** | conduwuit, matrix-nio | Messaging-Backend |
| **nginx** | nginx 1.24+ | Reverse-Proxy, TLS |
| **Ollama** | ollama | Lokale LLM-Inferenz |

---

## 2. Verzeichnisstruktur

### Repository

```
hydrahive/
├── core/                          # Python-Backend
│   └── src/hydrahive_core/
│       ├── main.py                # FastAPI-App, Kern-Endpoints, Router-Wiring
│       ├── orchestrator.py        # Boss-Agent, Task-Dispatching
│       ├── agent_runtime.py       # Agent-Lifecycle, Watchdog
│       ├── agent_discovery.py     # /agents/ Verzeichnis beobachten
│       ├── agent_config.py        # agent.yaml Parsing (Pydantic)
│       ├── project_config.py      # project.yaml Parsing (Pydantic)
│       ├── project_loader.py      # /projects/ Verzeichnis beobachten
│       ├── session_manager.py     # Chat-History, Persistenz
│       ├── matrix_agent.py        # Matrix-Bot Basisklasse + BossMatrixAgent
│       ├── skill_loader.py        # QMD Skill-Parsing
│       ├── tool_registry.py       # Tool-Interface, Path-Safety
│       ├── router_*.py            # Ausgelagerte API-Routen (Auth, Projekte, Chat, MCP, Gitea, ...)
│       ├── execution_mode_policy.py # safe/elevated/root fuer Agenten
│       └── provisioner.py         # Matrix-User, Samba-Share anlegen
├── console/                       # React-Frontend
│   ├── src/
│   │   ├── pages/                 # Seiten (eine Datei pro Route)
│   │   ├── components/            # Wiederverwendbare Komponenten
│   │   │   ├── layout/AdminLayout.tsx
│   │   │   ├── SkillsPanel.tsx
│   │   │   └── WebhooksPanel.tsx
│   │   ├── hooks/useAuth.ts       # Auth-State
│   │   └── lib/api.ts             # API-Client
│   └── package.json
├── installer/
│   ├── install.sh                 # Haupt-Installer
│   ├── amem/                      # A-MEM Shared Memory Installer + Services
│   └── modules/
│       ├── 01_os_check.sh
│       ├── 02_gpu_detect.sh
│       ├── 03_dependencies.sh
│       ├── 04_tuwunel.sh          # conduwuit Matrix-Server
│       ├── 05_admin_account.sh
│       ├── 06_core_service.sh     # Python-venv, systemd-Service
│       ├── 07_console.sh          # npm build, nginx-Config
│       ├── 08_ollama.sh           # Ollama (PROFILE=full)
│       └── 09_https.sh            # TLS, self-signed Cert
└── docs/
    ├── handbook.md
    ├── technical.md
    ├── api-reference.md
    └── development.md
```

### Laufzeit (auf dem Server)

```
/opt/hydrahive/
├── core/src/hydrahive_core/     # Core-Quellcode (wird deployed)
├── console/                   # Gebaute React-App
└── venv/                      # Python-Virtualenv

/agents/                       # Agent-Konfigurationen
├── boss-main/
│   ├── agent.yaml
│   ├── soul.md
│   └── skills/

/projects/                     # Projekt-Daten
├── buchhaltung/
│   ├── project.yaml
│   ├── webhooks.json
│   └── sessions/              # Chat-History

/etc/hydrahive/                  # Secrets und Config
├── admin_credentials          # Matrix-Admin-Passwort
├── jwt_secret                 # JWT-Signing-Key
├── llm_config.json            # LLM-Konfiguration
├── llm_env                    # Env-Variablen für LLM
├── claude_oauth_token         # Claude OAuth Token
├── users.json                 # Benutzer-Datenbank
├── agent_tokens/              # Matrix-Bot-Tokens
└── tls/                       # TLS-Zertifikate

/var/log/hydrahive/
└── audit.jsonl                # Audit-Log (append-only)
```

---

## 3. Core-Module

### main.py

FastAPI-Applikation mit Lifespan-Management. Enthält die Kern-Endpoints und bindet die ausgelagerten Router-Module ein.

**Wichtige Router-Module:**
- `router_system.py` — Status, Update, Logs, Health
- `router_projects.py` — Projekt-Workflows
- `router_agent_chat.py` — direkter Agent-Chat, Session-History
- `router_user_integrations.py` — WKS und Discord
- `router_project_integrations.py` — Webhooks und AgentLink
- `router_project_lifecycle.py` — Provisioning und Deprovisioning
- `router_llm.py` — Modell- und OAuth-Konfiguration
- `router_mcp.py` — MCP-Server
- `router_backup_restore.py` — Backups und Restore
- `router_core_misc.py` — Setup, Auth, Tools, Logs

**Startup-Reihenfolge:**
1. `AgentDiscovery.start()` — beobachtet `/agents/` mit watchdog
2. `ProjectLoader.start()` — beobachtet `/projects/`
3. `SessionManager.start()` — lädt aktive Sessions
4. `AgentRuntime.start()` — startet Core-Agenten, Watchdog-Loop
5. JWT-Secret laden oder generieren
6. Matrix Admin-Token holen
7. `_setup_matrix_clients()` — BossMatrixAgent für Projekte mit Room starten

### agent_discovery.py

Beobachtet `/agents/` mit `watchdog`. Bei Änderungen an `agent.yaml`-Dateien wird die Config neu geladen. Liefert `AgentConfig`-Objekte an AgentRuntime.

### agent_runtime.py

Verwaltet laufende Agenten als `AgentHandle`-Objekte:

```python
@dataclass
class AgentHandle:
    config:         AgentConfig
    heartbeat_cfg:  HeartbeatConfig
    status:         AgentStatus      # STARTING | RUNNING | STOPPED | ERROR
    last_heartbeat: float
    restart_count:  int
    task:           asyncio.Task     # _run_agent() Coroutine
    matrix_client:  MatrixAgent | None
```

**Watchdog-Loop** (`_watchdog_loop`, alle 10s):
- Prüft Heartbeat-Alter gegen `timeout`
- Bei Timeout: `on_failure` auswerten (restart / stop / alert)

**Matrix-Watchdog** (`_run_agent`):
- `matrix_client.run()` läuft in separatem Task
- Bei unerwartetem Ende → 15s warten → `start()` + `run()` neu

### orchestrator.py

Kernstück des Systems. Empfängt Nutzer-Nachrichten, baut LLM-Kontext auf, führt Tool-Calls aus.

**Message-Queue:** Pro Projekt eine `asyncio.Queue`. Parallele Nachrichten werden sequenziell verarbeitet.

**Ablauf `handle_message()`:**
1. Nachricht in Session-History speichern
2. `_queue_worker()` aufrufen (serialisiert über Queue)
3. `_handle_message_impl()`:
   - Soul + Skills laden → System-Prompt
   - Session-History + System-Prompt → LLM (via litellm)
   - Tool-Calls ausführen (`dispatch_task` → Worker spawnen)
   - Antwort in Session speichern
4. Tupel `(response, workers_used)` zurückgeben

**Streaming (`handle_message_stream()`):**
- Identischer Flow, aber `litellm.acompletion(stream=True)`
- Yields `data: {"text": "..."}` SSE-Events
- Abschluss: `data: {"done": true}`

### session_manager.py

Verwaltet Chat-Sessions. Jedes Projekt hat genau eine aktive Session.

```
/projects/<id>/sessions/
├── <uuid>.json       # vergangene Sessions
└── active.json       # aktive Session (Symlink oder direkte Datei)
```

Session enthält `Message`-Objekte mit `role`, `content`, `timestamp`, `agent_id`.

`get_context(max_messages=50)` gibt die letzten N Nachrichten als `list[dict]` zurück (OpenAI-kompatibel für litellm).

---

## 4. Agent-Lebenszyklus

```
Verzeichnis /agents/<id>/ angelegt
         │
         ▼
AgentDiscovery erkennt agent.yaml (watchdog)
         │
         ▼
AgentConfig wird geparst (Pydantic-Validierung)
         │
         ▼
AgentRuntime._spawn() → AgentHandle anlegen
         │
         ▼
_run_agent() als asyncio.Task starten
         │
    ┌────▼────┐
    │ Matrix? │
    │  ja     ▼─────────────────────────────────────┐
    │         matrix_client.start()                  │
    │         matrix_client.run() (Sync-Loop)        │
    │         ↕ Watchdog: bei Absturz → Restart      │
    │  nein   ▼─────────────────────────────────────┘
    │         Heartbeat-Ticker (asyncio.sleep)
    └─────────┘
         │
         ▼
Watchdog prüft last_heartbeat alle 10s
         │
    Timeout? → on_failure: restart → AgentRuntime._respawn()
```

---

## 5. Orchestrator und Message-Flow

```
Nutzer schreibt Nachricht
         │
         ▼
POST /projects/{id}/message[/stream]
         │
         ▼
orchestrator.handle_message()
         │
         ▼  (asyncio.Queue pro Projekt)
_handle_message_impl()
         │
         ├─ 1. User-Message → SessionManager.append()
         │
         ├─ 2. Boss-Agent Soul laden (soul.md)
         │
         ├─ 3. Skills matchen (scope=always oder trigger-match)
         │
         ├─ 4. System-Prompt = Soul + Skills
         │
         ├─ 5. litellm.acompletion(messages=[system, history, user])
         │
         └─ 6. Tool-Calls?
              │
              ├─ dispatch_task → Worker-Agent spawnen
              │   └─ Worker führt Tool aus (file_read, web_search, ...)
              │   └─ Ergebnis zurück an Boss
              │
              └─ Weitere LLM-Iteration (bis max_iterations oder fertig)
                       │
                       ▼
              Assistant-Message → SessionManager.append()
                       │
                       ▼
              return (response_text, workers_used)
```

---

## 6. Konfigurationsformate

### agent.yaml

```yaml
id: mein-agent              # Pflicht, eindeutig, entspricht Verzeichnisname
type: specialist             # boss | specialist | worker

identity: Mein Agent Name    # Anzeigename

llm:
  model: llama3.1:8b        # litellm-kompatibler Modellname
  temperature: 0.7           # 0.0–2.0
  max_tokens: 4096

soul: |                     # Optional: inline Persönlichkeit
  Du bist ein hilfreicher Assistent...
  # oder leer lassen → soul.md wird verwendet

tools:                       # Optional, Standard: []
  - file_read
  - file_write
  - web_search
  - http_request
  - dispatch_task
  - spawn_agent

heartbeat:
  interval: 30s              # oder: 30, "2m", 120
  timeout: 90s
  on_failure: restart        # restart | stop | alert
```

### project.yaml

```yaml
id: mein-projekt
version: "1.0.0"

identity:
  name: Mein Projekt
  description: Optional

agents:
  boss: boss-agent-id
  workers:
    - specialist-1
    - specialist-2

matrix:
  room: "!roomid:server.de"  # leer → wird beim Provisionieren angelegt

filesystem:
  path: /projects/mein-projekt   # Standard
  samba: true
  nfs: false

system:
  user: proj_mein-projekt    # Standard
  group: proj_mein-projekt   # Standard

chat:
  show_swarm: false
```

### soul.md

Freier Markdown-Text. Wird unverändert als System-Prompt-Präfix verwendet:

```markdown
# Steuerbert

Du bist Steuerbert, ein erfahrener Steuerberater-Assistent.

## Dein Charakter
- Präzise und verlässlich
- Du weist auf Fristen und Risiken hin
- Du arbeitest ausschließlich nach deutschem Steuerrecht

## Wichtige Regeln
- Keine Rechtsberatung, nur Information
- Bei Unsicherheit: Fachmann empfehlen
```

### QMD Skill-Datei

```markdown
---
skill: Umsatzsteuer
version: "1.0"
scope: on-demand           # always | on-demand
triggers:
  - umsatzsteuer
  - mehrwertsteuer
  - ust
priority: 10               # niedrig = höhere Priorität
---

## Umsatzsteuer in Deutschland

Standardsatz: 19%
Ermäßigter Satz: 7% für ...
```

---

## 7. Tool-System

Tools erweitern was ein Agent tun kann. Das Interface:

```python
class BaseTool(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...          # "file_read"

    @property
    @abstractmethod
    def description(self) -> str: ... # Für LLM sichtbar

    @property
    @abstractmethod
    def parameters(self) -> dict: ... # JSON-Schema (function calling)

    @abstractmethod
    async def execute(
        self,
        agent_id:   str,
        project_id: str,
        **kwargs,
    ) -> str: ...                     # Rückgabe als String ans LLM
```

### Filesystem-Safety

Alle Filesystem-Tools müssen `assert_path_within_project()` aufrufen:

```python
from .tool_registry import assert_path_within_project

async def execute(self, agent_id, project_id, path, **kwargs):
    safe_path = assert_path_within_project(path, project_id)
    # safe_path ist ein Path-Objekt, garantiert innerhalb /projects/<id>/
    content = safe_path.read_text()
    return content
```

Path-Traversal (`../../etc/passwd`), absolute Pfade außerhalb des Projekts und Symlink-Escapes werden mit `PathSafetyError` abgelehnt.

### Tool in Registry eintragen

```python
# tool_registry.py
registry = ToolRegistry()
registry.register(FileReadTool())
registry.register(FileWriteTool())
# ...
```

---

## 8. Skill-System (QMD)

QMD = YAML-Frontmatter + Markdown. Skills werden aus `/agents/<id>/skills/*.md` geladen.

**Ladereihenfolge:**
1. `load_skills(agent_dir)` lädt alle `.md`-Dateien
2. Sortierung nach `priority` (aufsteigend)
3. `select_skills(skills, user_text)` filtert nach scope/triggers
4. `skills_to_system_prompt(selected)` baut System-Prompt-Abschnitt

**scope-Logik:**
- `always` → immer geladen
- `on-demand` → geladen wenn mindestens ein `trigger`-Keyword im Nutzer-Text vorkommt (case-insensitive)

**Hot-Reload:** Skills werden bei jedem Request neu geladen — Änderungen an Skill-Dateien wirken sofort ohne Core-Neustart.

---

## 9. Matrix-Integration

### Architektur

```
conduwuit (Port 6167, intern)
    ↑ nginx proxy (Port 8008, extern)
    ↑
matrix-nio AsyncClient
    ↑
MatrixAgent (Basisklasse)
    ↑
BossMatrixAgent (pro Projekt mit Room)
```

### MatrixAgent Basisklasse

Kapselt Login, Room-Joining, Sync-Loop und Message-Sending:

```python
class MatrixAgent(ABC):
    async def start(self) -> None: ...    # Login + Rooms joinen
    async def run(self) -> None: ...      # Sync-Loop (blockierend)
    async def stop(self) -> None: ...     # Cleanup
    async def send_message(self, room_id, text): ...
    async def send_markdown(self, room_id, markdown): ...

    @abstractmethod
    async def on_user_message(self, room, text, sender): ...
```

### Authentifizierung

Bot-Accounts werden beim ersten Start automatisch auf conduwuit registriert. Der Access-Token wird in `/etc/hydrahive/agent_tokens/<id>.json` gespeichert. Bei Neustart wird der gespeicherte Token verwendet — kein Re-Login nötig.

### Watchdog

Der Matrix-Client läuft als `asyncio.Task` innerhalb von `_run_agent()`. Bei unerwartetem Ende (Exception, WebSocket-Drop):
1. `matrix_client.stop()` wurde bereits im `finally`-Block von `run()` aufgerufen
2. 15 Sekunden warten
3. `matrix_client.start()` → `matrix_client.run()` neu starten

---

## 10. Session-Management

Jedes Projekt hat genau eine aktive Session. Sessions werden auf Disk persistiert.

```python
@dataclass
class Message:
    role:      MessageRole    # user | assistant | system | tool
    content:   str
    timestamp: str            # ISO-8601 UTC
    agent_id:  str | None
    metadata:  dict

@dataclass
class Session:
    id:         str           # UUID4
    project_id: str
    started_at: str
    ended_at:   str | None
    messages:   list[Message]
```

`get_context(max_messages=50)` gibt die letzten N Nachrichten als `[{"role": "user", "content": "..."}]` zurück — direkt kompatibel mit litellm/OpenAI-API.

---

## 11. Sicherheitsmodell

### Authentifizierung

- JWT HS256, 24h Gültigkeit
- Secret wird in `/etc/hydrahive/jwt_secret` gespeichert (600, `hydrahive:hydrahive`)
- Bei fehlendem Secret → `503` (kein stilles Akzeptieren des leeren Secrets)
- Rate-Limiting: 10 Login-Versuche pro Minute pro IP

### Autorisierung

Aktuell: alle authentifizierten Nutzer haben vollen Zugriff. Rollenmodell ist vorbereitet (`role: admin` in users.json) aber noch nicht granular durchgesetzt.

### Filesystem-Isolation

Jedes Projekt läuft als eigener Linux-User `proj_<id>`. Agenten können nur auf `/projects/<id>/` schreiben. `assert_path_within_project()` wird in allen Filesystem-Tools erzwungen.

### Setup-Schutz

`POST /setup` ist nur verfügbar solange `users.json` leer ist. Race Condition zwischen parallelen Requests verhindert durch `asyncio.Lock`. Atomares Schreiben via `tmp.replace(target)`.

### Audit-Log

Alle sicherheitsrelevanten Aktionen werden in `/var/log/hydrahive/audit.jsonl` protokolliert. Append-only, non-blocking (Fehler beim Schreiben unterbrechen die Hauptoperation nicht).

---

## 12. Installer-Architektur

Der Installer ist modular aufgebaut. Jedes Modul ist ein eigenständiges bash-Skript das von `install.sh` via `source` eingebunden wird. Alle Module teilen:

- `info()`, `success()`, `warn()`, `error()` Funktionen (exportiert)
- `$HYDRAHIVE_DIR`, `$HYDRAHIVE_USER` Variablen
- `$PROFILE` Variable (`minimal` | `standard` | `full`)

**Idempotenz-Prinzip:** Jedes Modul prüft vor Installation ob die Komponente bereits vorhanden ist und überspringt sie wenn ja.

| Modul | Prüfung |
|---|---|
| `04_tuwunel.sh` | Versions-Check der installierten Binary |
| `06_core_service.sh` | venv existiert, users.json angelegt |
| `07_console.sh` | npm build immer, nginx config wird überschrieben |
| `08_ollama.sh` | `ollama` Binary vorhanden → skip |
| `09_https.sh` | Cert-Datei vorhanden → skip, Ablaufdatum loggen |


---

## 13. Datenfluss — Eine Nachricht von A bis Z

Wie läuft eine User-Nachricht durch das gesamte System?

```
1. Browser                POST /api/projects/buchhaltung/message/stream
                          Body: {"content": "Was ist die Umsatzsteuer?"}

2. nginx                  Proxy → localhost:8765/projects/buchhaltung/message/stream

3. FastAPI (main.py)      Auth-Check JWT → send_message_stream()

4. Orchestrator           asyncio.Queue für "buchhaltung"
   handle_message()  →   _queue_worker() → _handle_message_impl()

5. _handle_message_impl() 
   a) Session: append(USER, "Was ist die Umsatzsteuer?")
   b) System-Prompt: soul.md + QMD-Skills (on-demand: "umsatzsteuer" matched)
   c) History: letzte 20 Nachrichten aus Session
   d) Tools: dispatch_task, file_read (aus agent.yaml ∩ Registry ∩ permissions)

6. LLM-Call               OAuth-Token vorhanden?
   → Ja:  Anthropic SDK direkt (anthropic-beta: oauth-2025-04-20)
   → Nein: litellm (Ollama / OpenAI)

7. Streaming              Token für Token → SSE: data: {"text": "Die Umsatz..."}

8. Tool-Loop              LLM ruft dispatch_task auf?
   → Worker-Agent wird gespawnt (ephemeral)
   → Worker macht LLM-Call mit eigenem Kontext
   → Ergebnis zurück an Boss

9. Session                append(ASSISTANT, vollständige Antwort)

10. Browser               ReadableStream liest SSE → Token erscheinen live
    ChatPage              Markdown-Rendering (ReactMarkdown + prose)
```

---

## 14. Fehlerbehandlung und Resilience

### Agent-Ausfall
- **Heartbeat-Timeout:** Watchdog erkennt nach `timeout` Sekunden → `on_failure` Aktion
- `restart`: Agent wird neu gestartet, Matrix-Client reconnectet
- `stop`: Agent wird gestoppt, bleibt im Status `error`
- `alert`: Nur Log-Eintrag, kein automatischer Neustart

### Matrix-Verbindungsabbruch
- `matrix_agent.py` hat einen Watchdog-Task
- Bei Exception in `run()`: 15s warten → `start()` + `run()` neu
- Audit-Log: `agent.matrix_reconnect`

### Core-Neustart
- Sessions: persistent in `/projects/<id>/sessions/` → werden beim Start geladen
- Agenten: werden automatisch neu gestartet (AgentRuntime.start())
- Matrix-Rooms: werden aus `project.yaml` gelesen, Bots joinen wieder

### LLM-Fehler
- OAuth-Token abgelaufen → `[Fehler] LLM nicht erreichbar`
- Ollama offline → litellm-Fallback schlägt fehl → Fehlermeldung im Chat
- Kein automatischer Retry (geplant in zukünftiger Version)

---

## 15. Bekannte Limitierungen

| Bereich | Limitation | Workaround |
|---|---|---|
| Claude OAuth | Token läuft nach ~30 Tagen ab | `claude setup-token` wiederholen |
| main.py | Kern-Endpoints + Router-Wiring, nicht mehr alle Routen in einer Datei | Router-Refactoring weitgehend umgesetzt |
| Worker-Agenten | Keine eigene Matrix-Identität | Nur Boss ist Matrix-Bot |
| Task-Agent TTL | Nicht per Projekt konfigurierbar | 300s hardcoded in agent_runtime.py |
| Sessions | Kein Memory zwischen Sessions | QMD-Skills als persistentes Wissen nutzen |
| AgentLink | State-Transfer noch nicht produktiv | #13 offen |
