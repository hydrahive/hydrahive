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


def test_no_abort_shell_exec_different_files_same_prefix():
    """#623: Batch-cat auf unterschiedliche Dateien (gleicher Pfad-Prefix,
    aber je eigene Datei) ist legitime Serienarbeit, kein Loop."""
    prefix = '{"command": "cat /projects/homepage-sicherheitstest/cms_backup/' + "x" * 80
    history = [
        _fuzzy_fingerprint("shell_exec", f'{prefix}/file_{i}"}}')
        for i in range(6)
    ]
    abort, _ = check_fuzzy_loop(history)
    assert not abort, "Batch-cat auf 6 verschiedene Dateien darf nicht als Loop gelten."


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


def test_no_abort_real_world_listing_batch():
    """#623: 22 cat-Calls auf /projects/homepage-sicherheitstest/cms_backup
    mit unterschiedlichen Dateinamen sind ein legitimes Listing/Recovery,
    kein Loop. Echte Wiederholung wird durch test_abort_shell_exec_same_command
    abgesichert."""
    history = []
    for i in range(22):
        args = f'{{"command": "cat /projects/homepage-sicherheitstest/cms_backup/f{i}"}}'
        history.append(_fuzzy_fingerprint("shell_exec", args))

    abort, _ = check_fuzzy_loop(history)
    assert not abort, "22 cats auf je eigene Datei dürfen nicht als Loop gelten."


# =============================================================================
# #623 — per-Tool Identifier-Achse, Batch-Arbeit darf weiterlaufen
# =============================================================================

def test_no_abort_file_patch_different_paths():
    """#623: file_patch auf 6 unterschiedliche Pfade → kein Loop."""
    history = [
        _fuzzy_fingerprint(
            "file_patch",
            f'{{"path": "/projects/x/file_{i}.py", "patch": "@@ -1 +1 @@\\n-old\\n+new\\n"}}',
        )
        for i in range(6)
    ]
    abort, _ = check_fuzzy_loop(history)
    assert not abort, "Batch-Patch auf 6 verschiedene Dateien darf nicht abbrechen."


def test_abort_file_patch_same_path():
    """#623: file_patch 6× auf denselben Pfad → echter Loop, Abort."""
    history = [
        _fuzzy_fingerprint(
            "file_patch",
            f'{{"path": "/projects/x/file.py", "patch": "@@ -1 +1 @@\\n-v{i}\\n+v{i+1}\\n"}}',
        )
        for i in range(6)
    ]
    abort, dominant = check_fuzzy_loop(history)
    assert abort, "6 Patches auf dieselbe Datei sollten als Loop erkannt werden."
    assert dominant is not None and "/projects/x/file.py" in dominant


def test_abort_shell_exec_same_command():
    """#623 Gegenprobe: identischer shell_exec-Befehl 6× → Abort bleibt."""
    history = [
        _fuzzy_fingerprint("shell_exec", '{"command": "git status"}')
        for _ in range(6)
    ]
    abort, dominant = check_fuzzy_loop(history)
    assert abort
    assert dominant is not None and "git status" in dominant


def test_no_abort_file_read_different_paths():
    """#623: file_read auf 6 verschiedene Dateien → kein Loop (Code-Lesephase)."""
    history = [
        _fuzzy_fingerprint("file_read", f'{{"path": "/projects/x/mod_{i}.py"}}')
        for i in range(6)
    ]
    abort, _ = check_fuzzy_loop(history)
    assert not abort


# =============================================================================
# #845 — Pagination (gleiche Datei, verschiedene offsets) ist kein Loop
# =============================================================================

def test_no_abort_file_read_pagination_same_file():
    """#845: 6× file_read auf dieselbe Datei mit unterschiedlichen offsets
    (Gate #840 Schema-Minimum liefert 4000-Zeichen-Fenster → pagination via
    has_more:true ist legitim) darf nicht als Loop gewertet werden."""
    history = [
        _fuzzy_fingerprint(
            "file_read",
            f'{{"path": "/projects/x/big_file.py", "offset": {i * 4000}}}',
        )
        for i in range(6)
    ]
    abort, dominant = check_fuzzy_loop(history)
    assert not abort, (
        f"Pagination auf gleicher Datei mit verschiedenen offsets darf nicht "
        f"als Loop gelten. dominant={dominant!r}"
    )


