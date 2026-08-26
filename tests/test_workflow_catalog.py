"""Tests for the catalog — which use cases a division has.

The catalog used to be a dict in code, which meant every division ran the same
use cases and adding one was a code change. It is rows now, keyed by division,
so these cover what that buys: a division that starts empty, use cases created
and retired while the system runs, and one division's changes staying out of
another's.

    python -m pytest tests/ -q
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aat_system import llm_analyzer, workflow_catalog, workflow_repo
from aat_system.config import Division
from aat_system.models import Base

MF = Division.MULTIFAMILY
RETAIL = Division.OFFICE
CONSTRUCTION = Division.CONSTRUCTION


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'catalog.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    workflow_catalog.seed(
        session, workflow_repo.WORKFLOW_CATALOG, divisions=(MF, CONSTRUCTION)
    )
    yield session
    session.close()


# ---------------- What each division ships with ----------------

def test_a_seeded_division_gets_the_whole_set(db):
    for division in (MF, CONSTRUCTION):
        ids = {w["id"] for w in workflow_catalog.catalog(db, division)}
        assert ids == set(workflow_repo.WORKFLOW_CATALOG)


def test_a_division_that_was_not_seeded_starts_empty(db):
    """Seeding is per division: one left out builds its own set from nothing."""
    assert workflow_catalog.catalog(db, RETAIL) == []
    assert not workflow_catalog.exists(db, "vendor-insurance", RETAIL)


def test_office_retail_is_the_division_that_ships(db):
    """The active business line. Residential and Construction are paused, so a
    default seed must reach Office/Retail and leave the others alone."""
    assert workflow_catalog.SEEDED_DIVISIONS == (RETAIL,)
    workflow_catalog.seed(db, workflow_repo.OFFICE_CATALOG)
    ids = {w["id"] for w in workflow_catalog.catalog(db, RETAIL)}
    assert ids == set(workflow_repo.OFFICE_CATALOG)


def test_every_shipped_office_use_case_has_a_rubric_and_steps():
    """A use case with no rubric grades nothing, and no steps runs nothing."""
    for workflow_id in workflow_repo.OFFICE_CATALOG:
        assert workflow_id in llm_analyzer.WORKFLOW_RUBRICS
        assert workflow_repo.default_steps(workflow_id, shipped=True) is not workflow_repo.NEW_WORKFLOW_STEPS


def test_seeding_twice_does_not_duplicate(db):
    before = len(workflow_catalog.catalog(db, MF))
    workflow_catalog.seed(db, workflow_repo.WORKFLOW_CATALOG, divisions=(MF,))
    assert len(workflow_catalog.catalog(db, MF)) == before


def test_a_retired_use_case_does_not_come_back_on_the_next_seed(db):
    """Restarting the app must not undo someone taking a use case out of service."""
    workflow_catalog.set_archived(db, "breach-notice", MF, True)
    workflow_catalog.seed(db, workflow_repo.WORKFLOW_CATALOG, divisions=(MF,))
    live = {w["id"] for w in workflow_catalog.catalog(db, MF)}
    assert "breach-notice" not in live


# ---------------- Creating ----------------

def test_a_created_use_case_is_runnable_immediately(db):
    entry = workflow_catalog.create(
        db,
        RETAIL,
        title="Estoppel Certificate",
        folder="Lease Agreements",
        purpose="Confirm the tenant's account before a sale.",
        rubric=["Rent stated matches the lease", "No unrecorded side agreements"],
    )
    assert entry["id"] == "estoppel-certificate"

    # First read seeds the starter spine, so it has a definition and a version 1.
    definition = workflow_repo.get_definition(db, entry["id"], RETAIL)
    assert [s["kind"] for s in definition["steps"]] == [
        s["kind"] for s in workflow_repo.NEW_WORKFLOW_STEPS
    ]
    assert definition["is_default"] is True
    assert workflow_repo.current_version(db, entry["id"], RETAIL) == 1


def test_creating_in_one_division_leaves_the_others_alone(db):
    workflow_catalog.create(db, RETAIL, title="CAM Reconciliation", folder="Checklists")
    assert "cam-reconciliation" not in {w["id"] for w in workflow_catalog.catalog(db, MF)}
    assert workflow_catalog.exists(db, "cam-reconciliation", RETAIL)


def test_two_use_cases_with_the_same_name_get_distinct_ids(db):
    first = workflow_catalog.create(db, RETAIL, title="Vendor Insurance", folder="Vendor Insurances")
    second = workflow_catalog.create(db, RETAIL, title="Vendor Insurance", folder="Vendor Insurances")
    assert first["id"] == "vendor-insurance"
    assert second["id"] == "vendor-insurance-2"


def test_a_retired_slug_is_not_reused(db):
    """Reusing it would silently adopt the retired use case's records."""
    workflow_catalog.create(db, RETAIL, title="Signage Approval", folder="Checklists")
    workflow_catalog.set_archived(db, "signage-approval", RETAIL, True)
    again = workflow_catalog.create(db, RETAIL, title="Signage Approval", folder="Checklists")
    assert again["id"] == "signage-approval-2"


