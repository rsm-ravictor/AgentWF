import re
from pathlib import Path
from typing import Optional
from .config import REDACTION_PATTERNS, UPLOAD_ROOT, REDACTED_ROOT
from pypdf import PdfReader, PdfWriter

UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
REDACTED_ROOT.mkdir(parents=True, exist_ok=True)


def redact_text(text: str) -> str:
    redacted = text
    for pattern in REDACTION_PATTERNS:
        redacted = re.sub(pattern, "[REDACTED]", redacted)
    return redacted


def redact_pdf(source_path: Path, destination_path: Optional[Path] = None) -> Path:
    destination = destination_path or REDACTED_ROOT / source_path.name
    reader = PdfReader(str(source_path))
    writer = PdfWriter()

    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            redacted_text = redact_text(text)
            page.add_transformation({"/Type": "/Annot", "/Subtype": "/FreeText", "/Contents": redacted_text})
        writer.add_page(page)

    with open(destination, "wb") as f:
        writer.write(f)
    return destination


def redact_uploaded_file(source_path: Path, destination_path: Optional[Path] = None) -> Path:
    if source_path.suffix.lower() == ".pdf":
        return redact_pdf(source_path, destination_path)
    destination = destination_path or REDACTED_ROOT / source_path.name
    destination.write_bytes(source_path.read_bytes())
    return destination
