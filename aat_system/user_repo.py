"""User roster, levels, and what each level actually grants.

An account is a division plus a level. The division is the boundary; the level is
the depth. Permissions are not stored per user — they are derived from the
account's (division, level) pair in `permission_repo`, so what a level means is
changed in one place rather than on every account holding it.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from .auth import get_allowed_folders
from .config import (
    ACTIVE_DIVISIONS,
    DEFAULT_DIVISION,
    DIVISION_LABELS,
    PERMISSION_LABELS,
    ROLE_DESCRIPTIONS,
    ROLE_LABELS,
    ROLE_ORDER,
    Division,
    Permission,
    Role,
    division_key,
    permissions_for,
)
from . import permission_repo
from .models import User
from .security import get_password_hash

# A starting roster so Settings has real rows to manage on a fresh database, and
# so every division can be signed into at every level. Passwords are placeholders
# — the preview UI does not issue tokens. Each division gets its *own* super
# admin: the title belongs to a business line, not to the company.
#
# The last field marks a test account: same mechanics as any other, but named and
# grouped as an obvious place to try a level out.
def _roster_for(division: Division, slug: str, names: tuple) -> list:
    """One division's three levels, plus a test account for each."""
    super_name, admin_name, general_name = names
    return [
        (f"super.{slug}@aat.com", super_name, division, Role.SUPER_ADMIN, False),
        (f"admin.{slug}@aat.com", admin_name, division, Role.ADMIN, False),
        (f"user.{slug}@aat.com", general_name, division, Role.GENERAL, False),
        (f"test.super.{slug}@aat.com", "Test Super Admin", division, Role.SUPER_ADMIN, True),
        (f"test.admin.{slug}@aat.com", "Test Admin", division, Role.ADMIN, True),
        (f"test.user.{slug}@aat.com", "Test General", division, Role.GENERAL, True),
    ]


DEFAULT_ROSTER = (
    _roster_for(Division.MULTIFAMILY, "residential", ("Avery Reyes", "Dana Whitfield", "Robin Diaz"))
    + _roster_for(Division.OFFICE, "retail", ("Sam Ortega", "Priya Raman", "Chris Nolan"))
    + _roster_for(Division.CONSTRUCTION, "construction", ("Marisol Vega", "Theo Nakamura", "Alex Duarte"))
)

TEST_ACCOUNT_EMAILS = {email for email, _n, _d, _r, is_test in DEFAULT_ROSTER if is_test}

DEFAULT_PASSWORD = "prototype"

# An email that is not on the roster gets the lowest level. It is the safe
# default: a new account can run work and see its own, but cannot sign off,
# oversee anyone, or manage access.
FALLBACK_ROLE = Role.GENERAL


def seed_roster(db: Session) -> int:
    """Create the starting roster once. Existing users are left alone."""
    created = 0
    for email, name, division, role, _is_test in DEFAULT_ROSTER:
        if db.query(User).filter(User.email == email).first():
            continue
        db.add(
            User(
                email=email,
                name=name,
                division=division,
                role=role,
                hashed_password=get_password_hash(DEFAULT_PASSWORD),
                is_active=True,
            )
        )
        created += 1
    if created:
        db.commit()
    return created


