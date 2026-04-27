---
skill: hh-commit
version: 1.0
scope: on-demand
triggers: [commit, commiten, git commit, commit message, einchecken]
priority: 50
---

HydraHive Commit-Konventionen.

## Format

```
<typ>: <titel ≤ 70 Zeichen>

<body: warum, nicht was — was steht im Diff>

Co-Authored-By: HydraHive Bot <bot@hydrahive.org>
```

## Typ-Präfixe

| Präfix | Wann |
|---|---|
| `fix:` | Bug behoben |
| `feat:` | Neue Funktionalität |
| `refactor:` | Umstrukturierung ohne Verhaltensänderung |
| `docs:` | Nur Dokumentation |
| `test:` | Nur Tests |
| `chore:` | Build, Dependencies, Config |

## Regeln

- Titel beschreibt das WAS in ≤ 70 Zeichen
- Body erklärt das WARUM (Titel + Body zusammen = vollständiges Bild)
- Signatur `Co-Authored-By: HydraHive Bot <bot@hydrahive.org>` immer dabei
- Nur Dateien stagen die zum Scope gehören — nie `git add .` blind
- Kein `--no-verify`

## Beispiel

```
fix: server_file_write ARG_MAX-Fehler bei großen Dateien

printf %s mit 10MB base64 überschreitet OS-Limit (~2MB).
Base64 jetzt via SSH stdin übergeben statt als Shell-Argument.

Co-Authored-By: HydraHive Bot <bot@hydrahive.org>
```
