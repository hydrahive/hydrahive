# HydraHive -- Architektur-Dokumentation

## 1. Ueberblick

HydraHive ist eine selbst-gehostete Multi-Agenten-Plattform, die es ermoeglicht, KI-Agenten zu konfigurieren, in Projekte einzubinden und ueber eine Web-Konsole zu steuern. Jeder Agent hat eigene LLM-Konfiguration, Memory, Skills und Tools. Ein Boss-Agent orchestriert Worker-Tasks, fuehrt Tool-Loops aus und streamt Antworten per SSE an das Frontend. Das System unterstuetzt Multi-User mit Rollen (admin/user), OAuth, Matrix-Chat-Integration, Discord, WhatsApp und ein Plugin-System.

### Tech-Stack

| Schicht    | Technologie                                                  |
|------------|--------------------------------------------------------------|
| Backend    | Python 3.12, FastAPI, uvicorn, litellm, Pydantic             |
| Frontend   | React 18, TypeScript, Vite, Tailwind CSS                     |
| Datenbank  | SQLite (Sessions, Metriken), JSON-Dateien (Konfiguration)    |
| LLM-Proxy  | litellm (Multi-Provider: Anthropic, OpenAI, Ollama, etc.)    |
| Messaging  | Matrix (Synapse), Discord, WhatsApp, Telegram                |
| Deployment | systemd (hydrahive-core.service), nginx Reverse Proxy        |

### Verzeichnisstruktur

```
/home/till/octopos/            # Lokales Entwicklungs-Repo
  core/                        # Python-Backend (FastAPI)
    src/hydrahive_core/        # 119 Python-Module
  console/                     # React-Frontend (Vite)
    src/pages/                 # 48 Page-Komponenten
    src/components/            # Shared Components
    src/hooks/                 # React Hooks
    src/lib/                   # API-Client, Utilities
  installer/                   # install.sh + Module (Erst-Installation)
  scripts/                     # hydrahive-update.sh, hydrahive-backup.sh
  docs/                        # Dokumentation (handbook, handbuch, API-Ref)
  plugins/                     # Plugin-Verzeichnis
  voice/                       # Voice-Integration
  whatsapp-bridge/             # WhatsApp-Bridge
  website/                     # hydrahive.org Webseite
```

Auf dem Server:

```
/opt/hydrahive/                # Installationsverzeichnis (Git Checkout)
/etc/hydrahive/                # Konfiguration (llm_config.json, .env, etc.)
/agents/                       # Agent-Definitionen (YAML + Memory)
/projects/                     # Projekt-Verzeichnisse + Sessions (SQLite)
```

---

## 2. Backend-Architektur

### 2.1 Modul-Uebersicht (119 Module)

#### Kern-Runtime
| Modul                    | Aufgabe                                                    |
|--------------------------|------------------------------------------------------------|
| `main.py`                | FastAPI-App, Lifespan (Discovery, Runtime, JWT, Matrix)    |
| `orchestrator.py`        | Boss-Agent Dispatching, vereint alle Sub-Module             |
| `orchestrator_dispatch.py` | Tool-Loop, Worker-Dispatch, parallele Ausfuehrung, Synthese |
| `orchestrator_llm.py`    | LLM-Calls (litellm), Failover, OAuth, Retry-Logik          |
| `orchestrator_context.py`| System-Prompt, Memory-Budget, Context-Compaction            |
| `orchestrator_tools.py`  | Tool-Execution, Truncation, Signatur-Logging                |
| `orchestrator_mcp.py`    | MCP + Plugin Tool-Integration                               |
| `orchestrator_stream.py` | SSE-Streaming Response Handler                              |
| `session_manager.py`     | SQLite-basierte Sessions, aktive Sessions in Memory         |
| `session_memory.py`      | Session-Memory-Persistierung                                |
| `agent_config.py`        | Agent-YAML-Parsing, LLM-Config pro Agent                   |
| `agent_discovery.py`     | Agent-Verzeichnis scannen, YAML laden                       |
| `agent_runtime.py`       | Agent-Lifecycle (Start, Stop, Health)                       |
| `agent_teams.py`         | Agent-Teams fuer koordinierte Tasks                         |

