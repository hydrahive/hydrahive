# HydraHive Benutzerhandbuch

Dieses Handbuch erklärt Schritt für Schritt wie du HydraHive über die Webkonsole bedienst. Keine Kommandozeile nötig — alles läuft über den Browser.

---

## Inhaltsverzeichnis

1. [Erster Login](#1-erster-login)
2. [Die Webkonsole — Übersicht](#2-die-webkonsole--übersicht)
3. [Dashboard — Übersicht, Aktivität, Usage, Audit](#3-dashboard--übersicht-aktivität-usage-audit)
4. [Mein Agent — Dein persönlicher KI-Assistent](#4-mein-agent--dein-persönlicher-ki-assistent)
5. [Messenger-Integrationen einrichten](#5-messenger-integrationen-einrichten)
6. [Projekte / Agenten erstellen und verwalten (Admin)](#6-projekte--agenten-erstellen-und-verwalten-admin)
7. [Mit einem Projekt chatten](#7-mit-einem-projekt-chatten)
8. [Hub — Extensions, Plugins, Skill-Pakete](#8-hub--extensions-plugins-skill-pakete)
9. [ClawhHub — Skills aus der Community](#9-clawhub--skills-aus-der-community)
10. [Federation — Mehrere Server verbinden](#10-federation--mehrere-server-verbinden)
11. [Tailscale einrichten](#11-tailscale-einrichten)
12. [HydraBrain — 3D-Ansicht](#12-hydrabrain--3d-ansicht)
13. [Blueprint — Visueller Agent-Builder](#13-blueprint--visueller-agent-builder)
14. [Scratchpad — Ideen skizzieren](#14-scratchpad--ideen-skizzieren)
15. [Einstellungen — LLM Provider](#15-einstellungen--llm-provider)
16. [Einstellungen — MCP Server](#16-einstellungen--mcp-server)
17. [Einstellungen — VPN](#17-einstellungen--vpn)
18. [Einstellungen — Mail](#18-einstellungen--mail)
19. [Einstellungen — Benutzer & Rollen](#19-einstellungen--benutzer--rollen)
20. [Einstellungen — Backup & Restore](#20-einstellungen--backup--restore)
21. [Einstellungen — System-Update](#21-einstellungen--system-update)
22. [v2-Features im Überblick](#22-v2-features-im-überblick)
23. [Häufige Fragen](#23-häufige-fragen)

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

Nach dem Login siehst du die Webkonsole. Links ist die **Sidebar** mit allen Bereichen. Die Navigation ist flach aufgebaut — 10 Einträge ohne aufklappbare Gruppen:

| Symbol | Bereich | Was du dort tust |
|--------|---------|-----------------|
| 🏠 | **Dashboard** | Tabs: Übersicht, Aktivität, Usage, Audit |
| 🤖 | **Mein Agent** | Chatten mit deinem persönlichen KI-Assistenten |
| 👥 | **Projekte** | Alle KI-Agenten (= Projekte) anzeigen, erstellen, chatten |
| 🧠 | **HydraBrain** | 3D-Visualisierung aller Projekte |
| 🔍 | **Web-Suche** | SearXNG Suche verwalten |
| 🖥️ | **System** | Logs, Services, Hardware-Info |
| 👥 | **Benutzer** | Benutzer und Rollen verwalten (Admin) |
| ⚙️ | **Einstellungen** | LLM-Provider, API-Keys, Backup |
| 🔌 | **MCP-Server** | Model Context Protocol Server |
| 🌐 | **Federation** | Server miteinander verbinden |
| 🏪 | **Hub** | Extensions, Plugins, Skill-Pakete |

> **Wichtig (v2):** In HydraHive v2 sind **Agenten** und **Projekte** dasselbe — jedes Projekt ist sein eigener KI-Agent. Erstellen, konfigurieren und chatten läuft alles über **Projekte**.

> **Tipp für reguläre Benutzer:** Als normaler Benutzer arbeitest du hauptsächlich mit **Dashboard** und **Projekte**.

---

## 3. Dashboard — Übersicht, Aktivität, Usage, Audit

Das Dashboard ist die Startseite nach dem Login. Es hat vier Tabs:

| Tab | Inhalt |
|-----|--------|
| **Übersicht** | Agenten-Status, System-Auslastung, Quick-Links |
| **Aktivität** | Live-Ansicht: Welcher Agent arbeitet gerade, mit Logs |
| **Usage** | Token-Verbrauch und Kosten pro Agent und Modell |
| **Audit** | Protokoll aller Admin-Aktionen |

### Aktivität beobachten
1. Klicke in der Sidebar auf **Dashboard**
2. Wechsle zum Tab **Aktivität**
3. Du siehst alle Agenten als Karten mit Status, aktueller Aufgabe und Laufzeit

### Token-Verbrauch einsehen
1. Klicke auf **Dashboard** → Tab **Usage**
2. Du siehst den Verbrauch pro Agent und Modell, aufgeschlüsselt nach:
   - **Input-Tokens** — Was du dem Agent schickst
   - **Output-Tokens** — Was der Agent antwortet
   - **Cache-Hits** — Wiederverwendete Daten (günstiger)

---

## 4. Mein Agent — Dein persönlicher KI-Assistent

Jeder Benutzer hat einen eigenen KI-Agenten. So chattest du mit ihm:

1. Klicke in der Sidebar auf **Mein Agent**
2. Du siehst ein Chat-Fenster
3. Tippe deine Nachricht unten ins Textfeld
4. Drücke **Enter** oder klicke den **Senden**-Button
5. Der Agent antwortet — du siehst die Antwort im Chat

### Nützliche Slash-Befehle
Unter dem Chatfeld siehst du Buttons für Schnellbefehle:
- `/clear` — Chat leeren und neu starten
- `/model` — Aktuelles Modell anzeigen
- `/retry` — Letzte Antwort nochmal generieren
- `/help` — Hilfe anzeigen
- `/history` — Vergangene Sessions anzeigen

### Vergangene Sessions
1. Klicke links oben auf das **Uhr-Symbol**
2. Du siehst eine Liste vergangener Chats
3. Klicke auf **Fortsetzen** um einen alten Chat weiterzuführen
4. Klicke auf **Neuer Chat** um einen frischen Chat zu starten
5. Klicke auf **Zurück** um zum aktuellen Chat zurückzukehren

### Chat leeren
1. Tippe `/clear` ins Chatfeld und drücke **Enter**
2. Der Chat-Verlauf wird gelöscht und der Agent startet frisch

### Tabs in "Mein Agent"

Die Seite **Mein Agent** hat folgende Tabs:

| Tab | Inhalt |
|-----|--------|
| **Chat** | Chat-Fenster mit deinem persönlichen Agenten |
| **Heartbeat** | Status-Anzeige und Verbindungsprüfung |
| **Messenger** | Messenger-Integrationen (Discord, WhatsApp, Telegram, Mail) |
| **WKS** | Workspace-Einstellungen |
| **Butler** | Automatisierte Aufgaben deines Agenten |
| **Mein Konto** | Dein Benutzerkonto und Passwort ändern |

> **Hinweis:** Die Tabs **Settings**, **Skills** und **MCP** sind in der "Mein Agent"-Seite nicht mehr vorhanden. Diese Konfigurationen werden vom Admin in der **Agenten**-Seite verwaltet.

---

## 5. Messenger-Integrationen einrichten

Unter **Mein Agent → Messenger** kannst du deinen persönlichen Agenten mit verschiedenen Messenger-Diensten verbinden. Die Integrationen sind als aufklappbare Sektionen (Accordion) organisiert.

### Discord einrichten
1. Klicke auf **Mein Agent** → Tab **Messenger**
2. Klappe die Sektion **Discord** auf
3. Trage deinen Discord Bot Token ein
4. Klicke **Speichern**

### WhatsApp einrichten
1. Klicke auf **Mein Agent** → Tab **Messenger**
2. Klappe die Sektion **WhatsApp** auf
3. Folge den Anweisungen zum Verbinden deiner Nummer
4. Klicke **Speichern**

### Telegram einrichten
1. Klicke auf **Mein Agent** → Tab **Messenger**
2. Klappe die Sektion **Telegram** auf
3. Trage den Bot Token ein (erhältst du vom BotFather auf Telegram)
4. Klicke **Speichern**

### Mail einrichten
1. Klicke auf **Mein Agent** → Tab **Messenger**
2. Klappe die Sektion **Mail** auf
3. Trage deine E-Mail-Adresse ein
4. Klicke **Speichern**

> **Hinweis:** Damit Mail-Versand funktioniert, muss der Admin erst unter **Einstellungen → Mail** den SMTP-Server eingerichtet haben.

---

## 6. Projekte / Agenten erstellen und verwalten (Admin)

In HydraHive v2 ist jedes **Projekt** ein eigenständiger KI-Agent. Es gibt keine Boss/Worker-Hierarchie mehr — jedes Projekt ist gleichwertig und spezialisiert sich auf sein Thema.

### Alle Projekte anzeigen
1. Klicke in der Sidebar auf **Projekte**
2. Du siehst alle verfügbaren Projekte als Karten mit Status

### Neues Projekt erstellen
1. Klicke auf **Projekte**
2. Klicke oben rechts auf **+ Neues Projekt**
3. Der **Projekt-Wizard** öffnet sich — fülle aus:
   - **Projekt-ID** — Eindeutiger Name, nur Kleinbuchstaben, Zahlen und Bindestriche (z.B. `mein-assistent`)
   - **Anzeigename** — Der Name der in der Oberfläche erscheint
   - **Beschreibung** — Kurze Erklärung was dieses Projekt macht
   - **Template** — Vorausgefüllte AGENT.md (Allgemein, Code, Finanzen usw.)
   - **KI-Modell** — Welches Modell der Agent nutzt (Claude, GPT-4, Ollama…)
   - **Ausführungs-Modus** — Sicherheitsstufe:
     - **Safe** (Standard) — Befehle in Sandbox, gefährliche Operationen blockiert
     - **Elevated** — Erweiterte Rechte, bwrap-Sandbox
     - **Unrestricted** — Volle Kontrolle (nur für vertrauenswürdige Agenten)
4. Klicke **Erstellen** — das Projekt ist sofort einsatzbereit

### Projekt-Einstellungen bearbeiten
1. Klicke auf ein Projekt → **Settings**-Tab oben rechts
2. Dort kannst du ändern:
   - **LLM-Modell** und Provider
   - **AGENT.md** — Persönlichkeit, Regeln und Wissen des Agenten
   - **Members** — Welche Benutzer das Projekt nutzen dürfen
   - **Ausführungs-Modus** — Sicherheitsstufe
   - **Memory aufbauen** — Automatischer Scan des Projektverzeichnisses
3. Klicke **Speichern**

### Projekt löschen
1. Klicke auf **Projekte** → das gewünschte Projekt
2. Gehe zu **Einstellungen** → **Gefahrenzone**
3. Klicke auf **Projekt löschen** und bestätige

> **Achtung:** Persönliche Agenten (die mit `personal_` anfangen) können nicht gelöscht werden.

### v2 Core-Tools

Alle Projekte haben dieselben 9 Kern-Tools fest eingebaut — kein manuelles Zuweisen nötig:

| Tool | Was es tut |
|------|-----------|
| `file_read` | Dateien lesen |
| `file_write` | Dateien schreiben |
| `file_patch` | Dateien gezielt bearbeiten |
| `file_search` | In Dateien suchen |
| `web_search` | Im Internet suchen |
| `shell_exec` | Terminal-Befehle ausführen (mit Sandbox) |
| `read_memory` | Projektgedächtnis lesen |
| `write_memory` | Ins Projektgedächtnis schreiben |
| `ask_agent` | Ein anderes Projekt fragen |

### Plugin-Tools zuweisen (Admin)
Plugin-Tools werden über den Hub installiert und sind dann in den Projekt-Einstellungen auswählbar:
1. **Hub** → **Plugins** → Plugin installieren
2. In **Projekte** → Projekt → **Einstellungen** → Plugins auswählen

---

## 7. Mit einem Projekt chatten

### Projekt öffnen und chatten
1. Klicke in der Sidebar auf **Projekte**
2. Klicke auf das gewünschte Projekt
3. Du siehst das Chat-Fenster — schreibe deine Nachricht und drücke **Enter**
4. Der Agent antwortet — Tool-Aufrufe (Datei lesen, Suche usw.) sind als ausklappbare Blöcke sichtbar

### Typing-Indicator
Während der Agent arbeitet siehst du in der Sidebar einen animierten Punkt neben dem Projektnamen — so weißt du sofort wenn ein Projekt gerade aktiv ist.

### Persönlicher Agent (Mein Agent)
Jeder Benutzer hat ein eigenes Projekt: `personal_<benutzername>`. Du erreichst es über **Mein Agent** in der Navigation.

### Zeitpläne (Schedules)
Automatische Aufgaben planst du über **Schedules** (erreichbar über die Sidebar oder unter `/schedules`):
1. Klicke auf **+ Neuer Zeitplan**
2. Wähle das Projekt, die Uhrzeit (Cron-Syntax) und den Auftrag
3. Klicke **Speichern**

---

## 8. Hub — Extensions, Plugins, Skill-Pakete

Der **Hub** ist der eingebaute App-Store von HydraHive. Er hat drei Tabs:

| Tab | Inhalt |
|-----|--------|
| **Extensions** | Optionale Dienste installieren (SearXNG, Code-Server usw.) |
| **Plugins** | Plugin-Pakete herunterladen und aktivieren |
| **Skill-Pakete** | Skill-Sammlungen aus der Community installieren |

### Extension installieren
1. Klicke in der Sidebar auf **Hub**
2. Du bist im Tab **Extensions**
3. Du siehst verfügbare Erweiterungen (z.B. SearXNG, Code-Server, Tailscale)
4. Klicke bei der gewünschten Erweiterung auf **Installieren**
5. Die Installation läuft — du siehst den Fortschritt
6. Nach der Installation ist die Erweiterung aktiv

### Verfügbare Extensions

| Extension | Was sie tut |
|-----------|-----------|
| **SearXNG** | Private Suchmaschine — Agenten können das Web durchsuchen |
| **Code-Server** | VS Code im Browser — Code direkt bearbeiten |
| **Tailscale** | VPN für sichere Server-Verbindungen |
| **Vaultwarden** | Passwort-Manager |

### Plugins aus dem Hub installieren
1. Klicke auf **Hub** → Tab **Plugins**
2. Klicke bei einem Plugin auf **Installieren**
3. Das Plugin wird heruntergeladen und aktiviert
4. Der Admin weist es danach in der **Agenten**-Seite einem Agenten zu

### Skill-Pakete installieren
1. Klicke auf **Hub** → Tab **Skill-Pakete**
2. Wähle ein Skill-Paket das dich interessiert
3. Wähle den **Ziel-Agenten**
4. Klicke **Installieren**

---

## 9. ClawhHub — Skills aus der Community

ClawhHub ist eine öffentliche Datenbank mit tausenden KI-Skills. Die ClawhHub-Inhalte sind über den **Hub** erreichbar.

### ClawhHub Token einrichten (einmalig)
1. Erstelle einen Account auf [clawhub.ai](https://clawhub.ai)
2. Gehe auf clawhub.ai zu **Settings** → **API Token** erstellen
3. Kopiere den Token
4. In HydraHive: Klicke auf **Hub** → Tab **Skill-Pakete**
5. Ganz oben siehst du das Feld **ClawhHub API Token**
6. Füge deinen Token ein und klicke **Speichern**

### Skills suchen und installieren
1. Klicke auf **Hub** → Tab **Skill-Pakete**
2. Gib einen Suchbegriff ein (z.B. "python", "security", "git")
3. Klicke auf **Suchen**
4. Klicke auf einen Skill der dich interessiert
5. Wähle den **Ziel-Agent** (zu welchem Agent der Skill gehören soll)
6. Klicke **In Agent installieren**
7. Der Skill ist sofort aktiv

### Plugins aus ClawhHub browsen
1. Klicke auf **Hub** → Tab **Plugins**
2. Wähle den Typ: **Code Plugins**, **Bundle Plugins** oder **Skill Packages**
3. Du siehst eine Übersicht — aktuell nur zum Anschauen (Import kommt in Zukunft)

---

## 10. Federation — Mehrere Server verbinden

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

## 11. Tailscale einrichten

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

## 12. HydraBrain — 3D-Ansicht

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
| Blau | Inaktives Projekt |
| Cyan (leuchtend) | Projekt denkt / antwortet gerade |
| Grün (leuchtend) | Projekt liest gerade Daten |
| Orange | Projekt schreibt gerade Daten |
| Klein + grau | Memory-Dateien, Skills |

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

## 13. Blueprint — Visueller Agent-Builder

Im Blueprint-Bereich baust du Agenten visuell zusammen — als Graph mit Kästchen und Verbindungen.

### Bestehenden Agenten anzeigen
1. Klicke in der Sidebar auf **Blueprint**
2. Wechsle zum Tab **Agent-Blueprint**
3. Wähle oben links im Dropdown einen Agenten aus
4. Du siehst seinen Aufbau: Agent-Kästchen in der Mitte, Tools links, MCP-Server rechts

### Neues Projekt visuell erstellen
1. Klicke auf **Blueprint** → Tab **Agent-Blueprint**
2. Klicke auf **Neues Projekt**
3. Ein violettes Projekt-Kästchen erscheint in der Mitte
4. **Klicke auf das Kästchen** — rechts erscheinen die Einstellungen:
   - **Projekt-ID** — Eindeutiger Name (Pflichtfeld!)
   - **LLM Model** — Welches KI-Modell
   - **AGENT.md** — Persönlichkeit und Anweisungen
5. Klicke oben auf **Palette** um die Toolbox zu öffnen
6. Klicke auf **Skill**, **MCP Server** oder **Plugin** um Kästchen hinzuzufügen
7. **Ziehe eine Linie** vom neuen Kästchen zum Projekt-Kästchen
8. Wähle bei jedem Kästchen rechts im Panel was es sein soll
9. Wenn alles konfiguriert ist: Klicke oben rechts auf **Projekt erstellen**

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

## 14. Scratchpad — Ideen skizzieren

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

## 15. Einstellungen — LLM Provider

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

## 16. Einstellungen — MCP Server

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

### MCP Server einem Projekt zuweisen
1. Klicke auf **Projekte** → Projekt → **Einstellungen**
2. Unter **MCP Server** wähle die Server die das Projekt nutzen soll
3. Klicke **Speichern**

---

## 17. Einstellungen — VPN

Unter VPN konfigurierst du Tailscale für sichere Server-Verbindungen.

> **Hinweis:** Die Tailscale-Einrichtung erfolgt über die **Federation**-Seite (siehe Kapitel 11). Unter Einstellungen → VPN siehst du nur den Status.

---

## 18. Einstellungen — Mail

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

## 19. Einstellungen — Benutzer & Rollen

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

## 20. Einstellungen — Backup & Restore

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
1. Klicke bei einem Backup auf das **Löschen**-Symbol
2. Bestätige die Sicherheitsabfrage

---

## 21. Einstellungen — System-Update

### Update durchführen
1. Klicke auf **Einstellungen** → **System**
2. Oben siehst du die aktuelle Version
3. Wenn ein Update verfügbar ist, klicke auf **Update installieren**
4. Das Update läuft automatisch — der Server startet danach neu
5. Nach dem Neustart bist du auf der neuesten Version

---

## 22. v2-Features im Überblick

### Typing-Indicator
Während ein Projekt aktiv arbeitet siehst du einen animierten Punkt in der Projektliste. So siehst du auf einen Blick welche Projekte gerade beschäftigt sind — ohne die Seite zu wechseln.

### WhatsApp-Filter (pro Projekt)
Jedes Projekt kann seine eigene WhatsApp-Filterregel haben:
- **Private Chats** — Nur Direktnachrichten annehmen
- **Gruppen** — Gruppen-Chats bearbeiten
- **Keyword-Filter** — Nur Nachrichten mit bestimmtem Stichwort bearbeiten
- **Erlaubte Nummern** — Whitelist für bestimmte Kontakte
- **Gesperrte Nummern** — Blacklist

Diese Einstellungen findest du unter **Projekte** → Projekt → **Einstellungen** → **WhatsApp**.

### Butler (automatische Aufgaben pro Projekt)
Jedes Projekt hat seinen eigenen Butler — einen automatisierten Hintergrund-Agenten der regelmäßige Aufgaben erledigt. Einstellungen unter **Mein Agent** → Tab **Butler**.

### Ausführungs-Modus (Execution Mode)
Beim Erstellen oder in den Einstellungen eines Projekts wählst du die Sicherheitsstufe:

| Modus | Beschreibung | Geeignet für |
|-------|-------------|-------------|
| **Safe** | Sandbox-Ausführung, Blocklist aktiv | Alle Projekte (Standard) |
| **Elevated** | Erweiterte Rechte, bwrap-Sandbox | Entwicklungs-Projekte |
| **Unrestricted** | Keine Einschränkungen | Nur sehr vertrauenswürdige Agenten |

### Memory aufbauen
Jedes Projekt kann eine strukturierte Memory-Basis aus seinem Verzeichnis erstellen:
- **Projekte** → Projekt → **Einstellungen** → **Memory aufbauen**
- Der Agent scannt das Projektverzeichnis und erstellt `project_structure.md`
- Das Gedächtnis bleibt über Chats hinaus erhalten und wird automatisch weiterentwickelt

### Shared Sessions (Live-Input-Sharing)
Mehrere Benutzer können dieselbe Chat-Session gleichzeitig sehen und eingeben — wie `screen -x` für den Browser. Über die Projekt-Chat-Seite verfügbar.

---

## 23. Häufige Fragen

### "Mein Agent antwortet nicht"
1. Prüfe ob das Projekt läuft: **Projekte** → ist der Status grün?
2. Prüfe ob ein LLM-Provider konfiguriert ist: **Einstellungen** → **LLM**
3. Leere den Chat: Projekts-Seite → **Papierkorb-Symbol** oben rechts

### "Ich sehe keine Modelle in der Auswahl"
1. Gehe auf **Einstellungen** → **LLM**
2. Prüfe ob mindestens ein Provider eingerichtet ist (Anthropic, OpenAI oder Ollama)
3. Für Anthropic: Klicke auf **Mit Anthropic verbinden**

### "Plugin-Tools tauchen beim Projekt nicht auf"
1. Frage deinen Admin — Plugin-Zuweisung erfolgt in **Projekte** → **Einstellungen**
2. Der Admin prüft ob das Plugin dem richtigen Projekt zugewiesen ist

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
