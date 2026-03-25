# HydraHive — Deployment-Anleitung

> Version 0.1.0 · Stand 2026-03-25

---

## Inhaltsverzeichnis

1. [Systemanforderungen](#systemanforderungen)
2. [Erstinstallation](#erstinstallation)
3. [Konfiguration](#konfiguration)
4. [Update-Prozedur](#update-prozedur)
5. [Backup & Restore](#backup--restore)
6. [Secret Rotation](#secret-rotation)
7. [Troubleshooting](#troubleshooting)

---

## 1. Systemanforderungen

### Mindestanforderungen

| Ressource | Minimum | Empfohlen |
|-----------|---------|-----------|
| CPU       | 2 vCPU  | 4 vCPU    |
| RAM       | 4 GB    | 8 GB      |
| Disk      | 20 GB   | 50 GB SSD |
| OS        | Ubuntu 22.04 LTS x86_64 | Ubuntu 22.04 LTS x86_64 |
| Python    | 3.11    | 3.11      |
| Node.js   | 18 LTS  | 20 LTS    |

### Netzwerk-Ports (intern)

| Port  | Service              | Bindung      |
|-------|----------------------|--------------|
| 8765  | hydrahive-core API   | 127.0.0.1    |
| 6167  | conduwuit (Matrix)   | 127.0.0.1    |
| 3001  | Gitea                | 0.0.0.0      |
| 8010  | AgentLink Hub        | 127.0.0.1    |
| 80    | nginx (HTTP → HTTPS) | 0.0.0.0      |
| 443   | nginx (HTTPS)        | 0.0.0.0      |

### Netzwerk-Ports (extern, Firewall-Regeln)

Nur Port 443 (HTTPS) und optional 3001 (Gitea) müssen von außen erreichbar sein.
Port 22 (SSH) für Administration.

---

## 2. Erstinstallation

### 2.1 Vorbereitungen

Frische Ubuntu 22.04 VM aufsetzen. Root-Zugang per SSH sicherstellen.

```bash
# System aktualisieren
apt-get update && apt-get upgrade -y

# Repo klonen
apt-get install -y git
git clone https://github.com/hydrahive/hydrahive /opt/hydrahive-installer
cd /opt/hydrahive-installer
```

### 2.2 Installer ausführen

```bash
# Standard-Installation (interaktiv)
sudo bash installer/install.sh

# Mit eigenem Domain-Namen (Let's Encrypt möglich)
sudo DOMAIN=mein.server.de bash installer/install.sh

# Mit vorgegebenem Admin-Passwort
sudo ADMIN_PASSWORD=MeinPasswort bash installer/install.sh
```

Der Installer läuft vollständig durch und gibt am Ende eine Zusammenfassung aus.

### 2.3 Was der Installer macht (Schritt für Schritt)

**Phase 1 — Fundament:**

- `01_os_check.sh` — Prüft Ubuntu 22.04, CPU, RAM
- `02_gpu_detect.sh` — Erkennt GPU (NVIDIA/AMD/none), setzt `$PROFILE`
- `03_dependencies.sh` — Installiert nginx, curl, git, Python 3.11, Node.js
- `04_tuwunel.sh` — Installiert conduwuit (Matrix Homeserver) auf Port 6167
- `05_admin_account.sh` — Legt Admin-Credentials an (`/etc/hydrahive/admin_credentials`)

**Phase 2 — Core Runtime:**

- `06_core_service.sh` — Systemd-User `hydrahive`, venv unter `/opt/hydrahive/venv/`,
  pip install des Cores, Systemd-Unit `hydrahive-core.service`

**Phase 3 — Web-Console:**

- `07_console.sh` — React-Frontend nach `/opt/hydrahive/console/`
- `08_ollama.sh` — Ollama optional (GPU-abhängig)
- `09_https.sh` — Self-signed TLS-Zertifikat, nginx-Konfiguration

**Phase 4 — Git-Integration:**

- `10_gitea.sh` — Gitea als interne Code-Verwaltung (Port 3001)

**Phase 5 — AgentLink Hub:**

- `11_agentlink.sh` — AgentLink als Task-Queue/Handoff-Broker

**Phase 6 — VPN:**

- `12_vpn.sh` — WireGuard-Setup (optional)

**Optional:**

- `13_whatsapp_bridge.sh` — WhatsApp-Bridge (wird interaktiv abgefragt)

### 2.4 Ergebnis prüfen

```bash
# Alle Services aktiv?
systemctl is-active hydrahive-core hydrahive-conduwuit nginx gitea

# Health-Endpoint
curl -sf http://127.0.0.1:8765/health

# Logs
journalctl -u hydrahive-core -n 50
```

### 2.5 Erster Login

Nach erfolgreicher Installation:

```
URL:      https://<IP-der-VM>
User:     admin
Passwort: (aus /etc/hydrahive/admin_credentials → console_password)
```

Im Browser die TLS-Warnung für das self-signed Zertifikat akzeptieren.
Für produktive Nutzung mit öffentlicher Domain Let's Encrypt einrichten:

```bash
apt-get install -y certbot python3-certbot-nginx
certbot --nginx -d mein.server.de
```

---

## 3. Konfiguration

### 3.1 Verzeichnis-Übersicht

```
/etc/hydrahive/             # Alle Secrets und Konfigurationsdateien
├── admin_credentials       # console_password, matrix_password
├── jwt_secret              # JWT-Signing-Secret (auto-generiert)
├── internal_secret         # Internes Core-Shared-Secret
├── llm_env                 # API-Keys als Umgebungsvariablen (EnvironmentFile)
├── llm_config.json         # LLM-Provider-Konfiguration
├── gitea_config.json       # Gitea-Zugangsdaten
├── users.json              # Benutzerdatenbank
├── mcp_servers.json        # MCP-Server-Konfiguration
├── claude_oauth_token      # Claude OAuth-Token (optional, sk-ant-oat01-...)
└── tls/
    ├── hydrahive.crt       # TLS-Zertifikat
    └── hydrahive.key       # TLS-Private-Key (Modus 600)

/etc/hydrahive → /etc/hydrahive  # Symlink (Rückwärtskompatibilität)
```

### 3.2 LLM-Konfiguration (`/etc/hydrahive/llm_env`)

Diese Datei wird als systemd `EnvironmentFile` geladen. Format: `KEY=VALUE`, eine
Variable pro Zeile, keine Export-Statements.

```bash
# Anthropic (Claude)
ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
OPENAI_API_KEY=sk-...

# Google Gemini
GEMINI_API_KEY=AIza...

# Mistral
MISTRAL_API_KEY=...

# Claude OAuth (alternativ zu API-Key)
# Token → /etc/hydrahive/claude_oauth_token
```

Nach Änderungen:

```bash
sudo systemctl restart hydrahive-core
```

### 3.3 LLM-Config (`/etc/hydrahive/llm_config.json`)

Wird über die Console-UI (Einstellungen → LLM) geschrieben. Manuelle Beispielstruktur:

```json
{
  "default_model": "claude-opus-4-5",
  "providers": {
    "anthropic": { "enabled": true },
    "openai":    { "enabled": false },
    "ollama":    { "enabled": true, "base_url": "http://127.0.0.1:11434" }
  }
}
```

### 3.4 Gitea-Konfiguration (`/etc/hydrahive/gitea_config.json`)

```json
{
  "url":      "http://127.0.0.1:3001",
  "token":    "gitea-api-token",
  "username": "admin"
}
```

### 3.5 MCP-Server (`/etc/hydrahive/mcp_servers.json`)

Liste externer MCP-Server die Agenten nutzen können:

```json
{
  "servers": [
    {
      "id":       "mcp-godot",
      "name":     "Godot MCP",
      "url":      "http://127.0.0.1:7401/mcp",
      "transport": "streamableHttp"
    }
  ]
}
```

### 3.6 Agenten-Verzeichnis (`/agents/`)

Jeder Agent hat ein eigenes Verzeichnis:

```
/agents/
├── hydrahive_support/         # Beispiel-Agent
│   ├── agent.yaml             # Pflicht: Konfiguration
│   ├── soul.md                # Optional: Persönlichkeit/Anweisungen
│   └── memory/                # Optional: persistente Erinnerungen
│       ├── handbook.md
│       └── *.md               # Alle .md werden in den System-Prompt injiziert
└── personal_<username>/       # Persönlicher Agent je Nutzer
    ├── agent.yaml
    └── memory/
```

### 3.7 Projekte-Verzeichnis (`/projects/`)

```
/projects/
└── <project-id>/
    ├── project.yaml           # Projekt-Metadaten
    ├── .sessions/             # JSON-Dateien je Session
    │   └── <session-id>.json
    └── <dateien>/             # Projekt-Dateien (Agenten-Zugriff sandbox)
```

### 3.8 nginx-Konfiguration

Die nginx-Konfiguration liegt in `/etc/nginx/sites-available/hydrahive-console`.
API-Requests (Pfad `/api/`) werden transparent an Port 8765 weitergeleitet.
Das Frontend ist als SPA konfiguriert (`try_files $uri /index.html`).

---

## 4. Update-Prozedur

### 4.1 Vom Entwicklungs-Rechner (Standard)

Das Update-Script `scripts/hydrahive-update.sh` läuft auf dem lokalen Rechner und
synchronisiert per rsync auf die VM.

```bash
# Konfigurationsdatei anlegen (einmalig)
cp scripts/hydrahive.conf.example scripts/hydrahive.conf
# Anpassen: VM-Adresse, SSH-Key, Install-Dir

# Update ausführen
./scripts/hydrahive-update.sh
```

**Was das Script tut:**

1. `git pull` vom konfigurierten Remote (hydrahive → gitea-local → origin)
2. Core per rsync auf die VM synchronisieren
3. `pip install -e` auf der VM ausführen (neue Dependencies)
4. React-Console lokal bauen (`npm run build`)
5. Console-Build per rsync auf die VM synchronisieren
6. Docs rsync + in Agent-Memory kopieren
7. `systemctl restart hydrahive-core`
8. Update-Status in `/var/run/hydrahive-update.json` schreiben

**Konfiguration (`scripts/hydrahive.conf`):**

```bash
VM="hydrahive@YOUR-VM-IP"
SSH_KEY="$HOME/.ssh/your-ssh-key"
INSTALL_DIR="/opt/hydrahive"
INSTALL_USER="hydrahive"
SERVICE_NAME="hydrahive-core"
```

### 4.2 Self-Update auf der VM

Der Installer richtet einen Systemd-One-Shot-Service `hydrahive-selfupdate.service`
ein. Dieser kann per Console-UI oder manuell ausgelöst werden:

```bash
sudo systemctl start hydrahive-selfupdate
journalctl -u hydrahive-selfupdate -f
```

### 4.3 Nur Core aktualisieren (ohne Console-Build)

```bash
# SSH auf VM
ssh hydrahive@YOUR-VM-IP

# Manuelles pip install
sudo -u hydrahive /opt/hydrahive/venv/bin/pip install -e /opt/hydrahive/core -q
sudo systemctl restart hydrahive-core
```

### 4.4 Update verifizieren

```bash
# Status prüfen
curl http://127.0.0.1:8765/health

# Deploy-Zeitstempel
cat /var/run/hydrahive-update.json

# Logs
journalctl -u hydrahive-core -n 20
```

---

## 5. Backup & Restore

### 5.1 Automatisches Backup (Cron)

Das Backup-Script `scripts/hydrahive-backup.sh` läuft auf dem lokalen Rechner und
zieht Daten per rsync von der VM. Empfohlene Cron-Zeit: 03:00 Uhr.

```bash
# Crontab auf dem lokalen Rechner
0 3 * * * /home/till/hydrahive/scripts/hydrahive-backup.sh >> /home/till/hydrahive-backups/cron.log 2>&1
```

**Konfiguration:** Gleiche `scripts/hydrahive.conf` wie das Update-Script.

### 5.2 Manuelles Backup

```bash
./scripts/hydrahive-backup.sh
```

Backups landen in `~/hydrahive-backups/YYYY-MM-DD_HH-MM/`. Die letzten 10 Backups
werden behalten, ältere werden automatisch gelöscht.

**Backup-Inhalt:**

| Quelle (VM)         | Ziel (lokal)                          | Inhalt                    |
|---------------------|---------------------------------------|---------------------------|
| `/etc/hydrahive/`   | `TIMESTAMP/etc-hydrahive/`            | Secrets, Config, Users    |
| `/agents/`          | `TIMESTAMP/agents/`                   | Agent-Konfigurationen + Memory |
| `/projects/`        | `TIMESTAMP/projects/`                 | Projekte + Sessions       |

### 5.3 Restore

Das Frontend bietet unter `/admin/backups` eine Restore-Funktion über die API.
Manuell per rsync:

```bash
BACKUP="$HOME/hydrahive-backups/2026-03-20_03-00"

# Secrets und Config
rsync -av "$BACKUP/etc-hydrahive/" hydrahive@YOUR-VM-IP:/etc/hydrahive/

# Agenten
rsync -av "$BACKUP/agents/" hydrahive@YOUR-VM-IP:/agents/

# Projekte
rsync -av "$BACKUP/projects/" hydrahive@YOUR-VM-IP:/projects/

# Service neu starten
ssh hydrahive@YOUR-VM-IP "sudo systemctl restart hydrahive-core"
```

**Wichtig:** Nach einem Restore immer den Service neu starten, da der SessionManager
Sessions aus den JSON-Dateien lädt. Permissions nach dem Restore prüfen:

```bash
ssh hydrahive@YOUR-VM-IP "
  sudo chown -R hydrahive:hydrahive /agents /projects
  sudo chown root:hydrahive /etc/hydrahive
  sudo chmod 770 /etc/hydrahive
  sudo chmod 600 /etc/hydrahive/jwt_secret /etc/hydrahive/llm_env
"
```

### 5.4 Backup-Konsistenz prüfen

```bash
# Letztes Backup anzeigen
ls -lh ~/hydrahive-backups/

# Größe des letzten Backups
du -sh ~/hydrahive-backups/$(ls ~/hydrahive-backups | sort | tail -1)

# Anzahl Agenten im Backup
ls ~/hydrahive-backups/$(ls ~/hydrahive-backups | sort | tail -1)/agents/ | wc -l
```

---

## 6. Secret Rotation

### 6.1 JWT-Secret rotieren

Ein neues JWT-Secret macht alle bestehenden Login-Sessions ungültig. Alle Nutzer
müssen sich neu anmelden.

```bash
# Auf der VM
sudo -u hydrahive bash -c '
  openssl rand -hex 32 > /etc/hydrahive/jwt_secret
  chmod 600 /etc/hydrahive/jwt_secret
'
sudo systemctl restart hydrahive-core
```

### 6.2 Admin-Passwort ändern

```bash
# Neues Passwort setzen (Console-UI: Einstellungen → Benutzer)
# Oder direkt per API:
curl -X POST http://127.0.0.1:8765/users/admin/password \
  -H "Authorization: Bearer <JWT>" \
  -H "Content-Type: application/json" \
  -d '{"password": "NeuesPasswort123"}'

# Credentials-Datei aktualisieren (für Dokumentationszwecke)
sudo nano /etc/hydrahive/admin_credentials
```

### 6.3 API-Keys rotieren

```bash
# LLM-API-Keys in EnvironmentFile aktualisieren
sudo nano /etc/hydrahive/llm_env

# Service neu starten (liest EnvironmentFile beim Start)
sudo systemctl restart hydrahive-core
```

### 6.4 TLS-Zertifikat erneuern (self-signed)

```bash
# Altes Zertifikat löschen (Installer legt neues an)
sudo rm /etc/hydrahive/tls/hydrahive.crt /etc/hydrahive/tls/hydrahive.key

# Neues Zertifikat generieren
sudo openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
  -keyout /etc/hydrahive/tls/hydrahive.key \
  -out    /etc/hydrahive/tls/hydrahive.crt \
  -subj   "/CN=$(hostname)/O=HydraHive/C=DE" 2>/dev/null

sudo chmod 600 /etc/hydrahive/tls/hydrahive.key
sudo systemctl reload nginx
```

### 6.5 Internes Shared-Secret rotieren

```bash
sudo -u hydrahive bash -c '
  openssl rand -hex 32 > /etc/hydrahive/internal_secret
  chmod 600 /etc/hydrahive/internal_secret
'
sudo systemctl restart hydrahive-core
```

---

## 7. Troubleshooting

### Service startet nicht

**Symptom:** `systemctl start hydrahive-core` schlägt fehl.

```bash
# Detaillierte Fehlerausgabe
journalctl -u hydrahive-core -n 50 --no-pager

# Häufige Ursachen prüfen:
# 1. conduwuit nicht aktiv (hard dependency)
systemctl status hydrahive-conduwuit

# 2. Python-Import-Fehler
sudo -u hydrahive /opt/hydrahive/venv/bin/python -c "import hydrahive_core"

# 3. Fehlende Konfigurationsdatei
ls -la /etc/hydrahive/

# 4. Port bereits belegt
ss -tlnp | grep 8765
```

### Core antwortet nicht (HTTP 502 / Timeout)

```bash
# Direkt auf Port 8765 testen (bypasses nginx)
curl -v http://127.0.0.1:8765/health

# nginx-Status
systemctl status nginx
nginx -t

# Logs beider Services
journalctl -u hydrahive-core -n 20
journalctl -u nginx -n 20
```

### Matrix/conduwuit nicht erreichbar

```bash
# conduwuit läuft?
systemctl status hydrahive-conduwuit

# Port prüfen (6167 intern)
curl http://127.0.0.1:6167/_matrix/client/versions

# Logs
journalctl -u hydrahive-conduwuit -n 30
```

**conduwuit neu starten:**
```bash
sudo systemctl restart hydrahive-conduwuit
sleep 3
curl http://127.0.0.1:6167/_matrix/client/versions
```

**conduwuit komplett zurücksetzen** (löscht alle Matrix-Nachrichten!):
```bash
sudo systemctl stop hydrahive-conduwuit
sudo rm -f /var/lib/hydrahive/conduwuit/conduwuit.db*
sudo systemctl start hydrahive-conduwuit
# Danach: Agenten-Matrix-Accounts über den Installer neu anlegen
sudo bash /opt/hydrahive/installer/07_matrix_accounts.sh
```

**Matrix-Raum für Agenten neu anlegen** (nach Reset):
```bash
# Accounts prüfen
curl -s http://127.0.0.1:6167/_matrix/client/v3/login \
  -d '{"type":"m.login.password","user":"hydrahive_support","password":"<pw>"}' | python3 -m json.tool
```

### A-MEM (Semantisches Gedächtnis) nicht erreichbar

A-MEM ist ein MCP/SSE-Server — `GET /` gibt **404 zurück, das ist normal**.

```bash
# A-MEM läuft?
systemctl is-active hydrahive-amem  # muss "active" sein

# MCP-Endpoint testen (SSE, nicht REST)
curl -N http://127.0.0.1:8020/sse   # sollte SSE-Stream öffnen

# Logs
journalctl -u hydrahive-amem -n 30
```

**A-MEM neu starten:**
```bash
sudo systemctl restart hydrahive-amem
```

**A-MEM Datenpersistenz:**
- Notizen: `/opt/amem/data/` (SQLite + Vektor-Index)
- Redis-Cache: läuft als Dependency auf `127.0.0.1:6379`
- Backup: Redis-Daten werden von `hydrahive-backup.sh` eingeschlossen

```bash
# Redis läuft?
redis-cli ping   # → PONG
# Wenn nicht:
sudo systemctl start redis
```

### Agent antwortet nicht / LLM-Fehler

```bash
# API-Key gesetzt?
grep -c "API_KEY" /etc/hydrahive/llm_env

# LLM-Config valides JSON?
python3 -m json.tool /etc/hydrahive/llm_config.json

# Test-Request
curl -s http://127.0.0.1:8765/agents | python3 -m json.tool

# Core-Logs während einer Chat-Anfrage
journalctl -u hydrahive-core -f
# (gleichzeitig Chat-Anfrage senden)
```

### Dateiberechtigungen falsch

**Symptom:** Core startet, aber kann nicht in /agents/ oder /projects/ schreiben.

```bash
# Permissions reparieren
sudo chown -R hydrahive:hydrahive /agents /projects /opt/hydrahive
sudo chown root:hydrahive /etc/hydrahive
sudo chmod 770 /etc/hydrahive
sudo find /etc/hydrahive -type f -exec chmod 600 {} \;

sudo systemctl restart hydrahive-core
```

### Console lädt nicht / weißer Screen

```bash
# nginx-Konfiguration prüfen
nginx -t
cat /etc/nginx/sites-available/hydrahive-console

# Console-Build vorhanden?
ls -la /opt/hydrahive/console/index.html

# Browser-Cache leeren (Ctrl+Shift+R)
# Zertifikat-Warnung akzeptieren (self-signed)
```

### Gitea nicht erreichbar

```bash
# Status
systemctl status gitea

# Port
ss -tlnp | grep 3001

# Starten
sudo systemctl start gitea

# Konfiguration prüfen
cat /etc/hydrahive/gitea_config.json
```

### Update schlägt fehl (rsync permission denied)

Das Update-Script setzt vor dem rsync den Owner auf den SSH-User:

```bash
# Manuell auf der VM
sudo chown -R <ssh-user>:<ssh-user> /opt/hydrahive/core/
sudo chown -R <ssh-user>:<ssh-user> /opt/hydrahive/console/

# Dann Update erneut ausführen
./scripts/hydrahive-update.sh
```

### WhatsApp-Bridge Probleme

```bash
# Status
systemctl status hydrahive-whatsapp

# QR-Code erneut scannen
systemctl restart hydrahive-whatsapp
journalctl -u hydrahive-whatsapp -f
# QR-Code erscheint in den Logs
```

### Shell-Tool blockiert (shell_exec Blocklist)

Bestimmte Befehle sind in `shell_exec` permanent blockiert, um Datenverlust zu
verhindern. Die Blocklist umfasst u.a.:

- `rm -rf` (rekursives Löschen)
- `dd` (Disk-Operationen)
- `mkfs` (Dateisystem-Erstellung)
- `git`-Befehle in `/opt/`
- `systemctl stop hydrahive-core`

Wenn ein Agent einen legitimen privilegierten Befehl ausführen muss, muss dieser
manuell auf der VM ausgeführt werden.

### Log-Rotation einrichten

```bash
# /etc/logrotate.d/hydrahive erstellen
cat > /etc/logrotate.d/hydrahive << 'EOF'
/var/log/hydrahive/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    postrotate
        systemctl reload hydrahive-core 2>/dev/null || true
    endscript
}
EOF
```

### Diagnose-Zusammenfassung

```bash
# Schnell-Check aller kritischen Services
for svc in hydrahive-core hydrahive-conduwuit nginx gitea; do
    STATUS=$(systemctl is-active $svc 2>/dev/null)
    printf "%-30s %s\n" "$svc" "$STATUS"
done

# Health-Endpoints
curl -sf http://127.0.0.1:8765/health && echo "Core: OK" || echo "Core: FAIL"
curl -sf http://127.0.0.1:6167/_matrix/client/versions > /dev/null && echo "Matrix: OK" || echo "Matrix: FAIL"

# Disk-Nutzung
df -h /opt /agents /projects /etc/hydrahive
```
