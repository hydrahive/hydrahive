# OctopOS Handbuch

OctopOS ist ein selbst-gehosteter KI-Agent-Server. Er läuft auf einer eigenen VM oder einem Server, verwaltet KI-Agenten, Projekte und Dateisysteme — ohne externe Cloud-Abhängigkeit.

---

## Inhaltsverzeichnis

1. [Installation](#1-installation)
2. [Erster Login](#2-erster-login)
3. [Die Webkonsole](#3-die-webkonsole)
4. [Agenten anlegen](#4-agenten-anlegen)
5. [Projekte anlegen](#5-projekte-anlegen)
6. [Chat verwenden](#6-chat-verwenden)
7. [LLM-Konfiguration](#7-llm-konfiguration)
8. [Skills — Agenten-Wissen erweitern](#8-skills--agenten-wissen-erweitern)
9. [Webhooks](#9-webhooks)
10. [Benutzer und Rollen](#10-benutzer-und-rollen)
11. [Backup & Restore](#11-backup--restore)
12. [Audit-Log](#12-audit-log)
13. [Matrix-Integration](#13-matrix-integration)
14. [GPU-Monitoring](#14-gpu-monitoring)
15. [Troubleshooting](#15-troubleshooting)

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
| **Agenten** | Agenten anlegen, bearbeiten, Logs und Skills verwalten | alle (Schreiben: admin) |
| **Projekte** | Projekte anlegen, Chat öffnen, Webhooks konfigurieren | alle (Schreiben: admin) |
| **System** | Service-Status, Laufzeit-Informationen, GPU-Auslastung | alle |
| **Tools** | Verfügbare Tools anzeigen | alle |
| **LLM-Config** | Sprachmodell konfigurieren (Ollama, Claude, OpenAI) | admin |
| **Benutzer** | Benutzer anlegen und verwalten | admin |
| **Backup** | Backups erstellen, herunterladen und wiederherstellen | admin |
| **Audit-Log** | Alle sicherheitsrelevanten Aktionen nachverfolgen | admin |

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
└── skills/          # Skill-Dateien (optional)
    ├── steuerrecht.md
    └── buchhaltung.md
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

tools:
  - file_read
  - file_write

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

Der Status aller konfigurierten Tasks wird in der **Agenten**-Ansicht als Timer-Badge angezeigt (Anzahl aktiver Tasks).

> **Hinweis:** Heartbeat Tasks laufen nur auf `boss`-Agenten mit zugeordnetem Projekt. Verpasste Ausführungen (Server war aus) werden nicht nachgeholt.

### Verfügbare Tools

| Tool | Beschreibung |
|---|---|
| `file_read` | Datei im Projektverzeichnis lesen |
| `file_write` | Datei im Projektverzeichnis schreiben |
| `web_search` | Websuche durchführen |
| `http_request` | HTTP-Anfragen an externe APIs |
| `dispatch_task` | Andere Agenten beauftragen (nur boss) |
| `spawn_agent` | Kurzlebigen Worker-Agenten erstellen |
| `write_handoff` | Aufgabe/Kontext an anderen Agenten übergeben (AgentLink) |
| `read_handoff` | Übergabe-Auftrag entgegennehmen (AgentLink) |

> Alle Filesystem-Operationen sind auf `/projects/<projekt-id>/` beschränkt. Zugriff darüber hinaus wird verweigert.

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

---


#### Task-Agenten konfigurieren

```yaml
# /projects/buchhaltung/project.yaml
task_agents:
  ttl: 600        # Sekunden bis Task-Agent gestoppt wird (default: 300)
  max_parallel: 5 # max gleichzeitige Task-Agenten (default: 10)
```

## 6. Chat verwenden

1. **Projekte** → **Chat öffnen** beim gewünschten Projekt
2. Nachricht eingeben und `Enter` drücken (oder `Shift+Enter` für Zeilenumbruch)
3. Die Antwort erscheint Token-für-Token (Streaming)

### Swarm-Ansicht

Das **Netzwerk-Symbol** (oben rechts im Chat) schaltet die Swarm-Ansicht um. Bei aktivierter Ansicht wird unter jeder Antwort angezeigt welche Worker-Agenten beteiligt waren.

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
- `llama3.2:3b` — schnell, wenig RAM (4 GB)
- `llama3.1:8b` — ausgewogen (8 GB RAM)
- `mistral-nemo:12b` — beste Qualität lokal (12 GB RAM)

### Claude Max (OAuth)

Für Claude-Modelle via Claude Max Abonnement:

1. **LLM-Config** → **Claude Max**
2. OAuth-Token einfügen (`sk-ant-oat01-...`)
3. Speichern

Der Token-Status wird mit Ablauf-Datum angezeigt. oat01-Tokens gelten ~30 Tage.

### OpenAI / andere Anbieter

1. **LLM-Config** → **Anbieter konfigurieren**
2. API-Key und Modell eintragen

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

...
```

---

## 9. Webhooks

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

## 10. Benutzer und Rollen

Unter **Benutzer** (Admin only) werden weitere Accounts verwaltet. OctopOS kennt zwei Rollen:

| Rolle | Rechte |
|---|---|
| **admin** | Vollzugriff: Agenten/Projekte anlegen und löschen, LLM-Config, Backup, Benutzerverwaltung |
| **user** | Lesezugriff + Chat: Agenten und Projekte sehen und nutzen, keine Konfiguration |

- **Neuer Benutzer:** Benutzername, Passwort und Rolle (`admin` oder `user`) wählen
- **Passwort ändern:** Stift-Symbol
- **Löschen:** Papierkorb-Symbol (eigener Account kann nicht gelöscht werden)

---

## 11. Backup & Restore

Unter **Backup** (Admin only) können vollständige System-Backups erstellt und verwaltet werden.

### Was wird gesichert?

Ein Backup enthält als `tar.gz`:
- `/etc/octopos/` — alle Konfigurationsdateien (JWT-Secret, Users, LLM-Config, Admin-Credentials)
- `/agents/` — alle Agenten-Definitionen und Skills
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

Für regelmäßige automatische Backups auf Lilith (von extern):

```bash
# Skript liegt in scripts/octopos-backup.sh
./scripts/octopos-backup.sh
```

---

## 12. Audit-Log

Das Audit-Log protokolliert alle sicherheitsrelevanten Aktionen:

- Logins (erfolgreich und fehlgeschlagen)
- Benutzer anlegen/löschen
- Agenten anlegen/löschen
- Projekte anlegen/provisionieren
- Skills anlegen/ändern/löschen
- Webhooks anlegen/löschen/auslösen
- LLM-Token setzen

Gespeichert in `/var/log/octopos/audit.jsonl` — append-only, ein JSON-Objekt pro Zeile.

**Filter:** Nach Benutzer, Aktion, Projekt und Anzahl Einträge filterbar.

---

## 13. Matrix-Integration

OctopOS kann Nachrichten über Matrix (Element) empfangen und beantworten.

### Element-Client verbinden

Matrix-Server läuft auf Port 8008 (nginx-Proxy → conduwuit auf 6167):

- **Homeserver:** `https://<ip>:8008` oder `http://<ip>:8008`
- **Benutzer:** `@admin:<hostname>`
- **Passwort:** aus `/etc/octopos/admin_credentials`

### Agenten-Bot im Room

Wenn ein Projekt einen Matrix-Room hat, lauscht der Boss-Agent dort automatisch. Nachrichten im Room werden wie Chat-Nachrichten behandelt.

---

## 14. GPU-Monitoring

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

## 15. Troubleshooting

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

### Matrix-Bot antwortet nicht

```bash
sudo journalctl -u octopos-core -n 50 | grep -i matrix
```

Der Matrix-Watchdog startet den Bot automatisch neu. Bei dauerhaftem Fehler: `sudo systemctl restart octopos-core`

### OAuth-Token abgelaufen

**LLM-Config** → **Claude Max** → Token-Status prüfen. Neuen Token über `claude setup` holen und eintragen.

### Login funktioniert nicht (429 Too Many Requests)

Rate-Limiting: max. 10 Versuche pro Minute. Nach 60 Sekunden warten.

### Logs direkt auf Server

```bash
# Core-Logs
sudo journalctl -u octopos-core -f

# Audit-Log
sudo tail -f /var/log/octopos/audit.jsonl | python3 -m json.tool

# nginx-Logs
sudo tail -f /var/log/nginx/error.log
```
