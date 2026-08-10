from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
from .db import Base
from .config import Division, Role

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    division = Column(Enum(Division), nullable=False)
    role = Column(Enum(Role), nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)

    documents = relationship("Document", back_populates="owner")

class Folder(Base):
    __tablename__ = "folders"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    division = Column(Enum(Division), nullable=False)

    documents = relationship("Document", back_populates="folder")

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    folder_id = Column(Integer, ForeignKey("folders.id"), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    redacted_at = Column(DateTime, nullable=True)
    content_hash = Column(String, nullable=True)
    doc_metadata = Column(Text, nullable=True)

    folder = relationship("Folder", back_populates="documents")
    owner = relationship("User", back_populates="documents")

class Lease(Base):
    __tablename__ = "leases"

    id = Column(Integer, primary_key=True, index=True)
    tenant_name = Column(String, nullable=False)
    property_name = Column(String, nullable=False)
    lease_start = Column(DateTime, nullable=False)
    lease_end = Column(DateTime, nullable=False)
    status = Column(String, default="active")
    folder_id = Column(Integer, ForeignKey("folders.id"), nullable=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    notes = Column(Text, nullable=True)

    folder = relationship("Folder")
    document = relationship("Document")

class DarReport(Base):
    """One uploaded Daily Activity Report."""

    __tablename__ = "dar_reports"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    property_id = Column(String, nullable=True, index=True)
    property_name = Column(String, nullable=True)
    report_date = Column(String, nullable=True, index=True)
    shift_or_range = Column(String, nullable=True)
    reporting_officer = Column(String, nullable=True)
    highlights_detected = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    incidents = relationship("DarIncident", back_populates="report", cascade="all, delete-orphan")


class DarIncident(Base):
    """One highlighted incident from a DAR, tied to a unit.

    Persisting these is what makes "first violation date" mean anything across
    weeks of reports rather than only within a single upload.
    """

    __tablename__ = "dar_incidents"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("dar_reports.id"), nullable=False)
    property_id = Column(String, nullable=True, index=True)
    unit = Column(String, nullable=False, index=True)
    incident_date = Column(String, nullable=True, index=True)
    incident_time = Column(String, nullable=True)
    highlight = Column(String, nullable=False, default="none")
    category = Column(String, nullable=True)
    keywords = Column(Text, nullable=True)  # newline-delimited
    snippet = Column(Text, nullable=True)
    lease_relevant = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    report = relationship("DarReport", back_populates="incidents")


class BreachLog(Base):
    __tablename__ = "breach_logs"

    id = Column(Integer, primary_key=True, index=True)
    lease_id = Column(Integer, ForeignKey("leases.id"), nullable=False)
    breach_type = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    reported_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    lease = relationship("Lease")
    reporter = relationship("User")