def test_a_use_case_needs_a_title(db):
    with pytest.raises(ValueError):
        workflow_catalog.create(db, RETAIL, title="   ", folder="Checklists")


def test_the_folder_has_to_belong_to_the_division(db):
    """Construction's folders are its own; Retail cannot point a use case at one."""
    with pytest.raises(ValueError) as exc:
        workflow_catalog.create(db, RETAIL, title="Permit Check", folder="Permits and Approvals")
    assert "not a folder in" in str(exc.value)


def test_a_required_document_with_no_keywords_is_dropped(db):
    """It could never be satisfied, so it would block every run forever."""
    entry = workflow_catalog.create(
        db,
        RETAIL,
        title="Tenant Insurance",
        folder="Renters Insurance",
        documents=[
            {"name": "Certificate", "match": ["coi", "certificate"]},
            {"name": "Nothing to match on", "match": []},
        ],
    )
    assert [d["name"] for d in entry["documents"]] == ["Certificate"]


def test_keywords_are_lowercased_so_matching_is_predictable(db):
    entry = workflow_catalog.create(
        db,
        RETAIL,
        title="Fire Inspection",
        folder="Checklists",
        documents=[{"name": "Report", "match": ["Fire", "INSPECTION"]}],
    )
    assert entry["documents"][0]["match"] == ["fire", "inspection"]


# ---------------- Renaming and retiring ----------------

def test_a_rename_keeps_the_slug_so_history_stays_attached(db):
    entry = workflow_catalog.create(db, RETAIL, title="Kick-out Clause", folder="Lease Agreements")
    workflow_repo.get_definition(db, entry["id"], RETAIL)  # seed version 1

    renamed = workflow_catalog.update(db, entry["id"], RETAIL, title="Kick-out Review")
    assert renamed["id"] == entry["id"]
    assert renamed["title"] == "Kick-out Review"
    assert workflow_repo.current_version(db, entry["id"], RETAIL) == 1


def test_repointing_the_folder_is_validated_too(db):
    workflow_catalog.create(db, RETAIL, title="Suite Turnover", folder="Checklists")
    with pytest.raises(ValueError):
        workflow_catalog.update(db, "suite-turnover", RETAIL, folder="Lien Waivers")


def test_retiring_hides_a_use_case_but_keeps_it_readable(db):
    entry = workflow_catalog.create(db, RETAIL, title="Percentage Rent", folder="Checklists")
    workflow_repo.get_definition(db, entry["id"], RETAIL)

    workflow_catalog.set_archived(db, entry["id"], RETAIL, True)
    assert entry["id"] not in {w["id"] for w in workflow_catalog.catalog(db, RETAIL)}

    # Still readable, so its records and history do not become unreachable.
    assert workflow_catalog.entry(db, entry["id"], RETAIL, include_archived=True)["archived"]
    assert workflow_repo.get_definition(db, entry["id"], RETAIL)["steps"]


