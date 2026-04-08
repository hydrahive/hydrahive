# HydraHive Complete Test Plan

## Ziel

Dieser Testplan ist dafür gedacht, von einer KI per SSH schrittweise abgearbeitet zu werden, um HydraHive möglichst vollständig zu testen:

1. während des Einbaus neuer Features
2. nach größeren Änderungen
3. vor Releases
4. zur Suche nach "vergessenen" oder nicht sauber verlinkten Features

Der Plan ist absichtlich redundant. Das ist hier ein Vorteil, nicht ein Nachteil.

## Kernproblem, das dieser Plan löst

In HydraHive können Features bereits im Code existieren, aber:

- nicht in den Hauptflow eingebunden sein
- nicht im UI auftauchen
- nicht über API/Router erreichbar sein
- nicht im Orchestrator verwendet werden
- nicht im Agentenprompt landen
- nicht mehr referenziert sein
- nur teilweise angeschlossen sein

Deshalb reicht "Tests grün" nicht aus. Der Plan kombiniert:

1. Codepfad-Tests
2. API-Tests
3. Integrations-Tests
4. Verhaltens-Tests
5. Linkage-/Wiring-Tests
6. Explorations-Checks für tote oder verwaiste Features

## Testmodi

Es gibt drei Modi:

### Modus A: Fast Gate

Verwenden:
- bei jeder kleinen Änderung

Ziel:
- schnelle Sicherheitsprüfung in wenigen Minuten

### Modus B: Feature Build Loop

Verwenden:
- während des Einbaus eines neuen Features

Ziel:
- laufend prüfen, ob das Feature nur existiert oder wirklich verdrahtet ist

### Modus C: Full System Audit

Verwenden:
- vor Merge größerer Themen
- vor Release
- nach Refactors

Ziel:
- vollständiger End-to-End-Test inklusive Wiring-Prüfung

## Grundregel für die KI

Die KI soll nicht nur prüfen, ob etwas implementiert ist, sondern ob es über den echten Produktpfad erreichbar ist.

Für jedes Feature sind immer diese fünf Fragen zu beantworten:

1. Existiert der Code?
2. Ist er referenziert?
3. Ist er ausführbar?
4. Ist er im Hauptflow verdrahtet?
5. Ist das Ergebnis für Nutzer oder Folgekomponenten sichtbar?

Wenn eine dieser Fragen mit `nein` beantwortet wird, gilt das Feature als nicht vollständig integriert.

## Artefakte, die die KI pro Testlauf erzeugen soll

Die KI soll bei jedem Durchlauf folgende Dateien erzeugen:

1. `test_run_summary.md`
   Enthält:
   - Datum/Zeit
   - Commit/Branch
   - Testmodus
   - Gesamtstatus

2. `test_findings.md`
   Enthält:
   - Fehler
   - Risiken
   - tote Features
   - unverdrahtete Features
   - ungetestete Bereiche

3. `feature_linkage_report.md`
   Enthält pro Feature:
   - Code gefunden
   - Referenzen gefunden
   - API/CLI/UI erreichbar
   - E2E testbar
   - Status `OK`, `PARTIAL`, `MISSING_LINK`, `DEAD_CODE`

## Ablaufübersicht

Der Plan ist in 10 Testblöcke gegliedert:

1. Workspace and Environment
2. Static Wiring Audit
3. Unit and Module Tests
4. API Surface Tests
5. Orchestrator Flow Tests
6. Agent and Worker Tests
7. Tooling and Integration Tests
8. Memory and Context Tests
9. Failure and Recovery Tests
10. End-to-End Scenario Tests

## 1. Workspace and Environment

Ziel:
- sicherstellen, dass der Testlauf reproduzierbar ist

### Schritte

1. Git-Status erfassen
2. aktuelle Branch erfassen
3. relevante Env Vars erfassen
4. Dienste und Abhängigkeiten erfassen
5. Python-/Node-/Systemversionen erfassen
6. verfügbare externe Systeme erfassen
   Beispiele:
   - Redis
   - Qdrant
   - Ollama
   - Matrix
   - Discord
   - MCP Server

### Erfolgskriterien

- Laufumgebung ist dokumentiert
- fehlende externe Abhängigkeiten sind sichtbar

### KI-Ausgabe