#### Tool-System
| Modul                | Aufgabe                                            |
|----------------------|----------------------------------------------------|
| `tool_registry.py`   | `BaseTool` ABC + `ToolRegistry` Singleton           |
| `tool_loader.py`     | Automatisches Laden aller Tool-Implementierungen    |
| `tool_groups.py`     | Tool-Gruppierung (Filesystem, Git, System, etc.)    |
| `skill_loader.py`    | Skill-Pakete laden und als Tools registrieren        |
| `skill_package_rule.py` | Regeln fuer Skill-Package-Ausfuehrung              |

#### Context Management
| Modul                    | Aufgabe                                             |
|--------------------------|-----------------------------------------------------|
| `context_lifecycle.py`   | Context-Lifecycle (Erstellen, Loeschen, Bereinigen) |
| `orchestrator_context.py`| System-Prompt-Bau, Memory-Budget, Compaction        |
| `token_estimation.py`    | Token-Schaetzung fuer Context-Budget                |
| `prompt_speculation.py`  | Prompt-Prefetch/Spekulation                         |

#### Security
| Modul                     | Aufgabe                                             |
|---------------------------|-----------------------------------------------------|
| `permission_classifier.py`| Tool-Typ-Klassifikation, Permission-Check            |
| `destructive_warning.py`  | Warnung bei destruktiven Operationen                 |
| `boss_policy.py`          | Auto-Verification nach Mutations (Feature-Flag)      |
| `execution_mode_policy.py`| Execution-Mode-Policy (Plan/Execute)                 |
| `guard_utils.py`          | 15+ Guard-Checks im Core                             |
| `auth_utils.py`           | JWT-Verifikation, Token-Utilities                    |
| `secret_encryption.py`    | Agent-Secret-Verschluesselung                        |
| `rate_limiter.py`         | Rate-Limiting (Memory oder Redis)                    |

#### Infrastructure & Metrics
| Modul                 | Aufgabe                                              |
|-----------------------|------------------------------------------------------|
| `turn_journal.py`     | Ausfuehrliches Logging jedes Tool-Turns               |
| `session_metrics.py`  | Token-Verbrauch, Latenz, Metriken pro Session         |
| `proactive_mode.py`   | Background-Tasks mit Safety-Constraints               |
| `heartbeat.py`        | Core-Heartbeat fuer Health-Checks                     |
| `config_loader.py`    | Zentrales Config-Laden aus /etc/hydrahive/            |
| `migrations.py`       | Idempotente Schema-Migrationen beim Start             |

#### Verification & Workers
| Modul                      | Aufgabe                                          |
|----------------------------|--------------------------------------------------|
| `verification_contract.py` | Verification-Contract fuer Boss-Policy            |
| `built_in_workers.py`      | 4 spezialisierte Worker: explore, plan, verify, review |

