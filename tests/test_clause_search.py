"""Tests for Clause Search: the use case that ends in a drafted notice.

Clause Search is the only definition with a Draft step, and the only one whose
output is correspondence rather than a verdict. What is asserted here is what a
person sending that email is relying on:

* the clause is read out of the lease **on file**, not out of the attachment;
* a draft that could quote nothing does not pass quietly;
* nothing is ever sent — the run ends at a queued case and a record row.

The model is stubbed. A test that reached a provider would be a different kind
of test, and these have to run without a key.

    python -m pytest tests/ -q
"""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aat_system import (
    approval_repo,
    llm_analyzer,
    workflow_catalog,
    workflow_repo,
    workflow_runner,
)
from aat_system.config import Division, Role
from aat_system.models import Base, Document, Folder, User

OFFICE = Division.OFFICE

LEASE_TEXT = """OFFICE LEASE AGREEMENT

Landlord: American Assets Trust. Tenant: Northgate Design Co., Suite 210.
Term: 1 January 2026 through 31 December 2028.

Section 12(b). Use of Premises. Tenant shall not permit any use of the Premises
outside the hours of 7:00 a.m. to 9:00 p.m. without the prior written consent of
Landlord. Tenant shall have ten (10) days from written notice to cure any breach
of this Section.
"""

INCIDENT_TEXT = """DAILY ACTIVITY REPORT - 14 August 2026
Suite 210, Northgate Design Co. Loading dock, north elevation.
23:40 - contractors moving equipment through the suite. No prior consent on file.
"""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """An Office/Retail division with the shipped catalog and an archive on disk."""
    monkeypatch.setattr(workflow_repo, "ARCHIVE_ROOT", tmp_path / "repository")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'clause.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    owner = User(
        email="o@aat.com", name="Owner", division=OFFICE, role=Role.GENERAL, hashed_password="x"
    )
    session.add(owner)
    session.commit()
    session.owner_id = owner.id
    session.archive = tmp_path / "repository"
    workflow_catalog.seed(
        session, workflow_repo.OFFICE_CATALOG, llm_analyzer.WORKFLOW_RUBRICS, divisions=(OFFICE,)
    )
    yield session
    session.close()


def file_on_record(db, folder_name, filename, text):
    """Put a document in a folder *and* in the archive, as an upload would."""
    folder = db.query(Folder).filter(Folder.name == folder_name, Folder.division == OFFICE).first()
    if folder is None:
        folder = Folder(name=folder_name, division=OFFICE)
        db.add(folder)
        db.commit()
    path = Path(db.archive) / OFFICE.value / folder_name
    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text(text, encoding="utf-8")
    db.add(
        Document(
            filename=filename,
            folder_id=folder.id,
            owner_id=db.owner_id,
            uploaded_at=datetime.utcnow(),
        )
    )
    db.commit()


def stub_model(monkeypatch, quoted=True, unresolved=()):
    """Stand in for both model calls, recording the arguments each was given."""
    seen = {}

    def fake_analyze(**kwargs):
        seen["analysis"] = kwargs
        return llm_analyzer.DocumentVerdict(
            document_type="Daily activity report",
            is_expected_type=True,
            summary="Contractors in Suite 210 at 23:40 with no consent on file.",
            decision="needs_human_review",
            confidence="high",
            reasoning="The lease restricts the hours the premises may be used.",
            findings=[
                llm_analyzer.Finding(
                    requirement="The conduct is prohibited by a numbered section",
                    status="not_met",
                    evidence="Section 12(b)",
                )
            ],
            extracted_fields=[llm_analyzer.ExtractedField(label="Unit", value="Suite 210")],
            missing_information=[],
        )

    def fake_draft(**kwargs):
        seen["draft"] = kwargs
        return llm_analyzer.DraftedNotice(
            recipient="Northgate Design Co., Suite 210",
            subject="Suite 210 - use of premises outside permitted hours",
            body="On 14 August 2026 at 23:40 ...",
            quoted_clauses=(
                [
                    llm_analyzer.QuotedClause(
                        section="Section 12(b)",
                        text="Tenant shall not permit any use of the Premises outside the hours of "
                        "7:00 a.m. to 9:00 p.m. without the prior written consent of Landlord.",
                        breached_by="Contractors moving equipment through the suite at 23:40.",
                    )
                ]
                if quoted
                else []
            ),
            unresolved=list(unresolved),
        )

    monkeypatch.setattr(llm_analyzer, "analyze_document", fake_analyze)
    monkeypatch.setattr(llm_analyzer, "draft_notice", fake_draft)
    return seen


def execute(db, **kwargs):
    attachment = workflow_runner.Attachment(
        INCIDENT_TEXT.encode("utf-8"), "dar-2026-08-14.txt", "text/plain"
    )
    return list(workflow_runner.run(db, "clause-search", OFFICE, attachment=attachment, **kwargs))


def steps_of(events):
    return [e for e in events if e["type"] == "step" and e["status"] != "running"]


def outcome_of(events):
    return next(e for e in events if e["type"] == "outcome")


def both_documents(db):
    file_on_record(db, "Daily Activity Reports", "dar-2026-08-14.txt", INCIDENT_TEXT)
    file_on_record(db, "Lease Agreements", "northgate-lease.txt", LEASE_TEXT)


# ---------------- The definition ----------------

def test_the_office_catalog_is_the_three_use_cases_and_no_others(db):
    assert [uc["id"] for uc in workflow_catalog.catalog(db, OFFICE)] == [
        "insurance-certificate-audit",
        "coverage-matching",
        "clause-search",
    ]


