# HydraHive Core — Architektur-Katalog (v2)

Stand: 2026-04-11 | Branch: v2/project-architecture
111 Python-Dateien, ~44.700 Zeilen (davon 6.723 Backup)

## Kern-Architektur

### Orchestrator (Herzstück — LLM-Call-Pipeline)
```
User-Nachricht → System-Prompt bauen → Tools laden → LLM-Call → Tool-Loop → Antwort
```

| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `orchestrator.py` | 824 | Boss-Agent Task-Dispatching, handle_message (non-streaming) |
| `orchestrator_stream.py` | 1.096 | SSE-Streaming: handle_message_stream, OAuth/Codex/litellm Pfade |
| `orchestrator_context.py` | 1.255 | System-Prompt bauen, Memory-Injection, Compaction, Cache |
| `orchestrator_llm.py` | 1.155 | LLM-Call-Maschinerie: Failover, OAuth, Retry, Token-Tracking |
| `orchestrator_dispatch.py` | 694 | Tool-Loop, Worker-Dispatch, DAG-Scheduling |
| `orchestrator_tools.py` | ~400 | Tool-Execution, Result-Truncation, Signature-Check |
| `orchestrator_mcp.py` | ~80 | MCP-Server-Integration (Tool-Schemas laden, Calls dispatchen) |

### Tools (v2: 9 Core-Tools)
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `tool_registry.py` | 1.328 | 9 Core-Tools: shell_exec, file_read/write/patch/search, web_search, read/write_memory, ask_agent |
| `tool_registry_v1_backup.py` | 6.723 | Backup der alten 80-Tool-Registry (Referenz) |

### Projekt-System (v2: Projekt = Agent)
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `project_config.py` | ~220 | ProjectConfig laden: config.yaml (v2) + project.yaml (v1), AGENT.md |
| `project_loader.py` | ~110 | Projekt-Discovery: /projects/ scannen, Watchdog für Änderungen |
| `agent_config.py` | ~220 | AgentConfig: LLM-Config, Tools, agent_config_from_project() Bridge |
| `agent_discovery.py` | ~100 | Agent-Discovery: /agents/ scannen (v1, wird durch Projekte ersetzt) |

### Session-Management
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `session_manager.py` | 997 | SQLite-Sessions: append, get_context, History-Budgeting, Tool-Result-Compaction |
| `session_metrics.py` | ~170 | Token-Usage-Tracking pro Projekt (Input/Output/Cache) |
| `session_memory.py` | ~80 | Session-Memory: Zusammenfassung nach Session-Ende |

### Context-Management
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `context_lifecycle.py` | ~300 | microCompact, Tool-Budgets, Memory-Budget, ToolOpType-Klassifikation |
| `token_estimation.py` | ~35 | Token-Schätzung: chars/3.2 + Message-Overhead |

### Memory-System
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `memory_search.py` | ~150 | BM25 Memory-Suche (SQLite FTS5) |
| `memory_decay.py` | ~200 | Memory-Decay: Importance, Category, Recall-Tracking |
| `semantic_index.py` | ~100 | Semantische Dedup: FAISS-Index, Embedding-Suche |
| `learning_memory.py` | ~80 | Learning-Snapshots: Session → Memory-Extraktion |

### Security & Permissions
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `execution_mode_policy.py` | ~55 | v2: Vereinfacht — nur Admin-Check für unrestricted |
| `permission_classifier.py` | ~100 | Tool-Permission: always_allow, always_confirm |
| `guard_utils.py` | ~50 | Guard-Utilities für Router |
| `auth_utils.py` | ~80 | JWT-Auth, Token-Validation |
| `secret_encryption.py` | ~80 | AES-Verschlüsselung für Secrets |
| `rate_limiter.py` | ~260 | Token- und Request-Rate-Limiting pro Agent/User |

## API-Router

