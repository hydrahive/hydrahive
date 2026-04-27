---
skill: recap
version: 1.0
scope: on-demand
triggers: [recap, wo waren wir, zusammenfassung, weiter machen, session fortsetzen, was war der stand]
priority: 50
---

Erstelle eine kompakte Orientierungs-Zusammenfassung für den Wiedereinstieg in eine Session.

## Datenquellen (in dieser Reihenfolge prüfen)

1. Agent-Memory (`read_memory`) — was wurde dauerhaft gespeichert?
2. Letzten Tool-Calls dieser Session — was wurde zuletzt getan?
3. Workspace-Dateien — gibt es WIP-Marker, TODO-Kommentare, offene Branches?

## Output-Format

```
=== PICK UP HERE ===
Zuletzt:    [was wurde abgeschlossen]
Offen:      [konkrete unfertige Schritte]
Nächster Schritt: [eine klare Aktion]

Kontext:
- [relevante Datei oder Entscheidung 1]
- [relevante Datei oder Entscheidung 2]
```

## Regeln

- Actionable, nicht dokumentarisch — der Fokus liegt auf dem nächsten Schritt
- Keine Vermutungen: wenn der Stand unklar ist, nachfragen statt raten
- Wenn keine Daten vorhanden: direkt sagen und nach Kontext fragen