- Tabelle `environment_inventory`

## 2. Static Wiring Audit

Ziel:
- Features finden, die im Code existieren, aber nicht oder falsch verdrahtet sind

Das ist der wichtigste Block für dein beschriebenes Problem.

### Schritte

1. Alle Kernmodule erfassen
   Bereiche:
   - Orchestrator
   - Sessions
   - Memory
   - Tools
   - Router
   - Plugins
   - Worker
   - MCP
   - Verification
   - Worktree
   - Context Lifecycle

2. Für jedes neue oder relevante Symbol prüfen:
   - wo definiert?
   - wo importiert?
   - wo aufgerufen?
   - welcher Hauptpfad erreicht es?

3. Speziell suchen nach:
   - nie importierten Modulen
   - nie aufgerufenen Funktionen
   - registrierten aber nie genutzten Tools
   - Router-Endpunkten ohne produktiven Aufrufer
   - Config-Feldern ohne Runtime-Nutzung
   - Worker-Profilen ohne Dispatch-Pfad
   - Promptbausteinen ohne Einbindung

4. Prüfen, ob neue Features an allen erwarteten Stellen verlinkt sind:
   - Config
   - Import
   - Registry
   - Router
   - Orchestrator
   - UI/API
   - Tests
   - Dokumentation

### Suchmuster für die KI

Die KI sollte explizit per SSH suchen nach:

- Klassendefinitionen
- Factory-Registrierungen
- `register(`
- Router-Deklarationen
- Imports
- Referenzen des Feature-Namens
- Konfigurationsschlüsseln
- API-Feldern
- UI-Komponenten

### Erfolgskriterien

- jedes relevante Feature hat einen dokumentierten Hauptpfad
- tote oder halbverdrahtete Features sind markiert

### Statusklassen

- `OK`
- `PARTIAL`
- `MISSING_LINK`
- `DEAD_CODE`

## 3. Unit and Module Tests

Ziel:
- Grundlogik isoliert prüfen

### Bereiche

1. `session_manager`
2. `orchestrator_llm`
3. `orchestrator_context`
4. `tool_registry`
5. `memory_search`
6. `learning_memory`
7. neue Runtime-Module

### Schritte

1. alle vorhandenen Unit-Tests ausführen
2. fehlende Testabdeckung identifizieren
3. bei neuen Features prüfen:
   - gibt es Unit-Tests?
   - decken sie Happy Path und Failure Path ab?

### Erfolgskriterien

- Unit-Tests laufen
- neue Features haben mindestens Basisabdeckung

## 4. API Surface Tests

Ziel:
- prüfen, ob Features über echte Endpunkte erreichbar sind

### Schritte

1. API-Routen inventarisieren
2. für jede Hauptfunktion prüfen:
   - gibt es einen Endpunkt?
   - antwortet er?
   - ist Auth korrekt?
   - ist die Payload vollständig?

3. Speziell testen:
   - Agenten CRUD
   - Sessions
   - Chat/Stream
   - Tools
   - Skills
   - Projects
   - LLM config
   - Hooks
   - Verification
   - neue Feature-Endpunkte

### Erfolgskriterien

- alle kritischen Endpunkte antworten sinnvoll
- neue Features sind nicht nur im Code, sondern auch über API sichtbar, wenn sie sichtbar sein sollen

## 5. Orchestrator Flow Tests

Ziel:
- Hauptarbeitsweise des Systems prüfen

### Prüfszenarien

1. einfache User-Nachricht ohne Tools
2. User-Nachricht mit Toolnutzung
3. Tool-Fehler im Flow
4. Delegation an Worker
5. Antwortaggregation
6. Streaming-Antwort
7. Fallback-Modell
8. Retry nach transientem Fehler

### Pro Szenario prüfen

1. Session wird korrekt angelegt
2. User-Nachricht wird gespeichert
3. Systemprompt wird gebaut
4. richtige Tools werden angeboten
5. Toolausführung wird korrekt verarbeitet
6. Ergebnis landet wieder im Flow
7. Antwort wird persistiert

### Erfolgskriterien

- der Boss-Flow funktioniert nicht nur nominal, sondern auch mit Tool- und Fehlerpfaden

## 6. Agent and Worker Tests

Ziel:
- Delegation vollständig prüfen

