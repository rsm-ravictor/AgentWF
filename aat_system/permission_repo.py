"""What each level is allowed to do, per division, editable at runtime.

Access has two axes and they answer different questions. **Division** is which
business line an account belongs to — Residential, Office/Retail, Construction.
**Level** is how far it sees inside that line — general, admin, super admin.
Grants are therefore keyed by both: "super admin" is a title three different
people hold, one per division, and each division may define its levels
differently.

`config.ROLE_PERMISSIONS` is the shipped default for a level — the answer on a
fresh database and the answer "Restore defaults" returns to. This module is the
live configuration on top of it, and every gate in the app resolves through here
rather than through the constant, so a change in Settings is a change to what the
system actually allows.
"""

import json
from typing import List, Optional

from sqlalchemy.orm import Session

from .config import (
    ACTIVE_DIVISIONS,
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
from .models import RolePermissionSet


def _valid(keys) -> List[str]:
    """Keep only real permissions, in the canonical order.

    Order matters because the stored list is compared against the shipped
    default to decide whether a level still reads as "default".
    """
    wanted = set(keys or [])
    return [p.value for p in Permission if p.value in wanted]


def ensure_seeded(db: Session, updated_by: str = "AAT default") -> int:
    """Write the shipped defaults for any division/level never configured."""
    created = 0
    for division in Division:
        for role in Role:
            existing = (
                db.query(RolePermissionSet)
                .filter(RolePermissionSet.division == division, RolePermissionSet.role == role)
                .first()
            )
            if existing:
                continue
            db.add(
                RolePermissionSet(
                    division=division,
                    role=role,
                    permissions=json.dumps(permissions_for(role)),
                    updated_by=updated_by,
                )
            )
            created += 1
    if created:
        db.commit()
    return created


def granted_for(db: Session, division: Division, role: Role) -> List[str]:
    """The permissions a level currently holds in a division.

    Falls back to the shipped default when that pair has no row, so a database
    that predates a division or this table still behaves.
    """
    row = (
        db.query(RolePermissionSet)
        .filter(RolePermissionSet.division == division, RolePermissionSet.role == role)
        .first()
    )
    if row is None:
        return permissions_for(role)
    return _valid(json.loads(row.permissions or "[]"))


def granted_for_user(db: Session, user) -> List[str]:
    """The permissions one account holds, from its own division and level."""
    return granted_for(db, user.division, user.role)


def role_has(db: Session, division: Division, role: Role, permission: Permission) -> bool:
    return permission.value in granted_for(db, division, role)


def set_for(
    db: Session,
    division: Division,
    role: Role,
    permissions: List[str],
    updated_by: Optional[str] = None,
) -> dict:
    """Replace what a level grants in one division."""
    keys = _valid(permissions)
    row = (
        db.query(RolePermissionSet)
        .filter(RolePermissionSet.division == division, RolePermissionSet.role == role)
        .first()
    )
    if row is None:
        row = RolePermissionSet(division=division, role=role)
        db.add(row)
    row.permissions = json.dumps(keys)
    row.updated_by = updated_by or None
    db.commit()
    return _role_dict(db, division, role)


def reset(db: Session, division: Division, updated_by: Optional[str] = None) -> List[dict]:
    """Put one division's levels back to what the system ships with."""
    for role in Role:
        set_for(db, division, role, permissions_for(role), updated_by=updated_by)
    return matrix(db, division)


def _role_dict(db: Session, division: Division, role: Role) -> dict:
    granted = granted_for(db, division, role)
    default = permissions_for(role)
    row = (
        db.query(RolePermissionSet)
        .filter(RolePermissionSet.division == division, RolePermissionSet.role == role)
        .first()
    )
    return {
        "key": role.value,
        "label": ROLE_LABELS.get(role, role.value),
        "description": ROLE_DESCRIPTIONS.get(role, ""),
        "level": (ROLE_ORDER.index(role) + 1) if role in ROLE_ORDER else 0,
        "division": division.value,
        "division_key": division_key(division),
        "granted": granted,
        "default": default,
        "is_default": granted == default,
        "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
        "updated_by": row.updated_by if row is not None else None,
    }


def matrix(db: Session, division: Division) -> List[dict]:
    """One division's levels and what each grants, least to most privileged."""
    ensure_seeded(db)
    ordered = ROLE_ORDER + [r for r in Role if r not in ROLE_ORDER]
    return [_role_dict(db, division, role) for role in ordered]


def catalog() -> List[dict]:
    """Every permission, with the label the Settings column shows."""
    return [{"key": p.value, "label": PERMISSION_LABELS[p]} for p in Permission]


def division_catalog() -> List[dict]:
    """The reachable divisions, for the switcher above the matrix.

    Paused business lines are left out rather than deleted: their permission sets
    and users stay in the database, but nothing in the UI routes to them.
    """
    return [
        {"key": division_key(d), "value": d.value, "label": DIVISION_LABELS.get(d, d.value)}
        for d in ACTIVE_DIVISIONS
    ]
