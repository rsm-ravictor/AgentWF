"""User roster, roles, and what each role actually grants.

The Profile and Admin pages read from here. Roles are stored on the user row;
permissions are derived from the role in config.ROLE_PERMISSIONS rather than
stored per user, so there is one place to change what a role means.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from .auth import get_allowed_folders
from .config import (
    PERMISSION_LABELS,
    ROLE_DESCRIPTIONS,
    ROLE_LABELS,
    ROLE_ORDER,
    Division,
    Permission,
    Role,
    permissions_for,
)
from .models import User
from .security import get_password_hash

# A starting roster so the Admin page has real rows to manage on a fresh
# database. Passwords are placeholders — the preview UI does not issue tokens.
DEFAULT_ROSTER = [
    ("admin@aat.com", "Avery Reyes", Division.MULTIFAMILY, Role.SUPER_USER),
    ("head.mf@aat.com", "Jordan Blake", Division.MULTIFAMILY, Role.DIVISION_HEAD),
    ("head.retail@aat.com", "Sam Ortega", Division.OFFICE, Role.DIVISION_HEAD),
    ("owner@aat.com", "Priya Raman", Division.MULTIFAMILY, Role.SUBGROUP_OWNER),
    ("reviewer@aat.com", "Chris Nolan", Division.MULTIFAMILY, Role.REVIEWER),
    ("agent@aat.com", "Robin Diaz", Division.MULTIFAMILY, Role.AGENT),
]

DEFAULT_PASSWORD = "prototype"

# An email that is not on the roster gets the least-privileged role. It is the
# safe default: a new account can run work, but cannot sign off or manage anyone.
FALLBACK_ROLE = Role.AGENT


def seed_roster(db: Session) -> int:
    """Create the starting roster once. Existing users are left alone."""
    created = 0
    for email, name, division, role in DEFAULT_ROSTER:
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
    role: the email is matched against the roster. Anything unrecognised becomes
    an Agent rather than inheriting whatever the caller claims.
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


def profile(user: User) -> dict:
    """Everything the Profile page shows about one account."""
    granted = permissions_for(user.role)
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "division": user.division.value,
        "division_key": "retail" if user.division == Division.OFFICE else "mf",
        "role": user.role.value,
        "role_label": ROLE_LABELS.get(user.role, user.role.value),
        "role_description": ROLE_DESCRIPTIONS.get(user.role, ""),
        "access_level": ROLE_ORDER.index(user.role) + 1 if user.role in ROLE_ORDER else 0,
        "access_levels_total": len(ROLE_ORDER),
        # The meter is driven by permissions actually held, not by rank: the
        # operational roles are peers with different duties, so a rank bar would
        # imply a containment that does not hold. See ROLE_ORDER in config.
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
        "can_edit_workflow": Permission.EDIT_WORKFLOW.value in granted,
        "can_approve": Permission.APPROVE_WORKFLOW.value in granted,
    }


def list_users(db: Session, division: Optional[Division] = None) -> List[dict]:
    query = db.query(User)
    if division is not None:
        query = query.filter(User.division == division)
    users = query.order_by(User.name.asc()).all()
    return [profile(u) for u in users]


def roster_accounts(db: Session) -> List[dict]:
    """The seeded accounts, for the prototype login screen's quick pick.

    Deliberately only the seeded roster rather than every provisioned user, so a
    login screen does not enumerate the user table. Ordered least to most
    privileged, and each says whether it can edit a workflow definition, since
    that is the capability the login choice most often decides.
    """
    accounts = []
    for email, _name, _division, _role in DEFAULT_ROSTER:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            continue
        granted = permissions_for(user.role)
        accounts.append(
            {
                "email": user.email,
                "name": user.name,
                "role": user.role.value,
                "role_label": ROLE_LABELS.get(user.role, user.role.value),
                "role_description": ROLE_DESCRIPTIONS.get(user.role, ""),
                "division_key": "retail" if user.division == Division.OFFICE else "mf",
                "can_edit_workflow": Permission.EDIT_WORKFLOW.value in granted,
            }
        )
    accounts.sort(key=lambda a: ROLE_ORDER.index(Role(a["role"])) if Role(a["role"]) in ROLE_ORDER else 0)
    return accounts


def role_catalog() -> List[dict]:
    """Every role and what it grants — the Admin page's reference table."""
    return [
        {
            "key": role.value,
            "label": ROLE_LABELS.get(role, role.value),
            "description": ROLE_DESCRIPTIONS.get(role, ""),
            "level": index + 1,
            "permissions": permissions_for(role),
        }
        for index, role in enumerate(ROLE_ORDER)
    ]


def permission_catalog() -> List[dict]:
    return [{"key": p.value, "label": PERMISSION_LABELS[p]} for p in Permission]