### Prüfszenarien

1. Worker wird gefunden
2. Worker wird gestartet
3. Worker erhält richtigen Kontext
4. Worker bekommt richtige Toolmenge
5. Worker liefert Ergebnis zurück
6. Boss kann mehrere Worker aggregieren
7. Worker-Failures werden sauber behandelt

### Zusätzlich bei spezialisierten Workern

Für jeden Worker prüfen:

1. ist er registriert?
2. ist er dispatchbar?
3. ist sein Promptvertrag aktiv?
4. ist sein Outputformat stabil?
5. ist er im Boss-Flow wirklich nutzbar?

### Erfolgskriterien

- Worker existieren nicht nur auf dem Papier, sondern sind real dispatchbar

## 7. Tooling and Integration Tests

Ziel:
- alle Werkzeuge und externe Integrationen real prüfen

### Toolklassen

1. Filesystem
2. Git
3. Shell
4. HTTP/Web
5. MCP
6. Memory
7. Notification
8. Admin/Project
9. Verification
10. Worktree

### Pro Tool prüfen

1. registriert?
2. erlaubbar?
3. aufrufbar?
4. Ergebnisformat korrekt?
5. Fehlerformat korrekt?
6. im Orchestrator nutzbar?

### Für jede externe Integration prüfen

1. Verbindung möglich?
2. Fehler klar?
3. Fallback klar?
4. Timeouts sauber?
5. Logging ausreichend?

### Erfolgskriterien

- jedes Tool ist auf Registry-, Runtime- und Flow-Ebene testbar

## 8. Memory and Context Tests

Ziel:
- das Systemgedächtnis und Kontextverhalten prüfen

### Prüfszenarien

1. Memory-Index wird erstellt
2. Memory-Suche liefert Treffer
3. Learning Memory speichert Snapshots
4. Systemprompt enthält relevante Memory-Snippets
5. Skill-Auswahl funktioniert
6. Kontextbudget greift
7. Context compaction greift
8. Prompt caching greift

### Speziell bei neuen Features

Wenn `context_lifecycle` eingebaut wird, prüfen:

1. Tool-Result-Budgeting
2. Microcompact
3. Summary compact
4. overflow retry
5. cache-break diagnostics

### Erfolgskriterien

- Kontexte wachsen kontrolliert
- relevante Informationen bleiben erhalten
- Cache-Verhalten ist beobachtbar

## 9. Failure and Recovery Tests

Ziel:
- prüfen, wie robust HydraHive unter Fehlern ist

### Fehlerklassen

1. fehlende Tokens
2. API 401
3. API 429
4. API 5xx
5. Timeout
6. Toolfehler
7. Workerfehler
8. Stream-Abbruch
9. Session-Abbruch
10. defekte externe Integration

### Pro Fehlerklasse prüfen

1. wird der Fehler erkannt?
2. ist die Fehlermeldung verständlich?
3. erfolgt Retry?
4. erfolgt Failover?
5. erfolgt Recovery?
6. bleibt Session konsistent?

### Erfolgskriterien

- Fehlerpfade sind nicht nur vorhanden, sondern kontrolliert

## 10. End-to-End Scenario Tests

Ziel:
- echte Nutzerpfade von Anfang bis Ende prüfen

### Pflichtszenarien

1. einfacher Chat
2. Coding Task mit Dateiänderung
3. Coding Task mit Toolnutzung
4. Mehragenten-Task
5. Recherche-Task mit Web/MCP
6. Langlauf-Task mit viel Kontext
7. Task mit Verification
8. Task mit Worktree-Isolation
9. Recovery nach Fehler
10. Session-Fortsetzung

### Pro E2E-Szenario prüfen

1. Eingabe akzeptiert?
2. Routing korrekt?
3. Orchestrator korrekt?
4. Tools korrekt?
5. Antwort korrekt?
6. Artefakte sichtbar?
7. Logs/Status korrekt?

### Erfolgskriterien

- reale Produktpfade funktionieren komplett

## Zwei Prüfarten pro Feature

Für jedes Feature sind immer zwei Tests nötig:

### Test A: Existence Test

Frage:
- existiert die Implementierung?

### Test B: Reachability Test

Frage:
- kann ein echter Nutzerpfad das Feature tatsächlich auslösen?

