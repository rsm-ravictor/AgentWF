import os
from enum import Enum
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Division(str, Enum):
    """A business line. Everything in the system is scoped to one of these.

    Roles are held *per division*: Residential's super admin and Construction's
    super admin are different people with the same title and no reach into each
    other's work.
    """

    MULTIFAMILY = "Multifamily/Residential"
    OFFICE = "Office/Retail"
    CONSTRUCTION = "Construction"


class Role(str, Enum):
    """The three levels of access, from most to least.

    Deliberately three rather than a longer ladder of job titles: the level
    answers one question — how far does this account see — and the division
    answers where. A job title that needs different capabilities is a permission
    change on its level, not a new role.
    """

    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    GENERAL = "general"


class Permission(str, Enum):
    """Individual capabilities a role grants.

    Kept separate from Role so the Settings page can show *what* a level in a
    division actually lets someone do, rather than an opaque label.
    """

    VIEW_DASHBOARD = "view_dashboard"
    VIEW_REPOSITORY = "view_repository"
    UPLOAD_DOCUMENTS = "upload_documents"
    RUN_WORKFLOW = "run_workflow"
    APPROVE_WORKFLOW = "approve_workflow"
    EDIT_WORKFLOW = "edit_workflow"
    DELETE_RECORDS = "delete_records"
    MANAGE_USERS = "manage_users"
    MANAGE_ROLES = "manage_roles"
    # Whose work you can see. Without this an account sees only what it did
    # itself — the difference between a general user and someone overseeing them.
    VIEW_TEAM_ACTIVITY = "view_team_activity"
    # Reach beyond your own division. Off for everyone by default, including
    # super admins: a super admin runs one business line, not all of them.
    VIEW_ALL_DIVISIONS = "view_all_divisions"


PERMISSION_LABELS = {
    Permission.VIEW_DASHBOARD: "View dashboard",
    Permission.VIEW_REPOSITORY: "Browse the repository",
    Permission.UPLOAD_DOCUMENTS: "Upload documents",
    Permission.RUN_WORKFLOW: "Run workflows",
    Permission.APPROVE_WORKFLOW: "Approve and sign off",
    Permission.EDIT_WORKFLOW: "Edit workflow definitions",
    Permission.DELETE_RECORDS: "Delete records and reports",
    Permission.MANAGE_USERS: "Manage users",
    Permission.MANAGE_ROLES: "Assign roles and permissions",
    Permission.VIEW_TEAM_ACTIVITY: "See everyone's activity",
    Permission.VIEW_ALL_DIVISIONS: "See every division",
}

# Least to most privileged. Unlike the job-title ladder this replaced, each level
# here genuinely contains the one below it, so a rank reads as a rank.
ROLE_ORDER = [
    Role.GENERAL,
    Role.ADMIN,
    Role.SUPER_ADMIN,
]

ROLE_LABELS = {
    Role.SUPER_ADMIN: "Super admin",
    Role.ADMIN: "Admin",
    Role.GENERAL: "General",
}

ROLE_DESCRIPTIONS = {
    Role.SUPER_ADMIN: (
        "Oversees their whole division: every use case, every approval and record, "
        "who has access and what each level may do."
    ),
    Role.ADMIN: (
        "Oversees the general users in their division — their productivity and what they "
        "queued — and can edit use cases and sign off."
    ),
    Role.GENERAL: (
        "Runs use cases and uploads documents, and sees their own work only. "
        "Every outcome goes to an admin for sign-off."
    ),
}

_ALL_PERMISSIONS = list(Permission)

# Each of these contains everything below it.
SENIOR_TIER = [Role.ADMIN, Role.SUPER_ADMIN]

ROLE_PERMISSIONS = {
    # Everything within their own division. Cross-division reach is deliberately
    # not included: each division has its own super admin.
    Role.SUPER_ADMIN: [p for p in _ALL_PERMISSIONS if p != Permission.VIEW_ALL_DIVISIONS],
    Role.ADMIN: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_REPOSITORY,
        Permission.UPLOAD_DOCUMENTS,
        Permission.RUN_WORKFLOW,
        Permission.APPROVE_WORKFLOW,
        Permission.EDIT_WORKFLOW,
        Permission.DELETE_RECORDS,
        Permission.MANAGE_USERS,
        Permission.VIEW_TEAM_ACTIVITY,
    ],
    Role.GENERAL: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_REPOSITORY,
        Permission.UPLOAD_DOCUMENTS,
        Permission.RUN_WORKFLOW,
    ],
}


