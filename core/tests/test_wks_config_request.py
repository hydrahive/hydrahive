"""
test_wks_config_request.py — #677 Pydantic-Validator für ssh_port.

Testet WksConfigRequest direkt (ohne TestClient/FastAPI) — folgt dem
Muster der übrigen hydrahive-Tests (conftest.py mockt FastAPI).
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.router_user_integrations import WksConfigRequest


class TestWksConfigRequestSshPort:

    def test_default_is_22(self):
        r = WksConfigRequest(ip="10.0.0.1")
        assert r.ssh_port == 22

    def test_custom_port_accepted(self):
        r = WksConfigRequest(ip="10.0.0.1", ssh_port=2222)
        assert r.ssh_port == 2222

    def test_min_port_accepted(self):
        assert WksConfigRequest(ip="10.0.0.1", ssh_port=1).ssh_port == 1

    def test_max_port_accepted(self):
        assert WksConfigRequest(ip="10.0.0.1", ssh_port=65535).ssh_port == 65535

    def test_port_zero_rejected(self):
        with pytest.raises(Exception):
            WksConfigRequest(ip="10.0.0.1", ssh_port=0)

    def test_port_above_65535_rejected(self):
        with pytest.raises(Exception):
            WksConfigRequest(ip="10.0.0.1", ssh_port=65536)

    def test_negative_port_rejected(self):
        with pytest.raises(Exception):
            WksConfigRequest(ip="10.0.0.1", ssh_port=-1)
