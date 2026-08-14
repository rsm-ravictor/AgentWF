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


class WorkflowRevision(Base):
    """One saved version of one workflow's definition.

    Every write to workflow_steps — the first seed, an edit, a reset, a restore —
    lands a row here holding the whole definition as it stood after that write.
    Because a definition decides what a run executes, a bad edit is a broken
    system; this is what makes that recoverable. The change log on the Reference
    page reads these rows, and restoring one writes the steps back.

    The definition is stored as JSON rather than as step rows: a revision is a
    historical record, so it should keep exactly what was saved even if the step
    table's shape or the shipped defaults later change.
    """

    __tablename__ = "workflow_revisions"

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(String, nullable=False, index=True)
    division = Column(Enum(Division), nullable=False, index=True)
    # 1-based and per workflow+division, so "version 3" means something to a
    # person reading the log.
    version = Column(Integer, nullable=False)
    steps_json = Column(Text, nullable=False)
    step_count = Column(Integer, nullable=False, default=0)
    # seed | edit | reset | restore — how this version came to be.
    source = Column(String, nullable=False, default="edit")
    # A plain-language summary of what changed against the previous version,
    # written at save time while both sides are in hand.
    note = Column(Text, nullable=True)
    # Set when this version was produced by restoring an earlier one.
    restored_from = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    created_by = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("workflow_id", "division", "version", name="uq_revision_workflow_division_version"),
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
