"""
test_ssh_known_hosts.py — #674-A Host-Key-Store + Discovery.

Deckt ab:
- make_host_key: Validierung Target-Type + Target-ID
- load/save: Roundtrip, atomarer Write, leerer Store bei fehlender Datei
- compute_fingerprint: Format + Determinismus
- scan_host: Parsing echter ssh-keyscan-Ausgabe + scan_error-Pfade
- record_scan_result: TOFU, bestehende Keys bleiben erhalten
- approve_key / delete_key: Status-Übergänge
- verify_host_key: verified / changed / unknown / unverified
- get_enforcement_mode: Default + Override
"""
import base64
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import ssh_known_hosts as skh


# Synthetischer Test-Key: 32 Nullbytes → deterministisch.
_FAKE_ED25519_RAW = b"\x00" * 32
_FAKE_ED25519_B64 = base64.b64encode(_FAKE_ED25519_RAW).decode("ascii")

# Weiterer synthetischer Key für "two keys"-Tests.
_FAKE_RSA_RAW = b"\x01" * 256
_FAKE_RSA_B64 = base64.b64encode(_FAKE_RSA_RAW).decode("ascii")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Leitet ssh_known_hosts.json in tmp_path um."""
    p = tmp_path / "ssh_known_hosts.json"

    class _S:
        ssh_known_hosts_config = p

    monkeypatch.setattr("hydrahive_core.ssh_known_hosts.settings", _S)
    return p


# ═════════════════════════════════════════════════════ make_host_key


class TestMakeHostKey:

    def test_server_key(self):
        assert skh.make_host_key("server", "prod-web") == "server:prod-web"

    def test_wks_key(self):
        assert skh.make_host_key("wks", "till") == "wks:till"

    def test_rejects_bad_type(self):
        with pytest.raises(ValueError, match="target_type"):
            skh.make_host_key("http", "x")

    def test_rejects_empty_id(self):
        with pytest.raises(ValueError, match="target_id"):
            skh.make_host_key("server", "")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="target_id"):
            skh.make_host_key("server", "../etc")

    def test_rejects_too_long(self):
        with pytest.raises(ValueError, match="target_id"):
            skh.make_host_key("server", "a" * 65)


# ═════════════════════════════════════════════════════ load/save


class TestStoreIO:

    def test_empty_when_file_missing(self, store):
        data = skh.load_known_hosts()
        assert data == {"schema_version": 1, "hosts": {}}
        assert not store.exists()  # load darf die Datei nicht anlegen

    def test_roundtrip(self, store):
        payload = {
            "schema_version": 1,
            "hosts": {
                "server:prod-web": {
                    "target_type": "server", "target_id": "prod-web",
                    "ip": "1.2.3.4", "ssh_port": 22, "ssh_user": "root",
                    "host_keys": {}, "status": "unknown", "last_checked": None,
                },
            },
        }
        skh.save_known_hosts(payload)
        assert store.exists()
        reloaded = skh.load_known_hosts()
        assert reloaded == payload

    def test_save_chmod_0600(self, store):
        skh.save_known_hosts({"schema_version": 1, "hosts": {}})
        assert store.exists()
        mode = store.stat().st_mode & 0o777
        assert mode == 0o600

    def test_save_is_atomic(self, store):
        """save_known_hosts schreibt zuerst .tmp, dann os.replace — auch
        wenn das Zielfile existiert, wird der Inhalt konsistent ersetzt."""
        skh.save_known_hosts({"schema_version": 1, "hosts": {"a": {"x": 1}}})
        skh.save_known_hosts({"schema_version": 1, "hosts": {"b": {"y": 2}}})
        data = json.loads(store.read_text(encoding="utf-8"))
        assert "a" not in data["hosts"]
        assert "b" in data["hosts"]
        # .tmp darf nicht liegen bleiben
        assert not store.with_suffix(store.suffix + ".tmp").exists()

    def test_corrupt_file_returns_empty_store(self, store):
        store.write_text("not json", encoding="utf-8")
        data = skh.load_known_hosts()
        assert data["hosts"] == {}


# ═════════════════════════════════════════════════════ compute_fingerprint


class TestComputeFingerprint:

    def test_format(self):
        fp = skh.compute_fingerprint(_FAKE_ED25519_B64)
        assert fp.startswith("SHA256:")
        # SHA256 = 32 Byte → Base64 ohne Padding = 43 Zeichen
        payload = fp.split(":", 1)[1]
        assert len(payload) == 43

    def test_deterministic(self):
        assert (
            skh.compute_fingerprint(_FAKE_ED25519_B64)
            == skh.compute_fingerprint(_FAKE_ED25519_B64)
        )

    def test_different_input_different_output(self):
        assert (
            skh.compute_fingerprint(_FAKE_ED25519_B64)
            != skh.compute_fingerprint(_FAKE_RSA_B64)
        )

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            skh.compute_fingerprint("")

    def test_matches_ssh_keygen(self, tmp_path):
        """Cross-Check gegen openssh: `ssh-keygen -lf` muss denselben
        Fingerprint liefern. Skippt wenn ssh-keygen nicht verfügbar."""
        import shutil
        import subprocess
        if not shutil.which("ssh-keygen"):
            pytest.skip("ssh-keygen nicht installiert")
        key_file = tmp_path / "test.pub"
        key_file.write_text(f"ssh-ed25519 {_FAKE_ED25519_B64} test\n", encoding="utf-8")
        res = subprocess.run(
            ["ssh-keygen", "-lf", str(key_file)],
            capture_output=True, text=True, timeout=5,
        )
        if res.returncode != 0:
            pytest.skip(f"ssh-keygen konnte Key nicht lesen: {res.stderr.strip()}")
        # Ausgabe: "<bits> SHA256:<fp> <comment> (<type>)"
        tokens = res.stdout.strip().split()
        ssh_keygen_fp = next((t for t in tokens if t.startswith("SHA256:")), "")
        assert ssh_keygen_fp == skh.compute_fingerprint(_FAKE_ED25519_B64)


# ═════════════════════════════════════════════════════ scan_host


class TestScanHost:

    async def test_parses_keyscan_output(self, store):
        # Simuliere ssh-keyscan-Ausgabe für zwei Keys
        stdout = (
            f"# 1.2.3.4:22 SSH-2.0-OpenSSH_9.6\n"
            f"1.2.3.4 ssh-ed25519 {_FAKE_ED25519_B64}\n"
            f"1.2.3.4 ssh-rsa {_FAKE_RSA_B64}\n"
        ).encode()

        async def fake_exec(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(stdout, b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await skh.scan_host("1.2.3.4", 22)

        assert result["scan_error"] is None
        assert len(result["keys"]) == 2
        fps = {k["fingerprint_sha256"] for k in result["keys"]}
        assert skh.compute_fingerprint(_FAKE_ED25519_B64) in fps
        assert skh.compute_fingerprint(_FAKE_RSA_B64) in fps
        # public_key = reiner Base64, kein Algo-Prefix, kein Comment
        for k in result["keys"]:
            assert " " not in k["public_key"]
            assert not k["public_key"].startswith("ssh-")

    async def test_keyscan_binary_missing(self, store):
        async def fake_exec(*args, **kwargs):
            raise FileNotFoundError("ssh-keyscan not found")

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await skh.scan_host("1.2.3.4", 22)

        assert result["keys"] == []
        assert "nicht installiert" in result["scan_error"]

    async def test_timeout(self, store):
        async def fake_exec(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = None
            proc.kill = MagicMock()

            async def _never():
                import asyncio
                await asyncio.sleep(30)

            proc.communicate = _never
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await skh.scan_host("1.2.3.4", 22, timeout=1)

        assert result["keys"] == []
        assert "Timeout" in (result["scan_error"] or "")

    async def test_empty_host(self, store):
        result = await skh.scan_host("", 22)
        assert result["keys"] == []
        assert result["scan_error"] == "Host leer"

    async def test_ignores_unknown_algorithms(self, store):
        """Unbekannte Algorithmen (z.B. ssh-dss) werden ignoriert, nicht
        gespeichert. Stops unsichere Legacy-Algos aus dem Store."""
        stdout = f"1.2.3.4 ssh-dss {_FAKE_ED25519_B64}\n".encode()

        async def fake_exec(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(stdout, b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await skh.scan_host("1.2.3.4", 22)
        assert result["keys"] == []


# ═════════════════════════════════════════════════════ record_scan_result


class TestRecordScanResult:

    def test_initial_tofu_write(self, store):
        fp = skh.compute_fingerprint(_FAKE_ED25519_B64)
        entry = skh.record_scan_result(
            "server", "prod-web",
            ip="1.2.3.4", ssh_port=22, ssh_user="root",
            scanned_keys=[{
                "algorithm": "ssh-ed25519",
                "public_key": _FAKE_ED25519_B64,
                "fingerprint_sha256": fp,
            }],
        )
        assert entry["status"] == "unverified"
        assert entry["ip"] == "1.2.3.4"
        assert fp in entry["host_keys"]
        assert entry["host_keys"][fp]["status"] == "unverified"

    def test_existing_verified_key_is_preserved(self, store):
        """Wenn ein bereits verified Key erneut gescannt wird, behält er
        seinen verified Status. Sonst würde ein zweiter Scan alle Approvals
        zurücksetzen."""
        fp = skh.compute_fingerprint(_FAKE_ED25519_B64)
        skh.record_scan_result(
            "server", "prod-web",
            ip="1.2.3.4", ssh_port=22, ssh_user="root",
            scanned_keys=[{
                "algorithm": "ssh-ed25519",
                "public_key": _FAKE_ED25519_B64,
                "fingerprint_sha256": fp,
            }],
        )
        skh.approve_key("server", "prod-web", fp, approver="tester")

        # Gleicher Fingerprint, erneuter Scan
        entry = skh.record_scan_result(
            "server", "prod-web",
            ip="1.2.3.4", ssh_port=22, ssh_user="root",
            scanned_keys=[{
                "algorithm": "ssh-ed25519",
                "public_key": _FAKE_ED25519_B64,
                "fingerprint_sha256": fp,
            }],
        )
        assert entry["host_keys"][fp]["status"] == "verified"
        assert entry["host_keys"][fp]["verified_by"] == "tester"

    def test_new_key_added_as_unverified(self, store):
        """Zweiter, neuer Key (z.B. nach Rotation) kommt als unverified
        hinzu — bestehender verified Key bleibt."""
        fp1 = skh.compute_fingerprint(_FAKE_ED25519_B64)
        fp2 = skh.compute_fingerprint(_FAKE_RSA_B64)
        skh.record_scan_result(
            "server", "prod-web",
            ip="1.2.3.4", ssh_port=22, ssh_user="root",
            scanned_keys=[{
                "algorithm": "ssh-ed25519",
                "public_key": _FAKE_ED25519_B64,
                "fingerprint_sha256": fp1,
            }],
        )
        skh.approve_key("server", "prod-web", fp1)

        entry = skh.record_scan_result(
            "server", "prod-web",
            ip="1.2.3.4", ssh_port=22, ssh_user="root",
            scanned_keys=[
                {"algorithm": "ssh-ed25519", "public_key": _FAKE_ED25519_B64,
                 "fingerprint_sha256": fp1},
                {"algorithm": "ssh-rsa", "public_key": _FAKE_RSA_B64,
                 "fingerprint_sha256": fp2},
            ],
        )
        assert entry["host_keys"][fp1]["status"] == "verified"
        assert entry["host_keys"][fp2]["status"] == "unverified"
        # Host-Status: min. 1 verified → verified
        assert entry["status"] == "verified"


# ═════════════════════════════════════════════════════ approve / delete


class TestApproveDeleteKey:

    def _seed(self):
        fp = skh.compute_fingerprint(_FAKE_ED25519_B64)
        skh.record_scan_result(
            "server", "prod-web",
            ip="1.2.3.4", ssh_port=22, ssh_user="root",
            scanned_keys=[{
                "algorithm": "ssh-ed25519",
                "public_key": _FAKE_ED25519_B64,
                "fingerprint_sha256": fp,
            }],
        )
        return fp

    def test_approve_flips_status(self, store):
        fp = self._seed()
        updated = skh.approve_key("server", "prod-web", fp, approver="till")
        assert updated["status"] == "verified"
        assert updated["host_keys"][fp]["status"] == "verified"
        assert updated["host_keys"][fp]["verified_by"] == "till"
        assert updated["host_keys"][fp]["verified_method"] == "manual-approve"
        assert updated["host_keys"][fp]["verified_at"] is not None

    def test_delete_removes_key(self, store):
        fp = self._seed()
        updated = skh.delete_key("server", "prod-web", fp)
        assert updated["host_keys"] == {}
        assert updated["status"] == "unknown"

    def test_approve_unknown_host_returns_none(self, store):
        fp = skh.compute_fingerprint(_FAKE_ED25519_B64)
        assert skh.approve_key("server", "nope", fp) is None

    def test_approve_unknown_fingerprint_returns_none(self, store):
        self._seed()
        assert skh.approve_key(
            "server", "prod-web",
            "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        ) is None

    def test_rejects_malformed_fingerprint(self, store):
        with pytest.raises(ValueError, match="fingerprint"):
            skh.approve_key("server", "prod-web", "not-a-fingerprint")


# ═════════════════════════════════════════════════════ verify_host_key


class TestVerifyHostKey:

    def test_unknown_when_no_entry(self, store):
        assert skh.verify_host_key("server", "nope") == "unknown"

    def test_unverified_when_only_unverified_keys(self, store):
        fp = skh.compute_fingerprint(_FAKE_ED25519_B64)
        skh.record_scan_result(
            "server", "prod-web",
            ip="1.2.3.4", ssh_port=22, ssh_user="root",
            scanned_keys=[{
                "algorithm": "ssh-ed25519",
                "public_key": _FAKE_ED25519_B64,
                "fingerprint_sha256": fp,
            }],
        )
        assert skh.verify_host_key("server", "prod-web") == "unverified"

    def test_verified_when_observed_key_matches(self, store):
        fp = skh.compute_fingerprint(_FAKE_ED25519_B64)
        skh.record_scan_result(
            "server", "prod-web",
            ip="1.2.3.4", ssh_port=22, ssh_user="root",
            scanned_keys=[{
                "algorithm": "ssh-ed25519",
                "public_key": _FAKE_ED25519_B64,
                "fingerprint_sha256": fp,
            }],
        )
        skh.approve_key("server", "prod-web", fp)
        assert skh.verify_host_key(
            "server", "prod-web",
            observed_keys=[{"fingerprint_sha256": fp}],
        ) == "verified"

    def test_changed_when_observed_key_differs(self, store):
        fp_stored = skh.compute_fingerprint(_FAKE_ED25519_B64)
        fp_observed = skh.compute_fingerprint(_FAKE_RSA_B64)
        skh.record_scan_result(
            "server", "prod-web",
            ip="1.2.3.4", ssh_port=22, ssh_user="root",
            scanned_keys=[{
                "algorithm": "ssh-ed25519",
                "public_key": _FAKE_ED25519_B64,
                "fingerprint_sha256": fp_stored,
            }],
        )
        skh.approve_key("server", "prod-web", fp_stored)
        assert skh.verify_host_key(
            "server", "prod-web",
            observed_keys=[{"fingerprint_sha256": fp_observed}],
        ) == "changed"


# ═════════════════════════════════════════════════════ enforcement mode


class TestEnforcementMode:

    def test_default_warn(self, monkeypatch):
        monkeypatch.delenv("HYDRAHIVE_REQUIRE_HOST_KEYS", raising=False)
        assert skh.get_enforcement_mode() == "warn"

    def test_strict_via_env(self, monkeypatch):
        monkeypatch.setenv("HYDRAHIVE_REQUIRE_HOST_KEYS", "strict")
        assert skh.get_enforcement_mode() == "strict"

    def test_strict_via_true(self, monkeypatch):
        monkeypatch.setenv("HYDRAHIVE_REQUIRE_HOST_KEYS", "true")
        assert skh.get_enforcement_mode() == "strict"

    def test_unknown_value_defaults_warn(self, monkeypatch):
        monkeypatch.setenv("HYDRAHIVE_REQUIRE_HOST_KEYS", "schmuh")
        assert skh.get_enforcement_mode() == "warn"
