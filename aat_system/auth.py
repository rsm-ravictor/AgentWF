from fastapi import HTTPException, status
from .config import Division, Role
from .models import User

class AccessDenied(Exception):
    pass

DIVISION_FULL_ACCESS = {
    Division.MULTIFAMILY: [Role.DIVISION_HEAD],
    Division.OFFICE: [Role.DIVISION_HEAD],
}

SUBGROUP_SCOPES = {
    Role.SUBGROUP_OWNER: [
        "Vendor Insurances",
        "Renters Insurance",
        "Lease Agreements",
        "Checklists",
        "Breach Agreement Notices",
        "Daily Activity Reports",
        "AAT Company Requirements/Documents",
    ],
}

def assert_division_access(user: User, division: Division):
    if user.division != division:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to this division.")

def assert_folder_access(user: User, folder_name: str):
    if user.role == Role.DIVISION_HEAD:
        return
    if user.role == Role.SUBGROUP_OWNER and folder_name in SUBGROUP_SCOPES[Role.SUBGROUP_OWNER]:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not have access to this folder.")

def get_allowed_folders(user: User):
    if user.role == Role.DIVISION_HEAD:
        return SUBGROUP_SCOPES[Role.SUBGROUP_OWNER]
    if user.role == Role.SUBGROUP_OWNER:
        return SUBGROUP_SCOPES[Role.SUBGROUP_OWNER]
    return []
