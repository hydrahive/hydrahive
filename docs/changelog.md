# OctopOS Changelog

## 2026-03-23

### Fixed

- `fix(console): handle expired my-agent sessions`
- `My Agent` zeigte bei abgelaufenem oder ungültigem JWT nur leere Tabs.
- Die Konsole meldet 401-Antworten jetzt sichtbar als abgelaufene Sitzung und loggt den Nutzer sauber aus.
- Betroffene Bereiche:
  - `console/src/lib/api.ts`
  - `console/src/hooks/useAuth.tsx`
  - `console/src/pages/MyAgentPage.tsx`

### Result

- Einstellungen, Skills und MCP für `Mein Agent` sind bei gültiger Sitzung wieder sichtbar.
- Abgelaufene Browser-Sessions landen nicht mehr stillschweigend auf leeren Seiten.

### Fixed

- `fix(core): improve platform status reporting for WKS and Discord`
- WKS wird im Plattform-Überblick nicht mehr nur über die konfigurierte IP als verbunden markiert.
- Discord-Status behandelt `is_connected` jetzt robust als Property oder Callable.
- Betroffene Bereiche:
  - `core/src/octopos_core/router_user_integrations.py`
  - `core/tests/test_security_regressions.py`

### Result

- Der Plattform-Überblick zeigt Konfiguration und echte Verbindung sauberer getrennt an.
- WKS und Discord liefern keine falschen Positivstatus mehr durch reine Konfigurationswerte.

### Fixed

- `fix(core): harden rate limiter, learning memory and issue tooling`
- Redis-Failover im Rate-Limiter kann nach einer Abklingzeit wieder in den Redis-Modus zurückkehren.
- Lern-Memory-Schreibvorgänge sind jetzt serialisiert, damit parallele Kompaktionen keine widersprüchlichen Einträge erzeugen.
- Projekt-, System- und Memory-Schreibtools schließen ihre Dateihandles sauber.
- Gitea-Issue-Tools prüfen Titel- und Body-Längen vor dem Senden.
- WKS Shell-Execution übernimmt die Shell-Blockliste, damit destruktive Kommandos dort nicht ungefiltert durchrutschen.

### Result

- Redis-Recovery bleibt nach einem Ausfall nicht dauerhaft im lokalen Fallback hängen.
- Lernnotizen sind robuster bei parallelen Writes.
- Issue-Erstellung und WKS-Shell-Ausführung sind gegen offensichtliche Missbrauchsfälle besser abgesichert.

### Fixed

- `fix(core): block shell subshell bypasses in shell tools`
- Shell- und WKS-Shell-Ausführung blockieren jetzt Command Substitution (`$(...)`), Backticks und rekursive Wrapper wie `bash -c` sauberer.
- Die Blockliste greift damit auch bei verschachtelten Shell-Aufrufen, die destructive Kommandos verstecken würden.
- Betroffene Bereiche:
  - `core/src/octopos_core/tool_registry.py`
  - `core/tests/test_security_regressions.py`

### Result

- Der letzte offene Security-Punkt aus dem Deep-Dive ist geschlossen.
- Subshell-basierte Umgehungsversuche greifen nicht mehr durch die bisherige Blocklisten-Lücke.

## 2026-03-23 — Hygiene-Block: Supply-Chain, Test-Teardown, Runtime-Audit

### Fixed

- `fix(amem): add commit-pin support and HEAD-hash logging to install_amem.sh`
- `git clone --depth 1 origin/main` ohne Commit-Pin ist ein Supply-Chain-Risiko: der Installer zieht immer den aktuellen HEAD, ohne dass der installierte Commit nachvollziehbar ist.
- Neu: optionale Umgebungsvariable `AMEM_COMMIT=<sha>` ermöglicht reproduzierbaren Builds mit exaktem Commit-Pin.
- Neu: nach jedem Clone/Update wird der tatsächliche HEAD-Commit nach `/var/lib/octopos/amem/installed_commit.txt` geschrieben (Supply-Chain-Transparenz).
- Ohne gesetztes `AMEM_COMMIT` bleibt das bisherige Verhalten erhalten (origin/main), aber der Commit ist jetzt auditierbar.

- `fix(tests): add try/finally teardown to test_agent_lifecycle_end_to_end_roundtrip`
- Der Test manipulierte globale App-State (USERS_FILE, JWT_SECRET, AGENTS_DIR, discovery._dir etc.) ohne `try/finally`-Schutz.
- Bei einem Test-Fehler blieb der State dauerhaft verändert und hätte folgende Tests korrumpiert.
- Fix: gesamter Test-Body in `try/finally` eingebettet; Cleanup ist jetzt garantiert auch bei Exception.

- `fix(core): add 30s in-memory cache to collect_core_journal_report`
- `/admin/runtime/status` und `/logs/core/summary` riefen `collect_core_journal_report()` bei jedem Request neu auf (subprocess + journalctl).
- Timeout (5s) und Zeilenlimit (-n 200) waren bereits vorhanden, aber bei schnellen aufeinanderfolgenden Admin-Requests entstand unnötiger Overhead.
- Fix: einfacher In-Memory-Cache mit 30s TTL; Fehlerfall (journalctl nicht verfügbar) wird nicht gecacht.

### Result

- A-MEM-Installationen sind nachvollziehbar und können auf einen getesteten Commit gepinnt werden.
- Test-Teardown-Hygiene ist gewährleistet: kein dirty State bei Test-Fehlern.
- journalctl-Subprocess wird maximal alle 30s aufgerufen; redundante Calls innerhalb des Fensters kosten nichts.

## 2026-03-23 — fix(amem): A-MEM Commit-Pin gesetzt (Issue #159 geschlossen)

- Geprüfter A-MEM-Stand zum Zeitpunkt der OctopOS-Zertifizierung: `ceffb860f0712bbae97b184d440df62bc910ca8d`
- `AMEM_COMMIT` im Installer auf diesen SHA als Default gesetzt — neue Installationen verwenden ab sofort diesen verifizierten Stand.
- Überschreibbar per Umgebungsvariable `AMEM_COMMIT=<sha>` für kontrollierte Updates.
- Issue #159 damit vollständig geschlossen.
