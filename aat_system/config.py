import os
from enum import Enum
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Division(str, Enum):
    MULTIFAMILY = "Multifamily/Residential"
    OFFICE = "Office/Retail"

class Role(str, Enum):
    SUPER_USER = "super_user"
    ADMIN = "admin"
    DIVISION_HEAD = "division_head"
    SUBGROUP_OWNER = "subgroup_owner"
    REVIEWER = "reviewer"
    AGENT = "agent"


class Permission(str, Enum):
    """Individual capabilities a role grants.

    Kept separate from Role so the Admin page can show *what* a role actually
    lets someone do, rather than an opaque label.
    """

    VIEW_DASHBOARD = "view_dashboard"
    VIEW_REPOSITORY = "view_repository"
    UPLOAD_DOCUMENTS = "upload_documents"
    RUN_WORKFLOW = "run_workflow"
    APPROVE_WORKFLOW = "approve_workflow"
    EDIT_SOP = "edit_sop"
    DELETE_RECORDS = "delete_records"
    MANAGE_USERS = "manage_users"
    MANAGE_ROLES = "manage_roles"
    VIEW_ALL_DIVISIONS = "view_all_divisions"


PERMISSION_LABELS = {
    Permission.VIEW_DASHBOARD: "View dashboard",
    Permission.VIEW_REPOSITORY: "Browse the repository",
    Permission.UPLOAD_DOCUMENTS: "Upload documents",
    Permission.RUN_WORKFLOW: "Run workflows",
    Permission.APPROVE_WORKFLOW: "Approve and sign off",
    Permission.EDIT_SOP: "Edit standing instructions",
    Permission.DELETE_RECORDS: "Delete records and reports",
    Permission.MANAGE_USERS: "Manage users",
    Permission.MANAGE_ROLES: "Assign roles",
    Permission.VIEW_ALL_DIVISIONS: "See every division",
}

# Display order, least to most privileged overall. Note this is NOT a strict
# superset ladder: Agent, Reviewer and Subgroup Owner are peers with different
# duties — a Reviewer signs off but does not upload; an Agent uploads and runs
# work but never signs off. Only the senior tier (Division Head → Admin → Super
# User) genuinely accumulates. The Profile page therefore reports permissions
# granted rather than implying each step strictly contains the one below it.
ROLE_ORDER = [
    Role.AGENT,
    Role.REVIEWER,
    Role.SUBGROUP_OWNER,
    Role.DIVISION_HEAD,
    Role.ADMIN,
    Role.SUPER_USER,
]

ROLE_LABELS = {
    Role.SUPER_USER: "Super user",
    Role.ADMIN: "Administrator",
    Role.DIVISION_HEAD: "Division head",
    Role.SUBGROUP_OWNER: "Subgroup owner",
    Role.REVIEWER: "Reviewer",
    Role.AGENT: "Agent",
}

ROLE_DESCRIPTIONS = {
    Role.SUPER_USER: "Unrestricted. Manages roles and permissions across every division.",
    Role.ADMIN: "Manages users and roles within their division, and can edit standing instructions.",
    Role.DIVISION_HEAD: "Full access to every workflow and folder in their division, including sign-off.",
    Role.SUBGROUP_OWNER: "Access limited to assigned folders; can run workflows and upload, but not sign off.",
    Role.REVIEWER: "Reads and approves what the agent queues; cannot upload or change instructions.",
    Role.AGENT: "Runs workflows and uploads documents. Every outcome goes to a human for sign-off.",
}

_ALL_PERMISSIONS = list(Permission)

# The senior tier does accumulate — each of these contains everything below it.
SENIOR_TIER = [Role.DIVISION_HEAD, Role.ADMIN, Role.SUPER_USER]

ROLE_PERMISSIONS = {
    Role.SUPER_USER: _ALL_PERMISSIONS,
    Role.ADMIN: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_REPOSITORY,
        Permission.UPLOAD_DOCUMENTS,
        Permission.RUN_WORKFLOW,
        Permission.APPROVE_WORKFLOW,
        Permission.EDIT_SOP,
        Permission.DELETE_RECORDS,
        Permission.MANAGE_USERS,
        Permission.MANAGE_ROLES,
    ],
    Role.DIVISION_HEAD: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_REPOSITORY,
        Permission.UPLOAD_DOCUMENTS,
        Permission.RUN_WORKFLOW,
        Permission.APPROVE_WORKFLOW,
        Permission.EDIT_SOP,
        Permission.DELETE_RECORDS,
    ],
    Role.SUBGROUP_OWNER: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_REPOSITORY,
        Permission.UPLOAD_DOCUMENTS,
        Permission.RUN_WORKFLOW,
    ],
    Role.REVIEWER: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_REPOSITORY,
        Permission.APPROVE_WORKFLOW,
    ],
    Role.AGENT: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_REPOSITORY,
        Permission.UPLOAD_DOCUMENTS,
        Permission.RUN_WORKFLOW,
    ],
}


def permissions_for(role: Role) -> list:
    """Permissions a role carries, as plain strings."""
    return [p.value for p in ROLE_PERMISSIONS.get(role, [])]


def has_permission(role: Role, permission: Permission) -> bool:
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

DIVISION_FOLDER_MAPPING = {
    Division.MULTIFAMILY: CORE_FOLDERS,
    Division.OFFICE: CORE_FOLDERS,
}

REDACTION_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
    r"\b\d{16}\b",  # credit card numbers (simplified)
    r"\b\d{9}\b",  # Tax ID / EIN (simplified)
    r"\b\d{3}-\d{3}-\d{4}\b",  # phone numbers
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",  # email addresses
]

EMAIL_FOLDER_KEYWORDS = {
    "Vendor Insurances": ["vendor insurance", "certificate of insurance"],
    "Renters Insurance": ["renters insurance", "renter's insurance", "tenant insurance"],
    "Lease Agreements": ["lease agreement", "lease", "rental agreement"],
    "Checklists": ["checklist", "file checklist"],
    "Breach Agreement Notices": ["breach notice", "notice of breach", "lease breach"],
    "Daily Activity Reports": ["daily activity report", "activity report"],
    "AAT Company Requirements/Documents": ["aat company", "company requirements", "aat requirements"],
}

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///aat_system.db")
SECRET_KEY = os.getenv("SECRET_KEY", "replace-with-a-secure-random-string")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
IMAP_HOST = os.getenv("IMAP_HOST", "imap.example.com")
IMAP_USER = os.getenv("IMAP_USER", "placeholder@example.com")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "password")
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
SERVICE_API_KEY = os.getenv("SERVICE_API_KEY", "")
UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", "uploaded_files"))
REDACTED_ROOT = Path(os.getenv("REDACTED_ROOT", "redacted_files"))
ARCHIVE_ROOT = Path(os.getenv("ARCHIVE_ROOT", "repository"))
