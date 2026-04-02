# HydraHive — Infrastruktur-Dokumentation

Stand: März 2026 | VM: <your-vm-ip> | Hostname: <your-hostname>

---

## 1. Server-Hardware (KVM-VM auf Proxmox)

| Komponente | Details |
|------------|---------|
| CPU | 16 vCPUs (Host: x86-64, Hyperthreading) |
| RAM | 61 GB |
| Disk | 491 GB (`/dev/sda2`, eine Partition für alles) |
| GPU | NVIDIA GeForce GTX 1080 Ti (11 GB VRAM) |
| OS | Ubuntu 24.04.4 LTS |
| Kernel | 6.17.0-19-generic |
| Typ | KVM/QEMU VM |

---

## 2. Netzwerk

| Interface | IP | Zweck |
|-----------|-----|-------|
| `ens3` | `<your-vm-ip>/24` | LAN (Heimnetz) |
| `tailscale0` | `100.110.63.75/32` | Tailscale VPN (Remote-Zugriff) |
| `docker0` | `172.17.0.1/16` | Docker (keine Container aktiv) |

**Tailscale-Netz**: `eulenspiegel41@` Account, aktuell 2 Geräte registriert (dieser Server + iPhone).

---

## 3. Alle laufenden Dienste und Ports

### 3.1 Öffentlich erreichbar (von LAN aus)

| Port | Protokoll | Dienst | Beschreibung |
|------|-----------|--------|--------------|
| `80` | HTTP | **nginx → HydraHive Console** | React-Frontend + API-Proxy auf :8765 |
| `3002` | HTTP | **nginx → Gitea** | Web-UI für lokalen Git-Server |
| `8008` | HTTP | **nginx → Matrix** | Conduwuit Matrix-Homeserver (für Element etc.) |
| `8020` | HTTP | **A-MEM MCP** | A-MEM Memory-Server (MCP-Endpoint) |
| `8021` | HTTP | **A-MEM Search UI** | A-MEM Suchoberfläche |
| `139` | TCP | **Samba** | NetBIOS (Windows-Freigaben) |
| `445` | TCP | **Samba** | SMB (Windows-Freigaben) |
| `22` | SSH | **OpenSSH** | Admin-Zugriff |

### 3.2 Nur intern (localhost)

| Port | Dienst | Beschreibung |
|------|--------|--------------|
| `8765` | **HydraHive Core** | FastAPI Backend (uvicorn, 1 Worker) |
| `8010` | **AgentLink** | Agent-Handoff-API (FastAPI) |
| `8767` | **WhatsApp Bridge** | Node.js Baileys-Bridge |
| `6167` | **Conduwuit** | Matrix-Homeserver intern |
| `6379` | **Redis** | Cache (für AgentLink) |
| `5432` | **PostgreSQL** | Datenbank (nur `agentlink` DB aktiv) |
| `11434` | **Ollama** | Lokale LLM-Inferenz (für A-MEM) |
| `3001` | **Gitea intern** | Gitea-Prozess (nginx proxied nach 3002) |
| `8181` | **A-MEM Memory Search** | Semantischer Memory-Suchserver (ehem. QMD) |

---

## 4. Dienste im Detail

### 4.1 HydraHive Core (`hydrahive-core.service`)
- **Pfad**: `/opt/hydrahive/` (Core-Code: `core/src/hydrahive_core/`)
- **Port**: `127.0.0.1:8765`
- **User**: `hydrahive`
- **Venv**: `/opt/hydrahive/venv/` (Python 3.12, `/usr/bin/python3.12`)
- **Config**: `/etc/hydrahive/`
- **Abhängig von**: `hydrahive-conduwuit.service`
- **Loggt nach**: `journalctl -u hydrahive-core`
- **Neustart**: `systemctl restart hydrahive-core`
- **Update**: `sudo bash /opt/hydrahive/update.sh` (klont Gitea/GitHub, baut Console, deployt)
- Das ist das Herzstück — alle Agents, Chat, Tools, Projekte laufen hier.

### 4.2 HydraHive Console (nginx)
- **Config**: `/etc/nginx/sites-enabled/hydrahive-console`
- **Root**: `/opt/hydrahive/console/` (Vite-Build aus `console/src/`)
- **Port**: `:80` public, proxy auf `:8765` für `/api/`
- **Gebaut**: `npm run build` im `console/` Verzeichnis des Repos
- **Deployed**: via `update.sh` → `rsync dist/ → /opt/hydrahive/console/`

### 4.3 AgentLink (`agentlink.service`)
- **Pfad**: `/opt/agentlink/backend/`
- **Port**: `0.0.0.0:8000` (öffentlich im LAN!)
- **User**: `octopos`
- **Venv**: `/opt/agentlink/venv/`
- **Datenbank**: PostgreSQL `agentlink` DB
- **Abhängig von**: `postgresql.service`, `redis-server.service`
- **Zweck**: Agent-zu-Agent Handoff-System (wird von HydraHive-Core über `write_handoff`/`read_handoff` Tools genutzt)
- **Größe**: 422 MB