def test_abort_file_read_same_path_and_offset():
    """#845 Gegenprobe: 6× file_read mit gleichem path UND gleichem offset
    → echter Loop (Agent liest immer wieder dieselben 4000 Zeichen)."""
    history = [
        _fuzzy_fingerprint(
            "file_read",
            '{"path": "/projects/x/big_file.py", "offset": 0}',
        )
        for _ in range(6)
    ]
    abort, dominant = check_fuzzy_loop(history)
    assert abort
    assert dominant is not None and "/projects/x/big_file.py" in dominant


def test_file_read_missing_offset_defaults_to_zero():
    """#845: wenn offset nicht übergeben wird, wird 0 angenommen. So bleibt
    der Abort-Check konsistent: 6× file_read(path=X) ohne offset ≡ 6× offset=0."""
    fp_no_offset = _fuzzy_fingerprint(
        "file_read", '{"path": "/projects/x/big_file.py"}'
    )
    fp_offset_0 = _fuzzy_fingerprint(
        "file_read", '{"path": "/projects/x/big_file.py", "offset": 0}'
    )
    assert fp_no_offset == fp_offset_0


# ========================================================
# #853: file_patch/file_edit Fingerprint inkludiert search
# ========================================================

class TestFuzzyPatchFingerprint:
    """Tests für Issue #853: Multi-Patch-Refactor auf derselben Datei
    verschiedene Stellen soll ohne künstlichen Loop-Abruch möglich sein."""

    def test_different_search_same_path_no_loop_abort(self):
        """6× file_patch auf dieselbe Datei, verschiedene search-Strings —
        legitime Multi-Stellen-Editierung, kein Loop."""
        history = [
            _fuzzy_fingerprint(
                "file_patch",
                f'{{"path": "/projects/x/config.py", "search": "old_{i}", "replace": "new_{i}"}}',
            )
            for i in range(6)
        ]
        abort, dominant = check_fuzzy_loop(history)
        assert not abort, (
            f"6× file_patch auf gleiche Datei mit verschiedenen search-Strings "
            f"darf nicht als Loop gelten. dominant={dominant!r}"
        )

    def test_identical_search_and_path_triggers_loop(self):
        """6× file_patch auf dieselbe Datei mit identischem (path + search) —
        echter Loop, muss geblockt werden."""
        history = [
            _fuzzy_fingerprint(
                "file_patch",
                '{"path": "/projects/x/config.py", "search": "DEBUG = True", "replace": "DEBUG = False"}',
            )
            for _ in range(6)
        ]
        abort, dominant = check_fuzzy_loop(history)
        assert abort, "6× identischer (path + search) muss Loop auslösen"
        assert "/projects/x/config.py" in dominant

    def test_file_write_loop_detection_unchanged(self):
        """#853 darf file_write-Logik nicht verändern — file_write nutzt nur
        path, kein search. 6× file_write auf gleiche Datei = Loop."""
        history = [
            _fuzzy_fingerprint(
                "file_write",
                f'{{"path": "/projects/x/main.py", "content": "v{i}"}}',
            )
            for i in range(6)
        ]
        abort, dominant = check_fuzzy_loop(history)
        assert abort, "6× file_write auf gleiche Datei muss Loop auslösen"
        assert "/projects/x/main.py" in dominant

    def test_search_truncated_at_80_chars(self):
        """search-String über 80 Zeichen wird für den Fingerprint gekürzt —
        Zwei verschiedene search mit gleichem 80-Char-Präfix → identischer FP."""
        common_prefix = "x" * 80
        extra = "y" * 70
        fp_80 = _fuzzy_fingerprint(
            "file_patch",
            f'{{"path": "/p.py", "search": "{common_prefix}", "replace": "r"}}',
        )
        fp_150 = _fuzzy_fingerprint(
            "file_patch",
            f'{{"path": "/p.py", "search": "{common_prefix}{extra}", "replace": "r"}}',
        )
        # Gleicher Pfad + gleiches 80-Char-Präfix des search → identischer FP
        assert fp_80 == fp_150

    def test_file_edit_and_file_patch_are_different_tools(self):
        """file_edit und file_patch sind verschiedene Tools — verschiedene
        Tool-Namen erzeugen verschiedene Fingerprints, selbst bei gleichen
        Args. Das ist korrekt und gewünscht."""
        patch_fp = _fuzzy_fingerprint(
            "file_patch",
            '{"path": "/p.py", "search": "a", "replace": "b"}',
        )
        edit_fp = _fuzzy_fingerprint(
            "file_edit",
            '{"path": "/p.py", "search": "a", "replace": "b"}',
        )
        # Unterschiedliche Tool-Namen → unterschiedliche FP (korrekt)
        assert patch_fp != edit_fp
        assert patch_fp.startswith("file_patch::")
        assert edit_fp.startswith("file_edit::")
