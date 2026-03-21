# OctopOS API-Referenz

Basis-URL: `https://<server>/api`

Alle Endpoints außer `/health`, `/setup/*`, `/auth/*` und `/hooks/*` erfordern einen JWT-Bearer-Token:
```
Authorization: Bearer <token>
```

Token wird via `POST /auth/login` erhalten.

---

## Auth

### POST /auth/login

Login und JWT-Token erhalten.

**Body:**
```json
{ "username": "admin", "password": "geheim" }
```

**Response 200:**
```json
{ "access_token": "eyJ...", "token_type": "bearer" }
```

**Rate-Limit:** 10 Versuche/Minute pro IP → 429 Too Many Requests

---

### GET /auth/me

Aktuell eingeloggten User abfragen.

**Response 200:**
```json
{ "username": "admin" }
```

---

## Setup

### GET /setup/status

Prüft ob der Setup-Wizard noch benötigt wird.

**Response 200:**
```json
{ "needs_setup": true }
```

---

### POST /setup

Ersten Admin-User anlegen. Nur verfügbar solange keine Benutzer existieren.

**Body:**
```json
{ "username": "admin", "password": "mindestens8zeichen" }
```

**Response 201:**
```json
{ "created": true, "username": "admin", "role": "admin" }
```

---

## System

### GET /health

Health-Check (kein Auth).

**Response 200:**
```json
{ "status": "ok" }
```

---

### GET /status

Detaillierter System-Status.

**Response 200:**
```json
{
  "discovery": { "agents_dir": "/agents", "count": 3 },
  "projects":  { "projects_dir": "/projects", "count": 2 },
  "sessions":  { "active_projects": ["buchhaltung"] },
  "runtime": {
    "boss-main": {
      "status": "running",
      "type": "boss",
      "restart_count": 0,
      "last_heartbeat_age": 12.4,
      "heartbeat_timeout": 90.0,
      "on_failure": "restart"
    }
  }
}
```

---

## Agenten

### GET /agents

Alle Agenten auflisten.

**Response 200:**
```json
{
  "boss-main": {
    "config": {
      "type": "boss",
      "identity": "Hauptagent",
      "model": "llama3.1:8b"
    },
    "runtime": {
      "status": "running",
      "restart_count": 0,
      "last_heartbeat_age": 5.2,
      "heartbeat_timeout": 90.0,
      "on_failure": "restart"
    }
  }
}
```

---

### GET /agents/{id}

Einzelnen Agenten abrufen.

---

### POST /agents

Neuen Agenten anlegen.

**Body:**
```json
{
  "id": "steuer-agent",
  "type": "specialist",
  "identity": "Steuerbert",
  "model": "llama3.1:8b",
  "temperature": 0.7,
  "max_tokens": 4096,
  "tools": ["file_read", "file_write"],
  "soul": "Du bist ein erfahrener Steuerberater...",
  "heartbeat_interval": "30s",
  "heartbeat_timeout": "90s",
  "heartbeat_on_failure": "restart"
}
```

**Response 201:**
```json
{ "created": true, "id": "steuer-agent" }
```

---

### PUT /agents/{id}

Agenten-Konfiguration aktualisieren. Gleicher Body wie POST.

---

### DELETE /agents/{id}

Agenten deaktivieren (Verzeichnis bleibt erhalten, `agent.yaml` wird umbenannt).

---

### GET /agents/{id}/soul

Soul-Text eines Agenten abrufen.

**Response 200:**
```json
{ "soul": "# Steuerbert\n\nDu bist...", "exists": true }
```

---

### GET /agents/{id}/logs

Logs eines Agenten aus journalctl.

**Query-Parameter:**
- `lines` (int, default 100, max 1000): Anzahl Zeilen

**Response 200:**
```json
{
  "agent_id": "boss-main",
  "lines": [
    "2026-03-20T11:23:45+0100 octopos-devmaster octopos-core[123]: INFO orchestrator: ..."
  ],
  "count": 42,
  "source": "journalctl -u octopos-core"
}
```

