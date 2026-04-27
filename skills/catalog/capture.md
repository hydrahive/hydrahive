---
skill: capture
version: 1.0
scope: on-demand
triggers: [capture, session speichern, stand festhalten, dokumentieren, zusammenfassung speichern, merke dir]
priority: 50
---

Persistiere den aktuellen Session-Stand in Agent-Memory für spätere Sessions.

## Was zu erfassen ist

1. **Abgeschlossen** — welche Tasks wurden fertiggestellt (mit Ergebnis)
2. **Entscheidungen** — welche Architektur-/Design-Entscheidungen wurden getroffen und warum
3. **Dateien** — welche Dateien wurden erstellt, geändert, gelöscht
4. **Probleme** — offene Bugs, Risiken, bekannte Lücken
5. **Nächste Schritte** — konkrete actionable Folge-Aufgaben

## Speicherung

Nutze `write_memory` mit einem Dateinamen im Format `session-{datum}-{thema}.md`.

## Output-Struktur

```markdown
# Session {datum} — {thema}

## Abgeschlossen
- [Task]: [Ergebnis]

## Entscheidungen
- [Entscheidung]: [Begründung]

## Geänderte Dateien
- [Pfad]: [was geändert]

## Offen / Risiken
- [Problem oder Risiko]

## Nächste Schritte
1. [konkrete Aktion]
```

## Regeln

- Keine rohen Prompts speichern — nur Ergebnisse und Entscheidungen
- Bestehende Session-Dateien nie überschreiben — neuen Namen wählen
- Nächste Schritte sind Pflicht — ohne sie ist capture wertlos
