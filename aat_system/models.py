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

class Approval(Base):
    """One case waiting on a human decision.

    Rows are created when a run cannot clear on its own: a document analysis
    comes back `needs_human_review`/`reject`, or the run's decision step finds
    required documents missing. Nothing is ever cleared by the agent alone.
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
    source = Column(String, nullable=False, default="analysis")  # analysis | run | sample
    raised_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, nullable=True)


class WorkflowStep(Base):
    """One node of one workflow's definition — the single source of truth.

    The use case page renders three views of this table and nothing else: the
    colour-coded diagram on the left, the narrative walkthrough on the right,
    and the status track the run walks through. Editing the narrative rewrites
    these rows, so the picture, the words and the execution cannot drift apart.

    Rows are seeded from workflow_repo.DEFAULT_STEPS on first read, then owned by
    whoever edits them — changing a workflow does not mean changing code.
    """

    __tablename__ = "workflow_steps"

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(String, nullable=False, index=True)
    division = Column(Enum(Division), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    key = Column(String, nullable=False)
    title = Column(String, nullable=False)
    # Drives both the node colour in the diagram and what the runner does at
    # this step: intake | analysis | decision | human | record | note.
    kind = Column(String, nullable=False, default="note")
    summary = Column(Text, nullable=True)
    bullets = Column(Text, nullable=True)  # newline-delimited
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("workflow_id", "division", "key", name="uq_step_workflow_division_key"),
    )


class WorkflowRecord(Base):
    """One logged row of record-keeping for a workflow run.

    Every run ends here, and the same rows export as the workflow's record file.
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
