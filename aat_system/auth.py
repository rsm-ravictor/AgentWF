"""Access checks: which division an account belongs to, and what it may reach.

Two things are being enforced here, and they are different questions. *Division*
is the boundary — an account works in one business line, and only a permission
granted deliberately (`VIEW_ALL_DIVISIONS`) crosses it, which no level holds by
default. *Level* is the depth within that boundary, and what a level grants is
configured per division in `permission_repo`.
"""

from fastapi import HTTPException, status

from .config import Division, Permission, Role, folders_for
from .models import User


class AccessDenied(Exception):
    pass


def assert_division_access(user: User, division: Division, granted=None):
    """Refuse work in another division.

    `granted` is the account's live permission list when the caller has one;
    without it, no cross-division reach is assumed. That default is the safe one:
    each division has its own super admin, so belonging is the normal answer and
    crossing is the exception.
    """
    if user.division == division:
        return
    if granted is not None and Permission.VIEW_ALL_DIVISIONS.value in granted:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This account does not belong to that division.",
    )


def assert_folder_access(user: User, folder_name: str):
    if folder_name in get_allowed_folders(user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="User does not have access to this folder."
    )


def get_allowed_folders(user: User):
    """The folder categories an account can reach.

    Every level works the whole of its own division's repository — Construction's
    general users see Construction's folders, including the ones Residential does
    not have. What differs by level is what you may *do* (upload, approve, edit),
    which is a permission, and whose work you can see, which is another.
    """
    return folders_for(user.division)


def assert_permission(user: User, permission: Permission, granted=None):
    """Gate an action on what the account's level grants in its division."""
    from .config import permissions_for

    held = granted if granted is not None else permissions_for(user.role)
    if permission.value not in held:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your level ({user.role.value}) does not grant '{permission.value}'.",
        )