### Kern-Routen
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `main.py` | 2.055 | FastAPI-App, Lifespan, alle Router registrieren |
| `router_projects.py` | 583 | Projekt-CRUD, Chat-Endpoints (message, stream, interrupt) |
| `router_agent_chat.py` | ~350 | Agent-Chat: Session-History, Memory, Import/Export |
| `router_agent_admin.py` | ~200 | Agent-CRUD: erstellen, löschen, konfigurieren |
| `router_core_misc.py` | 939 | System-Info, Tool-Groups, Agent-Liste, Health-Check |
| `router_users.py` | 893 | User-CRUD, Login, persönliche Agents |
| `router_system.py` | 947 | System-Config, Features, Logs, Doctor |

### Feature-Routen
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `router_llm.py` | 737 | LLM-Verwaltung: Models, Provider, OAuth-Token |
| `router_hub.py` | 573 | ClawhHub: Skills/Plugins installieren |
| `router_backup_restore.py` | ~300 | Backup erstellen/wiederherstellen |
| `router_doctor.py` | 646 | System-Diagnose, Auto-Fix |
| `router_extensions.py` | ~200 | Extension-Verwaltung |
| `router_mcp.py` | ~150 | MCP-Server-Konfiguration |
| `router_groups.py` | ~200 | User-Gruppen-Verwaltung |
| `router_notifications.py` | ~100 | Push-Notifications |
| `router_usage.py` | ~150 | Token-Usage-API |
| `router_schedules.py` | ~150 | Cron-Schedules |
| `router_voice.py` | ~250 | Voice-Input (STT) |

### Integration-Routen
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `router_user_integrations.py` | 1.489 | Discord/Telegram/Matrix Bot-Setup |
| `router_project_integrations.py` | ~220 | Webhooks pro Projekt |
| `router_project_lifecycle.py` | ~120 | Projekt-Provisioning (Matrix-Rooms) |
| `router_github.py` | ~150 | GitHub-Webhook-Receiver |
| `router_repos.py` | ~100 | Git-Repository-Verwaltung |
| `router_tailscale.py` | ~200 | Tailscale VPN-Integration |
| `router_servers.py` | ~150 | Remote-Server-Verwaltung |
| `router_invites.py` | ~100 | Einladungs-System |

### Spezial-Routen
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `router_openai_compat.py` | ~200 | OpenAI-kompatible API (/v1/chat/completions) |
| `router_a2a.py` | ~150 | Agent-to-Agent Federation (A2A Protokoll) |
| `router_brain.py` | ~100 | Brain/Knowledge-Base API |
| `router_knowledge.py` | ~100 | Wissens-Verwaltung |
| `router_agent_skills.py` | ~100 | Skill-CRUD pro Agent |
| `router_agent_secrets.py` | ~80 | Secret-Verwaltung pro Agent |
| `router_config_map.py` | ~100 | Config-Map (Key-Value Store) |
| `router_pipelines.py` | ~200 | Pipeline-Verwaltung |
| `router_butler.py` | ~150 | Butler-Regeln (Automatisierungen) |
| `router_webhooks_butler.py` | ~100 | Webhook-Butler |
| `router_migration.py` | ~100 | Daten-Migration |
| `router_searxng.py` | ~80 | SearXNG-Konfiguration |
| `router_codeserver.py` | ~80 | Code-Server Integration |
| `router_vpn.py` | ~80 | VPN-Verwaltung |
| `router_skill_packages.py` | ~100 | Skill-Pakete |

## Messenger-Agents
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `discord_agent.py` | 671 | Discord Bot: Message-Handling, Channel-Routing |
| `telegram_agent.py` | ~400 | Telegram Bot |
| `matrix_agent.py` | ~450 | Matrix Bot (Conduwuit) |
| `whatsapp_agent.py` | ~300 | WhatsApp Bridge |
| `whatsapp_transcribe.py` | ~100 | WhatsApp Voice → Text |
| `whatsapp_tts.py` | ~80 | Text → WhatsApp Voice |
| `mail_watcher.py` | ~150 | E-Mail Inbox-Monitoring |

