import os
from enum import Enum
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Division(str, Enum):
    MULTIFAMILY = "Multifamily/Residential"
    OFFICE = "Office/Retail"

class Role(str, Enum):
    DIVISION_HEAD = "division_head"
    SUBGROUP_OWNER = "subgroup_owner"
    AGENT = "agent"
    REVIEWER = "reviewer"

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
