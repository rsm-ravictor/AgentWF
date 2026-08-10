"""Tests for DAR persistence and the standing register.

Exercises the round-trip — save incidents, read them back aggregated across
reports — against a throwaway SQLite file. No API key or model call involved.

    python -m pytest tests/ -q
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aat_system import dar_repo
from aat_system.dar_analyzer import Incident
from aat_system.models import Base, DarIncident, DarReport


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


def inc(unit, date, highlight="yellow", category="Noise", keywords=None, snippet="x", lease=False):
    return Incident(
        unit=unit, date=date, time="", highlight=highlight, category=category,
        keywords=keywords if keywords is not None else ["noise complaint"],
        snippet=snippet, lease_relevant=lease,
    )


def extraction(report_date="2026-08-08", **kw):
    base = {
        "property_name": "Harbor View",
        "report_date": report_date,
        "shift_or_range": "19:00-03:00",
        "reporting_officer": "R. Delgado",
        "highlights_detected": True,
        "notes": "",
    }
    base.update(kw)
    return base


def test_save_report_persists_report_and_incidents(db):
    report = dar_repo.save_report(
        db, extraction(), [inc("12A", "2026-08-08"), inc("4B", "2026-08-08", "red")],
        filename="dar1.pdf", property_id="RES-014",
    )
    assert report.id is not None
    assert db.query(DarReport).count() == 1
    assert db.query(DarIncident).count() == 2


def test_keywords_survive_the_round_trip(db):
    dar_repo.save_report(
        db, extraction(), [inc("12A", "2026-08-08", keywords=["noise complaint", "loud music"])],
        filename="dar1.pdf",
    )
    reg = dar_repo.unit_register(db)
    assert reg["units"][0]["keywords"] == ["noise complaint", "loud music"]


def test_first_violation_spans_multiple_reports(db):
    """The whole point of persisting: first violation is the earliest ever, not
    the earliest in the most recent upload."""
    dar_repo.save_report(db, extraction("2026-08-08"), [inc("12A", "2026-08-08")], filename="aug8.pdf")
    dar_repo.save_report(db, extraction("2026-08-15"), [inc("12A", "2026-08-15")], filename="aug15.pdf")

    reg = dar_repo.unit_register(db)
    assert len(reg["units"]) == 1, "same unit across two reports should be one row"
    unit = reg["units"][0]
    assert unit["first_violation_date"] == "2026-08-08"
    assert unit["latest_violation_date"] == "2026-08-15"
    assert unit["occurrences"] == 2


def test_recurrence_across_reports_escalates(db):
    """A single yellow is 'watch'; the same unit yellow-flagged in a later report
    escalates — the rule only works once incidents are stored."""
    dar_repo.save_report(db, extraction("2026-08-08"), [inc("8C", "2026-08-08", "yellow")], filename="a.pdf")
    assert dar_repo.unit_register(db)["units"][0]["triage"] == "watch"

    dar_repo.save_report(db, extraction("2026-08-15"), [inc("8C", "2026-08-15", "yellow")], filename="b.pdf")
    assert dar_repo.unit_register(db)["units"][0]["triage"] == "escalate"


def test_property_filter_scopes_the_register(db):
    dar_repo.save_report(db, extraction(), [inc("12A", "2026-08-08")], filename="a.pdf", property_id="RES-014")
    dar_repo.save_report(db, extraction(), [inc("9B", "2026-08-08")], filename="b.pdf", property_id="RES-020")

    assert len(dar_repo.unit_register(db)["units"]) == 2
    scoped = dar_repo.unit_register(db, property_id="RES-014")
    assert [u["unit"] for u in scoped["units"]] == ["12A"]


def test_register_totals_count_reports(db):
    dar_repo.save_report(db, extraction(), [inc("12A", "2026-08-08", "red")], filename="a.pdf")
    dar_repo.save_report(db, extraction(), [inc("4B", "2026-08-09", "yellow")], filename="b.pdf")

    totals = dar_repo.unit_register(db)["totals"]
    assert totals["reports"] == 2
    assert totals["incidents"] == 2
    assert totals["units_affected"] == 2
    assert totals["escalate"] == 1
    assert totals["watch"] == 1


def test_incidents_are_traceable_to_their_source_report(db):
    dar_repo.save_report(db, extraction("2026-08-08"), [inc("12A", "2026-08-08")], filename="aug8.pdf")
    dar_repo.save_report(db, extraction("2026-08-15"), [inc("12A", "2026-08-15")], filename="aug15.pdf")

    sources = dar_repo.unit_register(db)["units"][0]["sources"]
    assert [s["filename"] for s in sources] == ["aug8.pdf", "aug15.pdf"]


def test_sources_are_index_aligned_with_incidents(db):
    """The UI pairs incidents[j] with sources[j]. That alignment falls out of two
    functions independently preserving order, so pin it — a change to either
    would otherwise silently mislabel which report an incident came from."""
    dar_repo.save_report(
        db, extraction("2026-08-08"),
        [inc("12A", "2026-08-08", category="Noise"), inc("4B", "2026-08-08", category="Trash")],
        filename="aug8.pdf",
    )
    dar_repo.save_report(
        db, extraction("2026-08-15"),
        [inc("12A", "2026-08-15", category="Pet")],
        filename="aug15.pdf",
    )

    for unit in dar_repo.unit_register(db)["units"]:
        assert len(unit["sources"]) == len(unit["incidents"]), unit["unit"]
        for incident, source in zip(unit["incidents"], unit["sources"]):
            assert incident["date"] == source["incident_date"]
            assert incident["category"] == source["category"]
            assert incident["highlight"] == source["highlight"]


def test_list_reports_is_newest_first_with_severity_counts(db):
    dar_repo.save_report(
        db, extraction("2026-08-08"),
        [inc("12A", "2026-08-08", "red"), inc("4B", "2026-08-08", "yellow"), inc("9A", "2026-08-08", "yellow")],
        filename="a.pdf",
    )
    dar_repo.save_report(db, extraction("2026-08-15"), [inc("8C", "2026-08-15", "yellow")], filename="b.pdf")

    reports = dar_repo.list_reports(db)
    assert [r["filename"] for r in reports] == ["b.pdf", "a.pdf"], "newest first"
    first = next(r for r in reports if r["filename"] == "a.pdf")
    assert first["severity_counts"] == {"red": 1, "yellow": 2, "none": 0}
    assert first["incident_count"] == 3
    assert first["units"] == ["12A", "4B", "9A"]


def test_delete_report_cascades_to_incidents(db):
    report = dar_repo.save_report(
        db, extraction(), [inc("12A", "2026-08-08"), inc("4B", "2026-08-08")], filename="a.pdf",
    )
    assert dar_repo.delete_report(db, report.id) is True
    assert db.query(DarReport).count() == 0
    assert db.query(DarIncident).count() == 0, "incidents should cascade, not orphan"
    assert dar_repo.unit_register(db)["units"] == []


def test_delete_missing_report_returns_false(db):
    assert dar_repo.delete_report(db, 9999) is False


def test_empty_register_is_well_formed(db):
    reg = dar_repo.unit_register(db)
    assert reg["units"] == []
    assert reg["totals"]["incidents"] == 0
    assert reg["totals"]["reports"] == 0


def test_blank_unit_is_stored_as_unknown(db):
    dar_repo.save_report(db, extraction(), [inc("", "2026-08-08")], filename="a.pdf")
    assert dar_repo.unit_register(db)["units"][0]["unit"] == "Unknown"
