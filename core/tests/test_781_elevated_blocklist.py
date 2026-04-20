"""Test #781: elevated-Mode Minimal-Blocklist.

elevated laeuft in bwrap-Sandbox (scoped Dateisystem), trotzdem muss
User-/Service-Management geblockt sein. Safe-Mode hat ihre eigene
staerkere Blocklist (_SHELL_BLOCKLIST) — diese Tests sind nur fuer
den neuen _ELEVATED_BLOCKLIST + _check_elevated_blocklist.
"""
from __future__ import annotations

from hydrahive_core.tool_registry import _check_elevated_blocklist


# ── Blocked commands ────────────────────────────────────────────────────────

def test_systemctl_blocked():
    assert _check_elevated_blocklist("systemctl restart hydrahive-core") is not None
    assert _check_elevated_blocklist("sudo systemctl stop foo") is not None


def test_service_mgmt_blocked():
    for cmd in [
        "service nginx restart",
        "service ssh start",
        "sudo service cron stop",
    ]:
        assert _check_elevated_blocklist(cmd) is not None, f"Expected block: {cmd}"


def test_user_management_blocked():
    for cmd in [
        "useradd foo",
        "usermod -aG sudo foo",
        "userdel foo",
        "groupadd bar",
        "groupdel bar",
    ]:
        assert _check_elevated_blocklist(cmd) is not None, f"Expected block: {cmd}"


def test_passwd_blocked():
    assert _check_elevated_blocklist("passwd foo") is not None


def test_visudo_blocked():
    assert _check_elevated_blocklist("visudo") is not None


def test_sudo_root_blocked():
    assert _check_elevated_blocklist("sudo -u root whoami") is not None
    assert _check_elevated_blocklist("sudo -u 0 whoami") is not None
    assert _check_elevated_blocklist("sudo -i") is not None


def test_su_user_blocked():
    assert _check_elevated_blocklist("su - root") is not None
    assert _check_elevated_blocklist("su - foo") is not None


# ── Wrapper-Bypass-Versuche ──────────────────────────────────────────────────

def test_bash_c_wrapper_still_blocks():
    """bash -c '<blocked>' muss das innere Kommando durch-checken."""
    assert _check_elevated_blocklist("bash -c 'systemctl restart foo'") is not None
    assert _check_elevated_blocklist("sh -c 'passwd foo'") is not None


def test_env_wrapper_still_blocks():
    """env VAR=val <blocked> muss das innere Kommando durch-checken."""
    assert _check_elevated_blocklist("env FOO=bar useradd baz") is not None


# ── Allowed commands (nicht geblockt) ────────────────────────────────────────

def test_npm_not_blocked():
    """elevated soll weiterhin npm erlauben — Blocklist adressiert nur
    User-/Service-Management, nicht Package-Manager."""
    assert _check_elevated_blocklist("npm install") is None
    assert _check_elevated_blocklist("npm run build") is None


def test_git_not_blocked():
    assert _check_elevated_blocklist("git status") is None
    assert _check_elevated_blocklist("git clone https://example.com/r.git") is None


def test_apt_get_not_blocked():
    """apt-get im bwrap-Container darf weiterhin laufen (scoped)."""
    assert _check_elevated_blocklist("apt-get install curl") is None


def test_basic_commands_not_blocked():
    for cmd in ["ls -la", "cat /tmp/foo", "echo hello", "ps aux"]:
        assert _check_elevated_blocklist(cmd) is None, f"False positive: {cmd}"


# ── Edge Cases ──────────────────────────────────────────────────────────────

def test_empty_command():
    assert _check_elevated_blocklist("") is None


def test_null_byte():
    assert _check_elevated_blocklist("echo foo\x00bar") is not None


def test_unparseable_fail_closed():
    # Unquoted quote → shlex raises → FAIL-CLOSED
    assert _check_elevated_blocklist("echo 'foo") is not None


def test_systemctl_in_filename_not_blocked():
    """Dateinamen mit Underscore nach 'systemctl' werden nicht gematcht
    (Underscore zaehlt als word-char → \\bsystemctl\\b matcht nicht)."""
    assert _check_elevated_blocklist("cat /etc/systemctl_notes.txt") is None


def test_dash_separated_tokens_get_matched():
    """Bewusstes False-Positive-Akzeptanz: 'passwd-policy' wird als
    `passwd`-Match erkannt. Konservativer Default; in der Praxis selten
    relevant weil echte Dateipfade meist Underscores/Punkte nutzen."""
    assert _check_elevated_blocklist("echo 'passwd-policy'") is not None
