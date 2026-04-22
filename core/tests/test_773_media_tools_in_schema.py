"""
test_773_media_tools_in_schema.py — image/video/music_generate sind im
Tool-Schema des Boss-Agents.

Aus Audit #773: Tools waren registriert + JobService injiziert, aber das
Modell sah sie nicht (always_loaded=False + nicht in _V2_CORE_TOOL_IDS-
Whitelist). Resultat: Agent hat halluziniert "kann ich nicht".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_media_tools_in_v2_core_whitelist():
    from hydrahive_core.orchestrator import Orchestrator
    expected = {"image_generate", "video_generate", "music_generate"}
    assert expected.issubset(Orchestrator._V2_CORE_TOOL_IDS), (
        f"_V2_CORE_TOOL_IDS fehlt Media-Tools: "
        f"{expected - set(Orchestrator._V2_CORE_TOOL_IDS)}"
    )


def test_media_tools_always_loaded():
    """Wenn always_loaded=False waeren, fielen sie aus der Tool-Schema-Liste
    raus und der Agent muesste tool_search rufen — was er von alleine selten
    tut, weshalb er das Tool ignoriert (Halluzination "kann ich nicht")."""
    from hydrahive_core.tool_registry import registry
    for tid in ("image_generate", "video_generate", "music_generate"):
        tool = registry.get(tid)
        assert tool is not None, f"{tid} nicht registriert"
        assert tool.always_loaded is True, (
            f"{tid}.always_loaded={tool.always_loaded} — Tool wird sonst "
            "vom Modell nicht gesehen (Issue #773)."
        )


def test_media_tools_in_always_loaded_list():
    from hydrahive_core.tool_registry import registry
    ids = {t.id for t in registry.always_loaded_tools()}
    for tid in ("image_generate", "video_generate", "music_generate"):
        assert tid in ids, f"{tid} fehlt in registry.always_loaded_tools()"


def test_media_tools_not_in_deferred_list():
    """Konsequenz aus always_loaded=True: kein Eintrag mehr im
    deferred-tools-Block. Sonst doppelt."""
    from hydrahive_core.tool_registry import registry
    deferred_ids = {t.id for t in registry.deferred_tools()}
    for tid in ("image_generate", "video_generate", "music_generate"):
        assert tid not in deferred_ids, f"{tid} ist sowohl always_loaded als auch deferred"
