# OctopOS Handbuch

OctopOS ist ein selbst-gehosteter KI-Agent-Server. Er läuft auf einer eigenen VM oder einem Server, verwaltet KI-Agenten, Projekte und Dateisysteme — ohne externe Cloud-Abhängigkeit.

---

## Inhaltsverzeichnis

1. [Installation](#1-installation)
2. [Erster Start — Setup-Wizard](#2-erster-start--setup-wizard)
3. [Die Webkonsole](#3-die-webkonsole)
4. [Agenten anlegen](#4-agenten-anlegen)
5. [Projekte anlegen](#5-projekte-anlegen)
6. [Chat verwenden](#6-chat-verwenden)
7. [LLM-Konfiguration](#7-llm-konfiguration)
8. [Skills — Agenten-Wissen erweitern](#8-skills--agenten-wissen-erweitern)
9. [Webhooks](#9-webhooks)
10. [Benutzer verwalten](#10-benutzer-verwalten)
11. [Audit-Log](#11-audit-log)
12. [Matrix-Integration](#12-matrix-integration)
13. [Troubleshooting](#13-troubleshooting)

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

## 2. Erster Start — Setup-Wizard

Beim ersten Aufruf der Konsole erscheint der Setup-Wizard. Hier wird der Admin-Account angelegt.

- **Benutzername:** Nur `a-z`, `0-9`, `_`, `.`, `-` erlaubt
- **Passwort:** Mindestens 8 Zeichen

Nach dem Anlegen erscheint die Login-Seite. Der Account funktioniert direkt.

> Der Setup-Wizard erscheint nur solange noch kein Benutzer existiert. Danach ist `/setup` nicht mehr zugänglich.

---

## 3. Die Webkonsole

Die Konsole ist unter `https://<IP>` erreichbar. Alle Bereiche sind über die linke Sidebar erreichbar:

| Bereich | Funktion |
|---|---|
| **Dashboard** | Überblick über Agenten, Projekte, System-Status |
| **Agenten** | Agenten anlegen, bearbeiten, Logs und Skills verwalten |
| **Projekte** | Projekte anlegen, Chat öffnen, Webhooks konfigurieren |
| **System** | Service-Status, Laufzeit-Informationen |
| **Tools** | Verfügbare Tools anzeigen |
| **LLM-Config** | Sprachmodell konfigurieren (Ollama, Claude, OpenAI) |
| **Benutzer** | Weitere Benutzer anlegen und verwalten |
| **Audit-Log** | Alle sicherheitsrelevanten Aktionen nachverfolgen |

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
```

### Verfügbare Tools

| Tool | Beschreibung |
|---|---|
| `file_read` | Datei im Projektverzeichnis lesen |
| `file_write` | Datei im Projektverzeichnis schreiben |
| `web_search` | Websuche durchführen |
| `http_request` | HTTP-Anfragen an externe APIs |
| `dispatch_task` | Andere Agenten beauftragen (nur boss) |
| `spawn_agent` | Kurzlebigen Worker-Agenten erstellen |

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

## 6. Chat verwenden

1. **Projekte** → **Chat öffnen** beim gewünschten Projekt
2. Nachricht eingeben und `Enter` drücken (oder `Shift+Enter` für Zeilenumbruch)
3. Die Antwort erscheint Token-für-Token (Streaming)

### Swarm-Ansicht

Das **Netzwerk-Symbol** (oben rechts im Chat) schaltet die Swarm-Ansicht um. Bei aktivierter Ansicht wird unter jeder Antwort angezeigt welche Worker-Agenten beteiligt waren.

### Chat-History

Die Nachrichten einer Session bleiben erhalten und werden beim nächsten Öffnen automatisch geladen.

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

## 10. Benutzer verwalten

Unter **Benutzer** können weitere Admin-Accounts angelegt werden. Alle Benutzer haben aktuell Admin-Rechte.

- **Neuer Benutzer:** Benutzername + Passwort
- **Passwort ändern:** Stift-Symbol
- **Löschen:** Papierkorb-Symbol (eigener Account kann nicht gelöscht werden)

---

## 11. Audit-Log

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

## 12. Matrix-Integration

OctopOS kann Nachrichten über Matrix (Element) empfangen und beantworten.

### Element-Client verbinden

Matrix-Server läuft auf Port 8008 (nginx-Proxy → conduwuit auf 6167):

- **Homeserver:** `https://<ip>:8008` oder `http://<ip>:8008`
- **Benutzer:** `@admin:<hostname>`
- **Passwort:** aus `/etc/octopos/admin_credentials`

### Agenten-Bot im Room

Wenn ein Projekt einen Matrix-Room hat, lauscht der Boss-Agent dort automatisch. Nachrichten im Room werden wie Chat-Nachrichten behandelt.

---

## 13. Troubleshooting

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
