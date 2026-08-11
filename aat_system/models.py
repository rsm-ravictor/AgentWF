from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Enum, UniqueConstraint
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


class Approval(Base):
    """One case waiting on a human decision.

    Previously this queue was seeded in the frontend, which meant it could not be
    grouped, counted per workflow, or survive a reload. Rows are created when an
    analysis comes back `needs_human_review`/`reject`, and when a DAR unit triages
    to escalate — the latter is what feeds a red-flagged unit into Breach Notice.
    """

    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(String, nullable=False, index=True)
    division = Column(Enum(Division), nullable=False, index=True)
    property_id = Column(String, nullable=True, index=True)
    unit = Column(String, nullable=True)
    subject = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    found_documents = Column(Text, nullable=True)  # newline-delimited
    missing_documents = Column(Text, nullable=True)  # newline-delimited
    status = Column(String, nullable=False, default="pending", index=True)
    source = Column(String, nullable=False, default="analysis")  # analysis | dar | sample
    raised_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, nullable=True)


class WorkflowSop(Base):
    """Standing instructions for one workflow — the persistent reference doc.

    This is what the agent is meant to do every time the workflow runs: inputs it
    expects, steps it takes, how it decides pass/fail, and when it escalates. It
    lives in the database rather than in code so a division head can edit it from
    the Workflows page without a deploy.
    """

    __tablename__ = "workflow_sops"

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(String, nullable=False, index=True)
    division = Column(Enum(Division), nullable=False, index=True)
    inputs_expected = Column(Text, nullable=True)
    steps_taken = Column(Text, nullable=True)
    pass_fail_logic = Column(Text, nullable=True)
    escalation_rules = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String, nullable=True)

    __table_args__ = (UniqueConstraint("workflow_id", "division", name="uq_sop_workflow_division"),)


class WorkflowRecord(Base):
    """One logged row of record-keeping for a workflow run.

    The Workflows mini-dashboard reports "rows logged / last updated" off this
    table, and the same rows export as the workflow's record file.
    """

    __tablename__ = "workflow_records"

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(String, nullable=False, index=True)
    division = Column(Enum(Division), nullable=False, index=True)
    property_id = Column(String, nullable=True, index=True)
    unit = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    outcome = Column(String, nullable=False, default="signed_off")
    decision_note = Column(Text, nullable=True)
    document_name = Column(String, nullable=True)
    recorded_by = Column(String, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)


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
