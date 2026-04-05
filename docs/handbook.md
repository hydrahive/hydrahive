# HydraHive Handbuch

HydraHive ist ein selbst-gehosteter KI-Agent-Server. Er läuft auf einer eigenen VM oder einem Server, verwaltet KI-Agenten, Projekte und Dateisysteme — ohne externe Cloud-Abhängigkeit.

---

## Aktueller Betriebsmodus

HydraHive ist inzwischen mehr als nur Agenten- und Projektverwaltung. Der aktuelle Arbeitsstand ist:

- **Lokales Memory** bleibt agentenspezifisch: `soul.md`, `memory/`, Skills und Session-Kontext
- **A-MEM** ist die gemeinsame Langzeit-Wissensdatenbank fuer alle Agenten
- **Execution Modes** steuern Agentenrechte in `safe`, `elevated` und `root`
- **Discord** ist ein untrusted Eingangskanal und wird wie andere externe Inputs kontrolliert behandelt
- **Gitea** ist nicht nur Git-Remote, sondern auch Issue-, Review- und Arbeitskanal fuer Agenten

Die A-MEM-Instanz laeuft lokal auf dem Host. Zugriff erfolgt im LAN ueber die Host-IP, lokal weiter ueber `127.0.0.1`.

---

## Inhaltsverzeichnis