### 4.4 Gitea (`gitea.service`)
- **Pfad**: `/opt/gitea/`
- **Ports**: intern `:3001`, öffentlich via nginx `:3002`
- **User**: `git`
- **Repos**: Lokaler Spiegel des HydraHive-Repos, Projektrepos
- **URL**: `http://<your-vm-ip>:3002`
- **Credentials**: User `<gitea-user>`, Token in `/etc/hydrahive/gitea_config.json`
- **Zweck**: Primäre Quelle für `update.sh`; Agents können Repos anlegen/pushen

### 4.5 Matrix / Conduwuit (`hydrahive-conduwuit.service`)
- **Pfad**: intern bei Conduwuit-Installation
- **Ports**: intern `:6167`, öffentlich via nginx `:8008`
- **Config**: `/etc/conduwuit/` (root-only lesbar)
- **Server-Name**: `hydrahive` (intern)
- **Zweck**: Messenger-Backend für Agents (Boss-Agents kommunizieren über Matrix-Räume)
- **Client**: Element Desktop (von außen via `.102` Matrix-Proxy)
- **Achtung**: `hydrahive-core` hat `Requires=hydrahive-conduwuit` — wenn Conduwuit stirbt, stirbt der Core mit

### 4.6 WhatsApp Bridge (`hydrahive-whatsapp-bridge.service`)
- **Pfad**: `/opt/hydrahive/whatsapp-bridge/`
- **Port**: `127.0.0.1:8767`
- **Tech**: Node.js + Baileys-Library
- **Sessions**: `/etc/hydrahive/whatsapp-sessions/`
- **Zweck**: WhatsApp-Nachrichten an/von Personal-Agents (personal_admin, personal_till)

### 4.7 A-MEM (`hydrahive-amem.service`)
- **Pfad**: `/opt/amem/`
- **Venv**: `/opt/amem/.venv/`
- **User**: `hydrahive`
- **Config**: `/etc/hydrahive/amem.env`
- **Cache**: `/opt/hydrahive/amem-cache/` (Transformer-Modelle)
- **Abhängig von**: `ollama.service`
- **Größe**: 7.7 GB (inkl. Ollama-Modelle + Transformer-Cache)
- **Zweck**: KI-gestütztes Memory-System für Agents (MCP-Server)

### 4.8 Ollama (`ollama.service`)
- **Port**: `127.0.0.1:11434`
- **GPU**: GTX 1080 Ti (11 GB VRAM)
- **Installierte Modelle**:
  | Modell | Größe | Zuletzt genutzt |
  |--------|-------|-----------------|
  | `qwen2.5:7b` | 4.7 GB | vor 4 Tagen |
  | `llama3.1:8b` | 4.9 GB | vor 4 Tagen |
  | `mistral-nemo:12b` | 7.1 GB | vor 6 Tagen |
  | `llama3.2:3b` | 2.0 GB | vor 6 Tagen |
- **Zweck**: Lokale LLM-Inferenz für A-MEM (nicht für HydraHive-Core-Agents — die nutzen Claude API)

### 4.9 A-MEM Memory Search (`qmd-mcp.service`)
- **Port**: `[::1]:8181`
- **User**: `octopos`
- **Binary**: `/usr/bin/qmd`
- **Zweck**: Semantische Suche über Agent-Memory-Dateien (ehem. QMD — Question-Driven Memory)
- **Agents**: Wird in `update.sh` re-indexiert (`qmd update -q && qmd embed -q`)

### 4.10 PostgreSQL (`postgresql.service`)
- **Port**: `127.0.0.1:5432`
- **Datenbanken**: nur `agentlink` (von AgentLink-Backend genutzt)
- **User**: `postgres`
- **Zweck**: Persistenz für AgentLink (Sessions, Handoffs)

### 4.11 Redis (`redis-server.service`)
- **Port**: `127.0.0.1:6379`
- **Zweck**: Cache für AgentLink-Backend

### 4.12 Samba (`smbd` + `nmbd`)
- **Ports**: 139, 445
- **Hauptconfig**: `/etc/samba/smb.conf`
- **Shares-Config**: `/etc/samba/octopos-shares.conf` (wird automatisch gepflegt)
- **Aktuelle Freigaben**:
  | Share | Pfad | Benutzer |
  |-------|------|----------|
  | `testprojekt` | `/projects/testprojekt/files` | `proj_testprojekt` |
  | `buchhaltung` | `/projects/buchhaltung/files` | `proj_buchhaltung` |
  | `claude-projekt` | `/projects/claude-projekt/files` | `proj_claude-projekt` |
  | `octopos_dev` | `/projects/octopos_dev/files` | `proj_octopos_dev` |
