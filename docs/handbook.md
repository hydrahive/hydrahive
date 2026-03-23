# OctopOS Handbuch

OctopOS ist ein selbst-gehosteter KI-Agent-Server. Er läuft auf einer eigenen VM oder einem Server, verwaltet KI-Agenten, Projekte und Dateisysteme — ohne externe Cloud-Abhängigkeit.

---

## Aktueller Betriebsmodus

OctopOS ist inzwischen mehr als nur Agenten- und Projektverwaltung. Der aktuelle Arbeitsstand ist:

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
14. [Webhooks](#14-webhooks)
15. [Benutzer und Rollen](#15-benutzer-und-rollen)
16. [Backup & Restore](#16-backup--restore)
17. [Audit-Log](#17-audit-log)
18. [Matrix-Integration](#18-matrix-integration)
19. [GPU-Monitoring](#19-gpu-monitoring)
20. [System-Update](#20-system-update)
21. [Troubleshooting](#21-troubleshooting)

---

## 1. Installation

### Voraussetzungen

- Ubuntu 22.04 oder 24.04 LTS (amd64)
- Mindestens 4 GB RAM, 20 GB Disk
- Root-Zugriff
- Internetzugang für Download

### Installer ausführen

```bash
git clone https://github.com/tilleulenspiegel/octopos.git
cd octopos
sudo bash installer/install.sh
```

Der Installer läuft vollautomatisch und richtet ein:
- conduwuit (Matrix-Homeserver)
- OctopOS Core (Python-Backend)
- OctopOS Console (React-Frontend via nginx)
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
- **Passwort:** steht in `/etc/octopos/admin_credentials` auf dem Server

```bash
sudo cat /etc/octopos/admin_credentials
# matrix_admin_password=<generiertes-passwort>
```

Das angezeigte Passwort ist der initiale Login. Nach dem ersten Login empfiehlt sich das Anlegen eines persönlichen Admin-Accounts unter **Benutzer** → **Neuer Benutzer**.

> Weitere Benutzer können als `admin` (vollen Zugriff) oder `user` (nur Chat und Lesen) angelegt werden.

---

## 3. Die Webkonsole

Die Konsole ist unter `https://<IP>` erreichbar. Alle Bereiche sind über die linke Sidebar erreichbar:

| Bereich | Funktion | Zugriff |
|---|---|---|
| **Dashboard** | Überblick über Agenten, Projekte, System-Status | alle |
| **Mein Agent** | Persönlicher Agent: Chat, Einstellungen, Skills, WKS | alle |
| **Agenten** | Agenten anlegen, bearbeiten, Logs und Skills verwalten | alle (Schreiben: admin) |
| **Projekte** | Projekte anlegen, Chat öffnen, Webhooks konfigurieren | alle (Schreiben: admin) |
| **System** | Service-Status, Laufzeit-Informationen, GPU-Auslastung | alle |
| **Tools** | Verfügbare Tools anzeigen | alle |
| **LLM-Config** | Sprachmodell konfigurieren (Ollama, Claude, OpenAI) | admin |
| **MCP-Server** | Externe Tool-Server konfigurieren | admin |
| **Benutzer** | Benutzer anlegen und verwalten | admin |
| **Backup** | Backups erstellen, herunterladen und wiederherstellen | admin |
| **Audit-Log** | Alle sicherheitsrelevanten Aktionen nachverfolgen | admin |
| **Update** | Sidebar-Button: System auf neuesten Stand bringen | admin |

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

1. **Agenten** → **Neuer Agent**
2. Felder ausfüllen:
   - **Agent-ID:** Eindeutiger Bezeichner (z.B. `steuer-agent`)
   - **Anzeigename:** Wird im Chat angezeigt (z.B. `Steuerbert`)
   - **Typ:** `boss`, `specialist` oder `worker`
   - **LLM-Modell:** Verfügbares Modell (z.B. `llama3.1:8b`)
   - **Tools:** Welche Fähigkeiten der Agent hat (Checkboxen)
   - **Soul:** Freier Markdown-Text der die Persönlichkeit beschreibt
3. **Agent anlegen** klicken

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
  - qmd                  # MCP-Server-IDs aus /etc/octopos/mcp_servers.json

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
| OctopOS-Sabotage | `systemctl stop/disable/mask/kill octopos`, `killall uvicorn` |
| Geschützte Pfade | Redirects (`>`) nach `/etc/`, `/bin/`, `/usr/`, `/lib`, `/boot/`, `/dev/`, `/sys/`, `/proc/`, `/opt/octopos/` |
| Rechteänderungen | `chmod`/`chown` auf `/opt/`, `/etc/`, `/bin/` |
| Git in Systempfaden | `git clone`/`reset --hard` nach/in `/opt/octopos/` |
| Shell-Escapes | `$()` Command Substitution, Backticks `` ` ``, `eval`, Subshell über `bash -c` |
| Fork-Bomben | `:() { …` |

Geblockte Befehle werden mit einer Fehlermeldung abgelehnt und im Log protokolliert.

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

OctopOS richtet automatisch ein:
- Linux-User `proj_<id>` mit eigenem Home-Verzeichnis
- Verzeichnis `/projects/<id>/` für Agenten-Dateien
- Matrix-Room (wenn Matrix konfiguriert)
- Samba-Freigabe (wenn aktiviert)

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

Standardmäßig läuft Ollama lokal auf Port 11434. Modelle werden über **LLM-Config** → **Ollama** verwaltet.

Empfohlene Modelle:
- `llama3.2:3b` — schnell, wenig VRAM (4 GB)
- `llama3.1:8b` — ausgewogen (8 GB VRAM)
- `qwen2.5:7b` — gute Tool-Nutzung (8 GB VRAM)
- `mistral-nemo:12b` — beste Qualität lokal (12 GB VRAM)

### Claude Max (OAuth)

Für Claude-Modelle via Claude Max Abonnement:

1. **LLM-Config** → **Claude Max**
2. OAuth-Token einfügen (`sk-ant-oat01-...`)
3. Speichern

Der Token-Status wird mit Ablauf-Datum angezeigt. oat01-Tokens gelten ~30 Tage.

### OpenAI / andere Anbieter

1. **LLM-Config** → **Anbieter konfigurieren**
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

1. **Agenten** → Agent auswählen → **Skills** (Buch-Icon)
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

---

## 9. Persönlicher Agent (Mein Agent)

Jeder User bekommt automatisch einen persönlichen Agenten — `personal_<username>`. Dieser Agent läuft unabhängig von Projekten und ist direkt über **Mein Agent** in der Sidebar erreichbar.

### Chat

Der Chat unter **Mein Agent → Chat** funktioniert wie der Projekt-Chat: Streaming, Chat-History, Slash Commands. Der Agent merkt sich den Gesprächsverlauf sessionübergreifend.

### Einstellungen

Unter **Mein Agent → Einstellungen** kann jeder User seinen Agenten selbst konfigurieren:

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

### Skills-Tab

Eigene Skills anlegen — genau wie bei regulären Agenten.

### MCP-Tab

Externe MCP-Server zuweisen. Die verfügbaren Server werden vom Admin unter **MCP-Server** konfiguriert.

### WKS-Tab

Workstation-Zugang konfigurieren — siehe [Kapitel 11](#11-wks-zugang-workstation).

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

Agenten sollten für gezielte Suche statt vollständiger Injektion QMD nutzen (spart Tokens).

### QMD Memory Search

Wenn der Agent `qmd` als MCP-Server konfiguriert hat, kann er gezielt in den Memory-Dateien suchen:

```
# Im Chat mit dem Agenten:
"Was weißt du über das AgentLink-Projekt?"
→ Agent ruft qmd_query("AgentLink Projekt") auf
→ Liefert relevante Chunks statt alle Dateien zu laden
```

Das spart erheblich Tokens gegenüber der vollständigen Memory-Injektion.

---

## 11. WKS-Zugang (Workstation)

Persönliche Agenten können via SSH auf die eigene Workstation des Users zugreifen — Dateien lesen/schreiben und Befehle ausführen.

### Einrichten

1. **Mein Agent → WKS-Tab** öffnen
2. **IP-Adresse** der Workstation eintragen (z.B. `192.168.1.197`)
3. **SSH-Benutzer** eintragen (z.B. `till`)
4. **SSH Private Key** (PEM) einfügen
5. **Ollama-Port** (Standard: `11434`)
6. **Speichern**

**SSH-Key auf der Workstation einrichten:**

```bash
# Auf der Workstation:
# Den Public Key in authorized_keys eintragen
echo "ssh-ed25519 AAAA... octopos-wks@octopos" >> ~/.ssh/authorized_keys
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

Diese Tools müssen in **Mein Agent → Einstellungen → Tools** aktiviert werden.

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

1. **LLM-Config** → **Gitea** (oder direkt in `/etc/octopos/gitea_config.json`)
2. Gitea-URL, Token, Organisation eintragen

```json
{
  "url": "http://YOUR-VM-IP:3001",
  "token": "dein-gitea-token",
  "org": "octopos"
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

MCP (Model Context Protocol) ermöglicht Agenten den Zugriff auf externe Tool-Server. OctopOS unterstützt streamableHttp-Transport.

### MCP-Server konfigurieren (Admin)

1. **MCP-Server** in der Sidebar (Admin only)
2. **Neuer MCP-Server**
3. Felder ausfüllen: ID, Name, Transport (`streamableHttp`), URL

Oder direkt in `/etc/octopos/mcp_servers.json`:

```json
[
  {
    "id": "qmd",
    "name": "QMD Memory Search",
    "transport": "streamableHttp",
    "url": "http://127.0.0.1:8181/mcp"
  }
]
```

### Agent einem MCP-Server zuweisen

In `agent.yaml`:
```yaml
mcp_servers:
  - qmd
```

Oder in **Mein Agent → MCP-Tab** → Server aktivieren.

### QMD Memory Search MCP

QMD ist ein semantischer Suchserver für die Memory-Dateien der Agenten. Er läuft als systemd-Service auf Port 8181:

```bash
sudo systemctl status qmd-mcp
# Active: active (running) — QMD MCP server listening on http://localhost:8181/mcp
```

Agenten mit `qmd` in `mcp_servers` können gezielt in ihren Memory-Dateien suchen statt alle Dateien beim Start zu laden. Dies spart deutlich Tokens.

---

## 14. Webhooks

Webhooks ermöglichen externe Systeme OctopOS zu triggern — z.B. bei einem Git-Push automatisch einen Agenten starten.

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

Wenn ein Secret gesetzt ist, wird jeder ausgehende Webhook-Request mit `X-OctopOS-Signature: sha256=<hmac>` signiert. Prüfung auf Empfänger-Seite:

```python
import hmac, hashlib
expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
assert f"sha256={expected}" == request.headers["X-OctopOS-Signature"]
```

---

## 15. Benutzer und Rollen

Unter **Benutzer** (Admin only) werden weitere Accounts verwaltet. OctopOS kennt zwei Rollen:

| Rolle | Rechte |
|---|---|
| **admin** | Vollzugriff: Agenten/Projekte anlegen und löschen, LLM-Config, MCP-Server, Backup, Benutzerverwaltung, System-Update |
| **user** | Lesezugriff + Chat: Agenten und Projekte sehen und nutzen, eigenen Agenten konfigurieren, keine Systemkonfiguration |

- **Neuer Benutzer:** Benutzername, Passwort und Rolle (`admin` oder `user`) wählen
- **Passwort ändern:** Stift-Symbol
- **Löschen:** Papierkorb-Symbol (eigener Account kann nicht gelöscht werden)

Beim Anlegen eines Users wird automatisch ein persönlicher Agent `personal_<username>` erstellt.

---

## 16. Backup & Restore

Unter **Backup** (Admin only) können vollständige System-Backups erstellt und verwaltet werden.

### Was wird gesichert?

Ein Backup enthält als `tar.gz`:
- `/etc/octopos/` — alle Konfigurationsdateien (JWT-Secret, Users, LLM-Config, Admin-Credentials, WKS-Keys)
- `/agents/` — alle Agenten-Definitionen, Skills und Memory-Dateien
- `/projects/` — Projekt-Konfigurationen und Agenten-Dateien

**Nicht enthalten:** Betriebssystem, venv, Console-Build (werden bei Bedarf neu installiert).

### Backup erstellen

1. **Backup** → **Backup erstellen**
2. Das Backup erscheint sofort in der Liste mit Zeitstempel und Dateigröße

Backups liegen auf dem Server unter `/opt/octopos/backups/`.

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
# Skript liegt in scripts/octopos-backup.sh
./scripts/octopos-backup.sh
```

---

## 17. Audit-Log

Das Audit-Log protokolliert alle sicherheitsrelevanten Aktionen:

- Logins (erfolgreich und fehlgeschlagen)
- Benutzer anlegen/löschen
- Agenten anlegen/löschen
- Projekte anlegen/provisionieren
- Skills anlegen/ändern/löschen
- Webhooks anlegen/löschen/auslösen
- LLM-Token setzen
- WKS-Konfiguration ändern

Gespeichert in `/var/log/octopos/audit.jsonl` — append-only, ein JSON-Objekt pro Zeile.

**Filter:** Nach Benutzer, Aktion, Projekt und Anzahl Einträge filterbar.

---

## 18. Matrix-Integration

OctopOS kann Nachrichten über Matrix (Element) empfangen und beantworten.

### Element-Client verbinden

Matrix-Server läuft auf Port 8008 (nginx-Proxy → conduwuit auf 6167):

- **Homeserver:** `https://<ip>:8008` oder `http://<ip>:8008`
- **Benutzer:** `@admin:<hostname>`
- **Passwort:** aus `/etc/octopos/admin_credentials`

### Agenten-Bot im Room

Wenn ein Projekt einen Matrix-Room hat, lauscht der Boss-Agent dort automatisch. Nachrichten im Room werden wie Chat-Nachrichten behandelt.

---

## 19. GPU-Monitoring

Wenn eine NVIDIA-Grafikkarte im Server verfügbar ist, zeigt die **System**-Seite eine GPU-Auslastungsanzeige.

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

## 20. System-Update

OctopOS kann sich selbst aktualisieren — entweder manuell über die Console oder automatisch per Webhook.

### Update über die Console

Admins sehen in der Sidebar einen **Update**-Button mit dem aktuellen Commit-Hash. Ein Klick startet den Update-Prozess:

1. Aktuellen Stand von Gitea (primär) oder GitHub (Fallback) klonen
2. Core-Dateien aktualisieren (`rsync`)
3. Python-Dependencies neu installieren (`pip install -e .`)
4. Console bauen (`npm ci && npm run build`)
5. Console deployen
6. `octopos-core` neustarten
7. QMD Memory re-indexieren

Der Update läuft in einem isolierten systemd-Transient-Unit — der Core kann sich selbst neustarten ohne den Prozess zu unterbrechen.

**Status:** Der Button zeigt während des Updates einen Ladeindikator und danach den neuen Commit-Hash.

### Update per Kommandozeile

```bash
sudo bash /opt/octopos/update.sh
```

---

## 21. Troubleshooting

### Konsole nicht erreichbar

```bash
sudo systemctl status nginx
sudo nginx -t
sudo journalctl -u nginx -n 50
```

### Core reagiert nicht

```bash
sudo systemctl status octopos-core
sudo journalctl -u octopos-core -n 100 --no-pager
```

### Agent antwortet nicht

1. **Agenten** → Agent auswählen → Logs-Icon prüfen
2. Heartbeat-Status in der Agent-Liste prüfen (orange = Warnung)
3. LLM-Verbindung prüfen: **LLM-Config** → Status

### WKS-Verbindung schlägt fehl

```bash
# SSH manuell testen (vom OctopOS-Server):
sudo ssh -i /etc/octopos/wks_keys/<username> <ssh_user>@<wks_ip> hostname

# Häufige Ursachen:
# - SSH-Daemon auf WKS nicht aktiv: sudo systemctl enable --now ssh
# - Public Key nicht in authorized_keys
# - Firewall blockiert Port 22
```

### Matrix-Bot antwortet nicht

```bash
sudo journalctl -u octopos-core -n 50 | grep -i matrix
```

Der Matrix-Watchdog startet den Bot automatisch neu. Bei dauerhaftem Fehler: `sudo systemctl restart octopos-core`

### OAuth-Token abgelaufen

**LLM-Config** → **Claude Max** → Token-Status prüfen. Neuen Token über `claude setup` holen und eintragen.

### Login funktioniert nicht (429 Too Many Requests)

Rate-Limiting: max. 10 Versuche pro Minute. Nach 60 Sekunden warten.

### QMD-Service nicht erreichbar

```bash
sudo systemctl status qmd-mcp
sudo journalctl -u qmd-mcp -n 30
# Bei Bedarf neu starten:
sudo systemctl restart qmd-mcp
```

### Logs direkt auf Server

```bash
# Core-Logs
sudo journalctl -u octopos-core -f

# Update-Log
sudo tail -f /var/log/octopos-update.log

# Audit-Log
sudo tail -f /var/log/octopos/audit.jsonl | python3 -m json.tool

# nginx-Logs
sudo tail -f /var/log/nginx/error.log
```