def test_a_retired_use_case_can_be_reinstated(db):
    workflow_catalog.create(db, RETAIL, title="Holdover Notice", folder="Breach Agreement Notices")
    workflow_catalog.set_archived(db, "holdover-notice", RETAIL, True)
    workflow_catalog.set_archived(db, "holdover-notice", RETAIL, False)
    assert workflow_catalog.exists(db, "holdover-notice", RETAIL)


def test_an_unknown_use_case_is_refused(db):
    with pytest.raises(ValueError):
        workflow_catalog.entry(db, "not-a-use-case", RETAIL)
    with pytest.raises(ValueError):
        workflow_catalog.update(db, "not-a-use-case", RETAIL, title="x")


# ---------------- Definitions on top of a created use case ----------------

def test_reset_restores_the_starter_spine_not_another_divisions_steps(db):
    """A Retail use case sharing a slug with Residential's must not inherit it."""
    workflow_catalog.create(db, RETAIL, title="Vendor Insurance", folder="Vendor Insurances")
    workflow_repo.update_definition(
        db, "vendor-insurance", RETAIL, [{"title": "Only step", "kind": "note"}], "tester"
    )
    restored = workflow_repo.reset_definition(db, "vendor-insurance", RETAIL)

    assert [s["title"] for s in restored["steps"]] == [
        s["title"] for s in workflow_repo.NEW_WORKFLOW_STEPS
    ]
    # Residential's own definition of the same slug is untouched.
    mf = workflow_repo.get_definition(db, "vendor-insurance", MF)
    assert [s["title"] for s in mf["steps"]] == [
        s["title"] for s in workflow_repo.DEFAULT_STEPS["vendor-insurance"]
    ]


def test_a_created_use_case_ignores_leftover_rows_on_its_slug(db):
    """Steps can outlive the use case that owned them.

    Before the catalog was per division, reading a use case in any division
    seeded step rows for it, and those rows outlived the shared catalog. A use
    case created later on the same slug must not inherit them — it would start
    from another division's steps and reset to them.
    """
    # Residential's shipped steps, sitting on Retail's slug with no Retail entry.
    workflow_repo._write_steps(
        db,
        "vendor-insurance",
        RETAIL,
        workflow_repo.DEFAULT_STEPS["vendor-insurance"],
        "before the split",
        source="seed",
    )
    assert workflow_repo._ordered_steps(db, "vendor-insurance", RETAIL)

    workflow_catalog.create(db, RETAIL, title="Vendor Insurance", folder="Vendor Insurances")
    definition = workflow_repo.seed_definition(db, "vendor-insurance", RETAIL)

    assert [s["title"] for s in definition["steps"]] == [
        s["title"] for s in workflow_repo.NEW_WORKFLOW_STEPS
    ]
    # A fresh use case starts at version 1, not after the leftover history.
    assert workflow_repo.current_version(db, "vendor-insurance", RETAIL) == 1
    assert definition["is_default"] is True


def test_a_definition_cannot_be_written_for_a_use_case_the_division_lacks(db):
    with pytest.raises(ValueError):
        workflow_repo.update_definition(
            db, "vendor-insurance", RETAIL, [{"title": "Step", "kind": "note"}], "tester"
        )


def test_the_change_log_names_a_retired_use_case(db):
    entry = workflow_catalog.create(db, RETAIL, title="Roof Access", folder="Checklists")
    workflow_repo.get_definition(db, entry["id"], RETAIL)
    workflow_catalog.set_archived(db, entry["id"], RETAIL, True)

    log = workflow_repo.change_log(db, RETAIL)
    assert log, "the retired use case's history should still be listed"
    assert log[0]["workflow_title"] == "Roof Access"