- **Problem**: Keine Samba-User konfiguriert (`pdbedit -L` leer) → Freigaben nicht erreichbar
- **Zweck**: Projektordner als Windows-Netzlaufwerke im Intranet zugänglich machen

### 4.13 Tailscale (`tailscaled.service`)
- **IP**: `100.110.63.75`
- **Account**: `eulenspiegel41@`
- **Zweck**: Remote-Zugriff auf den Server von unterwegs (iPhone + Lilith registriert)
- **DNS-Problem**: Tailscale meldet DNS-Warnung (kein Internetzugriff auf DNS-Server)

### 4.14 OpenVPN
- **Config**: `/etc/openvpn/client/` + `/etc/openvpn/server/`
- **Status**: `openvpn.service` enabled, aber Verbindungsstatus unklar
- **Redundanz zu Tailscale** — eines der beiden könnte ggf. entfernt werden

### 4.15 Docker (`docker.service`)
- **Status**: Installiert, läuft, aber **keine Container aktiv**
- **Bridge**: `172.17.0.1/16` reserviert
- **Könnte**: deinstalliert oder zumindest gestoppt werden (spart ~50 MB RAM)

### 4.16 nginx
- **Sites**:
  - `hydrahive-console` → Port 80 (HydraHive UI)
  - `gitea` → Port 3002 (Gitea UI)
  - `matrix.conf` → Port 8008 (Matrix)

---

## 5. Systemd-Services Übersicht

| Service | Status | Zweck | Kann weg? |
|---------|--------|-------|-----------|
| `hydrahive-core` | ✅ aktiv | Hauptsystem | nein |
| `hydrahive-conduwuit` | ✅ aktiv | Matrix-Server | nein |
| `hydrahive-amem` | ✅ aktiv | KI-Memory | nein |
| `hydrahive-whatsapp-bridge` | ✅ aktiv | WhatsApp | nein |
| `agentlink` | ✅ aktiv | Agent-Handoffs | prüfen |
| `gitea` | ✅ aktiv | Git-Server | nein |
| `nginx` | ✅ aktiv | Reverse Proxy | nein |
| `ollama` | ✅ aktiv | Lokale LLMs | nein (A-MEM braucht es) |
| `qmd-mcp` | ✅ aktiv | Memory MCP | nein |
| `postgresql` | ✅ aktiv | DB für AgentLink | solange agentlink läuft |
| `redis-server` | ✅ aktiv | Cache für AgentLink | solange agentlink läuft |
| `smbd` + `nmbd` | ✅ aktiv | Samba-Freigaben | nein (Intranet-Shares) |
| `tailscaled` | ✅ aktiv | Remote-VPN | nein |
| `openvpn` | ✅ aktiv | VPN | prüfen (Redundanz zu Tailscale?) |
| `docker` | ✅ aktiv | Container | prüfen (keine Container!) |
| `sddm` | ✅ aktiv | Display Manager | **ja — Server braucht keinen** |
| `blueman-mechanism` | ✅ aktiv | Bluetooth | **ja — Server ohne Bluetooth** |

---

## 6. Verzeichnisstruktur

```
/opt/
├── hydrahive/          # HydraHive Installation (757 MB)
│   ├── core/           # Python-Backend (hydrahive_core Paket)
│   ├── console/        # Gebaute React-App (Vite dist/)
│   ├── venv/           # Python 3.12 venv
│   ├── whatsapp-bridge/
│   ├── amem-cache/     # Transformer-Modelle Cache
│   ├── backups/        # Backup-Archiv (.tar.gz)
│   ├── docs/           # Dokumentation
│   └── update.sh       # Self-Update Script
├── agentlink/          # AgentLink Backend (422 MB)
│   ├── backend/        # FastAPI App
│   └── venv/
├── amem/               # A-MEM Installation (7.7 GB!)
│   └── .venv/
├── amem-search/        # A-MEM Such-UI
├── gitea/              # Gitea Binary
└── containerd/         # Docker/containerd Daten

/etc/hydrahive/         # Alle Secrets & Configs
├── admin_credentials   # Admin-Login (JWT-Secret)
├── users.json          # User-Liste
├── llm_config.json     # LLM-Provider (Claude API Key etc.)
├── llm_env             # Env-Vars für Core-Service
├── gitea_config.json   # Gitea URL + Token
├── github_token        # GitHub PAT
├── claude_oauth_token  # Anthropic OAuth Token
├── mcp_servers.json    # MCP-Server Konfigurationen
├── agentlink.json      # AgentLink-URL für Core
├── amem.env            # A-MEM Konfiguration
├── whatsapp_bridge_secret
├── whatsapp-sessions/  # WhatsApp-Session-Daten
└── agent_tokens/       # Matrix-Tokens pro Agent

/agents/                # Agent-Konfigurationen
├── personal_till/      # Tills persönlicher Agent (Lilith)
├── personal_admin/     # Admin-Agent (Castiel)
├── personal_bianca/    # Biancas Agent (Rowena)
├── boss_main/          # Haupt-Boss-Agent
├── claude_boss/        # Claude Boss
├── [diverse Spezialisten]/
└── [Test-Agents]/      # test_boss, test_coder etc. (aufräumbar)

/projects/              # Projektdaten + Samba-Freigaben
├── testprojekt/
├── buchhaltung/
├── claude-projekt/
└── octopos_dev/
```

