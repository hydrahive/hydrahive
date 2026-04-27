---
skill: zero-defect
version: 1.0
scope: on-demand
triggers: [zero-defect, nochmal prüfen, vor dem commit, checkliste, abschließen, fertig melden, alles gecheckt]
priority: 40
---

Aktiviere maximale Präzision vor dem Abschluss einer Aufgabe. Diese Regeln gelten für jede Aktion bis die Aufgabe als erledigt gilt.

## Pflicht-Checkliste vor "fertig"

- [ ] Alle geänderten Dateien nochmal gelesen (keine Annahmen aus dem Gedächtnis)
- [ ] `py_compile` / TypeScript-Check / Syntax-Validator für jede geänderte Datei ausgeführt
- [ ] Service nach Restart sauber hochgekommen (`systemctl is-active` + Logs)
- [ ] Endpoint / Feature tatsächlich getestet, nicht nur "sollte funktionieren"
- [ ] Edge Cases bedacht: leere Eingabe, fehlende Datei, Network-Fehler
- [ ] Fehlerbehandlung vorhanden — kein unbehandelter Exception-Pfad
- [ ] Scope geprüft: nur erwartete Dateien verändert (`git status`)

## Verbotene Patterns

- Datei editieren ohne sie vorher gelesen zu haben
- "Fertig" melden ohne Verifikations-Command ausgeführt zu haben
- Kleines Change = kein Test nötig — **falsch**, jede Änderung bekommt einen Check
- Unsicherheit mit Selbstvertrauen übertünchen — Unklarheiten offen ansprechen

## Bei Fehlschlag

Nicht weitermachen. Fehler sofort melden mit exaktem Text — nicht umschreiben oder beschönigen.
