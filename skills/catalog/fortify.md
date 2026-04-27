---
skill: fortify
version: 1.0
scope: on-demand
triggers: [fortify, fehlerbehandlung, robuster, absichern, error handling, retry, fallback, resilient]
priority: 50
---

Mache bestehenden Code robuster durch strukturierte Fehlerbehandlung auf 5 Ebenen.

## Fortify-Ebenen

### 1. Input-Validierung
- Eingaben am System-Rand validieren (User-Input, externe APIs, Dateien)
- Klare Fehlermeldungen mit erwartetem Format
- Größenlimits setzen

### 2. Retry mit Backoff
- Transiente Fehler (Rate Limits, Timeouts, 5xx): max 3 Versuche, exponentielles Backoff
- **Nicht** retrien: Auth-Fehler (401/403), Validierungsfehler (400/422), nicht-existente Ressourcen (404)
- Max Delay: 30 Sekunden

### 3. Fallback-Responses
- Was passiert wenn Retries scheitern?
- Cache-Antwort / vereinfachtes Modell / Fehler sauber an User melden
- Niemals still schlucken — immer loggen

### 4. Timeouts
- LLM-APIs: 30–120s
- Tool-Calls: 10–60s
- DB-Queries: 5–15s
- Immer explizit setzen, nie auf Default vertrauen

### 5. Fehler-Kommunikation
- User bekommt verständliche Meldung, keinen Stack-Trace
- Logs enthalten Kontext (was wurde versucht, mit welchen Inputs)
- Unterscheide: Benutzer-Fehler vs. System-Fehler vs. externe Fehler

## Prinzip

Fehler früh erkennen, nah an der Ursache behandeln, nie in der Mitte der Call-Chain stumm verschwinden lassen.