---

## Agent Skills

### GET /agents/{id}/skills

Alle Skills eines Agenten.

**Response 200:**
```json
{
  "skills": [
    {
      "filename": "steuerrecht",
      "skill": "Steuerrecht Grundlagen",
      "version": "1.0",
      "scope": "on-demand",
      "triggers": ["steuer", "finanzamt"],
      "priority": 10,
      "content": "## Steuerrecht\n..."
    }
  ]
}
```

---

### POST /agents/{id}/skills

Neuen Skill anlegen. Erstellt `/agents/{id}/skills/{filename}.md`.

**Body:**
```json
{
  "filename": "steuerrecht",
  "skill": "Steuerrecht Grundlagen",
  "version": "1.0",
  "scope": "on-demand",
  "triggers": ["steuer", "finanzamt"],
  "priority": 10,
  "content": "## Steuerrecht\n..."
}
```

---

### PUT /agents/{id}/skills/{filename}

Skill aktualisieren. Gleicher Body wie POST.

---

### DELETE /agents/{id}/skills/{filename}

Skill löschen.

---

## Projekte

### GET /projects

Alle Projekte auflisten.

**Response 200:**
```json
{
  "buchhaltung": {
    "name": "Buchhaltung GmbH",
    "description": "...",
    "boss": "finanz-boss",
    "workers": ["steuer-agent"],
    "matrix_room": "!abc:server.de",
    "filesystem": "/projects/buchhaltung",
    "system_user": "proj_buchhaltung",
    "show_swarm": false
  }
}
```

---

### GET /projects/{id}

Einzelnes Projekt abrufen.

---

### POST /projects

Neues Projekt anlegen (ohne Provisionierung).

**Body:**
```json
{
  "id": "buchhaltung",
  "name": "Buchhaltung GmbH",
  "description": "Finanzagenten",
  "boss": "finanz-boss",
  "workers": ["steuer-agent"],
  "samba": true
}
```

---

### POST /projects/{id}/provision

Projekt provisionieren: Linux-User, Verzeichnisse, Matrix-Room, Samba.

---

### DELETE /projects/{id}/provision

Provisionierung rückgängig machen.

---

### DELETE /projects/{id}

Projekt löschen (Konfiguration + Sessions).

---

## Chat / Nachrichten

### POST /projects/{id}/message

Nachricht an Boss-Agenten senden (blockierend, vollständige Antwort).

**Body:**
```json
{ "content": "Wie hoch ist die Umsatzsteuer?" }
```

**Response 200:**
```json
{
  "response": "Die Umsatzsteuer in Deutschland beträgt...",
  "workers": ["steuer-agent"],
  "session_id": "uuid-..."
}
```

---

### POST /projects/{id}/message/stream

Streaming-Version via Server-Sent Events.

**Body:** Gleich wie `/message`

**Response:** `text/event-stream`
```
data: {"text": "Die "}
data: {"text": "Umsatz"}
data: {"text": "steuer..."}
data: {"done": true}
```

Bei Fehler:
```
data: {"error": "LLM nicht erreichbar"}
```

---

### GET /projects/{id}/session/history

Chat-History der aktiven Session abrufen.

**Query-Parameter:**
- `limit` (int, default 50): Max. Nachrichten

**Response 200:**
```json
{
  "session_id": "uuid-...",
  "messages": [
    { "role": "user", "content": "Hallo" },
    { "role": "assistant", "content": "Hallo! Wie kann ich helfen?" }
  ],
  "count": 2
}
```

---

## Webhooks

### GET /projects/{id}/webhooks

Alle Webhooks eines Projekts (Secrets maskiert).

**Response 200:**
```json
{
  "webhooks": [
    {
      "id": "uuid-...",
      "name": "Git-Push",
      "url": "https://example.com/hook",
      "events": ["message"],
      "created_at": "2026-03-20T12:00:00Z"
    }
  ]
}
```

---

### POST /projects/{id}/webhooks

Webhook anlegen.