---

## 7. Bekannte Probleme & offene TODOs

### Samba-Freigaben funktionieren nicht
- **Ursache**: Keine Samba-User angelegt. HydraHive legt Unix-User `proj_<name>` an,
  aber `smbpasswd -a proj_<name>` wurde nie aufgerufen.
- **Fix**: Für jeden Projekt-User Samba-Passwort setzen:
  ```bash
  sudo smbpasswd -a proj_testprojekt
  sudo smbpasswd -a proj_buchhaltung
  # etc.
  ```
- Oder: HydraHive-Code beim Projekt-Anlegen automatisch `smbpasswd` aufrufen lassen.

### Unbekannter Dienst auf Port 8020
- `ss -tlnp` zeigt Port 8020 offen, aber keinen zugehörigen Prozess
- Muss identifiziert werden: `sudo fuser 8020/tcp` oder `sudo lsof -i :8020`

### Alte Test-Agents können entfernt werden
- `test_boss`, `test_coder`, `test_reporter`, `test_researcher`
- `discord_specialist_1627f834`, `_644ac648`, `_9c690f65` (Session-Duplikate)
- `octopos-dev` (der Agent der `/opt/octopos/` gelöscht hat — Blocklist-Grund)
- `discord-agent` (ohne agent.yaml)
- `worker_template` (Template, kein echter Agent)

### Docker läuft leer
- `docker.service` + `containerd.service` aktiv aber keine Container
- Könnte gestoppt/disabled werden wenn nicht geplant genutzt

### Display Manager (sddm) läuft auf Server
- Server ist eine KVM-VM, kein Desktop nötig
- `systemctl disable --now sddm` spart Ressourcen

### Zwei VPNs (Tailscale + OpenVPN)
- Tailscale ist aktiv und verbunden
- OpenVPN-Status unklar — wird es noch genutzt?

### A-MEM braucht 7.7 GB
- Ollama-Modelle: qwen2.5:7b (4.7G), llama3.1:8b (4.9G), mistral-nemo:12b (7.1G), llama3.2:3b (2.0G)
- Nur eines wird für A-MEM gebraucht — die anderen könnten entfernt werden

---

## 8. Deployment & Update

```bash
# HydraHive updaten (klont Gitea/GitHub, baut Frontend, deployt, startet neu)
sudo bash /opt/hydrahive/update.sh

# Manuell einzelne Schritte:
sudo systemctl restart hydrahive-core       # Core neu starten
sudo systemctl status hydrahive-core        # Status prüfen
sudo journalctl -u hydrahive-core -f        # Live-Logs

# Backup
sudo bash /opt/hydrahive/scripts/hydrahive-backup.sh  # (falls vorhanden)

# nginx
sudo nginx -t && sudo systemctl reload nginx
```

---

## 9. Zugänge & Credentials (Übersicht, keine Passwörter)

| System | Zugang | Wo |
|--------|--------|-----|
| HydraHive Console | `admin` / in `/etc/hydrahive/admin_credentials` | http://<your-vm-ip> |
| Gitea | User `<gitea-user>` / Token in `gitea_config.json` | http://<your-vm-ip>:3002 |
| SSH | Key `~/.ssh/<your-ssh-key>` | `hydrahive@<your-vm-ip>` |
| Tailscale | `eulenspiegel41@` Account | Tailscale Dashboard |
| Matrix | Conduwuit Admin | `/etc/conduwuit/` |
| Ollama | kein Auth | nur localhost |
| PostgreSQL | User `agentlink` / Passwort in AgentLink `.env` | nur localhost |

---

## 10. System-Logs

```bash
# Wichtigste Logs
journalctl -u hydrahive-core -f           # Core live
journalctl -u hydrahive-amem -f           # A-MEM
journalctl -u agentlink -f                # AgentLink
tail -f /var/log/nginx/error.log          # nginx Fehler
tail -f /var/log/nginx/access.log         # nginx Zugriffe
tail -f /var/log/hydrahive-update.log     # Update-Log
/var/run/hydrahive-update.json            # Letzter Update-Status
```
