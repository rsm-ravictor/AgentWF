from fastapi import HTTPException, status
from .config import CORE_FOLDERS, Division, Permission, Role, has_permission
from .models import User

class AccessDenied(Exception):
    pass

DIVISION_FULL_ACCESS = {
    Division.MULTIFAMILY: [Role.SUPER_USER, Role.ADMIN, Role.DIVISION_HEAD],
    Division.OFFICE: [Role.SUPER_USER, Role.ADMIN, Role.DIVISION_HEAD],
}

SUBGROUP_SCOPES = {
    Role.SUBGROUP_OWNER: list(CORE_FOLDERS),
}

# Roles that see every folder in their division.
FULL_FOLDER_ROLES = {Role.SUPER_USER, Role.ADMIN, Role.DIVISION_HEAD}

def assert_division_access(user: User, division: Division):
    # A super user is not scoped to one division; everyone else is.
    if has_permission(user.role, Permission.VIEW_ALL_DIVISIONS):
        return
    if user.division != division:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to this division.")

def assert_folder_access(user: User, folder_name: str):
    if folder_name in get_allowed_folders(user):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not have access to this folder.")

def get_allowed_folders(user: User):
    if user.role in FULL_FOLDER_ROLES:
        return list(CORE_FOLDERS)
    if user.role in (Role.SUBGROUP_OWNER, Role.AGENT, Role.REVIEWER):
        return SUBGROUP_SCOPES[Role.SUBGROUP_OWNER]
    return []

def assert_permission(user: User, permission: Permission):
    """Gate an action on the caller's role."""
    if not has_permission(user.role, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role ({user.role.value}) does not grant '{permission.value}'.",
        )
