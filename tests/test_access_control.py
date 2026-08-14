"""Tests for roles, permissions, and folder scope.

The Profile and Admin pages derive everything from these tables, so a role that
silently grants too much would be invisible in the UI. These assert the shape of
the privilege ladder rather than each individual grant.

    python -m pytest tests/ -q
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aat_system import permission_repo, user_repo
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
from aat_system.models import Base, User


def user(role, division=Division.MULTIFAMILY):
    return User(id=1, email="x@aat.com", name="X", division=division, role=role, hashed_password="x")


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'access.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


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


def test_unknown_email_is_provisioned_at_least_privilege(db):
    resolved = user_repo.resolve_session_user(db, "stranger@example.com", Division.MULTIFAMILY)
    assert resolved.role == user_repo.FALLBACK_ROLE == Role.AGENT

    # A seeded account keeps the role it was given, not the fallback.
    user_repo.seed_roster(db)
    admin = user_repo.resolve_session_user(db, "admin@aat.com", Division.MULTIFAMILY)
    assert admin.role == Role.SUPER_USER


# ---------------- The roster, including the test accounts ----------------

def test_the_roster_covers_every_role(db):
    user_repo.seed_roster(db)
    roles = {a["role"] for a in user_repo.roster_accounts(db)}
    assert roles == {r.value for r in Role}


def test_there_is_a_test_account_for_every_role(db):
    user_repo.seed_roster(db)
    test_accounts = [a for a in user_repo.roster_accounts(db) if a["is_test"]]
    assert {a["role"] for a in test_accounts} == {r.value for r in Role}
    # Named so nobody mistakes one for a real person's account.
    assert all(a["name"].startswith("Test") for a in test_accounts)
    assert all(a["email"].startswith("test.") for a in test_accounts)


def test_seeding_the_roster_twice_creates_no_duplicates(db):
    first = user_repo.seed_roster(db)
    assert first == len(user_repo.DEFAULT_ROSTER)
    assert user_repo.seed_roster(db) == 0


# ---------------- Permissions configured at runtime ----------------

def test_permissions_start_at_the_shipped_defaults(db):
    permission_repo.ensure_seeded(db)
    for role in Role:
        assert permission_repo.granted_for(db, role) == permissions_for(role)


def test_an_unseeded_role_falls_back_to_its_shipped_default(db):
    # A database that predates the table still answers correctly.
    assert permission_repo.granted_for(db, Role.AGENT) == permissions_for(Role.AGENT)


def test_granting_a_permission_takes_effect_for_that_role(db):
    permission_repo.ensure_seeded(db)
    assert not permission_repo.role_has(db, Role.AGENT, Permission.EDIT_WORKFLOW)

    permission_repo.set_for(
        db,
        Role.AGENT,
        permissions_for(Role.AGENT) + [Permission.EDIT_WORKFLOW.value],
        updated_by="Jordan",
    )

    assert permission_repo.role_has(db, Role.AGENT, Permission.EDIT_WORKFLOW)
    # And the profile the UI gates on agrees, rather than quoting the constant.
    profile = user_repo.profile(user(Role.AGENT), db)
    assert profile["can_edit_workflow"] is True
    assert Permission.EDIT_WORKFLOW.value in profile["permissions"]


def test_a_role_can_be_stripped_to_nothing_and_stays_stripped(db):
    permission_repo.set_for(db, Role.REVIEWER, [], updated_by="Jordan")
    # The distinction that matters: no permissions is a real answer, not an
    # unconfigured role that quietly reverts to the defaults.
    assert permission_repo.granted_for(db, Role.REVIEWER) == []
    permission_repo.ensure_seeded(db)
    assert permission_repo.granted_for(db, Role.REVIEWER) == []


def test_unknown_permission_keys_are_ignored(db):
    permission_repo.set_for(db, Role.AGENT, ["edit_workflow", "become_president"])
    assert permission_repo.granted_for(db, Role.AGENT) == [Permission.EDIT_WORKFLOW.value]


def test_restoring_defaults_undoes_every_change(db):
    permission_repo.set_for(db, Role.AGENT, [Permission.MANAGE_USERS.value])
    permission_repo.set_for(db, Role.SUPER_USER, [])

    permission_repo.reset(db, updated_by="Jordan")

    for role in Role:
        assert permission_repo.granted_for(db, role) == permissions_for(role)


def test_the_matrix_reports_where_a_role_differs_from_default(db):
    permission_repo.set_for(db, Role.AGENT, [Permission.EDIT_WORKFLOW.value])
    rows = {r["key"]: r for r in permission_repo.matrix(db)}

    assert rows[Role.AGENT.value]["is_default"] is False
    assert rows[Role.AGENT.value]["default"] == permissions_for(Role.AGENT)
    assert rows[Role.REVIEWER.value]["is_default"] is True
    # Least privileged first, so the page reads as a ladder.
    assert [r["key"] for r in permission_repo.matrix(db)][0] == Role.AGENT.value


def test_the_role_catalog_reflects_a_live_change(db):
    permission_repo.set_for(db, Role.AGENT, [Permission.EDIT_WORKFLOW.value])
    catalog = {r["key"]: r for r in user_repo.role_catalog(db)}
    assert catalog[Role.AGENT.value]["permissions"] == [Permission.EDIT_WORKFLOW.value]