#### API Router (31 Router-Module)
| Router                          | API-Bereich                        |
|---------------------------------|------------------------------------|
| `router_agent_chat.py`         | Chat-Endpoints (Message senden/empfangen) |
| `router_agent_admin.py`        | Agent CRUD (erstellen, bearbeiten, loeschen) |
| `router_agent_skills.py`       | Skills pro Agent verwalten          |
| `router_agent_secrets.py`      | Agent-spezifische Secrets           |
| `router_backup_restore.py`     | Backup & Restore                    |
| `router_brain.py`              | HydraBrain (Knowledge-Base)         |
| `router_butler.py`             | Butler-Automationen                  |
| `router_webhooks_butler.py`    | Webhook-getriggerte Butler-Tasks     |
| `router_codeserver.py`         | Code-Server-Integration              |
| `router_config_map.py`         | Konfigurationsuebersicht              |
| `router_core_misc.py`          | Misc-Endpoints (Health, Version)      |
| `router_doctor.py`             | System-Diagnose                       |
| `router_extensions.py`         | Extension-Management                  |
| `router_github.py`             | GitHub-Integration                    |
| `router_groups.py`             | User-Gruppen                          |
| `router_hub.py`                | HydraHub (Marketplace)                |
| `router_invites.py`            | Einladungs-System                     |
| `router_knowledge.py`          | Knowledge-Base-Endpoints              |
| `router_llm.py`                | LLM-Konfiguration                     |
| `router_mcp.py`                | MCP-Server-Verwaltung                 |
| `router_migration.py`          | Daten-Migrationen                     |
| `router_notifications.py`      | Benachrichtigungen                    |
| `router_openai_compat.py`      | OpenAI-kompatible API                 |
| `router_pipelines.py`          | Pipeline-Definitionen                 |
| `router_plugins.py`            | Plugin-Management                     |
| `router_projects.py`           | Projekt CRUD                          |
| `router_project_integrations.py` | Projekt-Integrationen (Matrix, Git) |
| `router_project_lifecycle.py`  | Projekt-Lifecycle-Events              |
| `router_repos.py`              | Repository-Verwaltung                 |
| `router_schedules.py`          | Scheduled Tasks (Cron)                |
| `router_searxng.py`            | SearXNG Web-Suche                     |
| `router_servers.py`            | Server-Management                     |
| `router_skill_packages.py`     | Skill-Packages                        |
| `router_system.py`             | System-Info, Logs                     |
| `router_tailscale.py`          | Tailscale VPN                         |
| `router_usage.py`              | Token-Verbrauch / Usage-Statistiken   |
| `router_user_integrations.py`  | User-Integrationen (Discord, etc.)    |
| `router_users.py`              | User CRUD                             |
| `router_voice.py`              | Voice-Input/Output                    |
| `router_vpn.py`                | VPN-Konfiguration                     |
| `router_a2a.py`                | Agent-to-Agent-Kommunikation          |

#### Services (Background-Tasks)
| Modul                    | Aufgabe                                     |
|--------------------------|---------------------------------------------|
| `notification_service.py`| Push-Benachrichtigungen                      |
| `scheduler_service.py`   | Cron-basierte Scheduled Tasks                |
| `cleanup_service.py`     | Alte Sessions/Logs bereinigen                |
| `alert_service.py`       | Alert-Regelwerk und Benachrichtigungen       |

#### Integrationen
| Modul                  | Aufgabe                                       |
|------------------------|-----------------------------------------------|
| `matrix_agent.py`      | Matrix-Chat-Client fuer Boss-Agenten           |
| `discord_agent.py`     | Discord-Bot-Integration                        |
| `whatsapp_agent.py`    | WhatsApp-Bridge (via mautrix)                  |
| `whatsapp_transcribe.py` | WhatsApp Sprachnachrichten-Transkription     |
| `whatsapp_tts.py`      | Text-to-Speech fuer WhatsApp                   |
| `telegram_agent.py`    | Telegram-Bot-Integration                       |
| `mcp_client.py`        | MCP Protocol Client                            |
| `mcp_server.py`        | MCP Protocol Server (HydraHive als MCP-Host)   |
| `agentlink.py`         | AgentLink State/Handoff-System                  |
| `agentlink_client.py`  | AgentLink HTTP-Client                           |
| `agentlink_listener.py`| AgentLink Event-Listener                        |

#### Plugin-System
| Modul              | Aufgabe                                          |
|--------------------|--------------------------------------------------|
| `plugin_manager.py`| Plugins laden, aktivieren, deaktivieren           |
| `plugin_sdk.py`    | SDK fuer Plugin-Entwickler (register-Pattern)     |

#### Weitere Module
| Modul                  | Aufgabe                                       |
|------------------------|-----------------------------------------------|
| `settings.py`          | Zentrale Konfiguration (Pydantic BaseSettings) |
| `project_config.py`    | Projekt-Config-Parsing                         |
| `project_loader.py`    | Projekt-Verzeichnis scannen                    |
| `provisioner.py`       | Matrix-Raum/User-Provisioning                  |
| `repo_config.py`       | Repository-Konfiguration                       |
| `gitea.py`             | Gitea-API-Client                               |
| `learning_memory.py`   | Langzeit-Lernmemory (Fakten-Extraktion)        |
| `memory_decay.py`      | Memory-Decay (aeltere Erinnerungen abwerten)   |
| `memory_search.py`     | Semantische Memory-Suche                       |
| `semantic_index.py`    | Embedding-basierter Index                      |
| `browser_tools.py`     | Browser-Automatisierung                        |
| `auto_dream.py`        | Auto-Dream (proaktive Hintergrund-Analyse)     |
| `frustration_detection.py` | Frustration-Erkennung in User-Nachrichten  |
| `folder_watcher.py`    | Filesystem-Watcher fuer Pipelines              |
| `mail_watcher.py`      | E-Mail-Inbox-Watcher                           |
| `hooks.py`             | Event-Hook-System                              |
| `butler_executor.py`   | Butler-Regel-Ausfuehrung                       |
| `butler_rule.py`       | Butler-Regelwerk-Definitionen                  |
| `pipeline_executor.py` | Pipeline-Ausfuehrung                           |
| `group_service.py`     | User-Gruppen-Verwaltung                        |