## Hintergrund-Services
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `notification_service.py` | ~150 | Push-Notifications (SSE) |
| `scheduler_service.py` | ~150 | Cron-basierte Agent-Tasks |
| `cleanup_service.py` | ~260 | Alte Sessions/Dateien aufräumen |
| `alert_service.py` | ~100 | Smart Alerts bei Problemen |
| `auto_dream.py` | ~100 | Proaktive Ideen-Generierung |
| `proactive_mode.py` | ~120 | Proaktiver Agent-Modus |
| `folder_watcher.py` | ~100 | Dateisystem-Überwachung |

## Infrastruktur
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `settings.py` | ~80 | Zentrale Konfiguration (Pfade, Ports, Feature-Flags) |
| `provisioner.py` | 812 | System-Provisioning (Linux-User, Samba, Matrix-Rooms) |
| `gitea.py` | ~200 | Gitea-API-Client |
| `mcp_client.py` | ~100 | MCP-Client (stdio-basiert) |
| `mcp_server.py` | ~80 | HydraHive als MCP-Server |
| `peer_discovery.py` | ~150 | Multi-Instanz-Discovery (A2A) |
| `agentlink.py` | ~100 | AgentLink State/Handoff API |
| `agentlink_client.py` | ~80 | AgentLink HTTP-Client |
| `agentlink_listener.py` | ~160 | AgentLink Polling-Listener |
| `migrations.py` | ~200 | Datenbank-Migrationen |

## Weitere Module
| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `agent_runtime.py` | ~200 | Agent-Lifecycle: Start/Stop, Heartbeat, Activity-Tracking |
| `agent_teams.py` | ~100 | Team-Zuordnung von Agents |
| `skill_loader.py` | ~150 | Skill-System: on-demand Skills laden |
| `skill_package_rule.py` | ~80 | Skill-Paket-Regeln |
| `boss_policy.py` | ~80 | Boss-Agent Entscheidungslogik |
| `built_in_workers.py` | ~80 | Vordefinierte Worker-Templates |
| `coordinator_mode.py` | ~80 | Koordinator-Modus (Multi-Agent) |
| `destructive_warning.py` | ~50 | Warnung vor destruktiven Aktionen |
| `frustration_detection.py` | ~80 | User-Frustrations-Erkennung |
| `heartbeat.py` | ~80 | Heartbeat-System |
| `hooks.py` | ~100 | Hook-System (before/after Tool) |
| `openclaw_bridge.py` | ~80 | OpenClaw-Kompatibilitäts-Bridge |
| `prompt_speculation.py` | ~80 | Prompt-Prefetch/Speculation |
| `turn_journal.py` | ~80 | Turn-by-Turn Logging |
| `verification_contract.py` | ~50 | Verification-Contract |
| `butler_executor.py` | ~100 | Butler-Regel-Ausführung |
| `butler_rule.py` | ~80 | Butler-Regel-Definition |
| `config_loader.py` | ~50 | Config-File-Loader |
| `pipeline_executor.py` | ~150 | Pipeline-Ausführung |
| `repo_config.py` | ~50 | Repository-Konfiguration |
| `group_service.py` | ~200 | Gruppen-Berechtigungen |
| `browser_tools.py` | ~200 | Browser-Tools (Playwright, optional) |

## v2 Änderungen (11.04.2026)

### Gelöscht
- `tool_groups.py` — Keyword-basierte Tool-Filterung (191 Zeilen)
- `tool_loader.py` — META_TOOLS, request_tools, TOOL_CATEGORIES (138 Zeilen)
- `agent_roles.py` — Rollen-Presets reader/assistant/coder/admin (~200 Zeilen)
- `plugin_manager.py` — Plugin-Verwaltung (549 Zeilen)
- `plugin_sdk.py` — Plugin-SDK (214 Zeilen)
- `router_plugins.py` — Plugin-Admin-API (307 Zeilen)

