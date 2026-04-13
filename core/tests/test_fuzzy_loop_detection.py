"""
test_fuzzy_loop_detection.py — #618 Sliding-Window-Loop-Detector
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_tools import (
    _fuzzy_fingerprint,
    check_fuzzy_loop,
    _FUZZY_ARG_PREFIX_CHARS,
    _FUZZY_WINDOW,
    _FUZZY_THRESHOLD,
)


def test_fingerprint_truncates_args():
    long_args = '{"path": "/projects/homepage-sicherheitstest/cms_backup/' + "x" * 200 + '"}'
    fp = _fuzzy_fingerprint("shell_exec", long_args)
    # nur prefix
    assert len(fp) <= len("shell_exec::") + _FUZZY_ARG_PREFIX_CHARS
    assert fp.startswith("shell_exec::")


def test_no_abort_empty_history():
    abort, fp = check_fuzzy_loop([])
    assert not abort and fp is None


def test_no_abort_diverse_calls():
    """10 unterschiedliche Calls — kein Loop."""
    history = [f"shell_exec::ls /dir{i}" for i in range(10)]
    abort, _ = check_fuzzy_loop(history)
    assert not abort


def test_abort_on_repetition_variation_in_suffix():
    """Der kritische Case: Pfad variiert NUR nach den ersten 80 chars."""
    prefix = '{"command": "cat /projects/homepage-sicherheitstest/cms_backup/' + "x" * 80
    history = [
        _fuzzy_fingerprint("shell_exec", f'{prefix}/file_{i}"}}')
        for i in range(6)
    ]
    abort, dominant = check_fuzzy_loop(history)
    assert abort
    assert dominant is not None
    assert "shell_exec" in dominant


def test_abort_exact_repetition():
    history = ["shell_exec::cat /tmp/x"] * 7
    abort, dominant = check_fuzzy_loop(history)
    assert abort
    assert dominant == "shell_exec::cat /tmp/x"


def test_no_abort_interleaved():
    """cat/find abwechselnd, je ~50% — kein Loop (kein Pattern dominiert)."""
    history = []
    for _ in range(4):
        history.append("shell_exec::cat /x")
        history.append("shell_exec::find /y")
    # 4 cat + 4 find im Fenster → keins erreicht threshold=5
    abort, _ = check_fuzzy_loop(history)
    assert not abort


def test_different_tools_do_not_trigger():
    """Gleiche args mit unterschiedlichen tool_names → nicht gleiche Fingerprints."""
    history = []
    for i in range(5):
        history.append(f"file_read::/x/a")
        history.append(f"file_search::/x/a")
    # jede Signatur nur 5× im Fenster (von 8) — Achtung: test je nach window
    # 5 von jeder → dominante Signatur hat 5, threshold=5 → abort!
    abort, fp = check_fuzzy_loop(history)
    # window=8 letzte: [file_read, file_search, file_read, file_search,
    #                   file_read, file_search, file_read, file_search]
    # file_read::/x/a = 4, file_search::/x/a = 4 → kein threshold
    assert not abort


def test_threshold_boundary():
    """Genau threshold Treffer → abort. threshold-1 → kein abort."""
    history = ["a"] * (_FUZZY_THRESHOLD - 1)
    abort, _ = check_fuzzy_loop(history)
    assert not abort

    history = ["a"] * _FUZZY_THRESHOLD
    abort, _ = check_fuzzy_loop(history)
    assert abort


def test_window_limits_scope():
    """Alte Treffer außerhalb Fenster zählen nicht mehr."""
    # 10× "a", dann 8× "b" — im Fenster (letzte 8) nur "b"
    history = ["a"] * 10 + ["b"] * 8
    abort, fp = check_fuzzy_loop(history)
    assert abort
    assert fp == "b"


def test_real_world_homepage_loop():
    """Reproduziert das echte Loop-Pattern von heute Abend."""
    # 22 cat-Calls auf /projects/homepage-sicherheitstest/cms_backup mit
    # variierendem Dateinamen — alle gleichen Prefix < 80 chars.
    history = []
    for i in range(22):
        args = f'{{"command": "cat /projects/homepage-sicherheitstest/cms_backup/f{i}"}}'
        history.append(_fuzzy_fingerprint("shell_exec", args))

    abort, dominant = check_fuzzy_loop(history)
    assert abort
    assert dominant is not None
    assert "homepage-sicherheits" in dominant  # Prefix abgeschnitten, Teil reicht
