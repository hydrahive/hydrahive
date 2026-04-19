"""
test_browser_evaluate_guard.py — #752: browser_evaluate auf unrestricted beschränkt

Deckt den Execution-Mode-Guard in BrowserEvaluateTool.execute ab.
Playwright wird nicht aufgerufen — der Guard blockt vor dem _get_page.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from hydrahive_core.browser_tools import BrowserEvaluateTool


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.parametrize("mode", [None, "standard", "elevated", "sandboxed", ""])
def test_non_unrestricted_modes_blocked(mode):
    tool = BrowserEvaluateTool()
    kwargs = {}
    if mode is not None:
        kwargs["_execution_mode"] = mode
    out = _run(tool.execute(agent_id="a1", project_id="p1", script="1+1", **kwargs))
    assert "error" in out, f"mode={mode!r} sollte geblockt sein, got {out}"
    assert "unrestricted" in out["error"]
    assert "#752" in out["error"]


def test_unrestricted_not_blocked_by_guard(monkeypatch):
    """Im unrestricted-Mode passiert der Guard den Check —
    der eigentliche Call würde dann Playwright aufrufen. Wir mocken
    _get_page so, dass wir nur den Guard-Pfad testen."""
    tool = BrowserEvaluateTool()
    from hydrahive_core import browser_tools as bt

    class _FakePage:
        async def evaluate(self, script):
            return {"echo": script}

    async def _fake_get_page(agent_id):
        return _FakePage()

    monkeypatch.setattr(bt, "_get_page", _fake_get_page)
    out = _run(tool.execute(
        agent_id="a1", project_id="p1", script="'hi'", _execution_mode="unrestricted",
    ))
    assert "error" not in out, f"unrestricted sollte durch Guard kommen, got {out}"
    assert out == {"result": {"echo": "'hi'"}}


def test_error_mentions_alternatives():
    """Blocker-Message nennt die erlaubten Alternativ-Tools."""
    tool = BrowserEvaluateTool()
    out = _run(tool.execute(agent_id="a1", project_id="p1", script="x"))
    msg = out.get("error", "")
    for alt in ("browser_screenshot", "browser_click", "browser_fill", "browser_navigate"):
        assert alt in msg, f"Alternative {alt!r} fehlt in Error-Message"
