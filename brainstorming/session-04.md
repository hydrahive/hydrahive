# Session 4 — 19. März 2026

## Teilnehmer
- Till (Product Vision & Anwenderschicht)
- Claude Code (Systemebene & Technische Entscheidungen)

## Thema: User-Support-Flow & Webkonsole-Struktur

## Kernentscheidung: Web-Chat-UI ist ein Matrix-Client

Kein eigener Gateway-Service nötig. Die Web-Chat-UI verbindet sich per
`matrix-js-sdk` direkt mit dem Conduit-Homeserver auf AgentOS. Jeder
AgentOS-User bekommt einen Matrix-Account auf diesem Homeserver und ist
damit ein gleichberechtigter Teilnehmer im Projekt-Room — genau wie die
Agenten-Bots.

```
Web-Chat-UI (matrix-js-sdk)  ─┐
Element Client                ─┤──▶  Conduit  ──▶  #buchhaltung:agentOS.local
Andere Matrix-Clients         ─┘                         │
                                                    Boss-Agent (Bot)
                                                    Worker-Agenten (Bots)
```

Der User merkt nichts von Matrix-Internals — er sieht ein Chat-Interface
und ein Projekt. Die Agenten sehen ihn als weiteren Room-Teilnehmer.

## Element-Eingriff (entschieden)

Till (oder jeder Admin) kann sich mit dem Conduit-Homeserver über Element
verbinden, den Projekt-Room joinen und direkt eingreifen. Aus Sicht der
Agenten ist das ein weiterer Mensch im Room — kein Sonderfall, keine
spezielle Implementierung nötig.

Voraussetzung: Conduit-Zugangsdaten für den AgentOS-Homeserver.
AgentOS erstellt beim Setup automatisch einen Admin-Account.

## Webkonsole-Struktur (entschieden)

Zwei klar getrennte Bereiche:

### Admin-Bereich (REST API gegen AgentOS Core)
- Agenten-Verwaltung: anlegen, bearbeiten, aktivieren/deaktivieren
- Projekt-Verwaltung: anlegen, Agenten zuweisen, Shares konfigurieren
- System-Status: Service-Health, Heartbeats, Logs
- User-Verwaltung: AgentOS-User + Matrix-Accounts anlegen
- LLM-Konfiguration: Ollama-Modelle, API-Keys

### Chat-Bereich (matrix-js-sdk, direkt gegen Conduit)
- Pro Projekt ein Chat-Tab
- Direkte Matrix-Room-Verbindung — kein Proxy, kein Gateway
- Nachrichten-History aus Matrix (persistent)
- Typing indicators, Read receipts — alles was Matrix bietet
- Admin kann zwischen Projekten wechseln

## User-Flow (vollständig)

```
1. User öffnet Web-Console → wählt Projekt "Buchhaltung"
2. Chat-Tab öffnet Matrix-Room #buchhaltung:agentOS.local
3. User schreibt: "Kannst du mir die Umsatzsteuer für Q1 berechnen?"
4. matrix-js-sdk postet Nachricht in den Room
5. Boss-Agent (Lilith) sieht die Nachricht
6. Lilith beauftragt Steuerbert (Worker) mit der Berechnung
7. Steuerbert antwortet im Room
8. Lilith aggregiert und antwortet dem User im Room
9. matrix-js-sdk liefert die Antwort ans Web-Interface
10. User sieht die Antwort — ohne Matrix, ohne Swarm zu kennen
```

Parallel dazu kann Till in Element denselben Room öffnen und den gesamten
Agent-Swarm-Dialog mitverfolgen — oder eingreifen.

## Agenten-Sichtbarkeit im Chat (Design-Entscheidung)

Frage: Sieht der User im Web-Chat nur die Boss-Antworten, oder den
gesamten Swarm-Dialog (Boss beauftragt Steuerbert, Steuerbert antwortet)?

Empfehlung: **Konfigurierbar pro Projekt**
- `chat.show_swarm: false` → User sieht nur Boss-Nachrichten (cleaner UX)
- `chat.show_swarm: true` → User sieht alles (für Debugging/Power-User)

In Element sieht man immer alles — das ist der "Experten-Modus".

## Offene Punkte für Session 5
- GPU & lokale Modelle: Welche Modelle lokal, welche über Cloud?
- Installer-Flow: Welche Schritte, welche Prompts, automatische GPU-Erkennung?
- Modell-Strategie: Wann nimmt ein Agent welches Modell?

---
*Stand: Session 4 — 19. März 2026*