**Body:**
```json
{
  "name": "Git-Push Trigger",
  "url": "https://example.com/hook",
  "secret": "optional-hmac-secret",
  "events": ["message", "agent_error"]
}
```

**Events:** `message`, `agent_error`, `provision`, `agent_start`, `agent_stop`

---

### DELETE /projects/{id}/webhooks/{webhook_id}

Webhook löschen.

---

### POST /projects/{id}/webhooks/test

Test-Ping an alle Webhooks des Projekts.

---

### POST /hooks/{id}/wake

**Kein Auth erforderlich.** Externer Trigger — startet Boss-Agenten asynchron.

**Body:**
```json
{ "message": "Neuer Git-Push auf main — bitte Code-Review starten" }
```

**Response 202:**
```json
{ "accepted": true, "project_id": "buchhaltung" }
```

---

## Benutzer

### GET /users

Alle Benutzer auflisten (Passwörter nicht enthalten).

---

### POST /users

Neuen Benutzer anlegen.

**Body:**
```json
{ "username": "till", "password": "sicheresPasswort" }
```

---

### DELETE /users/{username}

Benutzer löschen (eigener Account nicht löschbar).

---

### PUT /users/{username}/password

Passwort ändern.

**Body:**
```json
{ "password": "neuesPasswort" }
```

---

## Audit-Log

### GET /audit/logs

Audit-Einträge abrufen (neueste zuerst).

**Query-Parameter:**
- `limit` (int, default 100, max 1000)
- `user` (string): Filter nach Benutzer
- `action` (string): Filter nach Aktion (z.B. `user.login`)
- `project` (string): Filter nach Projekt-ID

**Response 200:**
```json
{
  "logs": [
    {
      "id": "uuid-...",
      "timestamp": "2026-03-20T12:00:00Z",
      "user": "admin",
      "action": "user.login",
      "target": "admin",
      "project_id": null,
      "ip": "192.168.1.1",
      "details": {}
    }
  ],
  "count": 42
}
```

**Bekannte Actions:**
- `user.login`, `user.login_failed`, `user.create`, `user.delete`, `user.password_change`
- `agent.create`, `agent.update`, `agent.delete`
- `project.create`, `project.provision`, `project.delete`
- `skill.create`, `skill.update`, `skill.delete`
- `webhook.create`, `webhook.delete`, `webhook.fire`
- `llm.token_set`

---

## Agent-Memory

### GET /agents/{id}/memory

Alle Memory-Dateien eines Agenten auflisten.

**Response 200:**
```json
{ "files": ["user.md", "projects.md", "daily_2026-03-21.md"] }
```

---

### GET /agents/{id}/memory/{filename}

Einzelne Memory-Datei lesen.

**Response 200:**
```json
{ "filename": "user.md", "content": "# User\n\nName: Till..." }
```

---

### PUT /agents/{id}/memory/{filename}

Memory-Datei schreiben oder aktualisieren.

**Body:**
```json
{ "content": "# User\n\nName: Till\nRolle: Admin..." }
```

---

### DELETE /agents/{id}/memory/{filename}

Memory-Datei löschen.

---

## Persönlicher Agent

### GET /me/agent

Konfiguration des eigenen persönlichen Agenten abrufen.

**Response 200:**
```json
{
  "agent_id": "personal_admin",
  "config": {
    "identity": "Mein Assistent",
    "model": "claude-sonnet-4-6",
    "temperature": 0.7,
    "max_tokens": 4096,
    "tools": ["file_read", "web_search"],
    "soul": "Du bist mein persönlicher Assistent..."
  }
}
```

---

### PUT /me/agent

Eigenen persönlichen Agenten konfigurieren.

**Body:**
```json
{
  "identity": "Mein Assistent",
  "soul": "Du bist mein persönlicher Assistent...",
  "model": "claude-sonnet-4-6",
  "temperature": 0.7,
  "max_tokens": 4096,
  "fallback_models": ["ollama/llama3.1:8b"],
  "tools": ["file_read", "web_search", "wks_shell_exec"],
  "ollama_base_url": "http://192.168.178.197:11434"
}
```

