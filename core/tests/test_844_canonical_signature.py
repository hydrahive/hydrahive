"""
test_844_canonical_signature.py — Gate 8: AST/canonical Loop-Detection (#844).

Robuster als JSON-Normalisierung: int/float-Equivalenz, Unicode-NFC,
deep-struct-Vergleich.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dataclasses import dataclass


@dataclass
class _MockFn:
    name: str
    arguments: str


@dataclass
class _MockTC:
    function: _MockFn

    @classmethod
    def make(cls, name: str, args: str) -> "_MockTC":
        return cls(function=_MockFn(name=name, arguments=args))


# ─── _canonicalize Unit ────────────────────────────────────────────────

def test_canonicalize_int_float_equiv():
    from hydrahive_core.orchestrator_tools import _canonicalize
    assert _canonicalize(5) == _canonicalize(5.0)


def test_canonicalize_int_float_not_equal_for_actual_float():
    from hydrahive_core.orchestrator_tools import _canonicalize
    assert _canonicalize(5.5) != _canonicalize(5)


def test_canonicalize_dict_key_order():
    from hydrahive_core.orchestrator_tools import _canonicalize
    a = _canonicalize({"path": "foo", "limit": 5})
    b = _canonicalize({"limit": 5, "path": "foo"})
    assert a == b


def test_canonicalize_unicode_nfc():
    from hydrahive_core.orchestrator_tools import _canonicalize
    # café als NFD vs NFC
    nfd = "café"  # e + combining acute
    nfc = "café"    # é precomposed
    assert _canonicalize(nfd) == _canonicalize(nfc)


def test_canonicalize_nested():
    from hydrahive_core.orchestrator_tools import _canonicalize
    a = _canonicalize({"x": [1, 2, {"a": 1}], "y": "z"})
    b = _canonicalize({"y": "z", "x": [1.0, 2.0, {"a": 1.0}]})
    assert a == b


# ─── Signature Integration ─────────────────────────────────────────────

def test_signature_whitespace_invariant():
    from hydrahive_core.orchestrator_tools import _tool_call_signature
    s1 = _tool_call_signature([_MockTC.make("file_read", '{"path":"foo"}')])
    s2 = _tool_call_signature([_MockTC.make("file_read", '{"path": "foo"}')])
    s3 = _tool_call_signature([_MockTC.make("file_read", '{"path":"foo"} ')])
    assert s1 == s2 == s3


def test_signature_key_order_invariant():
    from hydrahive_core.orchestrator_tools import _tool_call_signature
    s1 = _tool_call_signature([_MockTC.make("file_read", '{"path":"foo","limit":5}')])
    s2 = _tool_call_signature([_MockTC.make("file_read", '{"limit":5,"path":"foo"}')])
    assert s1 == s2


def test_signature_int_float_invariant():
    from hydrahive_core.orchestrator_tools import _tool_call_signature
    s1 = _tool_call_signature([_MockTC.make("file_read", '{"limit":5}')])
    s2 = _tool_call_signature([_MockTC.make("file_read", '{"limit":5.0}')])
    assert s1 == s2


def test_signature_different_args_different():
    from hydrahive_core.orchestrator_tools import _tool_call_signature
    s1 = _tool_call_signature([_MockTC.make("file_read", '{"path":"foo"}')])
    s2 = _tool_call_signature([_MockTC.make("file_read", '{"path":"bar"}')])
    assert s1 != s2


def test_signature_extra_key_different():
    from hydrahive_core.orchestrator_tools import _tool_call_signature
    s1 = _tool_call_signature([_MockTC.make("file_read", '{"path":"foo"}')])
    s2 = _tool_call_signature([_MockTC.make("file_read", '{"path":"foo","limit":5}')])
    assert s1 != s2


def test_signature_file_write_excluded():
    from hydrahive_core.orchestrator_tools import _tool_call_signature
    s = _tool_call_signature([_MockTC.make("file_write", '{"path":"foo","content":"x"}')])
    assert s == ()  # excluded → empty signature
