# Agent Zero vs. HydraHive — Technische Analyse

Erstellt: 2026-03-27
Basis: Agent Zero `github.com/agent0ai/agent-zero` (depth-1 clone, Stand März 2026),
HydraHive `/home/till/octopos` (lokales Produktions-Repo, Stand März 2026)

---

## 1. ARCHITEKTUR-VERGLEICH

### 1.1 Multi-Agent-Koordination

**Agent Zero: Rekursive Superior/Subordinate-Kette**

Agent Zero kennt kein festes Boss/Worker-Konzept. Jeder Agent kann beliebig tief Sub-Agenten spawnen. Das Modell ist ein Baum, in dem jeder Knoten gleichzeitig Superior und Subordinate sein kann.

Kernmechanismus in `tools/call_subordinate.py` (Zeilen 11–34):
```python
sub = Agent(self.agent.number + 1, config, self.agent.context)
sub.set_data(Agent.DATA_NAME_SUPERIOR, self.agent)
self.agent.set_data(Agent.DATA_NAME_SUBORDINATE, sub)
result = await subordinate.monologue()
```

- `Agent.number` ist die Tiefe im Baum (0 = Root)
- Alle Agenten teilen einen `AgentContext` — gemeinsame Log-Instanz und `data`-Dict
- Das LLM entscheidet selbst, wann es einen Sub-Agenten ruft (über `call_subordinate`-Tool)
- Agent-Profile sind konfigurierbar (Zeile 19: `config.profile = agent_profile`)

**HydraHive: Festes Boss/Worker-Schema**

HydraHive verwendet ein explizites, konfigurationsgesteuertes Schema. `orchestrator.py` (Zeile 64–91):

```python
class Orchestrator:
    def __init__(self, discovery, runtime, sessions, tool_reg):
        self._discovery = discovery
        self._runtime = runtime
```

- `ProjectConfig` definiert explizit `agents.boss` — ein fester Boss-Agent pro Projekt
- Der Boss delegiert via `dispatch_task`-Tool; Worker-IDs werden zurückgegeben (`workers_used: list[str]`)
- Serialisierung per Projekt: `_project_queues` verhindert parallele Orchestrator-Zustände
- `orchestrator.py` (Zeile 204–246): asyncio.Queue-Worker mit 600s Idle-Timeout

**Bewertung:** Agent Zero ist flexibler (dynamische Tiefe), HydraHive ist deterministischer und besser für Multi-Tenant-Isolierung geeignet.

---

### 1.2 A2A (Agent-to-Agent) Kommunikation

**Agent Zero: FastA2A-Protokoll (Google A2A-Standard)**