Erst wenn beide Tests positiv sind, gilt ein Feature als integriert.

## Spezieller "Forgotten Link" Check

Das hier ist der Spezialteil gegen vergessene Verdrahtung.

Für jedes neue Feature muss die KI diese Matrix ausfüllen:

1. Code vorhanden
2. importiert
3. registriert
4. konfigurierbar
5. Orchestrator nutzt es
6. API/Router exponiert es
7. UI oder Client kann es erreichen
8. Logging sichtbar
9. Tests vorhanden
10. Doku erwähnt es

Wenn weniger als 8 von 10 erfüllt sind, Status:

- `PARTIAL`

Wenn Code existiert, aber Hauptpfad fehlt:

- `MISSING_LINK`

Wenn Code existiert, aber gar nichts darauf zeigt:

- `DEAD_CODE`

## Testrhythmus während des Einbaus

Während ein Feature eingebaut wird, soll die KI nicht erst am Ende testen.

### Nach jeder dieser Phasen testen

1. nach Core-Implementierung
2. nach Verdrahtung in Registry/Router/Orchestrator
3. nach UI/API-Anbindung
4. nach erstem E2E-Pfad
5. nach Failure-Path

### Minimaler Build-Loop

Für jedes neue Feature:

1. Static Wiring Audit
2. Unit Test
3. API/Flow Test
4. End-to-End Trigger Test
5. Forgotten Link Matrix

## Vollständiger Release-Testplan

Vor einem Release soll die KI diese Reihenfolge durchgehen:

1. Environment inventory
2. Static wiring audit
3. vollständige Unit- und Integrationstests
4. API surface test
5. orchestrator flow test
6. worker test
7. tool and integration test
8. memory/context test
9. failure/recovery test
10. vollständige E2E-Szenarien
11. finaler linkage report

## Exit-Kriterien

Ein Full System Audit ist nur dann bestanden, wenn:

1. keine `DEAD_CODE`-Features in kritischen Bereichen verbleiben
2. keine `MISSING_LINK`-Features in neuen Features verbleiben
3. alle Pflicht-E2E-Szenarien bestanden sind
4. Recovery-Tests für Hauptfehlerpfade bestanden sind
5. Findings dokumentiert und priorisiert sind

## Empfohlene Testberichte

Die KI soll Findings immer in diese Klassen sortieren:

1. `BLOCKER`
   - Feature kaputt
   - Hauptpfad nicht erreichbar
   - schwere Recovery-Lücke

2. `MAJOR`
   - Feature teilweise verdrahtet
   - Verhalten inkonsistent
   - wichtige Tests fehlen

3. `MINOR`
   - Logging/Doku/UI unvollständig

4. `OBSERVATION`
   - interessante tote Pfade
   - Altlasten
   - Refactor-Kandidaten

## Standard-Szenariokatalog für die KI

Diese Szenarien sollte die KI immer verwenden:

1. "sage nur hallo"
2. "lies Datei und fasse zusammen"
3. "ändere eine Datei"
4. "nutze Tool und gib Ergebnis zurück"
5. "delegiere an Worker"
6. "nutze Memory"
7. "provoziere Retry"
8. "provoziere Toolfehler"
9. "führe Verification aus"
10. "setze Session fort"

## Was die KI ausdrücklich nicht tun darf

Beim Testen darf die KI nicht nur grüne Tests sammeln und daraus schließen, dass alles integriert ist.

Sie muss aktiv nach diesen Dingen suchen:

1. implementiert aber unerreichbar
2. dokumentiert aber unverdrahtet
3. registriert aber nie angeboten
4. konfigurierbar aber wirkungslos
5. getestet aber nicht im Hauptpfad

## Praktische Nutzung

Die beste Nutzungsform ist:

### Während des Einbaus

- Modus B nach jedem nennenswerten Integrationsschritt

### Täglich oder vor Merge

- Modus A

### Vor Release oder nach großem Umbau

- Modus C

## Schluss

Wenn du verhindern willst, dass eingebaute Dinge wieder "verschwinden", dann muss die KI nicht nur Features testen, sondern ihre gesamte Verkabelung prüfen.

Genau dafür ist dieser Plan da:

Er testet nicht nur Funktion, sondern Existenz, Erreichbarkeit, Integration und Sichtbarkeit.
