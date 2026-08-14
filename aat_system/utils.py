import re
import unicodedata
from pathlib import Path
from .config import ALL_FOLDERS, UPLOAD_ROOT, REDACTED_ROOT, ARCHIVE_ROOT

INVALID_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._ \-]")


def normalize_filename(filename: str) -> str:
    filename = Path(filename).name
    filename = unicodedata.normalize("NFKD", filename)
    filename = INVALID_FILENAME_CHARS.sub("", filename)
    cleaned = filename.strip()
    return cleaned or "file"


def validate_folder_name(folder_name: str) -> str:
    """Accept any folder category the system knows, in any division.

    Division scope is enforced where a user's access is checked; this only keeps
    invented folder names out of the filesystem.
    """
    candidate = folder_name.strip()
    if candidate not in ALL_FOLDERS:
        raise ValueError(f"Invalid folder name: {folder_name}")
    return candidate


def ensure_storage_directories() -> None:
    for path in (UPLOAD_ROOT, REDACTED_ROOT, ARCHIVE_ROOT):
        path.mkdir(parents=True, exist_ok=True)