def resolve_session_user(db: Session, email: str, division: Division, name: str = "") -> User:
    """Look up the signing-in user, provisioning at least privilege if unknown.

    The preview UI does not issue tokens, so this is how a session gets a real
    level: the email is matched against the roster. Anything unrecognised becomes
    a general user in the division being signed into, rather than inheriting
    whatever the caller claims.
    """
    email = (email or "").strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user

    user = User(
        email=email,
        name=name.strip() or email.split("@")[0].replace(".", " ").title(),
        division=division,
        role=FALLBACK_ROLE,
        hashed_password=get_password_hash(DEFAULT_PASSWORD),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def session_division(db: Session, user: User, signing_into: Division) -> User:
    """Run a session in an active division when the account's own is paused.

    Residential and Construction are paused, but their accounts still exist and
    are still what people type at the login screen. Without this the division
    picker's answer is silently discarded in favour of the row's stored value,
    and signing in to Office/Retail lands in a division nothing routes to.

    The stored row is deliberately left alone: pausing a business line must not
    rewrite who belongs to it, or un-pausing one would find its roster moved out
    from under it. The instance is detached first so the swap cannot be flushed
    back, which makes this a property of the session rather than of the account.
    """
    if user.division in ACTIVE_DIVISIONS:
        return user
    target = signing_into if signing_into in ACTIVE_DIVISIONS else DEFAULT_DIVISION
    db.expunge(user)
    user.division = target
    return user


def create_account(
    db: Session,
    email: str,
    name: str,
    division: Division,
    role: Role,
    password: str = "",
) -> User:
    """Add an account to the roster.

    The password is optional because the preview UI issues no tokens — an account
    created here is signed into by username alone. It is still hashed and stored
    so the real `/token` flow works against these accounts unchanged.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("A valid email address is required.")
    if db.query(User).filter(User.email == email).first():
        raise ValueError(f"An account for {email} already exists.")

    user = User(
        email=email,
        name=(name or "").strip() or email.split("@")[0].replace(".", " ").title(),
        division=division,
        role=role,
        hashed_password=get_password_hash(password or DEFAULT_PASSWORD),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def profile(user: User, db: Optional[Session] = None) -> dict:
    """Everything the Profile page shows about one account.

    With a session, what the level grants is read from the live configuration for
    that account's *division* — Construction's admin may hold different
    permissions from Residential's. Without a session, the shipped defaults.
    Every caller that has a session should pass it: the UI gates on these flags,
    so reading the constant instead would show access that has since been changed.
    """
    granted = (
        permission_repo.granted_for_user(db, user) if db is not None else permissions_for(user.role)
    )
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "division": user.division.value,
        "division_key": division_key(user.division),
        "division_label": DIVISION_LABELS.get(user.division, user.division.value),
        "role": user.role.value,
        "role_label": ROLE_LABELS.get(user.role, user.role.value),
        "role_description": ROLE_DESCRIPTIONS.get(user.role, ""),
        # The title as it is actually held: "Super admin, Construction". A level
        # without its division is only half an answer, because the level does not
        # reach outside it.
        "title": f"{ROLE_LABELS.get(user.role, user.role.value)} · {DIVISION_LABELS.get(user.division, '')}",
        "access_level": ROLE_ORDER.index(user.role) + 1 if user.role in ROLE_ORDER else 0,
        "access_levels_total": len(ROLE_ORDER),
        "permission_count": len(granted),
        "permission_total": len(Permission),
        "is_active": bool(user.is_active),
        "permissions": granted,
        "permission_matrix": [
            {"key": p.value, "label": PERMISSION_LABELS[p], "granted": p.value in granted}
            for p in Permission
        ],
        "allowed_folders": get_allowed_folders(user),
        "can_manage_users": Permission.MANAGE_USERS.value in granted,
        "can_manage_roles": Permission.MANAGE_ROLES.value in granted,
        "can_edit_workflow": Permission.EDIT_WORKFLOW.value in granted,
        "can_approve": Permission.APPROVE_WORKFLOW.value in granted,
        # Whose work this account sees. False means its own only, which is what
        # separates a general user from the levels overseeing them.
        "can_view_team": Permission.VIEW_TEAM_ACTIVITY.value in granted,
        "can_view_all_divisions": Permission.VIEW_ALL_DIVISIONS.value in granted,
    }


def list_users(db: Session, division: Optional[Division] = None) -> List[dict]:
    query = db.query(User)
    if division is not None:
        query = query.filter(User.division == division)
    users = query.order_by(User.name.asc()).all()
    return [profile(u, db) for u in users]


def roster_accounts(db: Session) -> List[dict]:
    """The seeded accounts, for the prototype login screen's quick pick.

    Deliberately only the seeded roster rather than every provisioned user, so a
    login screen does not enumerate the user table. Each entry carries its
    division, because the same level in a different division is a different
    account with no reach into this one.
    """
    accounts = []
    for email, _name, _division, _role, is_test in DEFAULT_ROSTER:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            continue
        granted = permission_repo.granted_for_user(db, user)
        accounts.append(
            {
                "email": user.email,
                "name": user.name,
                "role": user.role.value,
                "role_label": ROLE_LABELS.get(user.role, user.role.value),
                "role_description": ROLE_DESCRIPTIONS.get(user.role, ""),
                "division_key": division_key(user.division),
                "division_label": DIVISION_LABELS.get(user.division, user.division.value),
                "can_edit_workflow": Permission.EDIT_WORKFLOW.value in granted,
                "can_view_team": Permission.VIEW_TEAM_ACTIVITY.value in granted,
                "is_test": is_test,
            }
        )
    # Most privileged first inside each division: the account someone reaches for
    # to set things up is at the top.
    accounts.sort(
        key=lambda a: -(ROLE_ORDER.index(Role(a["role"])) if Role(a["role"]) in ROLE_ORDER else 0)
    )
    return accounts


def role_catalog(db: Optional[Session] = None, division: Optional[Division] = None) -> List[dict]:
    """Every level and what it grants, for the Settings role menus.

    Reads the live grants for a division when given one, so the level menu agrees
    with the permissions matrix rather than quoting shipped defaults back.
    """
    return [
        {
            "key": role.value,
            "label": ROLE_LABELS.get(role, role.value),
            "description": ROLE_DESCRIPTIONS.get(role, ""),
            "level": index + 1,
            "permissions": (
                permission_repo.granted_for(db, division, role)
                if db is not None and division is not None
                else permissions_for(role)
            ),
        }
        for index, role in enumerate(ROLE_ORDER)
    ]


def permission_catalog() -> List[dict]:
    return [{"key": p.value, "label": PERMISSION_LABELS[p]} for p in Permission]