### 2.2 Request-Flow

```
User-Nachricht (Browser)
    |
    v
[nginx :80] --> [uvicorn :8765]
    |
    v
router_agent_chat.py  (POST /api/agents/{id}/chat)
    |
    v
Orchestrator.handle_message()
    |
    +-- _build_system_prompt()   # Soul + Skills + Memory + Context
    +-- _compact_if_needed()     # Context-Compaction wenn zu gross
    +-- _llm_call()              # litellm → Anthropic/OpenAI/Ollama
    |
    v
Tool-Loop (max 6 Runden)
    |
    +-- LLM antwortet mit tool_calls?
    |     +-- _execute_tool()    # Permission-Check → BaseTool.execute()
    |     +-- Ergebnis zurueck an LLM
    |     +-- Naechste Runde
    |
    +-- LLM antwortet mit Text?
          +-- get_final_message()
          +-- Session speichern
          +-- SSE-Stream an Client
```

### 2.3 Session Management

Sessions werden in SQLite persistiert (`/projects/{id}/.sessions/sessions.db`). Pro Projekt gibt es maximal eine aktive Session, die im Memory gehalten wird (`SessionManager._active`). Aeltere Sessions werden archiviert.

Jede Session enthaelt:
- Liste von `Message`-Objekten (role, content, tool_calls, tool_call_id)
- Metadata (erstellt, letzter Zugriff, Token-Schaetzung)
- Rounds-Gruppierung (User-Message + alle zugehoerigen Assistant/Tool-Messages)

### 2.4 Tool-Execution

```
BaseTool (ABC)                     # Jedes Tool erbt hiervon
  +-- name: str                    # Eindeutiger Tool-Name
  +-- description: str             # Fuer LLM sichtbar
  +-- parameters: dict             # JSON Schema (Function Calling)
  +-- execute(args, agent_id, project_id) -> str

ToolRegistry (Singleton)
  +-- register(tool: BaseTool)
  +-- get(name) -> BaseTool
  +-- list_for_agent(agent_id) -> list[dict]  # Gefiltert nach Rolle/Skills

Ausfuehrung:
  1. LLM sendet tool_call (name + arguments)
  2. orchestrator_tools._execute_tool() aufgerufen
  3. Permission-Check (permission_classifier.py)
  4. Bei MUTATION: Optional destructive_warning + Boss-Policy-Verification
  5. BaseTool.execute() laeuft
  6. Ergebnis wird truncated (max 50k Zeichen) und an LLM zurueckgegeben
```

---

## 3. Frontend-Architektur

### 3.1 Pages und Routen

