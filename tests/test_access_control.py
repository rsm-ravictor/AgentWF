"""Tests for levels, per-division permissions, and folder scope.

Access has two axes: the **division** an account belongs to, and its **level**
within it. Both matter — Residential's super admin and Construction's super admin
share a title and nothing else — so these assert the shape of the ladder, that
divisions do not leak into each other, and that a level means whatever its own
division says it means.

    python -m pytest tests/ -q
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aat_system import permission_repo, user_repo
from aat_system.auth import assert_division_access, assert_folder_access, get_allowed_folders
from aat_system.config import (
    CONSTRUCTION_FOLDERS,
    CORE_FOLDERS,
    ROLE_ORDER,
    SENIOR_TIER,
    Division,
    Permission,
    Role,
    folders_for,
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


def test_there_are_exactly_three_levels():
    assert [r.value for r in ROLE_ORDER] == ["general", "admin", "super_admin"]


def test_a_super_admin_holds_everything_except_reach_into_other_divisions():
    """The one thing a super admin does not get is somebody else's division.

    Each business line has its own, so unrestricted-within-my-division is the
    ceiling. Crossing over is a permission somebody has to grant deliberately.
    """
    granted = set(permissions_for(Role.SUPER_ADMIN))
    assert granted == {p.value for p in Permission} - {Permission.VIEW_ALL_DIVISIONS.value}


def test_no_level_sees_across_divisions_by_default():
    assert [r for r in Role if has_permission(r, Permission.VIEW_ALL_DIVISIONS)] == []


def test_the_levels_are_a_true_ladder():
    """General ⊂ Admin ⊂ Super admin. Promoting never takes access away."""
    ladder = [Role.GENERAL] + list(SENIOR_TIER)
    for lower, higher in zip(ladder, ladder[1:]):
        assert set(permissions_for(lower)) <= set(permissions_for(higher)), (
            f"{higher.value} does not include everything {lower.value} grants"
        )


def test_every_level_can_run_use_cases():
    """Execution is the point of the system; no level is a spectator."""
    for role in Role:
        assert has_permission(role, Permission.RUN_WORKFLOW), f"{role.value} cannot run a use case"


def test_a_general_user_executes_but_neither_approves_nor_oversees():
    assert has_permission(Role.GENERAL, Permission.RUN_WORKFLOW)
    assert has_permission(Role.GENERAL, Permission.UPLOAD_DOCUMENTS)
    for withheld in (
        Permission.APPROVE_WORKFLOW,
        Permission.EDIT_WORKFLOW,
        Permission.MANAGE_USERS,
        Permission.MANAGE_ROLES,
        # The one that keeps a general user's view to their own work.
        Permission.VIEW_TEAM_ACTIVITY,
    ):
        assert not has_permission(Role.GENERAL, withheld)


def test_an_admin_oversees_general_users_and_edits_use_cases():
    for granted in (
        Permission.VIEW_TEAM_ACTIVITY,
        Permission.EDIT_WORKFLOW,
        Permission.APPROVE_WORKFLOW,
        Permission.MANAGE_USERS,
    ):
        assert has_permission(Role.ADMIN, granted)
    # Assigning permissions is the super admin's, so an admin cannot widen itself.
    assert not has_permission(Role.ADMIN, Permission.MANAGE_ROLES)


def test_only_a_super_admin_assigns_permissions():
    assert {r for r in Role if has_permission(r, Permission.MANAGE_ROLES)} == {Role.SUPER_ADMIN}


def test_an_account_reaches_its_own_divisions_folders(db):
    assert get_allowed_folders(user(Role.GENERAL)) == list(CORE_FOLDERS)
    # Construction has paperwork the others do not, and its general users work it.
    construction = get_allowed_folders(user(Role.GENERAL, Division.CONSTRUCTION))
    assert construction == list(CONSTRUCTION_FOLDERS)
    assert "Permits and Approvals" in construction
    assert "Permits and Approvals" not in get_allowed_folders(user(Role.SUPER_ADMIN))


def test_folder_access_is_refused_for_a_folder_outside_scope():
    with pytest.raises(Exception):
        assert_folder_access(user(Role.GENERAL), "Nonexistent Folder")
    # A real folder, but not one this division has.
    with pytest.raises(Exception):
        assert_folder_access(user(Role.SUPER_ADMIN), "Change Orders")


def test_a_super_admin_is_refused_another_divisions_work():
    residential = user(Role.SUPER_ADMIN, Division.MULTIFAMILY)
    assert_division_access(residential, Division.MULTIFAMILY)  # its own: fine
    with pytest.raises(Exception):
        assert_division_access(residential, Division.CONSTRUCTION)
    # Unless somebody granted the cross-division permission.
    assert_division_access(
        residential, Division.CONSTRUCTION, granted=[Permission.VIEW_ALL_DIVISIONS.value]
    )


def test_profile_reports_the_level_the_division_and_matching_permissions():
    profile = user_repo.profile(user(Role.ADMIN, Division.CONSTRUCTION))
    assert profile["access_level"] == ROLE_ORDER.index(Role.ADMIN) + 1
    assert profile["access_levels_total"] == 3
    assert profile["division_key"] == "construction"
    # A level without its division is only half an answer.
    assert profile["title"] == "Admin · Construction"
    assert profile["can_approve"] is True
    assert profile["can_view_team"] is True
    assert profile["can_manage_roles"] is False
    assert profile["can_view_all_divisions"] is False
    granted = {p["key"] for p in profile["permission_matrix"] if p["granted"]}
    assert granted == set(profile["permissions"])


def test_a_general_users_profile_says_it_sees_only_its_own_work():
    profile = user_repo.profile(user(Role.GENERAL))
    assert profile["can_view_team"] is False


def test_role_catalog_is_ordered_least_privileged_first():
    catalog = user_repo.role_catalog()
    assert [r["level"] for r in catalog] == [1, 2, 3]
    assert catalog[0]["key"] == Role.GENERAL.value
    assert catalog[-1]["key"] == Role.SUPER_ADMIN.value


def test_unknown_email_is_provisioned_at_least_privilege(db):
    resolved = user_repo.resolve_session_user(db, "stranger@example.com", Division.CONSTRUCTION)
    assert resolved.role == user_repo.FALLBACK_ROLE == Role.GENERAL
    # Provisioned into the division being signed into, not a default one.
    assert resolved.division == Division.CONSTRUCTION

    # A seeded account keeps the level it was given, not the fallback.
    user_repo.seed_roster(db)
    boss = user_repo.resolve_session_user(db, "super.construction@aat.com", Division.CONSTRUCTION)
    assert boss.role == Role.SUPER_ADMIN


# ---------------- The roster, including the test accounts ----------------

def test_every_division_has_its_own_account_at_every_level(db):
    """The point of per-division levels: three super admins, not one.

    Residential, Retail and Construction each get their own, and none of them is
    the same account.
    """
    user_repo.seed_roster(db)
    accounts = user_repo.roster_accounts(db)

    for division_key in ("mf", "retail", "construction"):
        levels = {a["role"] for a in accounts if a["division_key"] == division_key and not a["is_test"]}
        assert levels == {r.value for r in Role}, f"{division_key} is missing a level"

    supers = [a for a in accounts if a["role"] == Role.SUPER_ADMIN.value and not a["is_test"]]
    assert len(supers) == 3
    assert len({a["email"] for a in supers}) == 3
    assert {a["division_key"] for a in supers} == {"mf", "retail", "construction"}


def test_there_is_a_test_account_for_every_level_in_every_division(db):
    user_repo.seed_roster(db)
    test_accounts = [a for a in user_repo.roster_accounts(db) if a["is_test"]]

    pairs = {(a["division_key"], a["role"]) for a in test_accounts}
    assert pairs == {(d, r.value) for d in ("mf", "retail", "construction") for r in Role}
    # Named so nobody mistakes one for a real person's account.
    assert all(a["name"].startswith("Test") for a in test_accounts)
    assert all(a["email"].startswith("test.") for a in test_accounts)


def test_creating_a_profile_puts_it_on_the_roster_with_its_level(db):
    created = user_repo.create_account(
        db,
        email="New.Person@AAT.com",
        name="New Person",
        division=Division.CONSTRUCTION,
        role=Role.ADMIN,
    )

    assert created.email == "new.person@aat.com"  # normalised, so logins match
    assert created.role == Role.ADMIN
    assert created.is_active is True

    profile = user_repo.profile(created, db)
    assert profile["can_approve"] is True
    assert profile["can_view_team"] is True
    assert profile["can_manage_roles"] is False
    assert profile["division_key"] == "construction"


def test_a_profile_created_without_a_name_gets_one_from_its_email(db):
    created = user_repo.create_account(
        db, email="dana.cole@aat.com", name="  ", division=Division.MULTIFAMILY, role=Role.GENERAL
    )
    assert created.name == "Dana Cole"


def test_a_profile_created_without_a_password_can_still_sign_in(db):
    from aat_system.security import verify_password

    created = user_repo.create_account(
        db, email="nopass@aat.com", name="No Pass", division=Division.MULTIFAMILY, role=Role.GENERAL
    )
    assert created.hashed_password
    assert verify_password(user_repo.DEFAULT_PASSWORD, created.hashed_password)


def test_a_duplicate_email_is_refused(db):
    user_repo.create_account(
        db, email="dup@aat.com", name="First", division=Division.MULTIFAMILY, role=Role.GENERAL
    )
    with pytest.raises(ValueError):
        user_repo.create_account(
            db, email="dup@aat.com", name="Second", division=Division.MULTIFAMILY, role=Role.GENERAL
        )


def test_an_invalid_email_is_refused(db):
    with pytest.raises(ValueError):
        user_repo.create_account(
            db, email="not-an-email", name="X", division=Division.MULTIFAMILY, role=Role.GENERAL
        )


def test_seeding_the_roster_twice_creates_no_duplicates(db):
    first = user_repo.seed_roster(db)
    assert first == len(user_repo.DEFAULT_ROSTER)
    assert user_repo.seed_roster(db) == 0


# ---------------- Permissions configured per division, at runtime ----------------

MF, RETAIL, CONSTRUCTION = Division.MULTIFAMILY, Division.OFFICE, Division.CONSTRUCTION


def test_permissions_start_at_the_shipped_defaults_in_every_division(db):
    permission_repo.ensure_seeded(db)
    for division in Division:
        for role in Role:
            assert permission_repo.granted_for(db, division, role) == permissions_for(role)


def test_an_unseeded_pair_falls_back_to_its_shipped_default(db):
    # A database that predates the table, or a division added after it, still answers.
    assert permission_repo.granted_for(db, CONSTRUCTION, Role.GENERAL) == permissions_for(Role.GENERAL)


def test_granting_a_permission_affects_only_that_division(db):
    """The whole point of keying grants by division.

    Letting Construction's general users edit use cases must not quietly let
    Residential's do the same.
    """
    permission_repo.ensure_seeded(db)
    permission_repo.set_for(
        db,
        CONSTRUCTION,
        Role.GENERAL,
        permissions_for(Role.GENERAL) + [Permission.EDIT_WORKFLOW.value],
        updated_by="Marisol",
    )

    assert permission_repo.role_has(db, CONSTRUCTION, Role.GENERAL, Permission.EDIT_WORKFLOW)
    assert not permission_repo.role_has(db, MF, Role.GENERAL, Permission.EDIT_WORKFLOW)
    assert not permission_repo.role_has(db, RETAIL, Role.GENERAL, Permission.EDIT_WORKFLOW)

    # And the profile the UI gates on reads the account's own division.
    assert user_repo.profile(user(Role.GENERAL, CONSTRUCTION), db)["can_edit_workflow"] is True
    assert user_repo.profile(user(Role.GENERAL, MF), db)["can_edit_workflow"] is False


def test_a_level_can_be_stripped_to_nothing_and_stays_stripped(db):
    permission_repo.set_for(db, RETAIL, Role.ADMIN, [], updated_by="Sam")
    # The distinction that matters: no permissions is a real answer, not an
    # unconfigured level that quietly reverts to the defaults.
    assert permission_repo.granted_for(db, RETAIL, Role.ADMIN) == []
    permission_repo.ensure_seeded(db)
    assert permission_repo.granted_for(db, RETAIL, Role.ADMIN) == []
    # Another division's admin is untouched.
    assert permission_repo.granted_for(db, MF, Role.ADMIN) == permissions_for(Role.ADMIN)


def test_unknown_permission_keys_are_ignored(db):
    permission_repo.set_for(db, MF, Role.GENERAL, ["edit_workflow", "become_president"])
    assert permission_repo.granted_for(db, MF, Role.GENERAL) == [Permission.EDIT_WORKFLOW.value]


def test_restoring_defaults_undoes_changes_in_that_division_only(db):
    permission_repo.set_for(db, MF, Role.GENERAL, [Permission.MANAGE_USERS.value])
    permission_repo.set_for(db, CONSTRUCTION, Role.GENERAL, [Permission.MANAGE_USERS.value])

    permission_repo.reset(db, MF, updated_by="Avery")

    for role in Role:
        assert permission_repo.granted_for(db, MF, role) == permissions_for(role)
    # Construction runs its own configuration; resetting Residential leaves it be.
    assert permission_repo.granted_for(db, CONSTRUCTION, Role.GENERAL) == [
        Permission.MANAGE_USERS.value
    ]


def test_the_matrix_is_per_division_and_flags_what_changed(db):
    permission_repo.set_for(db, CONSTRUCTION, Role.GENERAL, [Permission.EDIT_WORKFLOW.value])

    construction = {r["key"]: r for r in permission_repo.matrix(db, CONSTRUCTION)}
    residential = {r["key"]: r for r in permission_repo.matrix(db, MF)}

    assert construction[Role.GENERAL.value]["is_default"] is False
    assert construction[Role.GENERAL.value]["default"] == permissions_for(Role.GENERAL)
    assert construction[Role.GENERAL.value]["division_key"] == "construction"
    assert residential[Role.GENERAL.value]["is_default"] is True
    # Least privileged first, so the page reads as a ladder.
    assert [r["key"] for r in permission_repo.matrix(db, MF)][0] == Role.GENERAL.value


def test_every_division_appears_in_the_switcher():
    keys = [d["key"] for d in permission_repo.division_catalog()]
    assert keys == ["mf", "retail", "construction"]


def test_the_role_catalog_reflects_a_live_change_in_its_division(db):
    permission_repo.set_for(db, CONSTRUCTION, Role.GENERAL, [Permission.EDIT_WORKFLOW.value])
    catalog = {r["key"]: r for r in user_repo.role_catalog(db, CONSTRUCTION)}
    assert catalog[Role.GENERAL.value]["permissions"] == [Permission.EDIT_WORKFLOW.value]
    unchanged = {r["key"]: r for r in user_repo.role_catalog(db, MF)}
    assert unchanged[Role.GENERAL.value]["permissions"] == permissions_for(Role.GENERAL)
