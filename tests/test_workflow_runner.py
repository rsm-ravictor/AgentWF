"""Tests for the run engine.

The promise the use case page makes is that the diagram, the narrative and the
run are the same thing. These assert that: the run reports exactly the steps the
definition holds, an edited definition changes what the run does, and a run that
cannot clear leaves a queued case and a record row behind rather than passing
quietly.

No API key or model call is involved — runs without an attachment never touch
the model.

    python -m pytest tests/ -q
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aat_system import approval_repo, workflow_repo, workflow_runner
from aat_system.config import Division
from aat_system.models import Base, Document, Folder, User

MF = Division.MULTIFAMILY


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'run.db'}", connect_args={"check_same_thread": False})
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
    db.add(Document(filename=filename, folder_id=folder.id, owner_id=db.owner_id, uploaded_at=datetime.utcnow()))
    db.commit()


def execute(db, workflow_id="vendor-insurance", **kwargs):
    return list(workflow_runner.run(db, workflow_id, MF, **kwargs))


def steps_of(events):
    return [e for e in events if e["type"] == "step" and e["status"] != "running"]


def outcome_of(events):
    return next(e for e in events if e["type"] == "outcome")


# ---------------- Shape ----------------

def test_a_run_reports_every_step_in_the_definition(db):
    definition = workflow_repo.get_definition(db, "vendor-insurance", MF)
    events = execute(db)

    opened = events[0]
    assert opened["type"] == "run"
    assert [s["key"] for s in opened["steps"]] == [s["key"] for s in definition["steps"]]
    assert [s["key"] for s in steps_of(events)] == [s["key"] for s in definition["steps"]]


def test_each_step_is_announced_before_it_finishes(db):
    events = execute(db)
    running = [e for e in events if e["type"] == "step" and e["status"] == "running"]
    finished = steps_of(events)
    assert len(running) == len(finished)
    # The announcement for a step always precedes its result.
    for key in [s["key"] for s in finished]:
        order = [i for i, e in enumerate(events) if e.get("key") == key]
        assert events[order[0]]["status"] == "running"


def test_the_run_follows_an_edited_definition(db):
    workflow_repo.update_definition(
        db,
        "vendor-insurance",
        MF,
        [
            {"title": "Only gather", "kind": "intake", "summary": "", "bullets": []},
            {"title": "Only record", "kind": "record", "summary": "", "bullets": []},
        ],
    )
    titles = [s["title"] for s in steps_of(execute(db))]
    assert titles == ["Only gather", "Only record"]


def test_a_step_someone_added_still_appears_in_the_run(db):
    definition = workflow_repo.get_definition(db, "vendor-insurance", MF)
    steps = [
        {"title": s["title"], "kind": s["kind"], "summary": s["summary"], "bullets": s["bullets"]}
        for s in definition["steps"]
    ]
    steps.insert(1, {"title": "Call the vendor", "kind": "note", "summary": "Ring them first.", "bullets": ["By phone."]})

    workflow_repo.update_definition(db, "vendor-insurance", MF, steps)
    reported = steps_of(execute(db))
    assert "Call the vendor" in [s["title"] for s in reported]
    added = next(s for s in reported if s["title"] == "Call the vendor")
    assert added["facts"] == ["By phone."]


# ---------------- Outcome ----------------

def test_a_run_with_nothing_on_file_cannot_clear(db):
    events = execute(db)
    outcome = outcome_of(events)

    assert outcome["status"] == "needs_review"
    assert "Vendor insurance certificate" in outcome["blockers"]
    assert outcome["verdict"] is None

    intake = steps_of(events)[0]
    assert intake["status"] == "blocked"


def test_a_run_that_cannot_clear_queues_a_case_and_records_the_run(db):
    outcome_of(execute(db, property_id="RES-014", unit="3B", actor="Jordan"))

    pending = approval_repo.list_pending(db, MF, workflow_id="vendor-insurance")
    assert len(pending) == 1
    assert pending[0]["property"] == "RES-014"
    assert pending[0]["source"] == "run"
    assert "AAT requirements document" in pending[0]["missing"]

    summary = workflow_repo.record_summary(db, "vendor-insurance", MF)
    assert summary["rows_logged"] == 1
    assert summary["by_outcome"] == {"queued_for_review": 1}
    assert summary["last_updated_by"] == "Jordan"


def test_two_runs_on_the_same_unit_do_not_queue_twice(db):
    execute(db, property_id="RES-014", unit="3B")
    execute(db, property_id="RES-014", unit="3B")

    assert len(approval_repo.list_pending(db, MF, workflow_id="vendor-insurance")) == 1
    # Both runs are still recorded — deduping the queue must not lose the history.
    assert workflow_repo.record_summary(db, "vendor-insurance", MF)["rows_logged"] == 2


def test_documents_on_file_clear_the_intake_step(db):
    add_document(db, "Vendor Insurances", "brightline-coi.pdf")
    add_document(db, "AAT Company Requirements/Documents", "aat-requirements-2026.pdf")

    events = execute(db)
    intake = steps_of(events)[0]
    assert intake["status"] == "done"
    assert "2 of 2" in intake["detail"]
    assert outcome_of(events)["documents"]["present"] == 2


def test_documents_alone_do_not_clear_the_run(db):
    """Present-and-correct are different questions.

    Filenames matching is enough to say a document exists; it is not enough to
    say it passes. Without a graded document the run still goes to a human.
    """
    add_document(db, "Vendor Insurances", "brightline-coi.pdf")
    add_document(db, "AAT Company Requirements/Documents", "aat-requirements-2026.pdf")

    outcome = outcome_of(execute(db))
    assert outcome["status"] == "needs_review"
    assert any("attach one" in b for b in outcome["blockers"])


def test_runs_are_scoped_by_division(db):
    add_document(db, "Vendor Insurances", "brightline-coi.pdf", division=Division.OFFICE)
    intake = steps_of(execute(db))[0]
    assert intake["status"] == "blocked"


def test_an_unknown_workflow_is_rejected(db):
    with pytest.raises(ValueError):
        execute(db, workflow_id="not-a-workflow")
