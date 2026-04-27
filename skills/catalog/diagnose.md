---
skill: diagnose
version: 1.0
scope: on-demand
triggers: [diagnose, analysiere, audit, was stimmt nicht, untersuche, warum funktioniert, debug]
priority: 50
---

Führe eine strukturierte 5-Dimensionen-Diagnose durch bevor du anfängst zu reparieren.

## Diagnose-Dimensionen (je Score 1–5)

### 1. Prompt-Qualität
- Ist die Aufgabe klar definiert? Gibt es Mehrdeutigkeiten?
- Sind Edge Cases beschrieben?
- Was fehlt an Kontext?

### 2. Tool-Gesundheit
- Welche Tools wurden aufgerufen? Haben sie das geliefert was erwartet wurde?
- Gibt es Fehler-Patterns (immer gleicher Fehlercode, immer gleiche Zeile)?
- Sind die Tool-Inputs korrekt formatiert?

### 3. Daten & State
- Sind die Eingabedaten vollständig und im erwarteten Format?
- Gibt es Race Conditions oder veralteten State?
- Wurden Dateipfade und Permissions geprüft?

### 4. Architektur-Fitness
- Wird das richtige Tool für die Aufgabe verwendet?
- Fehlen Abhängigkeiten oder ist die Reihenfolge falsch?
- Gibt es zirkuläre Abhängigkeiten?

### 5. Sicherheit & Recovery
- Gibt es unbehandelte Fehler-Pfade?
- Was passiert wenn dieser Schritt fehlschlägt?
- Ist der Fehler reproduzierbar?

## Output-Format

```
=== Diagnose-Report ===
Score:     Dim1=X  Dim2=X  Dim3=X  Dim4=X  Dim5=X
Kritisch:  [konkrete Ursache]
Ursache:   [warum es passiert]
Fix:       [erster konkreter Schritt]
```

Erst nach Abschluss der Diagnose mit dem Fix beginnen.