| Route                  | Page-Komponente        | Beschreibung                       |
|------------------------|------------------------|------------------------------------|
| `/dashboard`           | DashboardPage          | Uebersicht, Activity, Usage        |
| `/my-agent`            | MyAgentPage            | Persoenlicher Agent-Chat            |
| `/agents`              | AgentsPage             | Agent-Verwaltung, Tools, Federation |
| `/agents/:id/chat`     | AgentChatPage          | Chat mit spezifischem Agent         |
| `/chat/:id`            | ChatPage               | Projekt-Chat                        |
| `/projects`            | ProjectsPage           | Projekt-Verwaltung                  |
| `/projects/new`        | ProjectCreatePage      | Neues Projekt erstellen             |
| `/blueprint`           | BlueprintPage          | Butler-Automationen, Workflows      |
| `/hub`                 | HubPage                | Extensions, Plugins, Skill-Packages |
| `/brain`               | HydraBrainPage         | Knowledge-Base                      |
| `/search`              | SearchPage             | SearXNG Web-Suche                   |
| `/code-editor`         | CodeEditorPage         | Code-Server-Integration             |
| `/schedules`           | SchedulesPage          | Scheduled Tasks                     |
| `/voice`               | VoicePage              | Voice-Input/Output                  |
| `/system`              | SystemPage             | System-Info, Logs, Doctor           |
| `/usermanagement`      | UserManagementPage     | User- und Secrets-Verwaltung        |
| `/settings`            | SettingsPage           | LLM, Backup, VPN, Gitea, etc.      |
| `/mcp`                 | McpConfigPage          | MCP-Server-Konfiguration            |
| `/prompt-guide`        | PromptGuidePage        | KI-Tipps fuer bessere Prompts       |
| `/quickstart`          | QuickstartPage         | Quickstart Guide                    |
| `/playground`          | PlaygroundPage         | API Playground (admin only)         |
| `/setup`               | SetupPage              | Erst-Einrichtung                    |
| `/login`               | LoginPage              | Login                               |
| `/wizard`              | WizardPage             | Setup-Wizard                        |
| `/onboarding`          | OnboardingWizardPage   | User-Onboarding                     |
| `/invite/:token`       | InvitePage             | Einladungs-Link                     |

### 3.2 Shared Components

- **ChatView** -- Wiederverwendbare Chat-Komponente (Message-Rendering, Tool-Results, Streaming-Anzeige, Code-Highlighting)
- **AdminLayout** -- Sidebar-Navigation, Dark-Mode-Toggle, Update-Notification, Companion-Dock
- **FloatingCompanion** -- Schwebender Assistent (optional, an Sidebar andockbar)
- **TourProvider** -- Onboarding-Tour fuer neue User
- **ErrorBoundary** -- Globaler Error-Handler

### 3.3 State Management

- React Hooks (useState, useEffect, useMemo) -- kein Redux
- `useAuth` -- JWT-Token, Login/Logout, User-Daten
- `useCapabilities` -- Feature-Flags und Capabilities vom Backend
- Custom Hooks pro Feature-Bereich (useUpdateStatus, useCoreConnection, useDarkMode)

### 3.4 API-Kommunikation

- `lib/api.ts` -- Zentraler HTTP-Client mit JWT-Header-Injection
- SSE-Streaming fuer Chat-Responses (EventSource-basiert)
- Alle API-Calls gegen `/api/*` (nginx Proxy zu uvicorn :8765)

---

## 4. Deployment

### 4.1 Server-Struktur

| Server | IP              | Rolle                                            |
|--------|-----------------|--------------------------------------------------|
| .181   | 192.168.178.181 | Bastelinstanz (Entwicklung, SSH erlaubt)          |
| .220   | 192.168.178.220 | Dev-Server (Tests, nur API-Zugang)                |
| .5     | 192.168.178.5   | Prod-Server (Kunden-API)                          |
| Prod   | --              | Stabil fuer Endbenutzer (kein direkter Zugriff)   |

### 4.2 Update-Workflow

```
1. Code aendern auf Lilith (/home/till/octopos)
2. git commit && git push hydrahive main
3. SSH auf Server (.181 / .220):
   sudo bash /opt/hydrahive/update.sh
   (oder Update-Button in der Console)
4. update.sh: git pull → pip install → npm run build → systemctl restart
```

Wichtig: Kein manuelles rsync oder SSH-Patching. Immer ueber update.sh deployen.

### 4.3 Service-Konfiguration

```ini
# /etc/systemd/system/hydrahive-core.service
[Service]
ExecStart=/usr/bin/uvicorn hydrahive_core.main:app --host 0.0.0.0 --port 8765
WorkingDirectory=/opt/hydrahive/core/src
User=octopos
```

nginx leitet Port 80 auf 8765 weiter und liefert das Frontend aus `/opt/hydrahive/console/dist/`.

### 4.4 Konfiguration

Alle Konfig-Dateien liegen unter `/etc/hydrahive/`:

