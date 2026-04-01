# HydraHive Benutzerhandbuch

Dieses Handbuch erklärt Schritt für Schritt wie du HydraHive über die Webkonsole bedienst. Keine Kommandozeile nötig — alles läuft über den Browser.

---

## Inhaltsverzeichnis

1. [Erster Login](#1-erster-login)
2. [Die Webkonsole — Übersicht](#2-die-webkonsole--übersicht)
3. [Mein Agent — Dein persönlicher KI-Assistent](#3-mein-agent--dein-persönlicher-ki-assistent)
4. [LLM-Modell wechseln](#4-llm-modell-wechseln)
5. [Agenten verwalten](#5-agenten-verwalten)
6. [Einem Agenten Tools zuweisen](#6-einem-agenten-tools-zuweisen)
7. [Projekte anlegen und nutzen](#7-projekte-anlegen-und-nutzen)
8. [HydraHub — Agenten und Plugins installieren](#8-hydrahub--agenten-und-plugins-installieren)
9. [ClawhHub — Skills aus der Community](#9-clawhub--skills-aus-der-community)
10. [Plugins verwalten](#10-plugins-verwalten)
11. [Skills — Agenten-Wissen erweitern](#11-skills--agenten-wissen-erweitern)
12. [Federation — Mehrere Server verbinden](#12-federation--mehrere-server-verbinden)
13. [Tailscale einrichten](#13-tailscale-einrichten)
14. [HydraBrain — 3D-Ansicht](#14-hydrabrain--3d-ansicht)
15. [Blueprint — Visueller Agent-Builder](#15-blueprint--visueller-agent-builder)
16. [Scratchpad — Ideen skizzieren](#16-scratchpad--ideen-skizzieren)
17. [Einstellungen — LLM Provider](#17-einstellungen--llm-provider)
18. [Einstellungen — MCP Server](#18-einstellungen--mcp-server)
19. [Einstellungen — VPN](#19-einstellungen--vpn)
20. [Einstellungen — Mail](#20-einstellungen--mail)
21. [Einstellungen — Benutzer & Rollen](#21-einstellungen--benutzer--rollen)
22. [Einstellungen — Backup & Restore](#22-einstellungen--backup--restore)
23. [Einstellungen — Erweiterungen](#23-einstellungen--erweiterungen)
24. [Einstellungen — System-Update](#24-einstellungen--system-update)
25. [Aktivität — Live-Monitor](#25-aktivität--live-monitor)
26. [Usage — Token-Verbrauch](#26-usage--token-verbrauch)
27. [Häufige Fragen](#27-häufige-fragen)

---

## 1. Erster Login

1. Öffne deinen Browser und gib die Adresse deines HydraHive-Servers ein (z.B. `https://192.168.178.181`)
2. Du siehst die Login-Seite
3. Gib deinen **Benutzernamen** und dein **Passwort** ein
4. Klicke auf **Anmelden**
5. Beim allerersten Login startet der **Einrichtungsassistent** — folge den Schritten

> **Tipp:** Der Standard-Admin-Account wird bei der Installation angelegt. Den Benutzernamen und das Passwort hast du bei der Installation festgelegt.

---

## 2. Die Webkonsole — Übersicht

Nach dem Login siehst du die Webkonsole. Links ist die **Sidebar** mit allen Bereichen:

| Symbol | Bereich | Was du dort tust |
|--------|---------|-----------------|
| 🏠 | **Dashboard** | Übersicht: Agenten-Status, System-Auslastung |
| 🤖 | **Mein Agent** | Chatten mit deinem persönlichen KI-Assistenten |
| 👥 | **Agenten** | Alle Agenten anzeigen, erstellen, bearbeiten |
| 📁 | **Projekte** | Projektbasiertes Arbeiten mit Agenten-Teams |
| 📊 | **Aktivität** | Live-Ansicht: Welcher Agent arbeitet gerade? |
| 💰 | **Usage** | Token-Verbrauch und Kosten pro Agent |
| ⏰ | **Zeitpläne** | Automatische Aufgaben planen |
| 🔍 | **Suche** | Globale Suche über alle Agenten und Projekte |
| 🧩 | **Erweiterungen** | Optionale Dienste installieren |
| 🔌 | **Plugins** | Plugin-System verwalten |
| 🏪 | **HydraHub** | Agenten, Plugins und Skills installieren |
| 🧠 | **HydraBrain** | 3D-Visualisierung aller Agenten |
| 🌐 | **Federation** | Server miteinander verbinden |
| 📐 | **Blueprint** | Visueller Agent-Builder und Scratchpad |
| ⚙️ | **Einstellungen** | LLM, VPN, Mail, Backup, Users |

---

## 3. Mein Agent — Dein persönlicher KI-Assistent

Jeder Benutzer hat einen eigenen KI-Agenten. So chattest du mit ihm:

1. Klicke in der Sidebar auf **Mein Agent**
2. Du siehst ein Chat-Fenster
3. Tippe deine Nachricht unten ins Textfeld
4. Drücke **Enter** oder klicke den **Senden**-Button
5. Der Agent antwortet — du siehst die Antwort im Chat

### Chat leeren
1. Klicke oben rechts auf das **Papierkorb-Symbol**
2. Der Chat-Verlauf wird gelöscht und der Agent startet frisch

### Einstellungen deines Agents
1. Klicke oben rechts auf das **Zahnrad-Symbol**
2. Hier kannst du ändern:
   - **Modell** — welches KI-Modell der Agent nutzt (z.B. Claude, GPT, Ollama)
   - **Temperatur** — wie kreativ der Agent antwortet (0 = präzise, 1 = kreativ)
   - **Name** — wie dein Agent heißt

---

## 4. LLM-Modell wechseln

Dein Agent braucht ein KI-Modell zum Arbeiten. So wechselst du es:

1. Klicke in der Sidebar auf **Mein Agent**
2. Klicke oben rechts auf das **Zahnrad-Symbol**
3. Unter **Modell** siehst du ein Dropdown
4. Wähle ein Modell aus:
   - **claude-sonnet-4-6** — Schnell und gut (Anthropic)
   - **claude-opus-4-6** — Leistungsstärker, langsamer (Anthropic)
   - **claude-haiku-4-5** — Am schnellsten, güntigsten (Anthropic)
   - **gpt-4o** — OpenAI Modell
   - **ollama/...** — Lokale Modelle auf deinem Server
5. Klicke **Speichern**

> **Wichtig:** Für Claude-Modelle muss unter Einstellungen → LLM ein Anthropic-Provider eingerichtet sein. Für Ollama muss Ollama auf dem Server laufen.

---

## 5. Agenten verwalten

Agenten sind KI-Assistenten die verschiedene Aufgaben übernehmen können.

### Alle Agenten anzeigen
1. Klicke in der Sidebar auf **Agenten**
2. Du siehst eine Liste aller Agenten mit Status (grün = läuft, grau = gestoppt)

### Neuen Agenten erstellen
1. Klicke auf **Agenten**
2. Klicke oben rechts auf **+ Neuer Agent**
3. Fülle das Formular aus:
   - **Agent-ID** — Eindeutiger Name, nur Kleinbuchstaben und Bindestriche (z.B. `mein-helfer`)
   - **Anzeigename** — Der Name der in der Oberfläche erscheint
   - **Typ** — Wähle eine Rolle:
     - **Boss** — Kann Aufgaben an andere Agenten delegieren
     - **Worker** — Arbeitet Aufgaben ab die er bekommt
     - **Specialist** — Eigenständiger Experte für ein Thema
   - **Modell** — Welches KI-Modell der Agent nutzt
4. Klicke **Erstellen**

### Agenten bearbeiten
1. Klicke auf **Agenten**
2. Klicke auf den Agenten den du bearbeiten willst
3. Klicke auf **Bearbeiten**
4. Ändere die gewünschten Einstellungen
5. Klicke **Speichern**

### Agenten löschen
1. Klicke auf **Agenten**
2. Klicke auf den Agenten
3. Klicke auf **Löschen**
4. Bestätige mit **Ja**

> **Achtung:** Persönliche Agenten (die mit `personal_` anfangen) können nicht gelöscht werden.

---

## 6. Einem Agenten Tools zuweisen

Tools sind Fähigkeiten die ein Agent nutzen kann — Dateien lesen, im Web suchen, Shell-Befehle ausführen usw.

### Tools über die Agenten-Seite zuweisen
1. Klicke auf **Agenten**
2. Klicke auf den gewünschten Agenten
3. Klicke auf **Bearbeiten**
4. Scrolle runter zu **Tools**
5. Du siehst viele Buttons — jeder ist ein Tool
6. **Klicke auf einen Button** um das Tool zu aktivieren (farbig = aktiv, grau = inaktiv)
7. Tools mit ⚠ Symbol sind sensibel (z.B. `shell_exec`, `delete_agent`)
8. Klicke **Speichern**

### Häufig genutzte Tools

| Tool | Was es tut |
|------|-----------|
| `file_read` | Dateien lesen |
| `file_write` | Dateien schreiben |
| `web_search` | Im Internet suchen |
| `http_request` | Websites und APIs aufrufen |
| `shell_exec` | Terminal-Befehle ausführen |
| `read_memory` | Agenten-Gedächtnis lesen |
| `write_memory` | Ins Agenten-Gedächtnis schreiben |
| `ask_agent` | Einen anderen Agenten fragen |
| `git_status` | Git-Status prüfen |
| `git_clone` | Git-Repository klonen (braucht `shell_exec`) |

### Plugin-Tools zuweisen
Plugin-Tools werden nicht einzeln zugewiesen sondern als ganzes Plugin:
1. Klicke in der Sidebar auf **Plugins**
2. Klicke auf das gewünschte Plugin
3. Im Detail-Fenster rechts: Setze ein **Häkchen** beim gewünschten Agenten
4. Alle Tools des Plugins sind jetzt für diesen Agenten verfügbar

---

## 7. Projekte anlegen und nutzen

Projekte sind Arbeitsräume in denen Agenten-Teams zusammenarbeiten.

### Neues Projekt erstellen
1. Klicke in der Sidebar auf **Projekte**
2. Klicke auf **+ Neues Projekt**
3. Gib dem Projekt einen **Namen** und eine **Beschreibung**
4. Wähle den **Boss-Agenten** (der die Arbeit koordiniert)
5. Wähle **Worker-Agenten** (die die Arbeit erledigen)
6. Klicke **Erstellen**

### Im Projekt chatten
1. Klicke auf **Projekte**
2. Klicke auf das gewünschte Projekt
3. Du siehst das Chat-Fenster des Projekts
4. Schreibe deine Nachricht und drücke **Enter**
5. Der Boss-Agent koordiniert die Arbeit und delegiert an Worker

---

## 8. HydraHub — Agenten und Plugins installieren

Der HydraHub ist der eingebaute App-Store von HydraHive.

### Agenten aus dem Hub installieren
1. Klicke in der Sidebar auf **HydraHub**
2. Du bist im Tab **Agenten** — hier siehst du vorgefertigte Agenten
3. Nutze die **Suche** oben oder die **Kategorien** links zum Filtern
4. Klicke auf einen Agenten der dich interessiert
5. Ein Detail-Fenster öffnet sich rechts
6. Optional: Ändere die **Agent-ID**
7. Klicke **Installieren**
8. Der Agent ist sofort verfügbar — kein Neustart nötig

### Agenten deinstallieren
1. Klicke im HydraHub auf einen installierten Agenten
2. Im Detail-Fenster klicke auf **Deinstallieren**
3. Bestätige mit **Ja**

### Plugins aus dem Hub installieren
1. Klicke auf **HydraHub**
2. Wechsle zum Tab **Plugins**
3. Klicke bei einem Plugin auf **Installieren**
4. Das Plugin wird heruntergeladen und aktiviert
5. Weise es danach unter **Plugins** einem Agenten zu (siehe Kapitel 10)

---

## 9. ClawhHub — Skills aus der Community

ClawhHub ist eine öffentliche Datenbank mit tausenden KI-Skills.

### ClawhHub Token einrichten (einmalig)
1. Erstelle einen Account auf [clawhub.ai](https://clawhub.ai)
2. Gehe auf clawhub.ai zu **Settings** → **API Token** erstellen
3. Kopiere den Token
4. In HydraHive: Klicke auf **HydraHub** → Tab **ClawhHub**
5. Ganz oben siehst du das Feld **ClawhHub API Token**
6. Füge deinen Token ein und klicke **Speichern**

### Skills suchen und installieren
1. Klicke auf **HydraHub** → Tab **ClawhHub** → Sub-Tab **Skills**
2. Gib einen Suchbegriff ein (z.B. "python", "security", "git")
3. Klicke auf **Suchen**
4. Klicke auf einen Skill der dich interessiert
5. Wähle den **Ziel-Agent** (zu welchem Agent der Skill gehören soll)
6. Klicke **In Agent installieren**
7. Der Skill ist sofort aktiv

### Plugins browsen
1. Klicke auf **HydraHub** → Tab **ClawhHub** → Sub-Tab **Plugins**
2. Wähle den Typ: **Code Plugins**, **Bundle Plugins** oder **Skill Packages**
3. Du siehst eine Übersicht — aktuell nur zum Anschauen (Import kommt in Zukunft)

---

## 10. Plugins verwalten

Plugins erweitern deine Agenten um neue Fähigkeiten (Tools).

### Plugins anzeigen
1. Klicke in der Sidebar auf **Plugins**
2. Du siehst alle installierten Plugins als Karten
3. Grünes Power-Symbol = aktiv, graues = deaktiviert

### Plugin einem Agenten zuweisen
1. Klicke auf **Plugins**
2. Klicke auf das Plugin das du zuweisen willst
3. Ein Detail-Fenster öffnet sich rechts
4. Unter **Agent-Zuweisung** siehst du alle Agenten als Checkboxen
5. **Setze ein Häkchen** bei jedem Agenten der das Plugin nutzen soll
6. Die Tools des Plugins sind sofort verfügbar — kein Neustart nötig

### Plugin aktivieren / deaktivieren
1. Klicke auf **Plugins**
2. Klicke auf das **Power-Symbol** auf der Plugin-Karte
3. Grün = aktiv, grau = deaktiviert

### Plugin deinstallieren
1. Klicke auf **Plugins**
2. Klicke auf das Plugin
3. Im Detail-Fenster unten: Klicke auf das **Papierkorb-Symbol**
4. Bestätige mit **Ja**

---

## 11. Skills — Agenten-Wissen erweitern

Skills sind Wissens-Dateien die einem Agenten beibringen wie er bestimmte Aufgaben erledigt.

### Skills eines Agenten anzeigen
1. Klicke auf **Agenten**
2. Klicke auf einen Agenten
3. Klicke auf den Tab **Skills**
4. Du siehst alle Skills des Agenten

### Skill aus ClawhHub installieren
Siehe Kapitel 9 — "Skills suchen und installieren"

### Eigenen Skill erstellen
1. Klicke auf **Agenten** → wähle einen Agenten → Tab **Skills**
2. Klicke auf **+ Neuer Skill**
3. Gib dem Skill einen **Namen**
4. Schreibe den Skill-Inhalt (Anweisungen für den Agenten)
5. Klicke **Speichern**

---

## 12. Federation — Mehrere Server verbinden

Federation verbindet mehrere HydraHive-Server miteinander. Agenten auf verschiedenen Servern können dann zusammenarbeiten.

### Peers (verbundene Server) anzeigen
1. Klicke in der Sidebar auf **Federation**
2. Oben siehst du alle verbundenen **Peers** (andere Server)
3. Grüner Punkt = erreichbar, roter Punkt = nicht erreichbar

### Peer manuell hinzufügen
1. Klicke auf **Federation**
2. Klicke auf **+ Peer hinzufügen**
3. Gib ein:
   - **Name** — Ein Name für den Server (z.B. "Produktiv-Server")
   - **URL** — Die Adresse des anderen Servers (z.B. `https://192.168.1.100`)
   - **Secret** — Ein gemeinsames Passwort (muss auf beiden Servern gleich sein)
4. Klicke **Speichern**

### Test-Task an einen Peer senden
1. Klicke auf **Federation**
2. Scrolle runter zu **Test-Task senden**
3. Wähle einen **Peer** und einen **Agenten** auf dem Peer
4. Schreibe eine **Nachricht**
5. Klicke **Task senden**
6. Du siehst die Antwort des Remote-Agenten

---

## 13. Tailscale einrichten

Tailscale verbindet deine Server sicher über das Internet — verschlüsselt, ohne Port-Forwarding.

### Schritt 1: Tailscale Account erstellen
1. Gehe auf [login.tailscale.com](https://login.tailscale.com)
2. Erstelle einen Account (geht mit Google, GitHub oder E-Mail)
3. Gehe zu **Settings** → **Keys**
4. Klicke auf **Generate access token**
5. Kopiere den Token (beginnt mit `tskey-api-...`)

### Schritt 2: API Key in HydraHive eintragen
1. Klicke in der Sidebar auf **Federation**
2. Scrolle zur Sektion **Tailscale**
3. Du siehst **Schritt 1: Tailscale API Key eintragen**
4. Füge deinen API Key ein (den `tskey-api-...` Token)
5. Klicke **Speichern**
6. Der Key wird geprüft — wenn gültig, erscheint Schritt 2

### Schritt 3: Server verbinden
1. Klicke auf **Einladen** — ein Auth-Key wird generiert (beginnt mit `tskey-auth-...`)
2. Kopiere den Auth-Key
3. Füge ihn im Feld **"Server mit Tailnet verbinden"** ein
4. Klicke **Verbinden**
5. Dein Server hat jetzt eine Tailscale-IP (z.B. `100.x.x.x`)

### Schritt 4: Andere Server einladen
1. Klicke auf **Einladen** — ein neuer Auth-Key wird generiert
2. Schicke diesen Key dem Admin des anderen Servers
3. Der andere Admin geht auf seiner HydraHive-Instanz auf **Federation** → **Tailscale**
4. Dort trägt er den gleichen **API Key** ein (Schritt 1)
5. Dann den **Auth-Key** ins Verbinden-Feld (Schritt 2) → **Verbinden**
6. Beide Server sind jetzt im selben Netzwerk

### Schritt 5: HydraHive-Instanzen finden
1. Klicke auf **HydraHive suchen**
2. HydraHive scannt alle Server im Tailscale-Netzwerk
3. Gefundene Instanzen erscheinen mit einem Button **"Als Peer hinzufügen"**
4. Klicke darauf — die Server sind jetzt verbunden und können zusammenarbeiten

### API Key ändern
1. Klicke auf **API Key ändern** neben der Tailscale-IP
2. Trage den neuen Key ein → **Speichern**

### Server trennen
1. Klicke auf **Trennen** — der Server wird vom Tailscale-Netzwerk getrennt

### Gerät aus dem Netzwerk entfernen
1. Klicke auf **Tailnet Devices** um alle Geräte zu sehen
2. Klicke auf das **Papierkorb-Symbol** neben dem Gerät das du entfernen willst
3. Bestätige mit **Ja**

> **Wichtig:**
> - Der **API Key** (`tskey-api-...`) ist für die Verwaltung — auf jedem Server gleich
> - Der **Auth Key** (`tskey-auth-...`) ist eine Einladung — einmalig, zum Beitreten
> - Verwechsle die beiden nicht!

---

## 14. HydraBrain — 3D-Ansicht

HydraBrain zeigt eine interaktive 3D-Karte aller Agenten, Tools und Verbindungen.

### Öffnen
1. Klicke in der Sidebar auf **HydraBrain**
2. Du siehst einen 3D-Graphen mit farbigen Kugeln

### Bedienung
- **Mausrad** — Rein- und rauszoomen
- **Linke Maustaste ziehen** — Ansicht drehen
- **Rechte Maustaste ziehen** — Ansicht verschieben
- **Auf eine Kugel klicken** — Details zum Knoten anzeigen

### Was die Farben bedeuten
| Farbe | Bedeutung |
|-------|-----------|
| Blau | Boss-Agent |
| Grün | Worker/Specialist-Agent |
| Cyan (hell-blau) | Agent denkt gerade |
| Grün (leuchtend) | Agent liest gerade Daten |
| Orange | Agent schreibt gerade Daten |
| Klein + grau | Tools, Memories, Skills |

### Federation einblenden
1. Klicke auf den **Radar-Button** in der Toolbar oben rechts
2. Remote-Server und ihre Agenten werden als separate Cluster angezeigt
3. Pink = Peer-Gateway, Gelb = Remote-Agenten
4. Nochmal klicken um sie auszublenden

### Labels ein-/ausblenden
1. Klicke auf den **Label-Button** in der Toolbar
2. Beschriftungen an den Kugeln werden ein-/ausgeblendet

### Ansicht neu laden
1. Klicke auf den **Refresh-Button** in der Toolbar
2. Agenten und Verbindungen werden neu geladen

> **Hinweis:** HydraBrain braucht WebGL (Hardware-Beschleunigung im Browser). Falls du eine leere Seite siehst, starte den Browser neu.

---

## 15. Blueprint — Visueller Agent-Builder

Im Blueprint-Bereich baust du Agenten visuell zusammen — als Graph mit Kästchen und Verbindungen.

### Bestehenden Agenten anzeigen
1. Klicke in der Sidebar auf **Blueprint**
2. Wechsle zum Tab **Agent-Blueprint**
3. Wähle oben links im Dropdown einen Agenten aus
4. Du siehst seinen Aufbau: Agent-Kästchen in der Mitte, Tools links, MCP-Server rechts

### Neuen Agenten visuell erstellen
1. Klicke auf **Blueprint** → Tab **Agent-Blueprint**
2. Klicke auf **Neuer Agent**
3. Ein violettes Agent-Kästchen erscheint in der Mitte
4. **Klicke auf das Kästchen** — rechts erscheinen die Einstellungen:
   - **Agent-ID** — Eindeutiger Name (Pflichtfeld!)
   - **Typ** — Boss, Worker oder Specialist
   - **LLM Model** — Welches KI-Modell
   - **Soul** — Persönlichkeit und Anweisungen
5. Klicke oben auf **Palette** um die Toolbox zu öffnen
6. Klicke auf **Tool**, **Skill**, **MCP Server** oder **Plugin** um Kästchen hinzuzufügen
7. **Ziehe eine Linie** vom neuen Kästchen zum Agent-Kästchen (von einem Punkt zum anderen)
8. Wähle bei jedem Kästchen rechts im Panel was es sein soll (z.B. welches Tool)
9. Wenn alles konfiguriert ist: Klicke oben rechts auf **Agent erstellen**

### Agent löschen
1. Im **Agent-Blueprint** Tab den Agenten im Dropdown auswählen
2. Klicke auf den roten **Löschen**-Button neben dem Dropdown
3. Bestätige mit **Ja**

### Andockpunkte am Agent-Kästchen
Das Agent-Kästchen hat 6 farbige Andockpunkte:
- **Links oben** (cyan) → Tools
- **Links unten** (lila) → Skills
- **Rechts oben** (pink) → MCP Server
- **Rechts unten** (gelb) → Plugins
- **Oben** (türkis) → Memory
- **Unten** (blau) → Repositories

---

## 16. Scratchpad — Ideen skizzieren

Das Scratchpad ist ein freies Whiteboard zum Brainstormen.

### Öffnen
1. Klicke auf **Blueprint**
2. Wechsle zum Tab **Scratchpad**

### Notiz erstellen
1. Klicke auf **Notiz** — ein graues Kästchen erscheint
2. Oder klicke auf **Farbe** und wähle eine Farbe für das Kästchen

### Notiz bearbeiten
1. **Klicke auf ein Kästchen** — rechts erscheinen die Einstellungen
2. Ändere den **Text** (wird im Kästchen angezeigt)
3. Ändere die **Notiz** (kleine Zusatzinfo unter dem Text)
4. Wähle eine andere **Farbe**

### Kästchen verbinden
1. Fahre mit der Maus über ein Kästchen — du siehst kleine Punkte an den Rändern
2. **Klicke und ziehe** von einem Punkt zu einem Punkt auf einem anderen Kästchen
3. Eine Verbindungslinie entsteht

### Kästchen verschieben
1. **Klicke und ziehe** das Kästchen an eine neue Position

### Alles löschen
1. Klicke auf **Alles löschen**
2. Bestätige mit **OK**

> **Tipp:** Das Scratchpad speichert automatisch alle 5 Sekunden. Deine Skizzen bleiben erhalten.

---

## 17. Einstellungen — LLM Provider

Hier konfigurierst du welche KI-Modelle verfügbar sind.

### Provider einrichten
1. Klicke in der Sidebar auf **Einstellungen**
2. Klicke auf **LLM**
3. Du siehst die verfügbaren Provider

### Anthropic (Claude) einrichten
1. Unter **Anthropic** klicke auf **Mit Anthropic verbinden**
2. Du wirst zu Anthropic weitergeleitet
3. Melde dich an und erlaube den Zugriff
4. Du wirst zurückgeleitet — Claude-Modelle sind jetzt verfügbar

### Ollama (lokale Modelle) einrichten
1. Ollama muss auf dem Server installiert sein
2. Unter **Ollama** siehst du den Status und verfügbare Modelle
3. Du kannst neue Modelle direkt herunterladen: Modellname eingeben → **Pull**

### Standard-Modell festlegen
1. Unter **Standard-Modell** wähle das Modell das als Default genutzt werden soll
2. Klicke **Speichern**

---

## 18. Einstellungen — MCP Server

MCP Server erweitern HydraHive um externe Tools (z.B. Datenbanken, APIs).

### MCP Server hinzufügen
1. Klicke auf **Einstellungen** → **MCP**
2. Klicke auf **+ Server hinzufügen**
3. Gib ein:
   - **ID** — Eindeutiger Name (z.B. `mein-mcp`)
   - **Name** — Anzeigename
   - **URL** — Adresse des MCP Servers
   - **Transport** — streamableHttp oder SSE
4. Klicke **Speichern**

### MCP Server einem Agenten zuweisen
1. Klicke auf **Agenten** → Wähle einen Agenten → **Bearbeiten**
2. Unter **MCP Server** wähle die Server die der Agent nutzen soll
3. Klicke **Speichern**

---

## 19. Einstellungen — VPN

Unter VPN konfigurierst du Tailscale für sichere Server-Verbindungen.

> **Hinweis:** Die Tailscale-Einrichtung erfolgt über die **Federation**-Seite (siehe Kapitel 13). Unter Einstellungen → VPN siehst du nur den Status.

---

## 20. Einstellungen — Mail

Hier konfigurierst du E-Mail-Versand für Agenten.

### Mail einrichten
1. Klicke auf **Einstellungen** → **Mail / KAS**
2. Gib deine SMTP-Daten ein:
   - **Server** — z.B. `smtp.gmail.com`
   - **Port** — z.B. `587`
   - **Benutzername** und **Passwort**
3. Klicke **Speichern**
4. Klicke **Test senden** um die Konfiguration zu prüfen

---

## 21. Einstellungen — Benutzer & Rollen

### Neuen Benutzer erstellen
1. Klicke auf **Einstellungen** → **Users**
2. Klicke auf **+ Neuer Benutzer**
3. Gib **Benutzername** und **Passwort** ein
4. Wähle die **Rolle**:
   - **Admin** — Voller Zugriff auf alles
   - **Standard** — Kann Agenten und Projekte nutzen
   - **Chatter** — Kann nur mit seinem persönlichen Agenten chatten
5. Klicke **Erstellen**

### Benutzer einladen (mit Link)
1. Klicke auf **Einstellungen** → **Users**
2. Klicke auf **Einladung erstellen**
3. Ein Einladungs-Link wird generiert
4. Schicke den Link an die Person
5. Die Person kann sich damit selbst registrieren

### Passwort ändern
1. Klicke auf **Einstellungen** → **Users**
2. Klicke auf den Benutzer
3. Klicke auf **Passwort ändern**
4. Gib das neue Passwort ein → **Speichern**

---

## 22. Einstellungen — Backup & Restore

### Backup erstellen
1. Klicke auf **Einstellungen** → **Backup**
2. Klicke auf **Backup erstellen**
3. Das Backup wird erstellt (dauert je nach Datenmenge einige Sekunden)
4. Du siehst es in der Liste mit Datum und Größe

### Backup herunterladen
1. Klicke bei einem Backup auf den **Download**-Button
2. Die Datei wird heruntergeladen

### Backup wiederherstellen
1. Klicke bei einem Backup auf **Wiederherstellen**
2. Bestätige mit **Ja**
3. Alle Agenten, Projekte und Einstellungen werden aus dem Backup geladen

> **Achtung:** Bei der Wiederherstellung werden die aktuellen Daten überschrieben!

### Backup löschen
1. Klicke bei einem Backup auf das **Papierkorb-Symbol**
2. Bestätige mit **Ja**

---

## 23. Einstellungen — Erweiterungen

Erweiterungen sind optionale Dienste die du mit einem Klick installieren kannst.

### Erweiterung installieren
1. Klicke auf **Einstellungen** → **Erweiterungen**
2. Du siehst verfügbare Erweiterungen (z.B. SearXNG, Code-Server, Tailscale)
3. Klicke bei der gewünschten Erweiterung auf **Installieren**
4. Die Installation läuft — du siehst den Fortschritt
5. Nach der Installation ist die Erweiterung aktiv

### Verfügbare Erweiterungen

| Erweiterung | Was sie tut |
|-------------|-----------|
| **SearXNG** | Private Suchmaschine — Agenten können das Web durchsuchen |
| **Code-Server** | VS Code im Browser — Code direkt bearbeiten |
| **Tailscale** | VPN für sichere Server-Verbindungen |
| **Vaultwarden** | Passwort-Manager |

---

## 24. Einstellungen — System-Update

### Update durchführen
1. Klicke auf **Einstellungen** → **System**
2. Oben siehst du die aktuelle Version
3. Wenn ein Update verfügbar ist, klicke auf **Update installieren**
4. Das Update läuft automatisch — der Server startet danach neu
5. Nach dem Neustart bist du auf der neuesten Version

---

## 25. Aktivität — Live-Monitor

### Agenten-Aktivität beobachten
1. Klicke in der Sidebar auf **Aktivität**
2. Du siehst alle Agenten als Karten
3. Jede Karte zeigt:
   - **Status** — Was der Agent gerade tut
   - **Aktuelle Aufgabe** — Die letzte Aktion
   - **Laufzeit** — Wie lange der Agent schon arbeitet

---

## 26. Usage — Token-Verbrauch

### Kosten einsehen
1. Klicke in der Sidebar auf **Usage**
2. Du siehst den Token-Verbrauch pro Agent und Modell
3. Aufgeschlüsselt nach:
   - **Input-Tokens** — Was du dem Agent schickst
   - **Output-Tokens** — Was der Agent antwortet
   - **Cache-Hits** — Wiederverwendete Daten (günstiger)

---

## 27. Häufige Fragen

### "Mein Agent antwortet nicht"
1. Prüfe ob der Agent läuft: **Agenten** → ist der Status grün?
2. Prüfe ob ein LLM-Provider konfiguriert ist: **Einstellungen** → **LLM**
3. Leere den Chat: **Mein Agent** → **Papierkorb-Symbol** oben rechts

### "Ich sehe keine Modelle in der Auswahl"
1. Gehe auf **Einstellungen** → **LLM**
2. Prüfe ob mindestens ein Provider eingerichtet ist (Anthropic, OpenAI oder Ollama)
3. Für Anthropic: Klicke auf **Mit Anthropic verbinden**

### "Plugin-Tools tauchen beim Agenten nicht auf"
1. Gehe auf **Plugins** in der Sidebar
2. Klicke auf das Plugin
3. Prüfe ob der Agent bei **Agent-Zuweisung** angehakt ist

### "Tailscale verbindet nicht"
1. Prüfe ob du den richtigen Key nutzt:
   - **API Key** (`tskey-api-...`) → kommt in Schritt 1 (oben)
   - **Auth Key** (`tskey-auth-...`) → kommt in Schritt 2 (Verbinden)
2. Die beiden Keys sind verschieden — nicht verwechseln!
3. Der API Key ist dein Verwaltungsschlüssel (gleich auf allen Servern)
4. Der Auth Key ist eine Einladung (einmalig, 24h gültig)

### "HydraBrain zeigt eine leere Seite"
1. HydraBrain braucht WebGL (Hardware-Beschleunigung)
2. Schließe den Browser komplett und starte ihn neu
3. Falls es weiterhin nicht geht: Prüfe ob Hardware-Beschleunigung aktiviert ist
   - Chrome: `chrome://settings` → suche "Hardware" → aktivieren