def test_clause_search_runs_notification_in_notice_out(db):
    kinds = [s["kind"] for s in workflow_repo.get_definition(db, "clause-search", OFFICE)["steps"]]
    assert kinds == ["intake", "analysis", "draft", "decision", "human", "record"]


def test_the_lease_is_the_only_document_it_needs_on_file(db):
    """The incident is typed, not filed.

    An incident is reported by someone describing it long before a report
    document exists, so requiring one on file would block the run on the wrong
    thing. The lease is the document that has to be there.
    """
    required = workflow_repo.required_documents(db, "clause-search", OFFICE)
    assert [(i["name"], i["folder"]) for i in required["items"]] == [
        ("Tenant lease", "Lease Agreements"),
    ]


def test_it_asks_for_three_lookup_values_and_one_account(db):
    entry = workflow_catalog.entry(db, "clause-search", OFFICE)
    by_role = {}
    for spec in entry["run_inputs"]:
        by_role.setdefault(spec["role"], []).append(spec["name"])
    assert by_role["lease_lookup"] == ["company", "property_id", "unit"]
    assert by_role["clause_query"] == ["incident_summary"]
    # The account is typed at length, so it needs a box that admits length.
    summary = next(s for s in entry["run_inputs"] if s["name"] == "incident_summary")
    assert summary["type"] == "textarea"


# ---------------- Reading the lease that is on file ----------------

def test_the_lease_on_file_reaches_both_model_calls(db, monkeypatch):
    """The clause is in the lease, and the lease is on file rather than attached.

    If the lease does not reach the prompt, the model can only quote from the
    incident report — the wrong document to take a clause out of, and it would
    do it plausibly.
    """
    both_documents(db)
    seen = stub_model(monkeypatch)

    execute(db, property_id="RTL-118", unit="Suite 210", actor="Jordan")

    for call in ("analysis", "draft"):
        assert "Section 12(b)" in seen[call]["context"], f"the lease never reached the {call} call"
        assert "northgate-lease.txt" in seen[call]["context"]
    # The draft is written from the reading already made, not from a second one.
    assert seen["draft"]["verdict"].decision == "needs_human_review"
    assert seen["draft"]["sender"] == "Jordan"


def test_the_intake_step_says_what_it_actually_read(db, monkeypatch):
    both_documents(db)
    stub_model(monkeypatch)

    intake = steps_of(execute(db))[0]
    read = next(f for f in intake["facts"] if f.startswith("Read into this run:"))
    assert "Tenant lease" in read


def test_a_lease_recorded_but_missing_from_the_archive_is_not_read(db, monkeypatch):
    """A row without a file behind it must not read as a lease that said nothing."""
    file_on_record(db, "Daily Activity Reports", "dar-2026-08-14.txt", INCIDENT_TEXT)
    folder = Folder(name="Lease Agreements", division=OFFICE)
    db.add(folder)
    db.commit()
    db.add(Document(filename="ghost-lease.txt", folder_id=folder.id, owner_id=db.owner_id))
    db.commit()
    seen = stub_model(monkeypatch)

    intake = steps_of(execute(db))[0]
    # Present by filename, so intake does not block — but nothing was read from it.
    assert intake["status"] == "done"
    assert "ghost-lease.txt" not in seen["analysis"]["context"]


# ---------------- The draft ----------------

def test_the_drafted_notice_carries_the_clause_word_for_word(db, monkeypatch):
    both_documents(db)
    stub_model(monkeypatch)

    events = execute(db, property_id="RTL-118", unit="Suite 210")
    drafted = next(s for s in steps_of(events) if s["title"] == "Draft the notice")
    assert "Section 12(b)" in drafted["facts"][0]

    # Compared with line breaks flattened: a lease wraps its lines, and a quote
    # that is verbatim in wording should not fail on where the source wrapped.
    flatten = lambda text: " ".join(text.split())
    draft = outcome_of(events)["draft"]
    assert flatten(draft["quoted_clauses"][0]["text"]) in flatten(LEASE_TEXT)


def test_a_draft_that_could_quote_nothing_does_not_pass(db, monkeypatch):
    """The citation is the point. A notice with no clause in it is not a notice."""
    both_documents(db)
    stub_model(monkeypatch, quoted=False)

    outcome = outcome_of(execute(db))
    assert outcome["status"] == "needs_review"
    assert any("quoted" in blocker for blocker in outcome["blockers"])


def test_what_the_documents_did_not_support_becomes_a_blocker(db, monkeypatch):
    both_documents(db)
    stub_model(monkeypatch, unresolved=["The lease sets no cure period for this section"])

    outcome = outcome_of(execute(db))
    assert "The lease sets no cure period for this section" in outcome["blockers"]


def test_nothing_is_sent_the_draft_is_queued_for_a_person(db, monkeypatch):
    both_documents(db)
    stub_model(monkeypatch)

    execute(db, property_id="RTL-118", unit="Suite 210", actor="Jordan")

    pending = approval_repo.list_pending(db, OFFICE, workflow_id="clause-search")
    assert len(pending) == 1
    assert "Draft ready to review" in pending[0]["reason"]
    assert workflow_repo.record_summary(db, "clause-search", OFFICE)["rows_logged"] == 1


def test_with_no_report_attached_there_is_nothing_to_draft_from(db):
    """No attachment means no model call, so the Draft step has no reading behind
    it. It has to say so rather than invent one."""
    both_documents(db)

    events = list(workflow_runner.run(db, "clause-search", OFFICE))
    drafted = next(s for s in steps_of(events) if s["title"] == "Draft the notice")
    assert drafted["status"] == "blocked"
    assert outcome_of(events)["draft"] is None