def permissions_for(role: Role) -> list:
    """Permissions a level carries by default, as plain strings.

    The shipped default only. What a level grants *now* is per division and lives
    in `permission_repo`, because Construction may run its levels differently from
    Residential.
    """
    return [p.value for p in ROLE_PERMISSIONS.get(role, [])]


def has_permission(role: Role, permission: Permission) -> bool:
    """Whether a level grants a permission by default. See `permission_repo`."""
    return permission in ROLE_PERMISSIONS.get(role, [])

CORE_FOLDERS = [
    "Vendor Insurances",
    "Renters Insurance",
    "Lease Agreements",
    "Checklists",
    "Breach Agreement Notices",
    "Daily Activity Reports",
    "AAT Company Requirements/Documents",
]

# Construction keeps the shared categories and adds its own. Folder sets are per
# division precisely so a new business line does not have to pretend its
# paperwork is the same as everyone else's.
CONSTRUCTION_FOLDERS = CORE_FOLDERS + [
    "Contractor Insurances",
    "Permits and Approvals",
    "Change Orders",
    "Lien Waivers",
    "Safety Reports",
]

DIVISION_FOLDER_MAPPING = {
    Division.MULTIFAMILY: CORE_FOLDERS,
    Division.OFFICE: CORE_FOLDERS,
    Division.CONSTRUCTION: CONSTRUCTION_FOLDERS,
}

# Every folder name the system recognises, across every division.
ALL_FOLDERS = list(dict.fromkeys(f for folders in DIVISION_FOLDER_MAPPING.values() for f in folders))


def folders_for(division: Division) -> list:
    """The folder categories one division works in."""
    return list(DIVISION_FOLDER_MAPPING.get(division, CORE_FOLDERS))


# The short key a division travels under in URLs and the UI. One mapping, so a
# new business line is added in exactly one place.
DIVISION_KEYS = {
    "mf": Division.MULTIFAMILY,
    "retail": Division.OFFICE,
    "construction": Division.CONSTRUCTION,
}

DIVISION_LABELS = {
    Division.MULTIFAMILY: "Residential / Multifamily",
    Division.OFFICE: "Office / Retail",
    Division.CONSTRUCTION: "Construction",
}

# The division the system runs as when nothing says otherwise. Residential and
# Construction are paused: their records stay in the database, but nothing routes
# to them, so an unparameterised call lands in Office/Retail rather than in a
# business line no one is working on.
DEFAULT_DIVISION = Division.OFFICE
DEFAULT_DIVISION_KEY = "retail"

# Which divisions the UI will let someone reach. Kept separate from the enum so
# pausing a business line is a one-line change rather than a data migration.
ACTIVE_DIVISIONS = (Division.OFFICE,)


def division_key(division: Division) -> str:
    for key, value in DIVISION_KEYS.items():
        if value == division:
            return key
    return DEFAULT_DIVISION_KEY


def resolve_division_key(key: str) -> Division:
    return DIVISION_KEYS.get((key or "").strip().lower(), DEFAULT_DIVISION)

REDACTION_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
    r"\b\d{16}\b",  # credit card numbers (simplified)
    r"\b\d{9}\b",  # Tax ID / EIN (simplified)
    r"\b\d{3}-\d{3}-\d{4}\b",  # phone numbers
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",  # email addresses
]

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///aat_system.db")
SECRET_KEY = os.getenv("SECRET_KEY", "replace-with-a-secure-random-string")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
SERVICE_API_KEY = os.getenv("SERVICE_API_KEY", "")
UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", "uploaded_files"))
REDACTED_ROOT = Path(os.getenv("REDACTED_ROOT", "redacted_files"))
ARCHIVE_ROOT = Path(os.getenv("ARCHIVE_ROOT", "repository"))