---

### POST /me/agent/message

Nachricht an den persönlichen Agenten senden (blockierend).

**Body:**
```json
{ "content": "Schreibe mir eine Zusammenfassung der gestrigen Notizen." }
```

**Response 200:**
```json
{
  "response": "Hier ist die Zusammenfassung...",
  "workers": [],
  "session_id": "uuid-..."
}
```

---

### POST /me/agent/message/stream

Streaming-Variante des persönlichen Agenten-Chats (SSE).

**Body / Response:** Identisch mit `/projects/{id}/message/stream`

---

### GET /me/agent/session/history

Chat-History des persönlichen Agenten abrufen.

**Query-Parameter:**
- `limit` (int, default 50)

**Response 200:**
```json
{
  "session_id": "uuid-...",
  "messages": [
    { "role": "user", "content": "Hallo" },
    { "role": "assistant", "content": "Hallo! Wie kann ich dir helfen?" }
  ],
  "count": 2
}
```

---

### DELETE /me/agent/session

Chat-Session des persönlichen Agenten zurücksetzen.

**Response 200:**
```json
{ "cleared": true }
```

---

## WKS (Workstation)

### GET /me/wks

WKS-Konfiguration des eingeloggten Users abrufen.

**Response 200:**
```json
{
  "configured": true,
  "ip": "192.168.178.197",
  "ssh_user": "till",
  "ollama_port": 11434,
  "has_ssh_key": true
}
```

---

### PUT /me/wks

WKS-Konfiguration speichern.

**Body:**
```json
{
  "ip": "192.168.178.197",
  "ssh_user": "till",
  "ollama_port": 11434,
  "ssh_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n..."
}
```

`ssh_key` ist optional — wird weglassen, wenn der Key unverändert bleiben soll.

---

### GET /me/wks/ollama-models

Verfügbare Ollama-Modelle auf der WKS abfragen.

**Response 200:**
```json
{
  "models": [
    { "id": "ollama/llama3.1:8b", "label": "WKS: llama3.1:8b", "provider": "wks" }
  ],
  "wks_url": "http://192.168.178.197:11434",
  "error": null
}
```

---

## LLM-Konfiguration

### GET /llm/config

Aktuelle LLM-Konfiguration (API-Keys maskiert).

---

### PUT /llm/config/claude_max

Claude OAuth-Token setzen.

**Body:**
```json
{ "token": "sk-ant-oat01-..." }
```

---

### PUT /llm/config/{provider}

Anderen Provider konfigurieren (openai, ollama, etc.).

---

### GET /llm/claude_token_status

OAuth Token-Status prüfen.

**Response 200:**
```json
{
  "exists": true,
  "remaining_days": 23,
  "expires_approx": "2026-04-19",
  "status": "ok"
}
```

`status`: `"ok"` (>7 Tage), `"warning"` (≤7 Tage), `"critical"` (≤3 Tage), `"expired"`

---

### GET /llm/ollama/models

Installierte Ollama-Modelle auflisten.

---

### POST /llm/ollama/pull

Ollama-Modell herunterladen.

**Body:**
```json
{ "model": "llama3.1:8b" }
```

---

### GET /llm/available-models

Alle verfügbaren Modelle abrufen (Server-Ollama + konfigurierte Cloud-Provider + WKS-Ollama des Users).

**Response 200:**
```json
{
  "models": [
    { "id": "ollama/llama3.1:8b", "label": "llama3.1:8b", "provider": "ollama" },
    { "id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "provider": "anthropic" },
    { "id": "ollama/qwen2.5:7b", "label": "WKS: qwen2.5:7b", "provider": "wks", "wks_base_url": "http://192.168.178.197:11434" }
  ]
}
```

---

## MCP-Server

### GET /mcp/servers

Alle konfigurierten MCP-Server auflisten.

**Response 200:**
```json
{
  "servers": [
    {
      "id": "qmd",
      "name": "QMD Memory Search",
      "transport": "streamableHttp",
      "url": "http://127.0.0.1:8181/mcp",
      "headers": {}
    }
  ]
}
```