Agent Zero implementiert den offenen [A2A-Standard](https://github.com/google-a2a) über die `fasta2a`-Bibliothek.

`tools/a2a_chat.py` (Zeile 6–53):
- Tool `a2a_chat` sendet Nachrichten an beliebige externe Agenten via URL
- `helpers/fasta2a_client.py`: `AgentConnection`-Klasse mit Bearer-Token-Auth
- Discovery über `/.well-known/agent.json` (Agent Card)
- Session-Persistenz: `context_id` wird pro `agent_url` gecacht (`_a2a_sessions`)
- Polling-basiertes Warten auf Task-Completion (Zeile 158–176)

**HydraHive: Proprietäres AgentLink-Protokoll**

`agentlink_client.py` + `agentlink_listener.py` implementieren ein internes Handoff-System (write_handoff/read_handoff). Kein standardisiertes A2A-Protokoll. Agent-zu-Agent-Kommunikation läuft über das `ask_agent`/`dispatch_task`-Tool intern.

**Bewertung:** Agent Zero unterstützt echte dezentrale A2A-Kommunikation mit anderen Frameworks. HydraHive ist auf interne Agenten beschränkt.

---

### 1.3 Plugin/Extension-System

**Agent Zero: Dreischichtiges Plugin-Modell**

Agent Zero hat ein vollständiges Plugin-Ökosystem:

`helpers/plugins.py`:
- Plugins: eigenständige Pakete in `/plugins/` oder `/usr/plugins/` mit `plugin.yaml`-Metadata
- Toggle-State via `.toggle-0`/`.toggle-1`-Dateien (enable/disable ohne Konfiguration)
- Drei Scopes: global, per-project, per-agent-profile
- `@extension.extensible`-Decorator (`helpers/extension.py` Zeile 52–209): Beliebige Funktion wird zu einem Extension-Point — Pre/Post-Hooks können die Ausführung kurzschließen oder Ergebnisse umschreiben
- Hot-Reload: Watchdog-System erkennt Dateiänderungen, räumt Cache auf, lädt Module neu
- Plugin-Hooks in `hooks.py` pro Plugin (install, uninstall, get_plugin_config, save_plugin_config)
- Plugins können eigene WebUI-Screens (`webui/main.html`, `webui/config.html`) mitbringen
- Git-basierte Plugin-Updates (`helpers/git.py` via `get_remote_commits_since_local`)

`helpers/extension.py` (Zeile 52–209): Das `@extensible`-Decorator ist architektonisch elegant — jede markierte Funktion emittiert automatisch `_functions/<module>/<qualname>/start` und `end` Extension-Points.

**HydraHive: Statische Skills + keine Hot-Plugins**

`skill_loader.py` implementiert `.md`-Dateien mit YAML-Frontmatter als "Skills" — das ist konzeptionell ähnlich zu Agent Zero Skills, aber kein Plugin-System:
- Scope: `always` oder `on-demand` (Keyword-Matching)
- Keine WebUI, keine Hooks, kein Installationsmechanismus
- Keine Extension-Points in Core-Funktionen

**Bewertung:** Agent Zero hat ein ausgereiftes Plugin-Ökosystem. HydraHive hat nur ein simples Skill-System für System-Prompt-Erweiterungen.

---

### 1.4 Memory-System

**Agent Zero: FAISS Vektorsuche + LangChain**

`helpers/vector_db.py`:
- `langchain_community.vectorstores.FAISS` mit Cosine-Similarity
- `CacheBackedEmbeddings` mit In-Memory-ByteStore (Embedding-Caching)
- `faiss.IndexFlatIP` (Inner Product für Cosine nach Normalisierung)
- Filter-Ausdrücke via `simpleeval` (Zeile 141–150): beliebige Metadata-Conditions als Python-Ausdrücke
- Persistierung: FAISS-Index kann serialisiert werden

`helpers/skills.py` (Agent Zero): Skills haben `triggers`-Listen und `allowed_tools`-Felder — strukturierter als HydraHive.

**HydraHive: SQLite FTS5 (BM25)**

`memory_search.py`:
- SQLite FTS5 mit BM25-Ranking — kein externer Service, kein GPU
- Lazy Re-Indexing: nur geänderte Dateien (mtime/size-Check, Zeile 108–113)
- Chunking an Markdown-Headings mit 100-char Overlap (Zeile 38–56)
- Optional Hybrid (0.7 vec + 0.3 bm25) wenn litellm Embeddings konfiguriert
- Pro Agent eigene DB: `/agents/{id}/memory_index.db`

**Bewertung:** Agent Zero hat semantische Vektorsuche (besser für konzeptionelle Ähnlichkeit). HydraHive hat deterministisches BM25 ohne Infrastruktur-Abhängigkeiten — robuster für Produktionsbetrieb.

---

### 1.5 Kommunikationsschicht

**Agent Zero: Socket.IO (WebSocket)**

`helpers/websocket.py` + `helpers/websocket_manager.py`:
- Socket.IO über `python-socketio`
- Namespace-basierte Trennung (Security: CSRF-Token-Validierung, Origin-Check)
- `helpers/ws.py`: Event-Routing mit `send_data(endpoint_name, event_name, data)`
- WebSocket-Handler in `python/websocket_handlers/` als Plugins registrierbar

**HydraHive: SSE + FastAPI**

`router_agent_chat.py` implementiert Server-Sent Events für Streaming. FastAPI-Router in über 15 Modulen aufgeteilt. Kein bidirektionales Protokoll — nur Push vom Server.

**Bewertung:** Socket.IO (Agent Zero) ist bidirektional und ermöglicht komplexere UI-Interaktionen. SSE (HydraHive) ist einfacher, ausreichend für Chat-Streaming und läuft besser hinter Standard-Reverse-Proxies ohne sticky sessions.

---

## 2. FEATURES IN AGENT ZERO DIE HYDRAHIVE NOCH NICHT HAT

### 2.1 Eingebauter Task-Scheduler mit Cron-Syntax
**Datei:** `helpers/task_scheduler.py`
**Was es macht:** Vollständiges Cron-Job-System für Agenten. Tasks haben State (IDLE/RUNNING/DISABLED/ERROR), Typen (adhoc/scheduled/planned), Timezone-Support via `pytz`. Tasks werden als YAML-Dateien in `usr/scheduler/` persistiert.
**API:** `api/scheduler_task_create.py`, `scheduler_task_run.py`, `scheduler_tasks_list.py`, `scheduler_tick.py`
**Aufwand:** Groß | **Nutzen:** Hoch

HydraHive hat keine vergleichbare Funktion. Agenten könnten eigenständig periodische Tasks ausführen (Backup-Reports, Health-Checks, tägliche Zusammenfassungen).

---

### 2.2 Standardisiertes A2A-Protokoll (Google FastA2A)
**Dateien:** `tools/a2a_chat.py`, `helpers/fasta2a_client.py`, `helpers/fasta2a_server.py`
**Was es macht:** Agenten können mit jedem FastA2A-kompatiblen Endpunkt kommunizieren — externe Agent Zero-Instanzen, aber auch zukünftig andere Frameworks. Agent Card unter `/.well-known/agent.json`.
**Aufwand:** Mittel | **Nutzen:** Hoch (für Multi-Instance-Setups und externe Integrationen)

---

### 2.3 Self-Update-Mechanismus
**Datei:** `helpers/self_update.py`
**Was es macht:** Agent Zero kann sich selbst aus dem GitHub-Repo updaten (git pull auf definierte Branches: main/ready/testing/development). Update-Status in `/exe/a0-self-update-status.yaml`. Mit Backup-Option vor dem Update.
**API:** `api/self_update_get.py`, `self_update_schedule.py`, `self_update_tags.py`
**Aufwand:** Klein | **Nutzen:** Mittel

HydraHive hat `hydrahive-update.sh` als externes Shell-Script, aber keine In-App-Update-Funktion.

---

### 2.4 Hot-Reload Plugin-System mit Extension-Points
**Dateien:** `helpers/plugins.py`, `helpers/extension.py`
**Was es macht:** Plugins können zur Laufzeit aktiviert/deaktiviert werden ohne Neustart. `@extensible`-Decorator macht jede Funktion zu einem Extension-Point.
**Aufwand:** Groß | **Nutzen:** Mittel (für Erweiterbarkeit durch externe Entwickler)

---

### 2.5 Cloudflare Tunnel-Integration
**Datei:** `helpers/tunnel_manager.py`, `run_tunnel.py`
**API:** `api/tunnel.py`, `api/tunnel_proxy.py`
**Was es macht:** Automatische öffentliche URL via Cloudflare Tunnel — kein Port-Forwarding nötig. Wichtig für Home-Lab-Setups.
**Aufwand:** Klein | **Nutzen:** Mittel

---

### 2.6 Speech-to-Text (Whisper) und Text-to-Speech (Kokoro)
**Dateien:** `helpers/whisper.py`, `helpers/kokoro_tts.py`
**API:** `api/transcribe.py`, `api/synthesize.py`
**Was es macht:** Eingebaute STT/TTS-Pipeline. Agenten können Sprachnachrichten entgegennehmen und ausgeben.
**Aufwand:** Mittel | **Nutzen:** Niedrig (für Desktop/Home-Lab-Use-Cases)

---

### 2.7 Intelligentes History-Kompaktierungs-System
**Datei:** `helpers/history.py`
**Was es macht:** Mehrstufiges Kompaktierungssystem mit konfigurierbaren Ratios:
- `CURRENT_TOPIC_RATIO = 0.5` — aktuelles Thema bekommt 50% des Token-Budgets
- `HISTORY_TOPIC_RATIO = 0.3` — alte Themen 30%
- `HISTORY_BULK_RATIO = 0.2` — komprimierte Bulk-History 20%
- Topics werden bei `new_topic()` versiegelt; alte Topics verlieren Zwischenschritte aber behalten Request+Response
- `COMPRESSION_TARGET_RATIO = 0.8` — Ziel ist 80% des aktuellen Volumens

HydraHive hat `_compact_if_needed` in `orchestrator_context.py` aber kein mehrstufiges Topic-basiertes System.
**Aufwand:** Groß | **Nutzen:** Hoch (für lange, gedächtnisintensive Sessions)

---

### 2.8 Per-Agent-Profil mit Prompt-Override-Hierarchie
**Datei:** `helpers/subagents.py`
**Was es macht:** Agent-Profile (developer/researcher/hacker/agent0) können Plugins, Extensions und Prompts auf mehreren Ebenen überschreiben: global → user → project → agent-profile. Priorität durch Pfad-Hierarchie.
**Aufwand:** Groß | **Nutzen:** Mittel

---

### 2.9 Notification-System (In-App)
**Datei:** `helpers/notification.py`
**API:** `api/notification_create.py`, `notifications_history.py`, `notifications_mark_read.py`
**Was es macht:** Strukturierte In-App-Benachrichtigungen mit Typen (INFO/WARNING/ERROR), Prioritäten, Gruppen-Deduplizierung und History.
HydraHive hat Matrix/Discord/Telegram-Notifier aber kein In-App-Notification-Center.
**Aufwand:** Klein | **Nutzen:** Mittel

---

## 3. IDEEN DIE HYDRAHIVE ÜBERNEHMEN KÖNNTE

### 3.1 Cron-basierter Task-Scheduler (Agent Zero: `helpers/task_scheduler.py`)

**Umsetzung für HydraHive:**
- Neue Tabelle `scheduled_tasks` in SQLite (task_id, agent_id, cron_expr, prompt, last_run, state)
- `router_scheduler.py` mit CRUD-Endpunkten (FastAPI)
- Background-Task in `main.py`-Lifespan: `asyncio.create_task(scheduler_tick())`
- `scheduler_tick()` prüft alle N Sekunden: welche Tasks sind fällig? → `orchestrator.handle_message()`
- Admin-UI in Console unter `/admin/scheduler`

Das passt perfekt zu HydraHives FastAPI + SQLite-Stack. Aufwand: ~2-3 Sessions.

---

### 3.2 Standardisiertes A2A-Protokoll

**Umsetzung für HydraHive:**
- `fasta2a`-Bibliothek installieren (PyPI)
- `/.well-known/agent.json`-Endpoint in `router_core_misc.py` ergänzen
- Neues Tool `a2a_chat` in `tool_registry.py`: sendet Nachrichten an externe Agent-URLs
- Konfigurierbar pro Agent in `agent.yaml` (welche externen Agenten erlaubt sind)
- Permission: neues `a2a.call`-Permission-Flag

Das ermöglicht HydraHive-Instanzen, mit anderen HydraHive-Servern oder Agent-Zero-Instanzen zu kommunizieren — wichtig für das geplante Multi-Node-Setup.

---

### 3.3 `@extensible`-Pattern für Core-Funktionen

**Umsetzung für HydraHive:**
- Das `@extensible`-Decorator-Muster aus `helpers/extension.py` ist elegant und einfach nachzubauen
- Markierte Funktionen in `orchestrator.py` und `tool_registry.py` mit Pre/Post-Extension-Points
- Extensions als Python-Module in `/agents/{id}/extensions/`
- Damit können Power-User das Verhalten von Agenten anpassen ohne Core-Code zu ändern

Aufwand: Mittel, Nutzen: Hoch für externe Nutzer.

---

### 3.4 In-App Notification-Center

**Umsetzung für HydraHive:**
- SQLite-Tabelle `notifications` (id, type, title, message, read, created_at, user_id)
- `router_notifications.py` mit GET/POST/PATCH (mark as read)
- SSE-Stream für Live-Benachrichtigungen (passt zu bestehendem SSE-Pattern)
- React-Glocken-Icon in Console mit Unread-Counter

Passt zu Multi-Tenant: jeder User sieht nur seine Benachrichtigungen. Aufwand: Klein (~1 Session).

---

### 3.5 Mehrstufige History-Kompaktierung

**Umsetzung für HydraHive:**
- `orchestrator_context.py`'s `_compact_if_needed` erweitern
- Topic-Konzept in `session_manager.py`: `Session.new_topic()` versiegelt den aktuellen Gesprächsstrang
- Kompaktierungs-LLM-Call: "Fasse die Zwischenschritte zusammen, behalte nur die wesentlichen Ergebnisse"
- Token-Budget-Ratios analog zu Agent Zero konfigurierbar in `agent.yaml`

Aufwand: Mittel, Nutzen: Hoch für lange Dev-Sessions.

---

### 3.6 Skills mit `allowed_tools`-Feld

Agent Zero's `helpers/skills.py` (Zeile 32) definiert `allowed_tools: List[str]` pro Skill. Ein Skill kann also einschränken, welche Tools in seinem Kontext sinnvoll sind.

**Umsetzung für HydraHive:** `skill_loader.py`'s `Skill`-Dataclass um `allowed_tools`-Feld erweitern. Wenn ein Skill aktiv ist, kann der Orchestrator zusätzliche Tools freischalten oder einschränken.

Aufwand: Klein, Nutzen: Mittel.

---

## 4. WAS HYDRAHIVE BESSER MACHT

### 4.1 Echtes Multi-Tenant mit Rollen-System

Agent Zero ist Single-User. Es gibt keine Nutzer-Datenbank, kein Rollen-System, kein Permissions-Modell jenseits von per-agent-config.

HydraHive hat:
- `router_users.py` mit vollständigem Admin/User-Rollen-System
- 15+ Permission-Flags (`filesystem.read`, `system.write`, `git.pr`, `shell.exec`, etc.)
- `execution_mode_policy.py` + `agent_config.py`: `effective_permissions(execution_mode)` gibt die Schnittmenge aus Agent-Konfiguration und User-Berechtigungen zurück
- Jeder User hat eigene Agenten, eigene Projekte, eigene API-Keys

Für externe Nutzer und Team-Setups ist HydraHive klar überlegen.

---

### 4.2 Strukturierte Sicherheits-Architektur

`tool_registry.py` (Zeilen 55–96): `assert_path_within_project()` ist ein harter Sicherheits-Check der Path-Traversal-Angriffe verhindert. Jeder Filesystem-Tool-Aufruf wird validiert.

Agent Zero hat keinen vergleichbaren systematischen Sandbox-Ansatz. Die Blocklist in HydraHive (`rm -rf`, `dd`, `mkfs`, Shell-Exec-Guards) ist explizit und auditierbar.

---

### 4.3 LLM-Provider-Failover mit OAuth-Support

`orchestrator_llm.py` implementiert:
- litellm-basiertes Multi-Provider-Failover
- Nativen Anthropic-OAuth-Flow (kein API-Key nötig)
- OpenAI-Codex-OAuth-Unterstützung
- `_should_failover()` erkennt Rate-Limit-Fehler und wechselt automatisch

Agent Zero unterstützt viele LLMs (via LangChain), aber keinen OAuth-Flow ohne API-Keys.

---

### 4.4 Admin-Console mit Backup & Restore

HydraHive hat:
- Vollständige Admin-UI unter `/admin/`
- Backup/Restore-System (`router_backup_restore.py`) mit Cron-Job (03:00 täglich)
- `/admin/backups` in der Console
- Doctor-Page (`router_doctor.py`) für System-Diagnose
- Rate-Limiter mit konfigurierbaren Limits pro User/Tool

Agent Zero hat keine persistente Admin-UI für Betrieb und Wartung.

---

### 4.5 Messenger-Integrationen als First-Class-Agenten

HydraHive hat `discord_agent.py`, `telegram_agent.py`, `matrix_agent.py`, `whatsapp_agent.py` als vollständige Agenten die eigenständig auf Nachrichten reagieren. Diese sind in das Rollen- und Session-System integriert.

Agent Zero hat ein `_telegram_integration`-Plugin, aber keine so tief integrierten Bot-Agenten.

---

### 4.6 Deterministisches Memory (BM25 vs. FAISS)

HydraHives SQLite FTS5-Ansatz hat in Produktionsumgebungen klare Vorteile:
- Kein GPU oder `faiss`-Binary nötig
- Keine Embedding-Modell-Abhängigkeit (kein Download, kein OOM)
- Deterministische Suchergebnisse (gleiche Query → gleiche Ergebnisse)
- Lazy Re-Indexing macht es auch auf schwacher Hardware schnell
- WAL-Modus erlaubt parallele Lese-Zugriffe ohne Locking

FAISS ist bei großen Memory-Mengen semantisch besser, aber operativ aufwendiger.

---

### 4.7 Projekt-basierte Isolation

HydraHive isoliert Agenten-Arbeit explizit in `/projects/<id>/`. Jedes Projekt hat eigene Sessions, eigenen Dateisystem-Namespace, eigene AgentConfig. Der Orchestrator serialisiert Anfragen per Projekt-Queue.

Agent Zero arbeitet mit einem einzelnen Workspace (`/usr/`) — keine echte Projekt-Isolation.

---

## ZUSAMMENFASSUNG

| Feature | Agent Zero | HydraHive |
|---|---|---|
| Multi-Tenant / Rollen | Nein | Ja (admin/user, 15+ Permissions) |
| A2A-Standard (FastA2A) | Ja | Nein (nur intern) |
| Plugin-System | Vollständig (YAML+Hooks+WebUI+Hot-Reload) | Nein |
| Extension-Points | Ja (`@extensible`) | Nein |
| Memory | FAISS (semantisch) | SQLite FTS5 BM25 (deterministisch) |
| Task-Scheduler | Ja (Cron) | Nein |
| Self-Update | In-App | Extern (Shell-Script) |
| Kommunikation | Socket.IO (bidirektional) | SSE + FastAPI |
| Admin-UI | Minimal | Vollständig |
| LLM-Failover | Über LangChain | litellm + OAuth |
| Sicherheits-Sandbox | Keine systematische | Path-Safety + Blocklist + Permissions |
| Messenger-Bots | Plugin (_telegram) | Native Agenten (Discord/Telegram/Matrix/WhatsApp) |
| Backup/Restore | Ja (im Backup-Plugin) | Ja (native, mit Cron) |
| Speech (STT/TTS) | Ja (Whisper/Kokoro) | Nein |
| Projekt-Isolation | Nein | Ja (/projects/<id>/) |

**Wichtigste Lücke in HydraHive:** Kein standardisiertes A2A-Protokoll und kein Task-Scheduler — beides lässt sich mit überschaubarem Aufwand ergänzen und würde den größten Mehrwert bringen.

**Stärke von HydraHive:** Multi-Tenant, Rollen-System, Produktions-Sicherheit und tiefe Messenger-Integration — das ist für reale Team-Setups und externe Nutzer deutlich ausgereifter als Agent Zero.