### Vereinfacht
- `tool_registry.py` — 80 → 9 Core-Tools (6.723 → 1.328 Zeilen)
- `execution_mode_policy.py` — Nur noch Admin-Check
- `agent_config.py` — effective_permissions() → None, keine Rollen-Resolution
- `orchestrator.py` — _allowed_tools() gibt 9 Core-Tools zurück

### Neu
- `project_config.py` — v2-Format: config.yaml + AGENT.md + messenger.yaml
- `agent_config.py` — agent_config_from_project() Bridge

## v3 Konsolidierung & Folgeschritte (14.04.2026)

### Architektur-Konsolidierung v3 (#635-#638, #641)
- #635 — Workspace-SSOT: `workspace_root(project_id)` ist die einzige Quelle für Workspace-Pfade; `file_*`, `shell_exec` und `git_*` resolven byte-identisch
- #636 — Eine Kontextpipeline für Stream + non-stream
- #637 — Kanonisches Tool-/Message-Modell + `to_anthropic_format`-Helper an allen Send-Pfaden
- #638 — Permission-/Execution-Mode-Konsolidierung: `_V2_CORE_TOOL_IDS` + `permission_classifier` + `execution_mode`-Sandbox als drei einzige Quellen; `permissions_required` und Permission-Listen entfernt
- #641 — CONFIRM-Roundtrip: `tool_confirmation.py` (Pending-Store + Wait), zentral konsumiert in `orchestrator_tools.execute_tool_call`; Banner-SSE + `/tool-confirm`-Endpoints in Project- und Agent-Pfaden

### Tote Felder & Legacy entfernt
- #642 — `AgentConfig`: `role`, `tools_extra`, `tools_deny`, `tool_selection` raus (Legacy-YAMLs bleiben über `extra: ignore` kompatibel)
- #643 — Legacy-Workspace-Welt: `GiteaClient.git_workspace`, `worktree_manager.py`, `/tmp/hydrahive-git/`-Webhook-rmtree raus
- #644 — `default_personal_agent_execution_modes`: tote `permissions`-Listen raus, Strip-on-Load in `upgrade_personal_agent_data`

### Trusted-Agent / `risk_policy`
- Neues `AgentConfig.risk_policy: Literal["interactive", "trusted"]` (Default `interactive`)
- Trusted-Bypass im zentralen CONFIRM-Branch von `orchestrator_tools.execute_tool_call`: `RiskLevel.CONFIRM` wird automatisch genehmigt + per `logger.info` audit-geloggt
- `RiskLevel.DENY` bleibt unverändert blockiert
- Personal-Agent-`trusted` nur durch Admin-User setzbar (Guard in `router_users.update_my_agent`)
- Admin-Agents über `AgentsPage` UI-bedienbar; Personal-Agent-Form über `MyAgentPage > Einstellungen`

## Datenfluss: User-Nachricht → Antwort

```
1. HTTP POST /projects/{id}/message/stream
   → router_projects.py
   
2. ProjectConfig laden
   → project_loader.py → project_config.py
   
3. AgentConfig auflösen
   v2: agent_config_from_project(project_cfg)
   v1: discovery.get(project_cfg.agents.boss)
   → agent_config.py

4. System-Prompt bauen
   → orchestrator_context.py
   AGENT.md + Memory-Prefetch + Skills + Handbook
   
5. Session-History laden
   → session_manager.py → context_lifecycle.py (Budgeting)
   
6. 9 Core-Tools als Schemas
   → tool_registry.py → orchestrator.py (_allowed_tools)
   
7. LLM-Call (SSE-Streaming)
   → orchestrator_stream.py → orchestrator_llm.py
   Provider: Anthropic OAuth / Codex / litellm
   
8. Tool-Loop (max 20 Runden)
   → orchestrator_stream.py → orchestrator_tools.py
   Tool ausführen → Result zurück → nächster LLM-Call
   
9. Antwort speichern + SSE-Events
   → session_manager.py + session_metrics.py
```