| Datei                    | Inhalt                                           |
|--------------------------|--------------------------------------------------|
| `.env`                   | HYDRAHIVE_* Umgebungsvariablen                   |
| `llm_config.json`        | LLM-Provider und Modelle pro Agent               |
| `admin_credentials`      | Admin-Passwort, Matrix-Credentials                |
| `jwt_secret`             | JWT-Signing-Secret (auto-generiert)               |
| `users.json`             | User-Definitionen mit Rollen                      |
| `repos.json`             | Repository-Konfigurationen                        |
| `mcp_servers.json`       | MCP-Server-Definitionen                           |
| `tailscale.json`         | Tailscale-API-Key und Konfiguration               |
| `agent_secrets.json`     | Verschluesselte Agent-Secrets                     |
| `schedules.json`         | Scheduled-Task-Definitionen                       |
| `notification_routes.json` | Benachrichtigungs-Routing                       |
| `cleanup.json`           | Cleanup-Service-Konfiguration                     |
| `alerts.json`            | Alert-Regelwerk                                   |
| `voice.json`             | Voice-Konfiguration                               |

---

## 5. Feature-Flags

Feature-Flags werden in `/etc/hydrahive/.env` als Umgebungsvariablen gesetzt:

```bash
# Auto-Verification: nach jeder MUTATION-Tool-Ausfuehrung prueft
# ein Verify-Worker automatisch ob die Aenderung korrekt war.
HYDRAHIVE_BOSS_POLICY_ENABLED=true

# Git-Worktree-Isolation: Worker-Tasks laufen in separaten
# Git-Worktrees statt im Haupt-Repository.
HYDRAHIVE_WORKTREE_ISOLATION=true

# MiniMax via direkten Anthropic-SDK (#864/#870). Default ON seit Live-
# Verify auf .177 (2026-04-23). MiniMax-Modelle laufen ueber
# client.messages.create mit base_url=api.minimax.io/anthropic statt
# ueber litellm+/v1/chat/completions. Loest Halluzinations-Familie
# (#792/#856/#862), weil MiniMax das Anthropic-Wire-Protokoll nativ
# spricht. Opt-Out nur fuer Debugging/Rollback via =0.
# HYDRAHIVE_MINIMAX_ANTHROPIC_SDK=0
```

Weitere ueberschreibbare Settings (alle mit `HYDRAHIVE_`-Praefix):

| Variable                    | Default            | Beschreibung                      |
|-----------------------------|--------------------|-----------------------------------|
| `HYDRAHIVE_ETC_DIR`         | `/etc/hydrahive`   | Konfig-Verzeichnis                |
| `HYDRAHIVE_OPT_DIR`         | `/opt/hydrahive`   | Installations-Verzeichnis         |
| `HYDRAHIVE_AGENTS_DIR`      | `/agents`          | Agent-Definitionen                |
| `HYDRAHIVE_PROJECTS_DIR`    | `/projects`        | Projekt-Verzeichnisse             |
| `HYDRAHIVE_OPENAI_API_KEY`  | `hydrahive`        | API-Key fuer litellm              |
| `HYDRAHIVE_SEARCH_ALPHA`    | `0.5`              | Hybrid-Suche Alpha-Wert           |
| `HYDRAHIVE_RATE_LIMIT_BACKEND` | `auto`          | Rate-Limit-Backend (auto/redis/memory) |

---

## 6. Key Patterns

### 6.1 Streaming (SSE)

Chat-Responses werden per Server-Sent Events (SSE) gestreamt. Der Endpoint `POST /api/agents/{id}/chat/stream` oeffnet einen SSE-Stream. Keepalive-Events werden alle 200ms gesendet, um Proxy-Timeouts zu vermeiden. Events:

- `token` -- einzelnes Text-Token
- `tool_call` -- Tool wird aufgerufen (Name + Arguments)
- `tool_result` -- Tool-Ergebnis
- `done` -- Stream beendet
- `error` -- Fehler aufgetreten

### 6.2 Context-Compaction

Dreistufige Kompaktierung, inspiriert von OpenClaw/Claude Code:

1. **Rolling Summary (Stufe 1)**: Wenn geschaetzte Tokens > Threshold (40k fuer grosse Modelle, 8k fuer kleine), werden aeltere Messages (alles ausser den letzten 3 Rounds) per LLM zusammengefasst. Format: Goal / Constraints / Progress (Done/InProgress/Blocked).

2. **Meta-Summary (Stufe 2)**: Wenn nach Stufe 1 immer noch zu gross, wird die Summary auf max 300 Woerter verdichtet.

3. **Full-Compaction**: Bei 80% des Context-Windows wird aggressiv kompaktiert.

Vor jeder Compaction wird ein Pre-Compact Memory Flush durchgefuehrt, der wichtige Fakten ins Agent-Memory schreibt.

### 6.3 Permission Classifier

Jede Tool-Ausfuehrung durchlaeuft den Permission Classifier:

1. **Static Rules** -- bekannte Tools werden fest klassifiziert (file_write = MUTATION, file_read = READ)
2. **LLM-Fallback** -- unbekannte Tools (MCP, Plugins) werden per LLM eingestuft
3. Bei MUTATION-Tools: optionale Bestaetigung (`destructive_warning.py`)
4. Bei aktivierter `boss_policy`: automatische Verification nach Mutation

### 6.4 Built-in Workers

Vier spezialisierte Worker werden als virtuelle Agenten im `dispatch_task` verfuegbar gemacht:

| Worker   | Beschreibung                                          | Tool-Set                |
|----------|-------------------------------------------------------|-------------------------|
| explore  | Read-only Codebase-Exploration, findet Files/Patterns | file_read, git_grep, list_directory |
| plan     | Implementierungsplan mit betroffenen Files und Risiken | file_read, git_grep, web_search |
| verify   | Build, Tests, Lint, Syntax-Check                       | shell_exec, git_diff, file_read |
| review   | Code-Review: Bugs, Security, Performance               | git_diff, git_grep, file_read |

### 6.5 Proactive Mode

Agents koennen im Hintergrund proaktiv Tasks ausfuehren (z.B. Code-Analyse, Monitoring). Safety-Constraints verhindern destruktive Aktionen im Proactive Mode.

### 6.6 Plugin-System

Plugins liegen unter `/plugins/<id>/` mit `plugin.yaml` + `plugin.py`. Das `register(api)`-Pattern erlaubt es Plugins, eigene Tools und Endpoints zu registrieren. Plugins werden ueber die Console (HubPage) installiert und verwaltet.

---

## 7. Entwickler-Workflow

### 7.1 Setup

```bash
# Lokales Repo
cd /home/till/octopos

# Frontend-Dependencies
cd console && npm install

# Backend laeuft auf dem Server, nicht lokal
```

### 7.2 Aendern und Testen

```bash
# Backend-Aenderung -- Syntax pruefen:
python3 -m py_compile core/src/hydrahive_core/<datei>.py

# Frontend-Aenderung -- TypeScript pruefen:
cd console && ./node_modules/.bin/tsc --noEmit

# Frontend bauen:
cd console && npm run build
```

### 7.3 Deployment

```bash
# Commit erstellen
git add <dateien>
git commit -m "Beschreibung

Co-Authored-By: HydraHive Bot <bot@hydrahive.org>"

# Pushen
git push hydrahive main

# Auf Server deployen
ssh -i ~/.ssh/claude_key_nopass octopos@192.168.178.181
sudo bash /opt/hydrahive/update.sh
```

### 7.4 Wichtige Regeln

1. **Kein SSH-Patching** -- Niemals Dateien direkt auf dem Server editieren. Immer push + update.sh.
2. **Restart vor Push testen** -- Nach Backend-Aenderungen auf .181 testen, bevor gepusht wird.
3. **Docs mitpflegen** -- handbook.md und handbuch.html im selben Commit wie Feature-Code.
4. **installer/install.sh mitpflegen** -- Bei neuen Features den Installer aktualisieren.
5. **Public Repo** -- hydrahive/hydrahive ist public. Keine persoenlichen Daten (IPs, Passwoerter) in Issues.
6. **Features fuer alle** -- Immer fuer API-Key + OAuth bauen, nicht nur fuer ein spezifisches Setup.