---

### POST /mcp/servers

Neuen MCP-Server anlegen.

**Body:**
```json
{
  "id": "qmd",
  "name": "QMD Memory Search",
  "transport": "streamableHttp",
  "url": "http://127.0.0.1:8181/mcp",
  "headers": {}
}
```

---

### PUT /mcp/servers/{id}

MCP-Server aktualisieren. Gleicher Body wie POST.

---

### DELETE /mcp/servers/{id}

MCP-Server löschen.

---

## Gitea

### GET /gitea/config

Gitea-Konfiguration abrufen (Token maskiert).

**Response 200:**
```json
{
  "url": "http://192.168.178.181:3001",
  "token": "***",
  "org": "octopos",
  "webhook_secret": "***"
}
```

---

### PUT /gitea/config

Gitea-Konfiguration speichern.

**Body:**
```json
{
  "url": "http://192.168.178.181:3001",
  "token": "gitea-token-abc",
  "org": "octopos",
  "webhook_secret": "optional-secret"
}
```

---

### GET /gitea/repos

Alle Gitea-Repos der konfigurierten Organisation auflisten.

**Response 200:**
```json
{
  "repos": [
    {
      "name": "octopos",
      "description": "AI Agent Server",
      "html_url": "http://192.168.178.181:3001/octopos/octopos",
      "default_branch": "main",
      "updated": "2026-03-20T12:00:00Z"
    }
  ]
}
```

---

### GET /gitea/repos/{id}/prs

Offene Pull Requests eines Repos.

**Response 200:**
```json
{ "prs": [...], "count": 3 }
```

---

## Tools

### GET /tools

Alle registrierten Tools mit ihrer Beschreibung und Parameter-Schema auflisten.

---

## System-Update

### GET /admin/update/status

Aktuellen Status des letzten System-Updates abrufen.

**Response 200:**
```json
{
  "status": "ok",
  "started_at": "2026-03-20T11:00:00Z",
  "finished_at": "2026-03-20T11:01:30Z",
  "commit": "abc1234",
  "commit_full": "abc1234def5678...",
  "message": "Update abgeschlossen",
  "log_tail": ["Pulling from git...", "Build OK", "Restarting service..."],
  "error": null
}
```

`status`: `"ok"` | `"running"` | `"error"` | `"unknown"`

---

### POST /admin/update/trigger

System-Update starten (git pull + Build + Service-Restart). Läuft asynchron im Hintergrund.

**Response 200:**
```json
{ "status": "running", "message": "Update gestartet" }
```

---

## System-Monitoring

### GET /system/gpu

GPU-Auslastung abfragen.

**Response 200 (GPU verfügbar):**
```json
{
  "available": true,
  "gpus": [
    {
      "name": "NVIDIA GeForce GTX 1080 Ti",
      "temp_c": 68,
      "util_gpu_pct": 42,
      "util_mem_pct": 71,
      "mem_total_mb": 11264,
      "mem_used_mb": 8000,
      "mem_free_mb": 3264,
      "power_draw_w": 180,
      "power_limit_w": 250
    }
  ]
}
```

**Response 200 (keine GPU):**
```json
{ "available": false, "reason": "nvidia-smi not found" }
```

---

### GET /system/heartbeat-tasks

Alle aktiven Heartbeat-Tasks aller Agenten auflisten.

**Response 200:**
```json
{
  "tasks": [
    {
      "agent_id": "boss-main",
      "task_id": "tagesstart",
      "schedule": "0 8 * * 1-5",
      "interval": null,
      "message": "Guten Morgen!",
      "project": "buchhaltung",
      "last_run": "2026-03-21T08:00:00Z"
    }
  ]
}
```

---

## System-Logs

### GET /logs/core

Core-Logs aus journalctl (alle, ungefiltert).

**Query-Parameter:**
- `lines` (int, default 200, max 1000)

**Response:** Gleich wie `/agents/{id}/logs`
