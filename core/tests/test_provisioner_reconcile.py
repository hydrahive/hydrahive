"""
test_provisioner_reconcile.py — Reconcile-Pfad (#813)

Deckt:
- reprovision(cfg) ruft _create_linux_user + _setup_samba in der richtigen Reihenfolge
- reprovision bricht sauber ab wenn Linux-User trotz Versuch nicht existiert
- reconcile_all_projects() iteriert alle Projekte aus dem Loader
- reconcile_all_projects() wirft bei ProjectLoader-Fehler keine Exception
- reconcile_all_projects() isoliert Fehler pro Projekt (ein Fehler kippt nicht den ganzen Lauf)
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def _mock_cfg(pid: str) -> SimpleNamespace:
    cfg = SimpleNamespace()
    cfg.id = pid
    cfg.effective_system_user = lambda: f"proj_{pid}"
    return cfg


@pytest.fixture
def provisioner():
    from hydrahive_core.provisioner import Provisioner
    return Provisioner(admin_token="t", server_name="test")


def test_reprovision_calls_user_then_samba(provisioner):
    cfg = _mock_cfg("alpha")
    call_order = []
    with patch.object(provisioner, "_create_linux_user", side_effect=lambda u, d: call_order.append(("user", u, d)) or None) as m_user, \
         patch.object(provisioner, "_setup_samba", side_effect=lambda p, u, d: call_order.append(("samba", p, u, d)) or None) as m_samba, \
         patch("subprocess.run") as m_run:
        m_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        warnings = provisioner.reprovision(cfg)

    assert warnings == []
    assert call_order[0][0] == "user"
    assert call_order[1][0] == "samba"
    assert call_order[0][1] == "proj_alpha"
    assert call_order[1][1] == "alpha"
    assert call_order[1][2] == "proj_alpha"


def test_reprovision_skips_samba_if_user_missing(provisioner):
    cfg = _mock_cfg("beta")
    with patch.object(provisioner, "_create_linux_user", return_value="useradd fail"), \
         patch.object(provisioner, "_setup_samba") as m_samba, \
         patch("subprocess.run") as m_run:
        m_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="no such user")
        warnings = provisioner.reprovision(cfg)

    assert any("Samba übersprungen" in w for w in warnings)
    m_samba.assert_not_called()


def test_reprovision_passes_through_samba_warning(provisioner):
    cfg = _mock_cfg("gamma")
    with patch.object(provisioner, "_create_linux_user", return_value=None), \
         patch.object(provisioner, "_setup_samba", return_value="Samba nicht installiert — Share übersprungen"), \
         patch("subprocess.run") as m_run:
        m_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        warnings = provisioner.reprovision(cfg)

    assert any("Samba nicht installiert" in w for w in warnings)


def test_reconcile_all_iterates_all_projects(provisioner):
    loader = MagicMock()
    loader.projects = {
        "alpha": _mock_cfg("alpha"),
        "beta":  _mock_cfg("beta"),
    }
    with patch.object(provisioner, "reprovision", return_value=[]) as m_rep:
        report = provisioner.reconcile_all_projects(loader)

    assert m_rep.call_count == 2
    assert set(report["skipped"]) == {"alpha", "beta"}
    assert report["reconciled"] == []
    assert report["errors"] == []


def test_reconcile_all_records_warnings(provisioner):
    loader = MagicMock()
    loader.projects = {"alpha": _mock_cfg("alpha")}
    with patch.object(provisioner, "reprovision", return_value=["samba reload failed"]):
        report = provisioner.reconcile_all_projects(loader)

    assert report["skipped"] == []
    assert len(report["reconciled"]) == 1
    assert report["reconciled"][0]["id"] == "alpha"


def test_reconcile_all_isolates_per_project_errors(provisioner):
    loader = MagicMock()
    loader.projects = {
        "alpha": _mock_cfg("alpha"),
        "beta":  _mock_cfg("beta"),
    }
    def flaky(cfg):
        if cfg.id == "alpha":
            raise RuntimeError("boom")
        return []
    with patch.object(provisioner, "reprovision", side_effect=flaky):
        report = provisioner.reconcile_all_projects(loader)

    assert len(report["errors"]) == 1
    assert report["errors"][0]["id"] == "alpha"
    assert "beta" in report["skipped"]


def test_reconcile_all_survives_loader_exception(provisioner):
    loader = MagicMock()
    type(loader).projects = property(lambda self: (_ for _ in ()).throw(RuntimeError("loader down")))
    report = provisioner.reconcile_all_projects(loader)

    assert report["errors"]
    assert "loader" in str(report["errors"][0]).lower()
