"""Tests für guard_utils.py (#355)."""
import pytest
import sys
from pathlib import Path

# Füge core/src zum PYTHONPATH hinzu
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.guard_utils import check_agent_access, check_project_access, derive_sender


def test_admin_can_access_any_agent():
    check_agent_access("any_agent", ("admin_user", "admin"))  # should not raise


def test_internal_can_access_any_agent():
    check_agent_access("any_agent", ("internal", "internal"))


def test_user_can_access_own_personal_agent():
    check_agent_access("personal_alice", ("alice", "user"))


def test_user_cannot_access_other_agent():
    try:
        check_agent_access("coder", ("alice", "user"))
        assert False, "Should have raised"
    except Exception:
        pass  # Any exception = access denied


def test_user_cannot_access_other_personal_agent():
    with pytest.raises(Exception):
        check_agent_access("personal_bob", ("alice", "user"))


def test_group_service_grants_access():
    class MockGroupService:
        def has_agent_access(self, username, agent_id):
            return username == "alice" and agent_id == "coder"

    check_agent_access("coder", ("alice", "user"), group_service=MockGroupService())


def test_group_service_denies_access():
    class MockGroupService:
        def has_agent_access(self, username, agent_id):
            return False

    with pytest.raises(Exception):
        check_agent_access("coder", ("alice", "user"), group_service=MockGroupService())


def test_admin_can_access_any_project():
    check_project_access("any_project", ("admin_user", "admin"))


def test_user_needs_group_for_project():
    with pytest.raises(Exception):
        check_project_access("secret_project", ("alice", "user"))


def test_derive_sender_normal():
    assert derive_sender(("alice", "user")) == "alice"


def test_derive_sender_admin():
    assert derive_sender(("admin", "admin")) == "admin"


def test_derive_sender_internal():
    assert derive_sender(("internal", "internal")) == "user"
