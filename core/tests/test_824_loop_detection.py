"""
test_824_loop_detection.py — Bug #824: Loop-Detection Detection Bug

Problem: _tool_call_signature() war whitespace-sensitiv.
Gleiche Args in verschiedenen Formatierungen (z.B. {"path":"foo"} vs
{"path": "foo"}) erzeugten unterschiedliche Signaturen → die Detection
erkannte 3× file_read nicht als Loop.

Root-Cause Fix: JSON-Normalisierung in _tool_call_signature():
args werden mit json.loads + json.dumps(sort_keys=True, separators=(",",":"))
normalisiert → gleiche logischen Args erzeugen identische Signatur.

Threshold bleibt bei 2 (default). threshold=3 wurde revertiert.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_tools import check_repeated_signature, _tool_call_signature


class MockFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments

class MockTC:
    def __init__(self, name, args):
        self.function = MockFn(name, args)


# ---------------------------------------------------------------------------
# Fix verification: JSON whitespace normalization
# ---------------------------------------------------------------------------

def test_signature_whitespace_normalization():
    """
    #824 Fix: verschiedene Whitespace-Formatierungen erzeugen
    dieselbe normalisierte Signatur.

    {"path":"foo"}           → {"path":"foo"}
    {"path": "foo"}          → {"path":"foo"}
    {"path":"foo"}           → {"path":"foo"}  (trailing space)
    {"limit":5,"path":"foo"}  → {"limit":5,"path":"foo"}  (same keys, different order → sort_keys makes equal)
    """
    # whitespace-Variationen mit gleichen Keys
    tc1 = MockTC("file_read", '{"path":"foo"}')
    tc2 = MockTC("file_read", '{"path": "foo"}')
    tc3 = MockTC("file_read", '{"path":"foo"} ')
    # key-order Variation: nur Sinn wenn die Args dieselben Keys haben
    tc4a = MockTC("file_read", '{"path":"foo","limit":5}')
    tc4b = MockTC("file_read", '{"limit":5,"path":"foo"}')

    sig1 = _tool_call_signature([tc1])
    sig2 = _tool_call_signature([tc2])
    sig3 = _tool_call_signature([tc3])
    sig4a = _tool_call_signature([tc4a])
    sig4b = _tool_call_signature([tc4b])

    assert sig1 == sig2, f"sig1 ({sig1!r}) != sig2 ({sig2!r}): whitespace sollte ignoriert werden"
    assert sig1 == sig3, f"sig1 ({sig1!r}) != sig3 ({sig3!r}): trailing space sollte ignoriert werden"
    assert sig4a == sig4b, f"sig4a ({sig4a!r}) != sig4b ({sig4b!r}): key-order sollte durch sort_keys normalisiert werden"


def test_signature_different_args_still_different():
    """Verschiedene Args erzeugen verschiedene Signaturen (keine false positives)."""
    tc1 = MockTC("file_read", '{"path":"foo"}')
    tc2 = MockTC("file_read", '{"path":"bar"}')
    tc3 = MockTC("file_read", '{"path":"foo","limit":8000}')

    sig1 = _tool_call_signature([tc1])
    sig2 = _tool_call_signature([tc2])
    sig3 = _tool_call_signature([tc3])

    assert sig1 != sig2, "different path values → different signatures"
    assert sig1 != sig3, "additional key → different signature"


# ---------------------------------------------------------------------------
# Core loop detection: check_repeated_signature
# ---------------------------------------------------------------------------

def test_signature_loop_aborts_at_3rd_repetition():
    """
    threshold=2: after 1 identical repeat (= 2nd call),
    the 3rd identical call triggers abort (repeated_count=2 >= threshold=2).

    Call sequence with threshold=2:
      round 1: sig A       → repeated_count=0
      round 2: sig A       → repeated_count=1  (abort=False)
      round 3: sig A       → repeated_count=2 >= 2 → should_abort=True
    """
    sig = ("file_read:{'path': '/p/orchestrator_context.py', 'offset': 1220}",)

    # round 1 — first appearance
    last, count, abort = check_repeated_signature(sig, last_signature=None, repeated_count=0, threshold=2)
    assert abort is False
    assert count == 0

    # round 2 — first repeat
    last, count, abort = check_repeated_signature(sig, last_signature=sig, repeated_count=0, threshold=2)
    assert abort is False
    assert count == 1

    # round 3 — second repeat → must abort
    last, count, abort = check_repeated_signature(sig, last_signature=sig, repeated_count=1, threshold=2)
    assert abort is True, "3rd identical repetition must trigger abort with threshold=2"
    assert count == 2


def test_signature_no_abort_on_different_signature():
    """Different signature resets the counter."""
    sig_a = ("file_read:{'path': '/p/a.py'}",)
    sig_b = ("file_read:{'path': '/p/b.py'}",)

    _, count, abort = check_repeated_signature(sig_b, last_signature=sig_a, repeated_count=2, threshold=2)
    assert abort is False
    assert count == 0  # reset


# ---------------------------------------------------------------------------
# Issue #824 exact scenario: 3× file_read with whitespace-varied args
# ---------------------------------------------------------------------------

def test_issue_824_exact_with_whitespace_variation():
    """
    Reproduziert exaktes Issue #824 Szenario:
    3 identische file_read calls mit gleichem path/offset/limit,
    aber die Args kommen vom LLM in leicht verschiedenen JSON-Formatierungen.

    Mit Fix (JSON-Normalisierung) müssen alle 3 Calls als IDENTISCHE
    Signatur erkannt werden → 3. Call (repeated_count=2) löst Abort aus
    bei threshold=2.
    """
    # Simuliere 3 identische Tool-Calls (gleicher logical content) mit je
    # unterschiedlichem Whitespace bzw. key-order.
    tc1 = MockTC("file_read", '{"path":"/p/ctx.py","offset":1220,"limit":60}')
    tc2 = MockTC("file_read", '{"path": "/p/ctx.py", "offset": 1220, "limit": 60}')
    tc3 = MockTC("file_read", '{"limit":60,"offset":1220,"path":"/p/ctx.py"}')  # andere key-order

    sig1 = _tool_call_signature([tc1])
    sig2 = _tool_call_signature([tc2])
    sig3 = _tool_call_signature([tc3])

    # Alle drei haben gleiche logical args → durch sort_keys + compact-separators
    # muessen die Signaturen identisch sein.
    assert sig1 == sig2, f"whitespace-variation: sig1={sig1!r} sig2={sig2!r}"
    assert sig1 == sig3, f"key-order-variation: sig1={sig1!r} sig3={sig3!r}"

    # Now simulate the loop with the NORMALIZED sig (sig1 == sig2)
    sig = sig1  # normalized
    states = []
    last_s = None
    last_count = 0
    for i in range(3):
        last_s, last_count, abort = check_repeated_signature(
            sig, last_signature=last_s, repeated_count=last_count, threshold=2
        )
        states.append((i + 1, last_count, abort))

    # call1: count=0, abort=False
    # call2: count=1, abort=False
    # call3: count=2 >= threshold=2 → abort=True
    assert states[0] == (1, 0, False), f"call1: {states[0]}"
    assert states[1] == (2, 1, False), f"call2: {states[1]}"
    assert states[2] == (3, 2, True), f"call3 must abort: {states[2]}"
