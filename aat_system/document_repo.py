from datetime import datetime
from pathlib import Path
from typing import Optional
from sqlalchemy.orm import Session
from .models import Folder, Document, Lease, User
from .config import CORE_FOLDERS, Division, ARCHIVE_ROOT
from .redaction import redact_uploaded_file
from .utils import validate_folder_name

ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)


def get_or_create_folder(db: Session, name: str, division: Division) -> Folder:
    folder = db.query(Folder).filter(Folder.name == name, Folder.division == division).first()
    if folder:
        return folder
    folder = Folder(name=name, division=division)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return folder


def ingest_document(db: Session, owner: User, source_path: Path, folder_name: str, division: Division, metadata: Optional[str] = None) -> Document:
    folder_name = validate_folder_name(folder_name)
    folder = get_or_create_folder(db, folder_name, division)
    redacted_path = redact_uploaded_file(source_path)
    archive_folder = ARCHIVE_ROOT / division.value / folder.name
    archive_folder.mkdir(parents=True, exist_ok=True)
    archived_path = archive_folder / redacted_path.name
    archived_path.write_bytes(redacted_path.read_bytes())

    document = Document(
        filename=archived_path.name,
        folder_id=folder.id,
        owner_id=owner.id,
        redacted_at=datetime.utcnow(),
        doc_metadata=metadata,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def scan_expired_leases(db: Session):
    now = datetime.utcnow()
    expired = db.query(Lease).filter(Lease.lease_end < now, Lease.status != "expired").all()
    for lease in expired:
        lease.status = "expired"
        db.add(lease)
    db.commit()
    return expired


def find_documents_by_folder(db: Session, division: Division, folder_name: str):
    return db.query(Document).join(Folder).filter(Folder.division == division, Folder.name == folder_name).all()


def find_lease_by_tenant(db: Session, tenant_name: str):
    return db.query(Lease).filter(Lease.tenant_name.ilike(f"%{tenant_name}%"))


def record_lease(db: Session, tenant_name: str, property_name: str, lease_start: datetime, lease_end: datetime, folder_id: Optional[int] = None, document_id: Optional[int] = None, notes: Optional[str] = None) -> Lease:
    lease = Lease(
        tenant_name=tenant_name,
        property_name=property_name,
        lease_start=lease_start,
        lease_end=lease_end,
        folder_id=folder_id,
        document_id=document_id,
        notes=notes,
    )
    db.add(lease)
    db.commit()
    db.refresh(lease)
    return lease
