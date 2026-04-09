# HydraHive Feature Linkage Checklist

Jedes Feature muss diese 10 Punkte erfüllen bevor es als "fertig" gilt.
Ein Feature das nur als Code existiert aber nicht verdrahtet, erreichbar
oder getestet ist, ist NICHT fertig.

## Checkliste

| # | Prüfpunkt | Beschreibung | Wie prüfen |
|---|-----------|-------------|------------|
| 1 | **Code Exists** | Modul/Datei existiert | `ls core/src/hydrahive_core/<modul>.py` |
| 2 | **Compiles** | Keine Syntax-Fehler | `python3 -m py_compile <datei>` |
| 3 | **Imported** | Wird von mindestens einer anderen Datei importiert | `grep -r "from.*<modul>" core/` |
| 4 | **Wired** | Wird tatsächlich aufgerufen (nicht nur importiert) | `grep -r "<funktion>()" core/` |
| 5 | **Triggerable** | Kann durch User-Aktion oder System-Event ausgelöst werden | Manuell testen |
| 6 | **Observable** | Ergebnis ist sichtbar (Log, UI, API-Response) | Logs prüfen / UI testen |
| 7 | **Tested** | Mindestens Smoke-Test auf .181 | Server-Restart + Feature testen |
| 8 | **Documented** | In ARCHITECTURE.md oder CLAUDE.md erwähnt | `grep "<feature>" docs/` |
| 9 | **No Dead Paths** | Keine unreachable Code-Pfade | Code-Review |
| 10 | **Release Ready** | Feature-Flag dokumentiert, Rollback-Plan klar | Commit-Message prüfen |

## Regel

**Feature darf NICHT als "fertig" / Issue "closed" gelten wenn:**
- Wired = NO (Code existiert aber wird nie aufgerufen)
- Triggerable = NO (kein Weg es auszulösen)
- Tested = NO (nie auf einem Server gelaufen)

## Beispiel: BookStack Wiki Integration

| # | Check | Status |
|---|-------|--------|
| 1 | Code Exists | ✅ plugins/bookstack-manager/plugin.py |
| 2 | Compiles | ✅ py_compile OK |
| 3 | Imported | ✅ PluginManager lädt es |
| 4 | Wired | ✅ 6 Tools registriert, API-Endpoints in main.py |
| 5 | Triggerable | ✅ Agent kann bookstack_search/create/log_lesson aufrufen |
| 6 | Observable | ✅ Wiki-Seiten erscheinen in BookStack UI |
| 7 | Tested | ✅ 250+ Seiten importiert, Lesson Learned geschrieben |
| 8 | Documented | ✅ In ARCHITECTURE.md Abschnitt 6.6 |
| 9 | No Dead Paths | ✅ Alle 6 Tools erreichbar |
| 10 | Release Ready | ✅ Config in Settings → Wiki, kein Feature-Flag nötig |

## Anwendung

Bei jedem neuen Feature VOR dem Issue-Close:
1. Checklist durchgehen
2. Alle 10 Punkte mit ✅ oder ❌ markieren
3. Bei ❌ auf Wired/Triggerable/Tested: Issue NICHT schließen

## Automatischer Check (geplant)

```bash
# Prüft ob ein Modul verdrahtet ist
python3 scripts/check-linkage.py <modul_name>
```
