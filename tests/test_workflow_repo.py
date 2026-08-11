"""Tests for the workflow catalog, standing instructions, and record-keeping.

Covers the three things the Workflows page reads: the required-document
checklist (is present/missing actually derived from the repository?), the SOP
seed-and-edit cycle, and the record summary the mini-dashboard reports.

    python -m pytest tests/ -q
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aat_system import approval_repo, workflow_repo
from aat_system.config import Division
from aat_system.models import Base, Document, Folder, User

MF = Division.MULTIFAMILY


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'wf.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    owner = User(email="o@aat.com", name="Owner", division=MF, role="AGENT", hashed_password="x")
    session.add(owner)
    session.commit()
    session.owner_id = owner.id
    yield session
    session.close()


def add_document(db, folder_name, filename, division=MF):
    folder = db.query(Folder).filter(Folder.name == folder_name, Folder.division == division).first()
    if folder is None:
        folder = Folder(name=folder_name, division=division)
        db.add(folder)
        db.commit()
    doc = Document(
        filename=filename,
        folder_id=folder.id,
        owner_id=db.owner_id,
        uploaded_at=datetime.utcnow(),
    )
    db.add(doc)
    db.commit()
    return doc


# ---------------- Catalog ----------------

def test_catalog_covers_every_phase_one_workflow():
    ids = {w["id"] for w in workflow_repo.catalog()}
    assert ids == {
        "vendor-insurance",
        "renters-insurance",
        "lease-checklist",
        "breach-notice",
        "security-report",
    }


def test_every_workflow_ships_default_standing_instructions():
    for wf_id in workflow_repo.WORKFLOW_CATALOG:
        defaults = workflow_repo.DEFAULT_SOPS[wf_id]
        for field in workflow_repo.SOP_FIELDS:
            assert defaults.get(field), f"{wf_id} has no {field}"


# ---------------- Required documents ----------------

def test_required_documents_start_missing_on_an_empty_repository(db):
    checked = workflow_repo.required_documents(db, "vendor-insurance", MF)
    assert checked["present"] == 0
    assert checked["total"] == 2
    assert len(checked["missing"]) == 2


def test_a_matching_filename_marks_a_document_present(db):
    add_document(db, "Vendor Insurances", "brightline-certificate-2026.pdf")
    checked = workflow_repo.required_documents(db, "vendor-insurance", MF)

    coi = next(i for i in checked["items"] if i["name"] == "Vendor insurance certificate")
    assert coi["present"] is True
    assert coi["matched_document"]["filename"] == "brightline-certificate-2026.pdf"

    # The requirements doc lives in a different folder and is still outstanding.
    assert checked["present"] == 1
    assert checked["missing"] == ["AAT requirements document"]


def test_a_document_in_the_wrong_folder_does_not_count(db):
    # A file named like the requirements doc, filed under leases, must not satisfy it.
    add_document(db, "Lease Agreements", "aat-requirements.pdf")
    checked = workflow_repo.required_documents(db, "vendor-insurance", MF)
    assert checked["present"] == 0


def test_required_documents_are_scoped_by_division(db):
    add_document(db, "Vendor Insurances", "certificate.pdf", division=Division.OFFICE)
    assert workflow_repo.required_documents(db, "vendor-insurance", MF)["present"] == 0
    assert workflow_repo.required_documents(db, "vendor-insurance", Division.OFFICE)["present"] == 1


def test_unknown_workflow_is_rejected(db):
    with pytest.raises(ValueError):
        workflow_repo.required_documents(db, "not-a-workflow", MF)


# ---------------- Standing instructions ----------------

def test_sop_seeds_defaults_on_first_read_and_is_marked_default(db):
    sop = workflow_repo.get_sop(db, "breach-notice", MF)
    assert sop["is_default"] is True
    assert "lease section" in sop["pass_fail_logic"].lower()


def test_editing_one_field_leaves_the_others_alone(db):
    original = workflow_repo.get_sop(db, "breach-notice", MF)
    edited = workflow_repo.update_sop(
        db, "breach-notice", MF, {"escalation_rules": "Escalate everything."}, updated_by="Jordan"
    )
    assert edited["escalation_rules"] == "Escalate everything."
    assert edited["steps_taken"] == original["steps_taken"]
    assert edited["is_default"] is False
    assert edited["updated_by"] == "Jordan"


def test_reset_restores_the_shipped_defaults(db):
    workflow_repo.update_sop(db, "breach-notice", MF, {"escalation_rules": "junk"})
    restored = workflow_repo.reset_sop(db, "breach-notice", MF, updated_by="Jordan")
    assert restored["is_default"] is True
    assert restored["escalation_rules"] == workflow_repo.DEFAULT_SOPS["breach-notice"]["escalation_rules"]


def test_sops_are_per_division(db):
    workflow_repo.update_sop(db, "breach-notice", MF, {"escalation_rules": "Multifamily rule."})
    office = workflow_repo.get_sop(db, "breach-notice", Division.OFFICE)
    assert office["is_default"] is True
    assert office["escalation_rules"] != "Multifamily rule."


# ---------------- Records ----------------

def test_record_summary_is_empty_before_anything_is_logged(db):
    summary = workflow_repo.record_summary(db, "vendor-insurance", MF)
    assert summary == {
        "rows_logged": 0,
        "last_updated": None,
        "last_updated_by": None,
        "last_subject": "",
        "by_outcome": {},
    }


def test_logging_records_updates_the_summary(db):
    workflow_repo.log_record(db, "vendor-insurance", MF, outcome="signed_off", recorded_by="Avery")
    workflow_repo.log_record(db, "vendor-insurance", MF, outcome="sent_back", recorded_by="Avery")
    workflow_repo.log_record(db, "vendor-insurance", MF, outcome="signed_off", subject="COI", recorded_by="Jordan")

    summary = workflow_repo.record_summary(db, "vendor-insurance", MF)
    assert summary["rows_logged"] == 3
    assert summary["by_outcome"] == {"signed_off": 2, "sent_back": 1}
    assert summary["last_updated_by"] == "Jordan"
    assert summary["last_subject"] == "COI"


def test_records_do_not_leak_between_workflows(db):
    workflow_repo.log_record(db, "vendor-insurance", MF, outcome="signed_off")
    assert workflow_repo.record_summary(db, "breach-notice", MF)["rows_logged"] == 0


def test_records_csv_has_a_header_and_one_row_per_record(db):
    workflow_repo.log_record(db, "vendor-insurance", MF, outcome="signed_off", property_id="RES-014", unit="3B")
    body = workflow_repo.records_csv(db, "vendor-insurance", MF)
    lines = [line for line in body.strip().splitlines() if line]
    assert lines[0].startswith("Recorded at,")
    assert len(lines) == 2
    assert "RES-014" in lines[1]


def test_generated_record_file_is_always_offered(db):
    files = workflow_repo.record_files(db, "vendor-insurance", MF)
    assert files[0]["kind"] == "generated"
    assert files[0]["url"].endswith("records.csv?division=mf")


def test_spreadsheets_in_the_workflow_folder_are_surfaced(db):
    add_document(db, "Vendor Insurances", "vendor-tracker.xlsx")
    add_document(db, "Vendor Insurances", "scan.pdf")  # not a record file
    names = [f["name"] for f in workflow_repo.record_files(db, "vendor-insurance", MF)]
    assert "vendor-tracker.xlsx" in names
    assert "scan.pdf" not in names


# ---------------- Approvals ----------------

def test_pending_counts_group_by_workflow(db):
    approval_repo.create(db, "vendor-insurance", MF, subject="A")
    approval_repo.create(db, "vendor-insurance", MF, subject="B")
    approval_repo.create(db, "breach-notice", MF, subject="C")

    counts = approval_repo.pending_counts(db, MF)
    assert counts == {"vendor-insurance": 2, "breach-notice": 1}


def test_resolving_removes_a_case_from_the_pending_queue(db):
    created = approval_repo.create(db, "vendor-insurance", MF, subject="A")
    approval_repo.resolve(db, created.id, "approved", resolved_by="Avery")

    assert approval_repo.list_pending(db, MF) == []
    # A case cannot be resolved twice.
    assert approval_repo.resolve(db, created.id, "approved") is None


def test_dedupe_stops_the_same_unit_being_raised_twice(db):
    first = approval_repo.create(db, "breach-notice", MF, subject="Unit 8C", property_id="RES-009", unit="8C", dedupe=True)
    second = approval_repo.create(db, "breach-notice", MF, subject="Unit 8C again", property_id="RES-009", unit="8C", dedupe=True)
    assert first is not None
    assert second is None
    assert len(approval_repo.list_pending(db, MF)) == 1


def test_a_resolved_unit_can_be_raised_again_later(db):
    first = approval_repo.create(db, "breach-notice", MF, subject="Unit 8C", unit="8C", dedupe=True)
    approval_repo.resolve(db, first.id, "approved")
    again = approval_repo.create(db, "breach-notice", MF, subject="Unit 8C recurs", unit="8C", dedupe=True)
    assert again is not None


def test_samples_seed_once_and_clear_cleanly(db):
    assert approval_repo.seed_samples(db, MF) == len(approval_repo.SAMPLE_APPROVALS)
    assert approval_repo.seed_samples(db, MF) == 0  # already populated

    real = approval_repo.create(db, "vendor-insurance", MF, subject="Real case", source="analysis")
    removed = approval_repo.clear_samples(db, MF)
    assert removed == len(approval_repo.SAMPLE_APPROVALS)

    remaining = approval_repo.list_pending(db, MF)
    assert [a["id"] for a in remaining] == [real.id]


def test_approvals_are_scoped_by_division(db):
    approval_repo.create(db, "vendor-insurance", MF, subject="MF case")
    assert approval_repo.list_pending(db, Division.OFFICE) == []
