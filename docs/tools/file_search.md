# file_search — Inhaltssuche mit grep

Durchsucht Datei-Inhalte im Projektverzeichnis nach einem Textmuster.

## Engine

GNU `grep -rn --include=<file_pattern>` — kein Python-Regex.

## Pattern-Sprache (BRE)

| Syntax | Bedeutung | Beispiel |
|--------|-----------|---------|
| `wort` | Literale Suche (einfachster Fall) | `estimate_tokens` |
| `.` | Genau ein beliebiges Zeichen | `a.c` findet `abc`, `a1c` |
| `.*` | Beliebig viele Zeichen (auch leer) | `fehler.*behand` |
| `^` | Zeilenanfang (anchoren) | `^def ` |
| `$` | Zeilenende | `fest$` |
| `\` | Escape-Sequenz | `\.` = Punkt, `\*` = Stern |
| `[abc]` | Zeichenklasse | `[0-9]` = Ziffer |
| `\b` | Wortgrenze (GNU grep) | `\bimport\b` |

**Wichtig:** `+`, `?`, `|` sind in BRE **keine Quantoren** — sie müssen escaped werden oder matchen sich selbst als Literale. Für ODER-Logik: `foo\|bar`. Für „ein oder mehr": `foo\+`.

Einfache Substring-Suche: Wort direkt eingeben, kein Regex nötig.

## Parameter

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|-------------|
| `pattern` | string | — | Suchmuster (BRE). **Pflicht.** |
| `path` | string | Projekt-Root | Verzeichnis oder Datei, relativ zum Projekt-Root |
| `file_pattern` | string | `*.py` | Glob-Filter für Dateinamen. `*.md` = nur Markdown, leer = alle |
| `max_results` | int | 50 | Max. Treffer (Min: 20, Max: 200) |

## Pfad-Auflösung

- `path` ist **immer relativ** zum Projekt-Root (`/projects/{project_id}`)
- Code in eingebundenen Repos liegt typischerweise unter `repo/` — also `path="repo/core/src"` nutzen
- Absolute Pfade werden gegen das Projekt-Root geprüft; Pfade ausserhalb werden **blockiert**

## Rückgabe

```json
{
  "matches": [
    {"file": "src/agent.py", "line": 42, "text": "def estimate_tokens(...)"},
    {"file": "src/agent.py", "line": 87, "text": "estimate_tokens(msg)"}
  ],
  "count": 2,
  "pattern": "estimate_tokens",
  "search_dir": "/projects/hydrahive-coding/repo"
}
```

`file` ist relativ zum `search_dir`. `text` ist auf 200 Zeichen gekürzt.

## Beispiele

**1. Einfache Wortsuche in Python-Files:**
```
pattern="estimate_tokens"
```
Sucht literales `estimate_tokens` in allen `*.py` — der Normalfall.

**2. Nur in einem Unterverzeichnis:**
```
pattern="TODO"
path="repo/core/src"
file_pattern="*.py"
```
Findet alle TODOs in Python-Files unter `repo/core/src/`.

**3. Markdown und Docs durchsuchen:**
```
pattern="Sicherheit"
path="repo/docs"
file_pattern="*.md"
max_results=30
```

**4. Regex-Anfang/Ende matchen:**
```
pattern="^def "
```
Findet Funktionsdefinitionen (nur wenn `def` am Zeilenanfang steht).

**5. Glob-Muster im file_pattern:**
```
pattern="password"
file_pattern="*.{py,yaml,json}"
```
Durchsucht Python, YAML und JSON — aber nicht `.md` oder `.txt`.

**6. Wortgrenze nutzen (keine Teiltreffer):**
```
pattern="\bimport\b"
```
Matcht `import` aber nicht `important` oder `reimport`.

## Grenzen & Fallstricke

- **BRE ≠ PCRE**: `+` und `?` sind keine Quantoren. `foo+` matcht `fo` + ein oder mehr `o`. Für „ein oder mehr": `foo\+`. Für „optional": `foo\?`.
- **Keine ODER-Gruppen à la `(foo|bar)`**: ODER ist `foo\|bar`. Klammern funktionieren nicht wie in PCRE.
- **Timeout**: Suche bricht nach 30s ab — relevant bei sehr grossen Repos.
- **file_pattern default `*.py`**: Vergisst man den Parameter, wird nur Python durchsucht — auch in `.md`-Dateien suchen erfordert explizites `file_pattern="*.md"` oder leer lassen.
- **max_results hart gedeckelt**: Auch wenn mehr Treffer existieren, werden max. 200 zurückgegeben (intern holt HydraHive `max_results * 3` vom grep, schneidet aber auf `max_results`).
- **Returncode 1 = keine Treffer**: Kein Fehler, sondern leere Ergebnisliste. Die Doku ist hier nicht eindeutig — das ist ein Bug.