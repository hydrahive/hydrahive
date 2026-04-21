"""
test_808_user_message_rollback_guard.py — Regression (#808)

Im Streaming-Pfad wird die User-Message vor dem LLM-Call in die Session
geschrieben. Schlägt der LLM-Call fehl, muss das pop_last-Rollback nur
dann laufen, wenn die Message wirklich vorher gespeichert wurde — sonst
würden wir fremde vorherige Messages entfernen (lost update).

#808-Audit behauptete dieser Guard fehle; der Guard ist seit e943f33
(07.04.) vorhanden. Dieser Test hält die Invariante fest.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


STREAM_PY = Path(__file__).parent.parent / "src" / "hydrahive_core" / "orchestrator_stream.py"


def test_pop_last_gated_by_user_msg_saved():
    """pop_last darf NUR aufgerufen werden, wenn user_msg_saved True ist."""
    source = STREAM_PY.read_text(encoding="utf-8")

    # Alle pop_last-Aufrufe finden und prüfen, dass unmittelbar davor ein
    # `if user_msg_saved:` (in derselben oder vorheriger Zeile) steht.
    pattern = re.compile(
        r"(?P<pre>.*\n){0,3}?[ \t]*if user_msg_saved:\s*\n"
        r"[ \t]+await orch\._sessions\.pop_last\(",
        flags=re.MULTILINE,
    )
    all_popcalls = list(re.finditer(r"orch\._sessions\.pop_last\(", source))
    guarded = list(pattern.finditer(source))

    assert all_popcalls, "Keine pop_last-Aufrufe gefunden — Stream-Code refactored?"
    assert len(guarded) == len(all_popcalls), (
        f"{len(all_popcalls)} pop_last-Aufruf(e), davon nur {len(guarded)} "
        f"hinter `if user_msg_saved:` — #808-Regression!"
    )


def test_user_msg_saved_flag_initialized_to_false():
    """Flag muss vor dem Append auf False initialisiert sein, sonst greift
    der Guard nicht bei Fehlern im Append selbst."""
    source = STREAM_PY.read_text(encoding="utf-8")
    assert "user_msg_saved = False" in source, \
        "user_msg_saved wird nicht mehr initialisiert — Guard kann leeren Zustand nicht erkennen"
    # Es muss auch ein True-Setter existieren nach dem append
    assert "user_msg_saved = True" in source, \
        "user_msg_saved = True fehlt — Flag wird nie gesetzt, Rollback würde NIE laufen"
