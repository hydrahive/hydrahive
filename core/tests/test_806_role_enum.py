"""
test_806_role_enum.py — Role Escalation Fix (#806)

UpdateUserRequest + CreateUserRequest müssen im Pydantic-Schema nur
"admin" oder "user" akzeptieren. Andere Werte → 422.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from pydantic import ValidationError


def test_update_user_request_rejects_unknown_role():
    from hydrahive_core.router_users import UpdateUserRequest

    with pytest.raises(ValidationError):
        UpdateUserRequest(role="superadmin")
    with pytest.raises(ValidationError):
        UpdateUserRequest(role="root")
    with pytest.raises(ValidationError):
        UpdateUserRequest(role="")
    with pytest.raises(ValidationError):
        UpdateUserRequest(role="ADMIN")  # case-sensitive


def test_update_user_request_accepts_known_roles():
    from hydrahive_core.router_users import UpdateUserRequest

    assert UpdateUserRequest(role="admin").role == "admin"
    assert UpdateUserRequest(role="user").role == "user"
    assert UpdateUserRequest(role=None).role is None
    # Feld optional
    assert UpdateUserRequest().role is None


def test_create_user_request_rejects_unknown_role():
    from hydrahive_core.router_users import CreateUserRequest

    with pytest.raises(ValidationError):
        CreateUserRequest(username="x", password="12345678", role="superadmin")


def test_create_user_request_accepts_known_roles_and_default():
    from hydrahive_core.router_users import CreateUserRequest

    r = CreateUserRequest(username="x", password="12345678")
    assert r.role == "user"  # Default
    assert CreateUserRequest(username="x", password="12345678", role="admin").role == "admin"
