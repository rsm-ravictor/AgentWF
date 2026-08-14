"""What each role is allowed to do, editable at runtime.

`config.ROLE_PERMISSIONS` is the shipped default — the answer on a fresh
database and the answer "Restore defaults" returns to. This module is the live
configuration on top of it: the Permissions page reads the matrix from here and
writes back what someone changed, and every gate in the app resolves through
here rather than through the constant, so a change on that page is a change to
what the system actually allows.

Roles themselves are fixed in code (they carry meaning the UI relies on); what a
role *grants* is data.
"""

import json
from typing import List, Optional

from sqlalchemy.orm import Session

from .config import (
    PERMISSION_LABELS,
    ROLE_DESCRIPTIONS,
    ROLE_LABELS,
    ROLE_ORDER,
    Permission,
    Role,
    permissions_for,
)
from .models import RolePermissionSet


def _valid(keys) -> List[str]:
    """Keep only real permissions, in the canonical order.

    Order matters because the stored list is compared against the shipped
    default to decide whether a role still reads as "default".
    """
    wanted = set(keys or [])
    return [p.value for p in Permission if p.value in wanted]


def ensure_seeded(db: Session, updated_by: str = "AAT default") -> int:
    """Write the shipped defaults for any role that has never been configured."""
    created = 0
    for role in Role:
        if db.query(RolePermissionSet).filter(RolePermissionSet.role == role).first():
            continue
        db.add(
            RolePermissionSet(
                role=role,
                permissions=json.dumps(permissions_for(role)),
                updated_by=updated_by,
            )
        )
        created += 1
    if created:
        db.commit()
    return created


def granted_for(db: Session, role: Role) -> List[str]:
    """The permissions a role currently holds.

    Falls back to the shipped default when the role has no row at all, so a
    database that predates this table still behaves.
    """
    row = db.query(RolePermissionSet).filter(RolePermissionSet.role == role).first()
    if row is None:
        return permissions_for(role)
    return _valid(json.loads(row.permissions or "[]"))


def role_has(db: Session, role: Role, permission: Permission) -> bool:
    return permission.value in granted_for(db, role)


def set_for(
    db: Session, role: Role, permissions: List[str], updated_by: Optional[str] = None
) -> dict:
    """Replace what a role grants."""
    keys = _valid(permissions)
    row = db.query(RolePermissionSet).filter(RolePermissionSet.role == role).first()
    if row is None:
        row = RolePermissionSet(role=role)
        db.add(row)
    row.permissions = json.dumps(keys)
    row.updated_by = updated_by or None
    db.commit()
    return _role_dict(db, role)


def reset(db: Session, updated_by: Optional[str] = None) -> List[dict]:
    """Put every role back to what the system ships with."""
    for role in Role:
        set_for(db, role, permissions_for(role), updated_by=updated_by)
    return matrix(db)


def _role_dict(db: Session, role: Role) -> dict:
    granted = granted_for(db, role)
    default = permissions_for(role)
    row = db.query(RolePermissionSet).filter(RolePermissionSet.role == role).first()
    return {
        "key": role.value,
        "label": ROLE_LABELS.get(role, role.value),
        "description": ROLE_DESCRIPTIONS.get(role, ""),
        "level": (ROLE_ORDER.index(role) + 1) if role in ROLE_ORDER else 0,
        "granted": granted,
        "default": default,
        "is_default": granted == default,
        "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
        "updated_by": row.updated_by if row is not None else None,
    }


def matrix(db: Session) -> List[dict]:
    """Every role and what it currently grants, least to most privileged."""
    ensure_seeded(db)
    ordered = ROLE_ORDER + [r for r in Role if r not in ROLE_ORDER]
    return [_role_dict(db, role) for role in ordered]


def catalog() -> List[dict]:
    """Every permission, with the label the Permissions page column shows."""
    return [{"key": p.value, "label": PERMISSION_LABELS[p]} for p in Permission]
