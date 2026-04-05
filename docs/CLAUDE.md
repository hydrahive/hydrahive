# HydraHive API für Claude Code

Dieses Dokument beschreibt wie du mit der HydraHive API kommunizierst. Lege es als Datei in deinem Projekt ab damit Claude Code die API kennt.

## Verbindung

```
API-URL:  https://<SERVER-IP>/api
Swagger:  https://<SERVER-IP>/api/docs
```

## Authentifizierung

Jeder Request braucht einen Bearer Token. So bekommst du einen:

```bash
curl -sk -X POST https://<SERVER-IP>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"<USER>","password":"<PASSWORT>"}'
```

Antwort:
```json
{"access_token": "eyJ...", "token_type": "bearer", "role": "admin"}
```

Den `access_token` in allen weiteren Requests als Header mitgeben:
```
Authorization: Bearer eyJ...
```

## Wichtigste Endpunkte

### System & Status

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/health` | Server-Status (kein Auth nötig) |
| GET | `/status` | System-Status mit Agenten |
| GET | `/admin/system/info` | CPU, RAM, Disk, Uptime, Load |
| GET | `/admin/system/services` | systemd Service-Status |
| POST | `/admin/system/service/{name}/restart` | Service neustarten |

### Agenten

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/agents` | Alle Agenten mit Config + Runtime |
| GET | `/agents/{id}` | Agent-Details |
| POST | `/agents` | Neuen Agent erstellen |
| PUT | `/agents/{id}` | Agent aktualisieren |
| DELETE | `/agents/{id}` | Agent löschen |
| GET | `/agents/{id}/soul` | Soul.md lesen |
| PUT | `/agents/{id}/soul` | Soul.md schreiben (`{"soul":"..."}`) |
| GET | `/agents/{id}/config/full` | Komplette agent.yaml als JSON |
| PUT | `/agents/{id}/config/full` | agent.yaml überschreiben (`{"config":{...}}`) |
| POST | `/agents/{id}/clone` | Agent duplizieren (`{"new_id":"..."}`) |
| GET | `/agents/{id}/skills` | Skills auflisten |
| POST | `/agents/{id}/skills` | Skill erstellen |
| DELETE | `/agents/{id}/skills/{filename}` | Skill löschen |

### Agent-Chat

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| POST | `/agents/{id}/message` | Nachricht senden (synchron) |
| POST | `/agents/{id}/message/stream` | Nachricht senden (SSE-Stream) |
| GET | `/agents/{id}/session/history` | Chat-Verlauf |
| DELETE | `/agents/{id}/session` | Chat leeren |
| POST | `/agents/{id}/interrupt` | Laufende Antwort abbrechen |

### Projekte

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/projects` | Alle Projekte |
| POST | `/projects` | Neues Projekt erstellen |
| GET | `/projects/{id}` | Projekt-Details |
| DELETE | `/projects/{id}` | Projekt löschen |
| POST | `/projects/{id}/message` | Nachricht an Projekt senden |
| POST | `/projects/{id}/git-clone` | Git-Repo ins Projekt klonen (`{"url":"...","branch":"main"}`) |

### Logs & Debugging

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/logs/core/live?lines=100&since=5m&grep=error` | Core-Logs mit Filter |
| GET | `/logs/nginx?error=true` | Nginx-Logs |
| GET | `/agents/{id}/logs?lines=50` | Agent-spezifische Logs |

### Dateisystem (eingeschränkt)

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/admin/files?path=/agents/` | Verzeichnis listen |
| GET | `/admin/files/read?path=/agents/bot/soul.md` | Datei lesen |
| PUT | `/admin/files/write` | Datei schreiben (`{"path":"...","content":"..."}`) |

Erlaubte Pfade: `/agents/`, `/plugins/`, `/etc/hydrahive/`, `/projects/`

### Shell (eingeschränkt)

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| POST | `/admin/shell` | Befehl ausführen (`{"command":"df -h"}`) |

Erlaubte Befehle: df, free, uptime, hostname, whoami, uname, systemctl, journalctl, tailscale, docker, podman, ls, cat, wc, du, head, tail, grep, find, which

### Plugins

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/plugins` | Alle Plugins + Status |
| GET | `/plugins/{id}` | Plugin-Details |
| POST | `/plugins/{id}/enable` | Plugin aktivieren |
| POST | `/plugins/{id}/disable` | Plugin deaktivieren |
| GET | `/plugins/agents/{agent_id}` | Plugins eines Agents |
| PUT | `/plugins/agents/{agent_id}` | Plugin-Zuweisung (`{"plugin_ids":["..."]}`)|

### LLM-Konfiguration

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/llm/config` | LLM-Provider-Konfiguration |
| GET | `/llm/available-models` | Verfügbare Modelle |
| GET | `/llm/ollama/models` | Lokale Ollama-Modelle |

### Tools

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/tools` | Alle verfügbaren Tools mit Beschreibung + Parametern |

### Federation / Tailscale

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/admin/tailscale/status` | Tailscale-Status |
| GET | `/admin/tailscale/devices` | Tailnet-Geräte |
| POST | `/admin/tailscale/scan` | HydraHive-Instanzen suchen |
| POST | `/admin/tailscale/invite` | Einladungs-Key generieren |

### Backup

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/admin/backups` | Backups auflisten |
| POST | `/admin/backup` | Backup erstellen |
| POST | `/admin/restore/{name}` | Backup wiederherstellen |

### Config-Export

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/admin/config/export` | Gesamte Server-Config als JSON |

## Beispiele

### Agent erstellen
```bash
curl -sk -X POST https://SERVER/api/agents \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "mein-agent",
    "type": "specialist",
    "identity": "Mein Agent",
    "model": "claude-sonnet-4-6",
    "tools": ["file_read", "file_write", "web_search", "shell_exec"],
    "soul": "Du bist ein hilfreicher Assistent."
  }'
```

### Nachricht an Agent senden
```bash
curl -sk -X POST https://SERVER/api/agents/mein-agent/message \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "Welche Dateien sind im aktuellen Verzeichnis?"}'
```

### Datei lesen
```bash
curl -sk "https://SERVER/api/admin/files/read?path=/agents/mein-agent/soul.md" \
  -H "Authorization: Bearer $TOKEN"
```

### Shell-Befehl ausführen
```bash
curl -sk -X POST https://SERVER/api/admin/shell \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"command": "df -h"}'
```

### System-Info abrufen
```bash
curl -sk https://SERVER/api/admin/system/info \
  -H "Authorization: Bearer $TOKEN"
```

## Hinweise

- Alle Admin-Endpunkte (`/admin/*`) erfordern Admin-Rolle
- Dateizugriff ist auf `/agents/`, `/plugins/`, `/etc/hydrahive/`, `/projects/` beschränkt
- Shell-Befehle sind auf eine Whitelist beschränkt (kein rm -rf, kein reboot)
- Streaming-Endpunkte (`/stream`) liefern Server-Sent Events (SSE)
- Die vollständige interaktive API-Dokumentation ist unter `/api/docs` erreichbar
