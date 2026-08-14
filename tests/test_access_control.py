"""Tests for roles, permissions, and folder scope.

The Profile and Admin pages derive everything from these tables, so a role that
silently grants too much would be invisible in the UI. These assert the shape of
the privilege ladder rather than each individual grant.

    python -m pytest tests/ -q
"""

import pytest

from aat_system import user_repo
from aat_system.auth import assert_folder_access, get_allowed_folders
from aat_system.config import (
    CORE_FOLDERS,
    ROLE_ORDER,
    SENIOR_TIER,
    Division,
    Permission,
    Role,
    has_permission,
    permissions_for,
)
from aat_system.models import User


def user(role, division=Division.MULTIFAMILY):
    return User(id=1, email="x@aat.com", name="X", division=division, role=role, hashed_password="x")


def test_every_role_appears_exactly_once_in_the_ladder():
    assert sorted(r.value for r in ROLE_ORDER) == sorted(r.value for r in Role)
    assert len(ROLE_ORDER) == len(set(ROLE_ORDER))


def test_super_user_holds_every_permission():
    assert set(permissions_for(Role.SUPER_USER)) == {p.value for p in Permission}


def test_only_super_user_sees_across_divisions():
    holders = [r for r in Role if has_permission(r, Permission.VIEW_ALL_DIVISIONS)]
    assert holders == [Role.SUPER_USER]


def test_senior_tier_accumulates_privilege():
    """Division Head -> Admin -> Super User each contain everything below.

    Promoting within the senior tier must never quietly take access away.
    """
    for lower, higher in zip(SENIOR_TIER, SENIOR_TIER[1:]):
        assert set(permissions_for(lower)) <= set(permissions_for(higher)), (
            f"{higher.value} does not include everything {lower.value} grants"
        )


def test_operational_roles_are_peers_with_distinct_duties():
    """Agent, Reviewer and Subgroup Owner are not a ladder, by design.

    An Agent runs work but never signs off; a Reviewer signs off but does not
    upload. Neither contains the other, which is why the Profile page reports
    permissions granted rather than a rank.
    """
    agent = set(permissions_for(Role.AGENT))
    reviewer = set(permissions_for(Role.REVIEWER))
    assert not agent <= reviewer
    assert not reviewer <= agent


def test_division_head_supersedes_every_operational_role():
    head = set(permissions_for(Role.DIVISION_HEAD))
    for role in (Role.AGENT, Role.REVIEWER, Role.SUBGROUP_OWNER):
        assert set(permissions_for(role)) <= head


def test_profile_meter_counts_permissions_not_rank():
    profile = user_repo.profile(user(Role.REVIEWER))
    assert profile["permission_count"] == len(permissions_for(Role.REVIEWER))
    assert profile["permission_total"] == len(list(Permission))


def test_agent_cannot_approve_or_manage():
    for permission in (Permission.APPROVE_WORKFLOW, Permission.MANAGE_USERS, Permission.MANAGE_ROLES):
        assert not has_permission(Role.AGENT, permission)


def test_reviewer_approves_but_cannot_upload():
    assert has_permission(Role.REVIEWER, Permission.APPROVE_WORKFLOW)
    assert not has_permission(Role.REVIEWER, Permission.UPLOAD_DOCUMENTS)


def test_subgroup_owner_runs_work_but_does_not_sign_off():
    assert has_permission(Role.SUBGROUP_OWNER, Permission.RUN_WORKFLOW)
    assert not has_permission(Role.SUBGROUP_OWNER, Permission.APPROVE_WORKFLOW)


def test_only_senior_roles_edit_workflow_definitions():
    editors = {r for r in Role if has_permission(r, Permission.EDIT_WORKFLOW)}
    assert editors == {Role.SUPER_USER, Role.ADMIN, Role.DIVISION_HEAD}


def test_division_head_reaches_every_core_folder():
    assert get_allowed_folders(user(Role.DIVISION_HEAD)) == list(CORE_FOLDERS)


def test_folder_access_is_refused_for_a_folder_outside_scope():
    with pytest.raises(Exception):
        assert_folder_access(user(Role.AGENT), "Nonexistent Folder")


def test_profile_reports_a_level_and_matching_permissions():
    profile = user_repo.profile(user(Role.DIVISION_HEAD))
    assert profile["access_level"] == ROLE_ORDER.index(Role.DIVISION_HEAD) + 1
    assert profile["access_levels_total"] == len(ROLE_ORDER)
    assert profile["can_approve"] is True
    assert profile["can_manage_users"] is False
    granted = {p["key"] for p in profile["permission_matrix"] if p["granted"]}
    assert granted == set(profile["permissions"])


def test_role_catalog_is_ordered_least_privileged_first():
    catalog = user_repo.role_catalog()
    assert [r["level"] for r in catalog] == list(range(1, len(ROLE_ORDER) + 1))
    assert catalog[0]["key"] == Role.AGENT.value
    assert catalog[-1]["key"] == Role.SUPER_USER.value


def test_unknown_email_is_provisioned_at_least_privilege(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from aat_system.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'users.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    resolved = user_repo.resolve_session_user(db, "stranger@example.com", Division.MULTIFAMILY)
    assert resolved.role == user_repo.FALLBACK_ROLE == Role.AGENT

    # A seeded account keeps the role it was given, not the fallback.
    user_repo.seed_roster(db)
    admin = user_repo.resolve_session_user(db, "admin@aat.com", Division.MULTIFAMILY)
    assert admin.role == Role.SUPER_USER
    db.close()