0. [Aktueller Betriebsmodus](#aktueller-betriebsmodus)
1. [Installation](#1-installation)
2. [Erster Login](#2-erster-login)
3. [Die Webkonsole](#3-die-webkonsole)
4. [Agenten anlegen](#4-agenten-anlegen)
5. [Projekte anlegen](#5-projekte-anlegen)
6. [Chat verwenden](#6-chat-verwenden)
7. [LLM-Konfiguration](#7-llm-konfiguration)
8. [Skills — Agenten-Wissen erweitern](#8-skills--agenten-wissen-erweitern)
9. [Persönlicher Agent (Mein Agent)](#9-persönlicher-agent-mein-agent)
10. [Gedächtnis-System (Memory)](#10-gedächtnis-system-memory)
11. [WKS-Zugang (Workstation)](#11-wks-zugang-workstation)
12. [Git-Tools](#12-git-tools)
13. [MCP-Server](#13-mcp-server)
14. [VPN-Zugang (Tailscale / Headscale)](#14-vpn-zugang-tailscale--headscale)
15. [Webhooks](#15-webhooks)
16. [Benutzer und Rollen](#16-benutzer-und-rollen)
17. [Backup & Restore](#17-backup--restore)
18. [Audit-Log](#18-audit-log)
19. [Matrix-Integration](#19-matrix-integration)
20. [GPU-Monitoring](#20-gpu-monitoring)
21. [System-Update](#21-system-update)
22. [WhatsApp-Integration](#22-whatsapp-integration)
23. [Discord-Integration](#23-discord-integration)
24. [Aktivitäts-Übersicht](#24-aktivitäts-übersicht)
25. [API Usage & Kostenübersicht](#25-api-usage--kostenübersicht)
26. [Diagnose & Tests](#26-diagnose--tests)
27. [Troubleshooting](#27-troubleshooting)
28. [Erweiterungs-Manager](#28-erweiterungs-manager)
29. [Schedules — Zeitgesteuerte Aufgaben](#29-schedules--zeitgesteuerte-aufgaben)
30. [Butler — Visuelle Automatisierungsregeln](#30-butler--visuelle-automatisierungsregeln)
31. [Web-Suche (SearXNG)](#31-web-suche-searxng)
32. [Code Editor (VS Code im Browser)](#32-code-editor-vs-code-im-browser)
33. [A2A Federation](#33-a2a-federation)
34. [Benachrichtigungen](#34-benachrichtigungen)
35. [Vaultwarden — Passwort-Manager](#35-vaultwarden--passwort-manager)
36. [HydraHub — Agenten & Plugins installieren](#36-hydrahub--agenten--plugins-installieren)
37. [Plugin-System](#37-plugin-system)
38. [ClawhHub — Externe Skills & Plugins](#38-clawhub--externe-skills--plugins)
39. [Tailscale Federation](#39-tailscale-federation)
40. [HydraBrain — 3D-Agentengraph](#40-hydrabrain--3d-agentengraph)

---

## 1. Installation

### Voraussetzungen

- Ubuntu 22.04 oder 24.04 LTS (amd64)
- Mindestens 8 GB RAM, 80 GB Disk
- Empfohlen: 32 GB RAM, 1 TB Disk (bei mehreren Nutzern, Extensions und Projekten mit großen Dateien)
- Root-Zugriff
- Internetzugang für Download

**Proxmox LXC:** Grundsätzlich unterstützt, aber VPN (Tailscale) benötigt TUN-Support. Im Proxmox-Host: `pct set <CTID> -features nesting=1` und unter Options → Features **TUN** aktivieren. Ohne TUN wird das VPN-Modul automatisch übersprungen. Empfohlen wird eine vollwertige VM statt LXC.

### Installer ausführen

```bash
git clone https://github.com/hydrahive/hydrahive.git
cd hydrahive
sudo bash installer/install.sh
```

Der Installer läuft vollautomatisch und richtet ein:
- conduwuit (Matrix-Homeserver)
- HydraHive Core (Python-Backend)
- HydraHive Console (React-Frontend via nginx)
- HTTPS mit self-signed Zertifikat
- Optional: Ollama (bei `PROFILE=full`)

**Idempotent:** Der Installer kann beliebig oft ausgeführt werden. Bereits installierte Komponenten werden übersprungen.

### HTTPS und Zertifikat

Nach der Installation ist die Konsole unter `https://<IP>` erreichbar. Browser zeigen eine Warnung wegen des self-signed Zertifikats — einmalig bestätigen.

Für ein echtes Zertifikat mit Let's Encrypt:
```bash
apt-get install -y certbot python3-certbot-nginx
certbot --nginx -d mein.domain.de
```

---

## 2. Erster Login

Nach der Installation ist die Konsole sofort nutzbar. Der Installer hat einen Admin-Account angelegt:

- **Benutzername:** `admin`
- **Passwort:** steht in `/etc/hydrahive/admin_credentials` auf dem Server

```bash
sudo cat /etc/hydrahive/admin_credentials
# matrix_admin_password=<generiertes-passwort>
```

Das angezeigte Passwort ist der initiale Login. Nach dem ersten Login empfiehlt sich das Anlegen eines persönlichen Admin-Accounts unter **Benutzer** → **Neuer Benutzer**.

> Weitere Benutzer können als `admin` (vollen Zugriff) oder `user` (nur Chat und Lesen) angelegt werden.

---

## 3. Die Webkonsole

Die Konsole ist unter `https://<IP>` erreichbar. Alle Bereiche sind über die linke Sidebar erreichbar — 10 Menüpunkte, flach ohne aufklappbare Gruppen:

| Bereich | Funktion | Zugriff |
|---|---|---|
| **Dashboard** | Überblick über Agenten, Projekte, System-Status — Tabs: Status, Aktivität, Usage, Audit | alle |
| **Mein Agent** | Persönlicher Agent: Chat, Heartbeat, Messenger, WKS, Butler, Mein Konto | alle |
| **Agenten** | Agenten anlegen und bearbeiten — Tabs: Agenten, Tools, Plugins, Federation, Blueprint | alle (Schreiben: admin) |
| **Projekte** | Projekte anlegen, Chat öffnen, Webhooks und Samba-Zugangsdaten — Tabs: Projekte, Schedules | alle (Schreiben: admin) |
| **Blueprint** | Automation, Pipelines, Architect, Workflow, Scratchpad, Benachrichtigungen | admin |
| **Hub** | HydraHub, Hub-Plugins, ClawhHub, Extensions, Plugins, Skill-Pakete | admin |
| **HydraBrain** | Interaktive 3D-Visualisierung aller Agenten und Verbindungen | admin |
| **System** | Doctor, GPU-Monitoring, Monitoring | alle |
| **User-Verwaltung** | Benutzer, Gruppen, Secrets, Berechtigungen | admin |
| **Settings** | Zentrale Konfiguration — Tabs: Übersicht (ConfigHub), LLM, Gitea, GitHub, VPN, Mail/KAS, Backup, Migration | admin |

### Die Seite Settings

Die Seite **Settings** (`/settings`) fasst die Admin-Konfiguration in mehreren Tabs zusammen:

| Tab | Inhalt |
|---|---|
| **Übersicht** | ConfigHub — Gesamtübersicht aller Konfigurationsparameter |
| **LLM** | Sprachmodell-Konfiguration (Ollama, Claude OAuth, OpenAI, Fallback-Modelle) |
| **Gitea** | Gitea-URL, Token, Organisation |
| **GitHub** | GitHub-Token und Repository-Anbindung |
| **VPN** | Tailscale-Verbindung konfigurieren |
| **Mail / KAS** | E-Mail und All-Inkl KAS-Anbindung |
| **Backup** | Backups erstellen, herunterladen, wiederherstellen |
| **Migration** | Daten-Import/Export zwischen Instanzen |

> **Hinweis:** Benutzer, Gruppen und Secrets werden jetzt unter **User-Verwaltung** verwaltet (nicht mehr unter Settings). MCP-Server und Plugins sind unter **Agenten** bzw. **Hub** zu finden.

### Konfigurationsmatrix — Wo wird was konfiguriert?

Nicht alle Einstellungen liegen auf der Settings-Seite. Diese Übersicht zeigt, wo welche Konfiguration gepflegt wird:

| Bereich | UI-Pfad | Config-Datei | Zweck |
|---|---|---|---|
| LLM-Provider | Settings → LLM | `/etc/hydrahive/llm_config.json` | API-Keys, Modell-Auswahl, OAuth |
| MCP-Server | Agenten → MCP (Tab) | `/etc/hydrahive/mcp_servers.json` | Externe Tool-Server |
| Gitea | Settings → Gitea | `/etc/hydrahive/gitea_config.json` | Git-Server-Anbindung |
| GitHub | Settings → GitHub | `/etc/hydrahive/github_token` | GitHub-Token |
| VPN | Settings → VPN | `/etc/hydrahive/vpn.json` | Tailscale-Konfiguration |
| Mail / KAS | Settings → Mail/KAS | `/etc/hydrahive/kas.json` | E-Mail und KAS-API |
| Benutzer | User-Verwaltung → Benutzer | `/etc/hydrahive/users.json` | Accounts und Rollen |
| Secrets | User-Verwaltung → Secrets | `/etc/hydrahive/users.json` | Persönliche Secrets |
| Discord | Mein Agent → Messenger → Discord | `/etc/hydrahive/agent_tokens/<agent_id>_discord.json` | Persönlicher Discord-Bot |
| WhatsApp | Mein Agent → Messenger → WhatsApp | `/etc/hydrahive/agent_tokens/<agent_id>_whatsapp.json` | WhatsApp-Bridge |
| A2A Federation | Agenten → Federation (Tab) | `/etc/hydrahive/a2a_peers.json` | Peer-Verbindungen |
| AgentLink | — (nur Datei) | `/etc/hydrahive/agentlink.json` | Handoff-API-Konfiguration |
| Claude OAuth | Settings → LLM | `/etc/hydrahive/claude_oauth_token` | Claude Max Token |
| JWT-Secret | — (automatisch) | `/etc/hydrahive/jwt_secret` | Auth-Token-Signierung |
| Schedules | Projekte → Schedules (Tab) | `/etc/hydrahive/schedules.json` | Zeitgesteuerte Tasks |
| Butler-Regeln | Blueprint → Automation oder Mein Agent → Butler | `/etc/hydrahive/butler/` | Automatisierungs-Pipelines |

### Mehrsprachigkeit (DE/EN)

Die Konsole unterstützt Deutsch und Englisch. Der Sprachumschalter befindet sich in der Sidebar (Schaltfläche **DE** / **EN**). Die gewählte Sprache wird im Browser gespeichert und beim nächsten Öffnen automatisch wiederhergestellt. Insgesamt sind ~730 Strings übersetzt.

---

## 4. Agenten anlegen

Agenten sind KI-Persönlichkeiten die Aufgaben ausführen. Jeder Agent hat einen Typ, ein Sprachmodell und optional eine "Soul" (Persönlichkeitsbeschreibung).

### Agent-Typen

| Typ | Beschreibung |
|---|---|
| **boss** | Nimmt Nutzer-Nachrichten entgegen, koordiniert Worker-Agenten |
| **specialist** | Spezialist für ein Themengebiet, wird vom Boss delegiert |
| **worker** | Kurzlebiger Task-Agent, wird on-demand gespawnt |

### Agent anlegen (Konsole)

1. **Agenten** → Tab **Agenten** → **Neuer Agent**
2. Felder ausfüllen:
   - **Agent-ID:** Eindeutiger Bezeichner (z.B. `steuer-agent`)
   - **Anzeigename:** Wird im Chat angezeigt (z.B. `Steuerbert`)
   - **Typ:** `boss`, `specialist` oder `worker`
   - **LLM-Modell:** Verfügbares Modell (z.B. `llama3.1:8b`)
   - **Tools:** Welche Fähigkeiten der Agent hat — Auswahl über gruppierten ToolGroupSelector (Checkboxen)
   - **MCP-Server:** Checkbox-Liste der verfügbaren MCP-Server
   - **Plugins:** Checkbox-Liste der installierten Plugins
   - **Erlaubte Agenten:** Checkbox-Liste für Agenten-Delegation
   - **Soul:** Freier Markdown-Text der die Persönlichkeit beschreibt
3. **Agent anlegen** klicken

> **Hinweis:** Persönliche Agenten (`personal_*`) sind jetzt in der Agenten-Liste sichtbar und bearbeitbar — können jedoch nicht gelöscht werden.

### Agent-Konfiguration (Datei)

Agenten liegen als Verzeichnisse unter `/agents/<id>/`:

```
/agents/steuer-agent/
├── agent.yaml       # Konfiguration
├── soul.md          # Persönlichkeit (optional)
├── skills/          # Skill-Dateien (optional)
│   ├── steuerrecht.md
│   └── buchhaltung.md
└── memory/          # Gedächtnis-Dateien (optional)
    ├── user.md
    └── projects.md
```

**agent.yaml:**
```yaml
id: steuer-agent
type: specialist
identity: Steuerbert

llm:
  model: llama3.1:8b
  temperature: 0.7
  max_tokens: 4096
  fallback_models:
    - claude-haiku-4-5-20251001
  ollama_base_url: null   # WKS-Ollama-Endpunkt (optional)

tools:
  - file_read
  - file_write
  - read_memory
  - write_memory

mcp_servers:
  - amem                 # MCP-Server-IDs aus /etc/hydrahive/mcp_servers.json

sources:
  - name: "Dokumentation"
    url: "https://docs.example.com"
    description: "Offizielle Projektdokumentation"

heartbeat:
  interval: 30s
  timeout: 90s
  on_failure: restart

heartbeat_tasks:
  - id: tagesstart
    message: "Guten Morgen! Bitte prüfe offene Aufgaben und erstelle eine Tages-Zusammenfassung."
    schedule: "0 8 * * 1-5"    # Mo–Fr um 08:00
    active_hours: "07:00-22:00"

  - id: erinnerung
    message: "Bitte prüfe ob neue Dokumente hochgeladen wurden."
    interval: 3600              # jede Stunde
```

### Heartbeat Tasks — Automatische Aktivierung

`heartbeat_tasks` definiert periodische Aufgaben die der Agent automatisch ausführt — ohne manuelles Triggern.

| Feld | Pflicht | Beschreibung |
|---|---|---|
| `id` | ja | Eindeutiger Name des Tasks |
| `message` | ja | Nachricht die an den Agenten gesendet wird |
| `schedule` | nein | Cron-Ausdruck: `"0 8 * * *"` (täglich 08:00) |
| `interval` | nein | Sekunden-Intervall: `3600` (jede Stunde) |
| `project` | nein | Explizites Projekt; sonst: erstes Projekt des Boss-Agenten |
| `active_hours` | nein | Nur in diesem Zeitfenster aktiv: `"08:00-22:00"` |

Entweder `schedule` (Cron-Syntax) oder `interval` (Sekunden) muss angegeben werden.

> **Hinweis:** Heartbeat Tasks laufen nur auf `boss`-Agenten mit zugeordnetem Projekt. Verpasste Ausführungen (Server war aus) werden nicht nachgeholt.

### Verfügbare Tools

| Tool | Beschreibung |
|---|---|
| `file_read` | Datei im Projektverzeichnis lesen |
| `file_write` | Datei im Projektverzeichnis schreiben |
| `web_search` | Websuche durchführen |
| `http_request` | HTTP-Anfragen an externe APIs |
| `shell_exec` | Shell-Befehl auf dem Server ausführen (siehe Blocklist unten) |
| `read_system_file` | Systemdatei außerhalb des Projekts lesen (z.B. Konfigurationen) |
| `write_system_file` | Systemdatei schreiben (eingeschränkt) |
| `dispatch_task` | Andere Agenten beauftragen (nur boss) |
| `spawn_agent` | Kurzlebigen Worker-Agenten erstellen |
| `ask_agent` | Synchron einen anderen Agenten befragen |
| `delegate_agent` | Asynchron einen Agenten beauftragen |
| `write_handoff` | Aufgabe/Kontext an anderen Agenten übergeben (AgentLink) |
| `read_handoff` | Übergabe-Auftrag entgegennehmen (AgentLink) |
| `read_memory` | Gedächtnis-Datei des Agenten lesen |
| `write_memory` | Gedächtnis-Datei des Agenten schreiben |
| `git_status` | Git-Status eines Projekts abfragen |
| `git_diff` | Git-Diff anzeigen |
| `git_commit` | Dateien committen |
| `git_push` | Commits zu Gitea pushen |
| `git_create_pr` | Pull Request erstellen |
| `wks_shell_exec` | Shell-Befehl auf der eigenen Workstation ausführen (SSH) |
| `wks_file_read` | Datei von der Workstation lesen (SFTP) |
| `wks_file_write` | Datei auf die Workstation schreiben (SFTP) |

> Alle Filesystem-Operationen sind auf `/projects/<projekt-id>/` beschränkt. Zugriff darüber hinaus wird verweigert.

### shell_exec Blocklist

`shell_exec` läuft ohne Sandbox mit vollem Systemzugriff, ist aber gegen destruktive Aktionen gesichert. Folgende Kommandos werden **immer blockiert**, unabhängig vom Agenten:

| Kategorie | Blockiert |
|---|---|
| Rekursives Löschen | `rm -r`, `rm -rf`, `rm` auf `/opt/` |
| Disk-Destruktion | `dd of=/dev/…`, `mkfs`, `fdisk`, `parted`, `shred`, `wipefs` |
| HydraHive-Sabotage | `systemctl stop/disable/mask/kill hydrahive`, `killall uvicorn` |
| Geschützte Pfade | Redirects (`>`) nach `/etc/`, `/bin/`, `/usr/`, `/lib`, `/boot/`, `/dev/`, `/sys/`, `/proc/`, `/opt/hydrahive/` |
| Rechteänderungen | `chmod`/`chown` auf `/opt/`, `/etc/`, `/bin/` |
| Git in Systempfaden | `git clone`/`reset --hard` nach/in `/opt/hydrahive/` |
| Shell-Escapes | `$()` Command Substitution, Backticks `` ` ``, `eval`, Subshell über `bash -c` |
| Fork-Bomben | `:() { …` |

Geblockte Befehle werden mit einer Fehlermeldung abgelehnt und im Log protokolliert.

### Agent Sources — Quellen & Suchmaschinen

Agenten können eine Liste von URLs oder Suchmaschinen zugewiesen bekommen. Diese werden beim Start automatisch in den System-Prompt injiziert. Der Agent nutzt `http_request`, um die Quellen vor dem Antworten abzurufen und so aktuelle oder domänenspezifische Informationen einzubeziehen.

**Konfiguration in `agent.yaml`:**
```yaml
sources:
  - name: "Unreal Engine Docs"
    url: "https://dev.epicgames.com/documentation/en-us/unreal-engine"
    description: "Offizielle Unreal Engine Dokumentation"
  - name: "Context7"
    url: "https://context7.com/unreal-engine"
    description: "Semantische Suche für Unreal-Kontext"
```

**Konfiguration in der Konsole:** **Agenten** → Tab **Agenten** → Agent bearbeiten → Abschnitt **„Quellen & Suchmaschinen"**

Jede Quelle hat drei Felder:

| Feld | Beschreibung |
|---|---|
| `name` | Anzeigename der Quelle |
| `url` | URL die der Agent abruft |
| `description` | Kurze Beschreibung — hilft dem Agenten die Quelle richtig einzusetzen |

### System-Handbuch (system_handbook.md)

Jeder Agent erhält beim Start automatisch den Inhalt von `/etc/hydrahive/system_handbook.md` in seinen System-Prompt injiziert. Das System-Handbuch ist das gemeinsame Regelwerk für alle Agenten auf dieser HydraHive-Instanz.

**Standardmäßig enthält es:**
- Research-first-Prinzip: erst lesen → planen → handeln
- Anleitungen zur Nutzung der A-MEM-Tools
- AgentLink Handoff-Anweisungen
- Repository-Referenzen und Projekt-Konventionen

**Anpassen:**
```bash
sudo nano /etc/hydrahive/system_handbook.md
```

Änderungen werden beim nächsten Agenten-Start wirksam. Das System-Handbuch ist für alle Agenten identisch — agentenspezifische Anweisungen gehören in die `soul.md` des jeweiligen Agenten.

---

## 5. Projekte anlegen

Ein Projekt ist ein isolierter Arbeitsbereich: eigener Linux-User, eigenes Verzeichnis, optionale Samba-Freigabe, optionaler Matrix-Room.

### Projekt anlegen (Konsole)

1. **Projekte** → **Neues Projekt**
2. Felder ausfüllen:
   - **Projekt-ID:** Eindeutiger Bezeichner (z.B. `buchhaltung`)
   - **Name:** Anzeigename
   - **Boss-Agent:** Welcher Agent das Projekt leitet
   - **Worker-Agenten:** Kommagetrennte Liste optionaler Helfer
   - **Samba-Freigabe:** Ob ein Windows-Share eingerichtet wird
3. **Projekt anlegen** klicken

HydraHive richtet automatisch ein:
- Linux-User `proj_<id>` mit eigenem Home-Verzeichnis
- Verzeichnis `/projects/<id>/` für Agenten-Dateien
- Matrix-Room (wenn Matrix konfiguriert)
- Samba-Freigabe inkl. automatisch generiertem Passwort (wenn aktiviert)

### Samba-Zugangsdaten

Nach dem Anlegen erscheinen in der Projektkarte (Workspace-Bereich) die Samba-Zugangsdaten:

- **Benutzername:** `proj_<id>` (Linux-User)
- **Passwort:** automatisch generiert, versteckt — per Auge-Icon aufdecken
- **Reset:** Neues Zufallspasswort setzen per Klick auf "Reset"

Die Zugangsdaten werden auf dem Server in `/etc/hydrahive/samba_credentials` (chmod 600, nur root) gespeichert.

**Samba-Pfad:** `\\<server-ip>\<projekt-id>` oder `smb://<server-ip>/<projekt-id>`

### Projekt-Konfiguration (Datei)

```yaml
# /projects/buchhaltung/project.yaml
id: buchhaltung
version: "1.0.0"

identity:
  name: Buchhaltung GmbH
  description: Finanzagenten für die GmbH

agents:
  boss: finanz-boss
  workers:
    - steuer-agent
    - buchhalter

matrix:
  room: "!abc123:mein-server.de"    # leer lassen → wird automatisch angelegt

filesystem:
  samba: true

chat:
  show_swarm: false    # true = Worker-Agenten im Chat anzeigen
```

#### Task-Agenten konfigurieren

```yaml
task_agents:
  ttl: 600        # Sekunden bis Task-Agent gestoppt wird (default: 300)
  max_parallel: 5 # max gleichzeitige Task-Agenten (default: 10)
```

---

## 6. Chat verwenden

1. **Projekte** → **Chat öffnen** beim gewünschten Projekt
2. Nachricht eingeben und `Enter` drücken (oder `Shift+Enter` für Zeilenumbruch)
3. Die Antwort erscheint Token-für-Token (Streaming)

### Swarm-Ansicht

Das **Netzwerk-Symbol** (oben rechts im Chat) schaltet die Swarm-Ansicht um. Bei aktivierter Ansicht wird unter jeder Antwort angezeigt welche Worker-Agenten beteiligt waren.

### Token-Anzeige

Unter jeder Assistenten-Antwort erscheint eine kleine Anzeige mit dem Tokenverbrauch der aktuellen Anfrage:

```
↑ 12.450 ↓ 380 Tokens
```

- **↑** = Input-Tokens (gesendeter Kontext inkl. System-Prompt und History)
- **↓** = Output-Tokens (erzeugte Antwort)

Die Anzeige hilft dabei, den LLM-Ressourcenverbrauch im Blick zu behalten — besonders bei kostenpflichtigen Modellen (Claude, GPT-4 etc.).

### Chat-History

Die Nachrichten einer Session bleiben erhalten und werden beim nächsten Öffnen automatisch geladen.

### Slash Commands

Im Chat stehen Schnellbefehle zur Verfügung. Tippe `/` um die verfügbaren Befehle anzuzeigen — die Auswahl erscheint als Dropdown.

| Befehl | Funktion |
|---|---|
| `/help` | Alle verfügbaren Befehle anzeigen |
| `/clear` | Chat-Verlauf leeren (nur in der Anzeige, keine Server-Änderung) |
| `/status` | Projekt-Informationen anzeigen (Boss-Agent, Modell, Worker) |
| `/model` | Aktuell verwendetes LLM-Modell anzeigen |
| `/retry` | Letzte eigene Nachricht erneut senden |

**Bedienung:** Nach dem `/` mit Pfeiltasten navigieren, `Tab` oder `Enter` zum Auswählen, `Escape` schließt das Dropdown.

---

## 7. LLM-Konfiguration

### Ollama (lokal, kostenfrei)

Standardmäßig läuft Ollama lokal auf Port 11434. Modelle werden über **Settings** → Tab **LLM** → Abschnitt **Ollama** verwaltet.

Empfohlene Modelle:
- `llama3.2:3b` — schnell, wenig VRAM (4 GB)
- `llama3.1:8b` — ausgewogen (8 GB VRAM)
- `qwen2.5:7b` — gute Tool-Nutzung (8 GB VRAM)
- `mistral-nemo:12b` — beste Qualität lokal (12 GB VRAM)

### Claude Max (OAuth)

Für Claude-Modelle via Claude Max Abonnement:

1. **Settings** → Tab **LLM** → Abschnitt **Claude Max**
2. OAuth-Token einfügen (`sk-ant-oat01-...`)
3. Speichern

Der Token-Status wird mit Ablauf-Datum angezeigt. oat01-Tokens gelten ~30 Tage.

### OpenAI / andere Anbieter

1. **Settings** → Tab **LLM** → Abschnitt **Anbieter konfigurieren**
2. API-Key und Modell eintragen

### Fallback-Modelle

Jeder Agent kann Fallback-Modelle definieren. Wenn das primäre Modell nicht erreichbar ist, wird automatisch das nächste versucht:

```yaml
llm:
  model: ollama/llama3.1:8b
  fallback_models:
    - claude-haiku-4-5-20251001
    - ollama/llama3.2:3b
```

---

## 8. Skills — Agenten-Wissen erweitern

Skills sind Markdown-Dateien die dem Agenten zusätzliches Wissen geben. Sie werden automatisch in den System-Prompt geladen.

### Skill anlegen (Konsole)

1. **Agenten** → Tab **Agenten** → Agent auswählen → **Skills** (Buch-Icon)
2. **Neuer Skill**
3. Felder ausfüllen:
   - **Dateiname:** Dateiname ohne `.md` (z.B. `steuerrecht`)
   - **Skill-Name:** Anzeigename
   - **Scope:** `always` (immer geladen) oder `on-demand` (nur bei Keyword-Treffer)
   - **Trigger-Keywords:** Bei `on-demand` — wann der Skill aktiviert wird
   - **Priority:** Reihenfolge bei mehreren Skills (1 = höchste Priorität)
   - **Inhalt:** Markdown-Text mit dem Wissen

### Skill-Datei (direkt)

```markdown
---
skill: Steuerrecht Grundlagen
version: "1.0"
scope: on-demand
triggers:
  - steuer
  - finanzamt
  - umsatzsteuer
  - einkommensteuer
priority: 10
---

## Steuerrecht Grundlagen

Umsatzsteuer (USt) beträgt in Deutschland standardmäßig 19%.
Ermäßigter Satz: 7% für Lebensmittel, Bücher, ÖPNV.
```

> **Hot-Reload:** Skills werden bei jedem Request neu eingelesen. Kein Core-Neustart notwendig.

### Skill-Pakete (Blueprint Editor)

Skill-Pakete bündeln mehrere Skills zu wiederverwendbaren Gruppen. Der visuelle Blueprint Editor ermöglicht das Zusammenstellen und Verknüpfen von Skills — ähnlich wie im Butler-Editor.

**Erreichbar unter:** **Hub** → Tab **Skill-Pakete**.

**Knoten-Typen:**

| Knoten | Beschreibung |
|---|---|
| **Skill** | Ein konkreter Skill — über Dropdown aus allen vorhandenen Skills auswählen |
| **Bedingung** | Optionaler Bedingungsknoten — steuert ob ein Skill geladen wird |
| **Abhängigkeit** | Verweis auf ein anderes Skill-Paket — ermöglicht Paket-Verschachtelung |
| **Ausgabe** | Abschlussknoten — definiert den Endpunkt des Pakets |

**Paket anlegen:**
1. **Hub** → Tab **Skill-Pakete** → **+ Neues Paket**
2. Knoten aus der Palette auf die Canvas ziehen
3. Knoten verbinden
4. Name vergeben → **Speichern**
5. Paket über den Toggle aktivieren/deaktivieren

Aktive Pakete werden automatisch beim Laden des Agenten-Kontexts einbezogen. Pakete liegen unter `/etc/hydrahive/skill_packages/`.

### Self-Learning Skills (Agent-gesteuert)

Agenten können eigenständig neue Skills anlegen, auflisten und löschen — ohne manuellen Eingriff über die Konsole.

**Agent-Tools:**

| Tool | Beschreibung |
|---|---|
| `create_skill` | Neuen Skill anlegen (`filename`, `content`); Frontmatter wird automatisch gesetzt; `author: agent` wird eingetragen |
| `list_skills` | Alle vorhandenen Skills des Agenten auflisten |
| `delete_skill` | Skill löschen — nur wenn `author: agent` im Frontmatter steht |

**Regeln:**

- Agenten können nur Skills löschen die sie selbst angelegt haben (`author: agent`). System-Skills (`author: human` oder ohne `author`-Feld) sind schreibgeschützt.
- Admin kann alle Skills — einschließlich agent-erstellter — über die Konsole verwalten.
- In der Konsole zeigt ein Bot-Icon neben dem Skill-Namen an, dass der Skill von einem Agenten erstellt wurde.

**Beispiel:** Ein Agent erhält neue Informationen und legt eigenständig einen Skill an:

```
# Agent im Chat:
→ create_skill(filename="neue_preisliste", content="---\nskill: Preisliste 2026\nscope: on-demand\ntriggers: [preis, angebot]\nauthor: agent\n---\n\nStandardpreis: 99 € / Monat\n...")
```

---

## 9. Persönlicher Agent (Mein Agent)

Jeder User bekommt automatisch einen persönlichen Agenten — `personal_<username>`. Dieser Agent läuft unabhängig von Projekten und ist direkt über **Mein Agent** in der Sidebar erreichbar.

### Chat

Der Chat unter **Mein Agent → Chat** funktioniert wie der Projekt-Chat: Streaming, Chat-History, Slash Commands. Der Agent merkt sich den Gesprächsverlauf sessionübergreifend.

### Mein Konto

Unter **Mein Agent → Mein Konto** kann jeder User seinen Agenten selbst konfigurieren:

| Einstellung | Beschreibung |
|---|---|
| **Name / Identität** | Anzeigename des Agenten |
| **Soul** | Markdown-Text für Persönlichkeit und Verhalten |
| **Primäres Modell** | Dropdown mit allen verfügbaren Modellen (Server-Ollama + WKS-Ollama + Cloud) |
| **Temperatur** | Kreativität (0 = deterministisch, 1 = kreativ) |
| **Max Tokens** | Maximale Antwortlänge |
| **Fallback-Modelle** | Alternativ-Modelle wenn primäres nicht erreichbar |
| **Tools** | Welche Tools der Agent nutzen darf |
| **Agenten-Delegation** | Welche anderen Agenten beauftragt werden dürfen |

### Messenger

Unter **Mein Agent → Messenger** werden alle Kommunikationskanäle als Akkordeon-Abschnitte verwaltet:

- **WhatsApp** — WhatsApp-Bridge konfigurieren und verknüpfen
- **Discord** — Discord-Bot-Token und Server-Einstellungen
- **Telegram** — Telegram-Bot einrichten
- **Mail** — E-Mail-Einstellungen

Die früheren separaten Tabs "Plattformen" und "Integrationen" entfallen — alle Kanäle sind in diesem einzelnen Tab zusammengefasst.

### WKS-Tab

Workstation-Zugang konfigurieren — siehe [Kapitel 11](#11-wks-zugang-workstation).

### Butler-Tab

Automatisierungsregeln für Messenger-Nachrichten — visuelle Blueprint-Flows. Siehe [Kapitel 30](#30-butler--visuelle-automatisierungsregeln).

---

## 10. Gedächtnis-System (Memory)

Das Gedächtnis-System ermöglicht Agenten, Informationen persistent über Sessions hinaus zu speichern und gezielt abzurufen.

### Funktionsweise

Memory-Dateien liegen als Markdown-Dateien unter `/agents/<id>/memory/`:

```
/agents/personal_admin/memory/
├── user.md          # Informationen über den User
├── projects.md      # Aktive Projekte und Kontext
├── daily_2026-03-21.md  # Tages-Notizen
└── handbook.md      # Wichtige Referenz-Dokumente
```

Beim Start einer Session werden alle Memory-Dateien automatisch in den System-Prompt des Agenten injiziert.

### Tools

| Tool | Beschreibung |
|---|---|
| `read_memory` | Einzelne Gedächtnis-Datei lesen (`filename`) |
| `write_memory` | Gedächtnis-Datei schreiben oder aktualisieren (`filename`, `content`) |

Agenten sollten für gezielte Suche statt vollständiger Injektion A-MEM Memory Search nutzen (spart Tokens).

### A-MEM Memory Search

Wenn der Agent `qmd` als MCP-Server konfiguriert hat, kann er gezielt in den Memory-Dateien suchen:

```
# Im Chat mit dem Agenten:
"Was weißt du über das AgentLink-Projekt?"
→ Agent ruft qmd_query("AgentLink Projekt") auf
→ Liefert relevante Chunks statt alle Dateien zu laden
```

Das spart erheblich Tokens gegenüber der vollständigen Memory-Injektion.

### System-Topologie (automatisch generiert)

Beim Start des Core generiert HydraHive automatisch eine Datei `system_topology.md` im Memory-Verzeichnis jedes Agenten. Diese Datei enthält:

- Alle laufenden Services mit ihren Ports
- Konfigurierte WKS-IPs der Nutzer
- Aktive Plattform-Verbindungen (Matrix, Gitea, Discord, WhatsApp, VPN)
- Aktive MCP-Server

Die Datei wird bei jedem Core-Neustart aktualisiert. Agenten kennen damit von Beginn an die Systemstruktur, ohne sie manuell abfragen zu müssen.

```
/agents/personal_admin/memory/
├── user.md
├── projects.md
└── system_topology.md    ← automatisch generiert beim Start
```

---

## 11. WKS-Zugang (Workstation)

Persönliche Agenten können via SSH auf die eigene Workstation des Users zugreifen — Dateien lesen/schreiben und Befehle ausführen.

### Einrichten

1. **Mein Agent → WKS-Tab** öffnen
2. **IP-Adresse** der Workstation eintragen (z.B. `192.168.1.101`)
3. **SSH-Benutzer** eintragen (z.B. `till`)
4. **SSH Private Key** (PEM) einfügen
5. **Ollama-Port** (Standard: `11434`)
6. **Speichern**

**SSH-Key auf der Workstation einrichten:**

```bash
# Auf der Workstation:
# Den Public Key in authorized_keys eintragen
echo "ssh-ed25519 AAAA... hydrahive-wks@hydrahive" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# SSH-Daemon aktivieren (falls nicht aktiv)
sudo systemctl enable --now ssh
```

**Verbindung testen:**

Im WKS-Tab → **Verbindung testen** — zeigt verfügbare Ollama-Modelle auf der WKS an.

### WKS-Tools

| Tool | Beschreibung |
|---|---|
| `wks_shell_exec` | Shell-Befehl auf der WKS ausführen (mit optionalem `cwd`) |
| `wks_file_read` | Datei von der WKS lesen (absoluter Pfad) |
| `wks_file_write` | Datei auf die WKS schreiben (absoluter Pfad) |

Diese Tools müssen in **Mein Agent → Mein Konto → Tools** aktiviert werden.

### WKS-Ollama

Wenn Ollama auf der Workstation läuft und der Port von außen erreichbar ist, erscheinen die WKS-Modelle automatisch im Modell-Dropdown:

```bash
# Ollama auf allen Interfaces lauschen lassen:
sudo mkdir -p /etc/systemd/system/ollama.service.d
echo '[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"' | sudo tee /etc/systemd/system/ollama.service.d/listen.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

Im Modell-Dropdown erscheinen WKS-Modelle als `WKS: <modellname>`. Beim Auswählen wird automatisch die WKS-IP als `ollama_base_url` im Agenten gespeichert.

---

## 12. Git-Tools

Agenten können direkt mit Gitea interagieren — Git-Status prüfen, Commits erstellen und Pull Requests öffnen.

### Gitea konfigurieren

1. **Settings** → Tab **Gitea** (oder direkt in `/etc/hydrahive/gitea_config.json`)
2. Gitea-URL, Token, Organisation eintragen

```json
{
  "url": "http://192.168.1.100:3001",
  "token": "dein-gitea-token",
  "org": "hydrahive"
}
```

### Git-Tools

| Tool | Beschreibung |
|---|---|
| `git_status` | Status eines Repos abfragen (`project_id`) |
| `git_diff` | Unstaged/staged Änderungen anzeigen |
| `git_commit` | Dateien committen (`files`, `message`, optional `branch`) |
| `git_push` | Branch zu Gitea pushen |
| `git_create_pr` | Pull Request erstellen (`title`, `head`, optional `base`, `body`) |

### Projekt-ID

Alle Git-Tools akzeptieren einen optionalen `project_id`-Parameter. Standardmäßig wird die ID des aktuellen Projekts verwendet. Für persönliche Agenten muss ein explizites Projekt angegeben werden:

```
# Im Chat:
"Erstelle einen Commit für testprojekt mit den aktuellen Änderungen"
→ Agent: git_commit(project_id="testprojekt", files=[...], message="...")
```

---

## 13. MCP-Server

MCP (Model Context Protocol) ermöglicht Agenten den Zugriff auf externe Tool-Server. HydraHive unterstützt `streamableHttp`- und `sse`-Transport.

### MCP-Integration ist vollautomatisch

Sobald ein Agent einen MCP-Server in seiner Konfiguration hat, werden die Tools des Servers **automatisch** verfügbar. Es ist keine manuelle Tool-Registrierung notwendig.

**Tool-Namenskonvention:** `mcp_{server_id}_{tool_name}`

Beispiel: Server-ID `amem` → Tools heißen `mcp_amem_add_note`, `mcp_amem_search_memory` usw.

### MCP-Server konfigurieren (Admin)

1. **Agenten** → Tab **MCP**
2. **Neuer MCP-Server**
3. Felder ausfüllen: ID, Name, Transport (`streamableHttp` oder `sse`), URL

Oder direkt in `/etc/hydrahive/mcp_servers.json`:

```json
[
  {
    "id": "amem",
    "name": "A-MEM Shared Memory",
    "transport": "sse",
    "url": "http://192.168.178.5:8080/sse"
  }
]
```

### Agent einem MCP-Server zuweisen

In `agent.yaml`:
```yaml
mcp_servers:
  - amem
```

Oder in **Agenten** → Agent bearbeiten → MCP-Checkbox-Liste → Server aktivieren.

### A-MEM — Shared Memory MCP

A-MEM ist die gemeinsame Langzeit-Wissensdatenbank für alle Agenten. A-MEM kann pro Agent unter **Agenten → Agent bearbeiten → MCP-Server** aktiviert werden (`mcp_servers: [amem]`). A-MEM läuft als eigenständiger Service und ist über das LAN erreichbar.

Agenten mit `amem` in `mcp_servers` erhalten automatisch folgende Tools:

| Tool | Beschreibung |
|---|---|
| `mcp_amem_add_note` | Neue Wissensnotiz speichern |
| `mcp_amem_search_memory` | Volltextsuche in allen Notizen |
| `mcp_amem_vector_search` | Semantische Vektorsuche |
| `mcp_amem_update_note` | Bestehende Notiz aktualisieren |
| `mcp_amem_delete_note` | Notiz löschen |
| `mcp_amem_get_note` | Einzelne Notiz abrufen |
| `mcp_amem_amem_stats` | Statistiken der Wissensdatenbank |

A-MEM eignet sich für agentenübergreifendes Wissen: Recherche-Ergebnisse, Projekt-Erkenntnisse, geteilte Faktensammlungen.

### A-MEM Memory Search MCP

A-MEM Memory Search ist ein semantischer Suchserver für die Memory-Dateien der Agenten (ehem. QMD). Er läuft als systemd-Service auf Port 8181:

```bash
sudo systemctl status qmd-mcp
# Active: active (running) — A-MEM MCP server listening on http://localhost:8181/mcp
```

Agenten mit `qmd` in `mcp_servers` können gezielt in ihren Memory-Dateien suchen statt alle Dateien beim Start zu laden. Dies spart deutlich Tokens.

---

## 14. VPN-Zugang (Tailscale / Headscale)

HydraHive unterstützt Tailscale als VPN-Overlay für sichere Verbindungen ohne Portweiterleitung im Router. Damit können Workstations und externe Nutzer den HydraHive-Server über ein verschlüsseltes Overlay-Netz erreichen — unabhängig von NAT oder Firewall-Einstellungen.

### Funktionsweise

Tailscale baut ein Mesh-VPN auf Basis von WireGuard auf. Jedes Gerät im Tailnet erhält eine stabile IP (100.x.x.x). Optional kann ein selbst-gehostetes Headscale als Coordinator statt der Tailscale-Cloud verwendet werden.

### Einrichten

1. **Settings** → Tab **VPN** öffnen
2. Tailscale-Auth-Key eintragen (aus dem Tailscale-Dashboard oder von Headscale generiert)
3. Optional: **Headscale-URL** eintragen für selbst-gehosteten Coordinator
4. **Verbinden** klicken

Der Installer richtet Tailscale automatisch ein wenn das Modul `12_vpn.sh` aktiv ist:

```bash
sudo bash installer/install.sh   # VPN-Modul wird automatisch eingebunden
```

### API-Endpunkte

| Endpunkt | Methode | Beschreibung |
|---|---|---|
| `/admin/vpn/status` | GET | Aktuellen Verbindungsstatus abfragen |
| `/admin/vpn/connect` | POST | VPN-Verbindung aufbauen |
| `/admin/vpn/down` | POST | VPN-Verbindung trennen |

```bash
# Status abfragen
curl https://<ip>/api/admin/vpn/status \
  -H "Authorization: Bearer <token>"

# Verbinden
curl -X POST https://<ip>/api/admin/vpn/connect \
  -H "Authorization: Bearer <token>"
```

### Headscale (selbst-gehostet)

Wer keine Tailscale-Cloud nutzen möchte, kann Headscale als eigenen Coordinator betreiben:

```bash
# Headscale auf separatem Server installieren
curl -fsSL https://github.com/juanfont/headscale/releases/latest/download/headscale_linux_amd64 \
  -o /usr/local/bin/headscale
chmod +x /usr/local/bin/headscale

# Auth-Key generieren
headscale preauthkeys create --user hydrahive --expiration 24h
```

Die Headscale-URL (z.B. `https://headscale.mein-server.de`) und den generierten Auth-Key dann in **Settings → VPN** eintragen.

### Typischer Anwendungsfall

```
Workstation (Till, WKS 192.168.1.50)
    │  Tailscale (100.64.0.5)
    ▼
HydraHive-Server (100.64.0.1)
    │  /api/
    ▼
Agent-Chat, WKS-Tools, Admin-Konsole
```

> **Hinweis:** VPN ist optional. Ohne VPN ist HydraHive nur im lokalen Netz erreichbar, sofern kein öffentliches Portforwarding eingerichtet ist.

---

## 15. Webhooks

Webhooks ermöglichen externe Systeme HydraHive zu triggern — z.B. bei einem Git-Push automatisch einen Agenten starten.

### Webhook anlegen (Konsole)

1. **Projekte** → Webhook-Icon beim Projekt
2. **Neuer Webhook**
3. URL, Name, Events und optional Secret eintragen

### Externer Trigger (Wake-Endpoint)

```bash
curl -X POST https://<ip>/api/hooks/<projekt-id>/wake \
  -H "Content-Type: application/json" \
  -d '{"message": "Neuer Git-Push auf main — bitte Code-Review starten"}'
```

Dieser Endpoint ist öffentlich zugänglich (kein Auth nötig) und startet den Boss-Agenten asynchron.

### Signierung

Wenn ein Secret gesetzt ist, wird jeder ausgehende Webhook-Request mit `X-HydraHive-Signature: sha256=<hmac>` signiert. Prüfung auf Empfänger-Seite:

```python
import hmac, hashlib
expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
assert f"sha256={expected}" == request.headers["X-HydraHive-Signature"]
```

---

## 16. Benutzer und Rollen

Unter **User-Verwaltung** (Admin only) werden Benutzer, Gruppen, Secrets und Berechtigungen verwaltet. HydraHive kennt zwei Rollen:

| Rolle | Rechte |
|---|---|
| **admin** | Vollzugriff: Agenten/Projekte anlegen und löschen, Settings (LLM, MCP, VPN usw.), Backup, User-Verwaltung, System-Update |
| **user** | Lesezugriff + Chat: Agenten und Projekte sehen und nutzen, eigenen Agenten konfigurieren, keine Systemkonfiguration |

- **Neuer Benutzer:** Benutzername, Passwort und Rolle (`admin` oder `user`) wählen
- **Passwort ändern:** Stift-Symbol
- **Löschen:** Papierkorb-Symbol (eigener Account kann nicht gelöscht werden)

Beim Anlegen eines Users wird automatisch ein persönlicher Agent `personal_<username>` erstellt.

---

## 17. Backup & Restore

Unter **Settings → Backup** (Admin only) können vollständige System-Backups erstellt und verwaltet werden.

### Was wird gesichert?

Ein Backup enthält als `tar.gz`:
- `/etc/hydrahive/` — alle Konfigurationsdateien (JWT-Secret, Users, LLM-Config, Admin-Credentials, WKS-Keys)
- `/agents/` — alle Agenten-Definitionen, Skills und Memory-Dateien
- `/projects/` — Projekt-Konfigurationen und Agenten-Dateien

**Nicht enthalten:** Betriebssystem, venv, Console-Build (werden bei Bedarf neu installiert).

### Backup erstellen

1. **Settings** → Tab **Backup** → **Backup erstellen**
2. Das Backup erscheint sofort in der Liste mit Zeitstempel und Dateigröße

Backups liegen auf dem Server unter `/opt/hydrahive/backups/`.

### Backup herunterladen

Klick auf das Download-Symbol neben dem Backup. Die `tar.gz`-Datei wird heruntergeladen.

### Backup wiederherstellen

1. Klick auf das Wiederherstellen-Symbol
2. Bestätigen
3. Der Core-Service startet automatisch neu
4. Nach 10 Sekunden Seite neu laden

> Nach der Wiederherstellung sind alle Agenten, Projekte und Konfigurationen auf den Stand des Backups zurückgesetzt.

### Backup per Kommandozeile

```bash
# Backup erstellen
curl -X POST https://<ip>/api/admin/backups \
  -H "Authorization: Bearer <token>"

# Alle Backups auflisten
curl https://<ip>/api/admin/backups \
  -H "Authorization: Bearer <token>"
```

### Automatisches Backup

```bash
# Skript liegt in scripts/hydrahive-backup.sh
./scripts/hydrahive-backup.sh
```

---

## 18. Audit-Log

Das Audit-Log ist unter **Dashboard → Tab Audit** erreichbar (Admin only). Es protokolliert alle sicherheitsrelevanten Aktionen:

- Logins (erfolgreich und fehlgeschlagen)
- Benutzer anlegen/löschen
- Agenten anlegen/löschen
- Projekte anlegen/provisionieren
- Skills anlegen/ändern/löschen
- Webhooks anlegen/löschen/auslösen
- LLM-Token setzen
- WKS-Konfiguration ändern

Gespeichert in `/var/log/hydrahive/audit.jsonl` — append-only, ein JSON-Objekt pro Zeile.

**Filter:** Nach Benutzer, Aktion, Projekt und Anzahl Einträge filterbar.

---

## 19. Matrix-Integration

HydraHive kann Nachrichten über Matrix (Element) empfangen und beantworten.

### Element-Client verbinden

Matrix-Server läuft auf Port 8008 (nginx-Proxy → conduwuit auf 6167):

- **Homeserver:** `https://<ip>:8008` oder `http://<ip>:8008`
- **Benutzer:** `@admin:<hostname>`
- **Passwort:** aus `/etc/hydrahive/admin_credentials`

### Agenten-Bot im Room

Wenn ein Projekt einen Matrix-Room hat, lauscht der Boss-Agent dort automatisch. Nachrichten im Room werden wie Chat-Nachrichten behandelt.

---

## 20. GPU-Monitoring

Wenn eine NVIDIA-Grafikkarte im Server verfügbar ist, zeigt **System** → Tab **GPU** eine GPU-Auslastungsanzeige.

### Angezeigte Werte

| Anzeige | Beschreibung |
|---|---|
| GPU-Auslastung | Prozent der Shader-Prozessoren in Benutzung |
| VRAM-Auslastung | Prozent des Grafikspeichers belegt |
| Speicher | Verwendet / Gesamt in MB |
| Temperatur | GPU-Kerntemperatur in °C |
| Leistungsaufnahme | Watt (aktuell / Limit) |

Die Auslastungsbalken sind farbkodiert: grün (< 80 %), orange (< 95 %), rot (≥ 95 %).

### Voraussetzung

NVIDIA-Treiber und `nvidia-smi` müssen installiert sein. Ohne GPU oder ohne Treiber wird die GPU-Sektion nicht angezeigt (kein Fehler).

---

## 21. System-Update

HydraHive kann sich selbst aktualisieren — entweder manuell über die Console oder automatisch per Webhook.

### Update über die Console

Admins sehen in der Sidebar einen **Update**-Button mit dem aktuellen Commit-Hash. Ein Klick startet den Update-Prozess:

1. Aktuellen Stand von Gitea (primär) oder GitHub (Fallback) klonen
2. Core-Dateien aktualisieren (`rsync`)
3. Python-Dependencies neu installieren (`pip install -e .`)
4. Console bauen (`npm ci && npm run build`)
5. Console deployen
6. `hydrahive-core` neustarten
7. A-MEM Memory re-indexieren

Der Update läuft in einem isolierten systemd-Transient-Unit — der Core kann sich selbst neustarten ohne den Prozess zu unterbrechen.

**Status:** Der Button zeigt während des Updates einen Ladeindikator und danach den neuen Commit-Hash.

### Update per Kommandozeile

```bash
sudo bash /opt/hydrahive/update.sh
```

### Automatische Updates (systemd)

Der `hydrahive-selfupdate.service` ermöglicht geplante automatische Updates:

```bash
# Status prüfen
sudo systemctl status hydrahive-selfupdate

# Manuell auslösen
sudo systemctl start hydrahive-selfupdate

# Automatisch täglich um 03:00 Uhr (Beispiel-Cron):
# sudo crontab -e → 0 3 * * * systemctl start hydrahive-selfupdate
```

Der `update.sh` aktualisiert sich beim Update selbst — die neueste Version liegt immer unter `/opt/hydrahive/update.sh`.

---

## 22. WhatsApp-Integration

HydraHive unterstützt WhatsApp als Kommunikationskanal. Eine Node.js-basierte Bridge (Baileys) verbindet WhatsApp mit dem Agenten-System.

### Funktionsumfang

| Funktion | Beschreibung |
|---|---|
| **Nachrichten empfangen** | Eingehende WhatsApp-Nachrichten werden an den konfigurierten Agenten weitergeleitet |
| **Nachrichten senden** | Agenten können WhatsApp-Nachrichten an Kontakte oder Gruppen senden |
| **Sprachnachrichten transkribieren** | Eingehende Voice Notes werden automatisch per Whisper transkribiert |
| **Text-to-Speech** | Agenten-Antworten können als Sprachnachricht zurückgesendet werden |

### Service

Die WhatsApp-Bridge läuft als systemd-Service:

```bash
sudo systemctl status hydrahive-whatsapp-bridge
sudo journalctl -u hydrahive-whatsapp-bridge -f
```

### Einrichten

1. Service starten: `sudo systemctl start hydrahive-whatsapp-bridge`
2. QR-Code im Log anzeigen lassen: `sudo journalctl -u hydrahive-whatsapp-bridge -n 50`
3. WhatsApp auf dem Mobilgerät öffnen → **Verknüpfte Geräte** → QR-Code scannen
4. Nach erfolgreicher Verknüpfung läuft die Bridge dauerhaft im Hintergrund

### Konfiguration

Die Bridge-Konfiguration liegt in `/etc/hydrahive/whatsapp_config.json`:

```json
{
  "agent_id": "personal_admin",
  "tts_enabled": true,
  "transcribe_voice": true,
  "allowed_contacts": []
}
```

- `allowed_contacts`: Wenn leer, werden alle eingehenden Nachrichten akzeptiert. Wenn befüllt, nur Nachrichten dieser Nummern (Format: `491234567890`).
- `tts_enabled`: Ob Antworten als Sprachnachricht zurückgesendet werden.
- `transcribe_voice`: Ob eingehende Voice Notes transkribiert werden (erfordert Whisper).

> **Hinweis:** Die WhatsApp-Verknüpfung muss nach einem Geräte-Reset oder nach längerer Inaktivität erneuert werden. Der QR-Code wird dann erneut im Service-Log angezeigt.

---

## 23. Discord-Integration

HydraHive unterstützt Discord als Kommunikationskanal. Agenten können Discord-Nachrichten empfangen und antworten.

### Einrichten

1. **Mein Agent** → Tab **Messenger** → Akkordeon-Abschnitt **Discord** aufklappen
2. Bot-Token eingeben (aus dem Discord Developer Portal)
3. Guild-ID und Kanal-IDs eingeben → **Verbinden**

Bot-Token und Guild-ID findest du im [Discord Developer Portal](https://discord.com/developers/applications) unter Applications → Deine App → Bot (Token) bzw. Guild Settings (Server-ID bei aktiviertem Developer Mode).

### Funktionsumfang

- Eingehende Nachrichten werden an den persönlichen Agenten weitergeleitet
- Antworten werden im gleichen Kanal gepostet
- **Loop-Detektion:** Bot-zu-Bot-Nachrichten werden automatisch unterdrückt (kein Echo-Loop)

### Discord-Tools für Agenten

Über die Tool-Auswahl (**Mein Agent → Mein Konto → Erlaubte Tools**) kannst du dem Agenten weitere Discord-Werkzeuge freischalten:

| Tool | Beschreibung |
|---|---|
| `discord_send` | Nachricht in einen Channel senden |
| `discord_read` | Letzte Nachrichten aus einem Channel lesen |
| `discord_list_channels` | Text-Channels der Guild auflisten |
| `discord_list_all_channels` | Alle Channels inkl. Kategorien auflisten |
| `discord_create_category` | Neue Kategorie erstellen |
| `discord_create_channel` | Neuen Text-Channel erstellen |
| `discord_delete_channel` | Channel oder Kategorie löschen |
| `discord_set_topic` | Channel-Topic setzen |
| `discord_rename_channel` | Channel umbenennen |
| `discord_list_members` | Mitglieder der Guild auflisten |
| `discord_list_roles` | Rollen der Guild auflisten |
| `discord_delete_message` | Nachricht löschen |
| `discord_pin_message` | Nachricht anpinnen |

### Erweiterte Konfiguration

**Filter:**

| Einstellung | Standard | Beschreibung |
|---|---|---|
| **Bots ignorieren** | An | Nachrichten anderer Bots werden nicht an den Agenten weitergegeben |
| **Nur bei @Erwähnung** | Aus | Agent reagiert nur wenn er direkt im Channel @erwähnt wird |
| **User-Whitelist** | Leer | Nur diese Discord-User-IDs dürfen den Agenten erreichen |
| **User-Blacklist** | Leer | Diese Discord-User-IDs werden ignoriert |
| **Rollen-Whitelist** | Leer | Nur Mitglieder mit diesen Rollen-IDs dürfen schreiben |
| **Rollen-Blacklist** | Leer | Mitglieder mit diesen Rollen werden ignoriert |

**Channel-Modi:**

Pro Channel kann ein Modus festgelegt werden:

| Modus | Verhalten |
|---|---|
| `rw` (Standard) | Agent liest und antwortet |
| `ro` | Agent liest nur — keine Antworten in diesem Channel |

**Loop-Detektion:**

| Einstellung | Standard | Beschreibung |
|---|---|---|
| Loop-Detektion | An | Aktiviert den Circuit Breaker |
| Bot-Schwellenwert | 3 | Wie viele Bot-Nachrichten vor dem Auslösen |
| PingPong-Fenster | 30s | Zeitfenster für Schnell-Nachrichten-Erkennung |
| Cooldown | 300s | Wie lange der Circuit Breaker geschlossen bleibt |

### Hinweis

Discord gilt als „untrusted" Eingangskanal — Nachrichten werden wie externe Nutzer-Inputs behandelt und nicht mit erhöhten Rechten ausgeführt.

---

## 24. Aktivitäts-Übersicht

Der Tab **Aktivität** im Dashboard zeigt in Echtzeit alle aktiven Agenten und deren aktuellen Status. Erreichbar unter **Dashboard → Tab Aktivität**.

### Funktionen

| Funktion | Beschreibung |
|---|---|
| **Live-Status** | Jeder laufende Agent mit aktuellem Task, Laufzeit und Modell |
| **Farbkategorien** | Boss (blau), Specialist (lila), Worker (grün), Personal (orange) |
| **Detail-Modal** | Klick auf Agent → Live-Logs und Session-Details |
| **Notfall-Stop** | Agent sofort stoppen ohne SSH-Zugriff |
| **Alert-System** | Kritische Fehler werden als Banner angezeigt |
| **Stale-Daten** | Bei Verbindungsunterbrechung bleiben letzte Daten sichtbar (gedimmt) |

Die Seite aktualisiert sich automatisch alle paar Sekunden.

---

## 25. API Usage & Kostenübersicht

Der Tab **Usage** im Dashboard zeigt Token-Verbrauch und geschätzte API-Kosten aller Agenten. Erreichbar unter **Dashboard → Tab Usage**.

### Übersicht

| Kennzahl | Beschreibung |
|---|---|
| **Input Tokens** | Tokens im System-Prompt + Nutzer-Nachrichten |
| **Output Tokens** | Vom LLM generierte Tokens |
| **Cache Hits** | Wiederverwendete Tokens dank Prompt Caching |
| **API-Kosten** | Geschätzte Kosten in USD nach Modell-Preistabelle |

### Aufschlüsselung

Jedes Projekt hat eine eigene Karte mit Aufklapp-Funktion:
- Kosten und Tokens nach Modell
- Sessions mit Token-Daten
- Cache-Effizienz-Anzeige

Die Seite enthält außerdem eine **Preisreferenz-Tabelle** ($/1M Tokens) für alle konfigurierten Modelle.

> Token-Daten werden ab dem ersten Agent-Gespräch nach dem Update gespeichert. Ältere Sessions ohne Token-Counts werden als solche gekennzeichnet.

---

## 26. Diagnose & Tests

Unter **System** (Sidebar) gibt es drei Tabs zur Systemdiagnose: **Doctor**, **GPU** und **Monitoring**. Die Diagnose-Werkzeuge sind im Tab **Doctor** zu finden:

### Doctor

Prüft automatisch die wichtigsten Systemkomponenten:

- Core-Erreichbarkeit und Response-Time
- Matrix/conduwuit-Verbindung
- Samba-Status und Share-Konfiguration
- Freier Speicher auf allen Partitionen
- Agent-Konfigurationsfehler
- VPN-Verbindungsstatus

### Unit-Tests

Führt die integrierten Unit-Tests (22+ Tests) direkt aus der Konsole aus:
- Orchestrator-Tests
- Sicherheits-Tests (Shell-Exec Sandbox, Rate-Limiting)
- Tool-Registry-Tests

Beide Tools sind nur für Admins sichtbar und schreiben keine Änderungen.

---

## 27. Troubleshooting

### Konsole nicht erreichbar

```bash
sudo systemctl status nginx
sudo nginx -t
sudo journalctl -u nginx -n 50
```

### Core reagiert nicht

```bash
sudo systemctl status hydrahive-core
sudo journalctl -u hydrahive-core -n 100 --no-pager
```

### Agent antwortet nicht

1. **Agenten** → Agent auswählen → Logs-Icon prüfen
2. Heartbeat-Status in der Agent-Liste prüfen (orange = Warnung)
3. LLM-Verbindung prüfen: **Settings** → Tab **LLM** → Status

### WKS-Verbindung schlägt fehl

```bash
# SSH manuell testen (vom HydraHive-Server):
sudo ssh -i /etc/hydrahive/wks_keys/<username> <ssh_user>@<wks_ip> hostname

# Häufige Ursachen:
# - SSH-Daemon auf WKS nicht aktiv: sudo systemctl enable --now ssh
# - Public Key nicht in authorized_keys
# - Firewall blockiert Port 22
```

### Matrix-Bot antwortet nicht

```bash
sudo journalctl -u hydrahive-core -n 50 | grep -i matrix
```

Der Matrix-Watchdog startet den Bot automatisch neu. Bei dauerhaftem Fehler: `sudo systemctl restart hydrahive-core`

### OAuth-Token abgelaufen

**Settings** → Tab **LLM** → Abschnitt **Claude Max** → Token-Status prüfen. Neuen Token über `claude setup` holen und eintragen.

### Login funktioniert nicht (429 Too Many Requests)

Rate-Limiting: max. 10 Versuche pro Minute. Nach 60 Sekunden warten.

### A-MEM Memory Search nicht erreichbar

```bash
sudo systemctl status qmd-mcp
sudo journalctl -u qmd-mcp -n 30
# Bei Bedarf neu starten:
sudo systemctl restart qmd-mcp
```

### Logs direkt auf Server

```bash
# Core-Logs
sudo journalctl -u hydrahive-core -f

# Update-Log
sudo tail -f /var/log/hydrahive-update.log

# Audit-Log
sudo tail -f /var/log/hydrahive/audit.jsonl | python3 -m json.tool

# nginx-Logs
sudo tail -f /var/log/nginx/error.log
```

---

## 28. Erweiterungs-Manager

Die Erweiterungsverwaltung ermöglicht das nachträgliche Installieren optionaler Komponenten direkt aus der Webkonsole — ohne SSH oder manuelle Befehle.

### Erreichbar unter

**Hub** → Tab **Extensions** (nur Admin).

### Verfügbare Erweiterungen

| Erweiterung | Beschreibung | Port |
|---|---|---|
| **SearXNG** | Datenschutzfreundliche Metasuchmaschine | 8888 |
| **Gitea** | Selbst-gehosteter Git-Server | 3000 |
| **Ollama** | Lokale LLM-Inferenz (GPU/CPU) | 11434 |
| **WhatsApp Bridge** | WhatsApp-Integration via whatsapp-web.js | 8767 |
| **Headscale** | Self-hosted Tailscale-Koordinator | 8089 |
| **code-server** | VS Code im Browser | 8080 |

### Installation

1. Gewünschte Erweiterung anklicken
2. **Installieren** drücken
3. Live-Log verfolgen — Installation läuft im Hintergrund
4. Bei Erfolg: Status wechselt auf **Aktiv**

### Deinstallation

**Deinstallieren** Button neben der installierten Erweiterung. Daten bleiben erhalten — nur Service und Binaries werden entfernt.

### Hinweis

Neue Erweiterungen werden automatisch mit jedem System-Update mitgeliefert. Bereits installierte Erweiterungen werden nicht überschrieben.

---

## 29. Schedules — Zeitgesteuerte Aufgaben

Mit Schedules können Agenten zu festen Zeiten oder in regelmäßigen Abständen automatisch aktiviert werden.

### Erreichbar unter

**Projekte** → Tab **Schedules**.

### Schedule anlegen

1. **+ Neuer Schedule**
2. Agent auswählen
3. Nachricht eingeben (was der Agent tun soll)
4. Zeitplan konfigurieren:
   - **Einmalig:** Datum + Uhrzeit
   - **Wiederkehrend:** Cron-Ausdruck oder Intervall (z.B. `*/30 * * * *` für alle 30 Minuten)
5. Speichern → Schedule läuft ab sofort

### Cron-Syntax

```
┌─────── Minute (0-59)
│ ┌───── Stunde (0-23)
│ │ ┌─── Tag des Monats (1-31)
│ │ │ ┌─ Monat (1-12)
│ │ │ │ └ Wochentag (0-6, 0=Sonntag)
│ │ │ │ │
* * * * *
```

Beispiele:
- `0 8 * * 1-5` — Montag–Freitag um 08:00
- `*/15 * * * *` — alle 15 Minuten
- `0 0 * * *` — täglich um Mitternacht

### Heartbeat vs. Schedule

| | Heartbeat | Schedule |
|---|---|---|
| Konfiguriert in | Agent-Einstellungen | Projekte → Tab Schedules |
| Trigger | Zeitintervall | Cron / Einmalig |
| Nachricht | fest im Agent | frei wählbar |
| Zweck | Monitoring, Watchdog | Aufgaben, Reports |

---

## 30. Butler — Visuelle Automatisierungsregeln

Butler ist ein Blueprint-Editor für Messenger-Automatisierung. Regeln werden visuell als verbundene Knoten definiert — ähnlich Unreal Engine Blueprints oder n8n.

### Erreichbar unter

**Blueprint** → Tab **Automation** (für globale Flows) oder **Mein Agent** → Tab **Butler** (für persönliche Flows).

### Konzept

Ein Butler-Flow besteht aus:
- **Trigger-Knoten** — wann die Regel greift (z.B. "WhatsApp-Nachricht empfangen")
- **Bedingungsknoten** — Filter die true/false ausgeben
- **Aktionsknoten** — was passiert wenn Bedingungen erfüllt sind

```
[Nachricht empfangen]
        ↓
[Zeitfenster 23:00–08:00]
    ja ↓            nein ↓
[Agent antwortet]  [Ignorieren]
```

### Knoten-Typen

**Trigger:**
| Knoten | Beschreibung |
|---|---|
| Nachricht empfangen | Eingehende Messenger-Nachricht — Kanal wählbar (Alle/WhatsApp/Telegram/Discord) |

**Bedingungen:**
| Knoten | Beschreibung |
|---|---|
| Zeitfenster | Prüft ob aktuelle Uhrzeit in einem Bereich liegt — Übernacht (23:00–08:00) unterstützt |
| Wochentag | Wochentage als Checkboxen auswählbar |
| Kontakt bekannt? | Prüft ob Absender in der Kontaktliste eingetragen ist |
| Text enthält | Stichwort-Filter auf den Nachrichteninhalt |

**Aktionen:**
| Knoten | Beschreibung |
|---|---|
| Agent antwortet | Leitet Nachricht an gewählten Agenten weiter |
| Agent mit Vorgabe | Wie "Agent antwortet" aber mit zusätzlicher Instruktion (z.B. "Antworte kurz auf Deutsch") |
| Feste Antwort | Sendet direkt einen vordefinierten Text — kein LLM, sofortige Antwort |
| In Warteschlange | Speichert Nachricht für spätere Bearbeitung |
| Ignorieren | Verwirft die Nachricht stillschweigend |
| Weiterleiten | Leitet an einen anderen Agenten weiter |

### Flow erstellen

1. **Butler** öffnen
2. **Neuen Flow anlegen** (Dropdown oben)
3. Knoten aus der Palette (links) auf die Canvas ziehen
4. Knoten mit der Maus verbinden — von Ausgabe-Punkt zu Eingabe-Punkt
5. Knoten anklicken → Properties-Panel (rechts) öffnet sich zur Bearbeitung
6. Flow-Name vergeben und **Speichern** drücken
7. **Aktiv** schalten

### Mehrere Flows

Flows laufen parallel. Bei eingehender Nachricht werden alle aktiven Flows geprüft. Der erste passende Flow wird ausgeführt.

### Hinweis zur Ausführung

Butler-Regeln greifen automatisch **vor** dem normalen Agenten-Processing. Wenn ein Flow "Ignorieren" ausgibt, kommt die Nachricht nie beim Agenten an.

---

## 31. Web-Suche (SearXNG)

SearXNG ist eine selbst-gehostete Metasuchmaschine die mehrere Suchmaschinen gleichzeitig abfragt — ohne Tracking, ohne API-Key.

### Voraussetzung

SearXNG muss über **Hub** → Tab **Extensions** installiert sein.

### Nutzung durch Agenten

Nach der Installation steht das Tool `web_search` automatisch allen Agenten zur Verfügung:

```
Agent: Suche nach aktuellen Nachrichten zu Python 3.13
→ web_search(query="Python 3.13 release notes")
→ Ergebnisse werden direkt in den Kontext injiziert
```

### Direktzugriff

Die SearXNG-Oberfläche ist unter **System** → Tab **Monitoring** oder direkt als eigene Seite erreichbar (eingebettet via iframe).

### Konfiguration

SearXNG läuft auf Port 8888 (intern). nginx proxied die Anfragen. Die Konfigurationsdatei liegt unter `/etc/searxng/settings.yml`.

---

## 32. Code Editor (VS Code im Browser)

code-server bringt VS Code vollständig in den Browser — inklusive Extensions, Terminal und Git-Integration.

### Voraussetzung

code-server muss über **Hub** → Tab **Extensions** installiert sein.

### Erreichbar unter

**Hub** → Tab **Extensions** → Code Editor öffnen (oder direkt über den eingebetteten Link).

### Funktionsumfang

- Vollständige VS Code Oberfläche im Browser
- Integriertes Terminal (SSH zum HydraHive-Server)
- Git-Integration (Gitea)
- Extension-Marketplace (eingeschränkt auf Open-VSX)
- Dark Mode, Themes, alle Keybindings

### Technisch

code-server läuft unter dem `hydrahive`-Benutzer auf Port 8080 (intern). nginx proxied unter `/code/`. Die Konfiguration liegt unter `/opt/hydrahive/.config/code-server/config.yaml`.

---

## 33. A2A Federation

HydraHive unterstützt das FastA2A-Protokoll für Agent-zu-Agent-Kommunikation zwischen verschiedenen HydraHive-Instanzen oder kompatiblen Systemen.

### Erreichbar unter

**Agenten** → Tab **Federation** (nur Admin).

### Funktionsweise

Eine HydraHive-Instanz kann als **A2A-Server** fungieren — andere Instanzen (Peers) können Agenten direkt ansprechen und Aufgaben delegieren.

### Agent-Karte (Agent Card)

Jeder Agent hat eine maschinenlesbare Beschreibungsdatei die seine Fähigkeiten, Skills und Kommunikationskanäle beschreibt. Diese wird automatisch generiert.

### Peer einrichten

1. **Agenten** → Tab **Federation** → **Peers** → **+ Peer hinzufügen**
2. URL der anderen HydraHive-Instanz eingeben
3. Agent-Karten des Peers werden automatisch geladen
4. Agenten können jetzt via `ask_agent` peer-übergreifend kommunizieren

### Sicherheit

Alle A2A-Verbindungen werden via Bearer-Token authentifiziert. Ohne gültiges Token keine Peer-Kommunikation.

---

## 34. Benachrichtigungen

Das Notification Center zeigt systemweite Ereignisse in Echtzeit — neue Nachrichten, Agenten-Fehler, Update-Status.

### Erreichbar über

Glocken-Icon oben rechts in der Konsole.

### Benachrichtigungstypen

| Typ | Beschreibung |
|---|---|
| **Info** | Allgemeine Systemmeldungen |
| **Erfolg** | Abgeschlossene Updates, erfolgreiche Aktionen |
| **Warnung** | Nicht-kritische Probleme (z.B. Heartbeat-Verzögerung) |
| **Fehler** | Kritische Fehler die sofortige Aufmerksamkeit erfordern |

### Technisch

Benachrichtigungen werden via SSE (Server-Sent Events) in Echtzeit gepusht — kein Polling. Verbindungsunterbrechungen werden automatisch wiederhergestellt.

---

## 35. Vaultwarden — Passwort-Manager

Vaultwarden ist ein selbst-gehosteter Passwort-Manager (kompatibel mit dem Bitwarden-Protokoll). Er ermöglicht das sichere Speichern und Abrufen von Passwörtern und Secrets — auch durch Agenten.

### Voraussetzung

Vaultwarden muss über **Hub** → Tab **Extensions** installiert sein. Die Installation baut Vaultwarden aus dem Rust-Source-Code — das dauert beim ersten Mal ca. 5–10 Minuten.

### Erreichbar unter

- **Web-Oberfläche:** `https://<server>/vault/`
- **Admin-Panel:** `https://<server>/vault/admin` (Token in `/etc/hydrahive/admin_credentials`)

### Erstzugang

Neue Konten können vom Admin per Einladung angelegt werden (`SIGNUPS_ALLOWED=false`). Im Admin-Panel unter `/vault/admin` → **Invite User** die E-Mail-Adresse eintragen. Der Nutzer erhält eine Einladung und kann sich ein Konto anlegen.

### Bitwarden-Client verbinden

Der Standard-Bitwarden-Client (Browser-Extension, Desktop, Mobil) verbindet sich mit der eigenen Instanz:

1. Bitwarden öffnen → **Server-URL ändern**
2. Custom URL: `https://<server>/vault`
3. Anmelden mit den zuvor angelegten Zugangsdaten

### Daten

- **Daten:** `/var/lib/vaultwarden/`
- **Konfiguration:** `/etc/hydrahive/vaultwarden.env`
- **Admin-Token:** In `/etc/hydrahive/admin_credentials` unter `vaultwarden_admin_token`

### Service-Verwaltung

```bash
sudo systemctl status vaultwarden
sudo journalctl -u vaultwarden -n 50
sudo systemctl restart vaultwarden
```

---

## 36. Migration — Installation übertragen

Überträgt eine vollständige HydraHive-Installation auf einen neuen Server (Agenten, Memory, Konfiguration, API-Tokens).

### Was wird übertragen

| Inhalt | Pfad |
|---|---|
| Agenten (Config, Memory, Skills, Soul) | `/agents/` |
| Benutzerkonten & Secrets | `/etc/hydrahive/users.json`, `jwt_secret`, `internal_secret` |
| LLM-Konfiguration | `/etc/hydrahive/llm_config.json`, `llm_env` |
| API-Tokens | `claude_oauth_token`, `openai_codex_token.json`, `github_token` |
| Platform-Tokens | `/etc/hydrahive/agent_tokens/` (Discord, WhatsApp) |
| MCP-Server-Config | `/etc/hydrahive/mcp_servers.json` |
| Schedules | `/etc/hydrahive/schedules.json` |
| System-Handbuch | `/etc/hydrahive/system_handbook.md` |
| Notification-History | `/var/log/hydrahive/notifications.db` |
| A-MEM ChromaDB *(optional)* | `/var/lib/hydrahive/amem/chromadb_data/` |

Nicht übertragen: TLS-Zertifikate (server-spezifisch, werden auf dem Ziel neu generiert).

### Option 1: Lokales Archiv (Export + Import)

```bash
# Auf Quell-Server: verschlüsseltes Archiv erstellen
sudo bash scripts/hydrahive-export.sh --output /tmp/migration.tar.gz.enc

# Optional: A-MEM-Daten mit einschließen (~300 MB)
sudo bash scripts/hydrahive-export.sh --output /tmp/migration.tar.gz.enc --include-amem

# Archiv auf Ziel-Server kopieren
scp /tmp/migration.tar.gz.enc user@newserver:/tmp/

# Auf Ziel-Server einspielen (HydraHive muss bereits installiert sein)
sudo bash scripts/hydrahive-import.sh --input /tmp/migration.tar.gz.enc
```

### Option 2: Direkter Transfer (kein lokales Archiv)

```bash
# Auf Quell-Server: direkt zu Ziel-Server streamen
sudo bash scripts/hydrahive-transfer.sh \
  --target user@192.168.1.100 \
  --key ~/.ssh/id_ed25519

# Mit A-MEM
sudo bash scripts/hydrahive-transfer.sh \
  --target root@newserver \
  --include-amem
```

Das Archiv wird AES-256-CBC-verschlüsselt und direkt über SSH gestreamt — es liegt zu keinem Zeitpunkt unverschlüsselt auf der Festplatte.

### Voraussetzungen Ziel-Server

1. Frische HydraHive-Installation (`install.sh` ausgeführt)
2. SSH-Zugang mit sudo/root-Rechten
3. `openssl` verfügbar (Standard auf Ubuntu)

### Nach der Migration prüfen

```bash
systemctl status hydrahive-core
journalctl -u hydrahive-core -n 30

# Agenten vorhanden?
ls /agents/

# Config übernommen?
cat /etc/hydrahive/users.json
```

---

## 36. HydraHub — Agenten & Plugins installieren

**HydraHub** ist der integrierte Paketmanager von HydraHive. Erreichbar unter **Hub** → Tab **HydraHub** (nur Admin).

Der HydraHub hat drei Tabs:

### Agenten

Kuratierte Agenten-Templates die mit einem Klick installiert werden können. Jeder Agent besteht aus einer `agent.yaml` (Konfiguration) und `soul.md` (Persönlichkeit/Anweisungen).

1. **Hub** → Tab **HydraHub** → Unter-Tab **Agenten**
2. Agent auswählen → Detail-Drawer öffnet sich
3. Optional: eigene Agent-ID vergeben
4. **Installieren** klicken
5. Agent ist sofort verfügbar — kein Neustart nötig

Installierte Agenten können über den **Deinstallieren**-Button im Detail-Drawer wieder entfernt werden.

### Plugins

HydraHive-Plugins die neue Tools für Agenten bereitstellen. Jedes Plugin besteht aus `plugin.yaml` (Manifest) und `plugin.py` (Code).

1. **Hub** → Tab **HydraHub** → Unter-Tab **Plugins**
2. Plugin auswählen → **Installieren**
3. Plugin wird nach `/plugins/` installiert und sofort geladen
4. Unter **Hub** → Tab **Plugins** dem gewünschten Agent zuweisen

### ClawhHub

Zugriff auf die [ClawhHub](https://clawhub.ai)-Registry — tausende Skills und Plugins aus der OpenClaw-Community.

**Skills Tab:** Skills suchen, inspizieren und in den Skills-Ordner eines Agenten installieren. ClawhHub-Skills werden automatisch ins HydraHive-Format konvertiert.

**Plugins Tab:** Browse-Ansicht für OpenClaw Code Plugins und Bundle Plugins (aktuell Read-Only — direkte Nutzung erfordert das HydraHive Plugin-System).

**ClawhHub API Token:**
Wird für die Suche und Installation benötigt. Erstelle einen Token unter [clawhub.ai/settings](https://clawhub.ai/settings) und trage ihn im ClawhHub-Tab ein.

---

## 37. Plugin-System

Das Plugin-System erweitert HydraHive um eigene Tools, Hooks und Services — ohne den Core-Code zu ändern.

### Plugin-Struktur

```
/plugins/mein-plugin/
  plugin.yaml          # Manifest (Pflicht)
  plugin.py            # Code (Pflicht)
```

**plugin.yaml:**
```yaml
id: mein-plugin
name: Mein Plugin
version: 1.0.0
description: Was das Plugin tut
author: Dein Name
type: tool              # tool | hook | service
permissions: []         # z.B. filesystem.read
auto_attach: false      # true = bei allen Agenten aktiv
```

**plugin.py:**
```python
def register(api):
    @api.tool(
        description="Beschreibung für das LLM",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchanfrage"}
            },
            "required": ["query"],
        },
    )
    async def mein_tool(query: str, **ctx) -> str:
        return f"Ergebnis für: {query}"

    @api.hook("message.after")
    async def nach_nachricht(project_id, response, **_):
        # Wird nach jeder Agent-Antwort aufgerufen
        pass
```

### Plugin-Typen

| Typ | Beschreibung |
|-----|-------------|
| **tool** | Registriert neue Tools die der Agent aufrufen kann |
| **hook** | Reagiert auf Events (message.before/after, tool.before/after) |
| **service** | Kombiniert Tools + Hooks + Background-Tasks |

### Plugin-Verwaltung

**Hub** → Tab **Plugins** → Übersicht aller installierten Plugins.

- **Aktivieren/Deaktivieren** — per Power-Button auf der Plugin-Karte
- **Agent-Zuweisung** — Plugin-Detail öffnen → Agenten per Checkbox zuweisen
- **Neu laden** — nach Code-Änderungen das Plugin per Reload-Button neu laden

### Eigene Plugins entwickeln

1. Verzeichnis unter `/plugins/mein-plugin/` anlegen
2. `plugin.yaml` + `plugin.py` erstellen (siehe Struktur oben)
3. `register(api)` Funktion implementieren
4. Core-Service neustarten oder unter Plugins → **Alle neu laden**
5. Plugin einem Agent zuweisen

### Verfügbare Hook-Events

| Event | Argumente | Wann |
|-------|-----------|------|
| `message.before` | project_id, content, sender | Vor der Nachrichtenverarbeitung |
| `message.after` | project_id, content, response | Nach der Agent-Antwort |
| `tool.before` | project_id, tool_name, tool_input | Vor Tool-Ausführung |
| `tool.after` | project_id, tool_name, result | Nach Tool-Ausführung |

### Tool-IDs

Plugin-Tools bekommen automatisch das Präfix `plg_{plugin_id}_{tool_name}`. Beispiel: Plugin `csv-tools` mit Tool `analyze` → Tool-ID: `plg_csv-tools_analyze`.

---

## 38. ClawhHub — Externe Skills & Plugins

[ClawhHub](https://clawhub.ai) ist die öffentliche Registry für OpenClaw/Claude-Code Skills und Plugins.

### ClawhHub-Token einrichten

1. Account auf [clawhub.ai](https://clawhub.ai) erstellen
2. Unter Settings → API Token erstellen
3. **Hub** → Tab **ClawhHub** → Token eintragen

### Skills installieren

1. **Hub** → Tab **ClawhHub** → Unter-Tab **Skills** → Suche eingeben (z.B. "python", "security")
2. Skill auswählen → Detail-Drawer öffnet sich
3. **Ziel-Agent** auswählen (in welchen Agent der Skill installiert wird)
4. **In Agent installieren** klicken
5. Skill wird als `.md` Datei im Skills-Ordner des Agents gespeichert

ClawhHub-Skills werden automatisch vom ClawhHub-Format ins HydraHive-Format konvertiert (Frontmatter, Triggers, Scope).

### Plugins browsen

Unter dem **Plugins** Unter-Tab können OpenClaw Code Plugins und Bundle Plugins durchsucht werden. Diese sind aktuell Read-Only — für die direkte Nutzung in HydraHive ist das Plugin-System (Kapitel 37) vorgesehen.

---

## 39. Tailscale Federation

Tailscale ermöglicht die sichere Vernetzung mehrerer HydraHive-Instanzen über das Internet — verschlüsselt, ohne Port-Forwarding, ohne SSL-Zertifikate.

### Voraussetzungen

- [Tailscale](https://tailscale.com) Account (kostenloser Plan reicht)
- Tailscale auf jedem Server installiert (`curl -fsSL https://tailscale.com/install.sh | sh`)

### Einrichtung (alles auf der Federation-Seite)

#### Schritt 1: API Key

1. [Tailscale Admin](https://login.tailscale.com/admin/settings/keys) → **Generate access token**
2. **Agenten** → Tab **Federation** → Tailscale-Sektion → API Key eintragen → **Speichern**

#### Schritt 2: Server verbinden

1. **Einladen** klicken → Auth Key wird generiert (24h gültig)
2. Auth Key kopieren
3. Auf diesem oder einem anderen Server: Auth Key bei **"Server mit Tailnet verbinden"** eintragen → **Verbinden**

#### Schritt 3: Andere Server einladen

1. Auth Key an den Admin des anderen Servers schicken
2. Der trägt den Key auf seiner Federation-Seite ein → **Verbinden**
3. Fertig — beide Server sind im selben Tailnet

#### Schritt 4: HydraHive-Instanzen finden und peeren

1. **HydraHive suchen** klicken → scannt alle Tailscale-IPs nach HydraHive
2. Gefundene Instanzen erscheinen mit **"Als Peer hinzufügen"** Button
3. Klick → Instanz wird automatisch als A2A-Peer registriert

### Tailnet verwalten

- **Tailnet Devices** — alle Geräte im Tailnet anzeigen
- **Löschen** (Trash-Icon) — Gerät aus dem Tailnet + zugehörigen A2A-Peer entfernen
- **API Key ändern** — für Account-Wechsel
- **Trennen** — diesen Server vom Tailnet trennen

### Wichtig

- Der **API Access Token** ist für die Verwaltung des Tailnets — nur für Admins
- **Auth Keys** sind Einladungen — einmalig, zeitbegrenzt, zum Beitreten
- Auf jedem Server muss der gleiche API Access Token eingetragen werden

---

## 40. HydraBrain — 3D-Agentengraph

**HydraBrain** zeigt eine interaktive 3D-Visualisierung aller Agenten, Tools, Memories und ihrer Verbindungen.

### Aufrufen

**HydraBrain** in der Sidebar (nur Admin) — eigener Menüpunkt. Benötigt WebGL (Hardware-Beschleunigung im Browser).

### Ansicht

- **Blaue Knoten** — Boss-Agenten
- **Grüne Knoten** — Worker/Specialist-Agenten
- **Kleine Knoten** — Tools, Memories, Skills
- **Verbindungslinien** — zeigen welcher Agent welche Tools/Memories nutzt

### Aktivitäts-Anzeige

Wenn ein Agent arbeitet, ändert sich die Knotenfarbe:
- **Cyan** — Agent denkt
- **Grün** — Agent liest Daten
- **Orange** — Agent schreibt Daten

### Federation

Klick auf den **Federation-Button** (Radar-Icon) → Remote-Peers werden gescannt und als separate Cluster im Graph angezeigt:
- **Pink** — Peer-Gateway
- **Gelb** — Remote-Agenten

### Steuerung

- **Scrollen** — Zoom
- **Ziehen** — Drehen
- **Klick auf Knoten** — Details anzeigen
- **Labels** — Ein/Aus-Button in der Toolbar
- **Neu laden** — Refresh-Button aktualisiert die Daten
