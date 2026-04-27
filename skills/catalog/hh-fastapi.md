---
skill: hh-fastapi
version: 1.0
scope: on-demand
triggers: [fastapi, pydantic, route, endpoint, 422, register_routes, async, python backend]
priority: 50
---

HydraHive FastAPI / Python Regeln — bekannte Fallstricke.

## Pydantic Body-Models

**Immer auf Modul-Ebene definieren**, nie innerhalb einer `register_routes()`-Funktion:

```python
# FALSCH — silent 422 auf alle Requests
def register_routes(router):
    class MyBody(BaseModel):
        name: str
    @router.post("/foo")
    def foo(body: MyBody): ...

# RICHTIG
class MyBody(BaseModel):
    name: str

def register_routes(router):
    @router.post("/foo")
    def foo(body: MyBody): ...
```

## Runtime-State darf Core-Start nie blockieren

Neues Verzeichnis / neue ENV / neue DB-Migration braucht:
1. Eintrag in `installer/modules/` UND `update.sh`
2. `try/except` im Import — nie hart crashen
3. Graceful Degradation — Core läuft, Feature gibt sauberen Fehler

**Wer den Core beim Start killt, killt auch den Web-Update-Pfad → Server nicht mehr heilbar.**

## Nach async-Umbau

Nach `async with` → `asyncio.create_task` Umbau:
- Einrückung des nachfolgenden Codes prüfen
- Dann `py_compile` ausführen

## Syntax-Check vor Push

```bash
python -m py_compile core/src/hydrahive_core/geänderte_datei.py
```

`py_compile` = OK reicht nicht für Laufzeit-Fehler (NameError, ImportError). Immer auch Restart-Test auf .177.
