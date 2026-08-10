"""Tests for DAR aggregation.

Covers the deterministic half of the DAR pipeline — grouping, triage, sorting,
and dedupe. Requires no API key, since no model call is involved.

    python -m pytest tests/ -q
"""

from aat_system.dar_analyzer import Incident, aggregate_by_unit


def inc(unit, date, highlight, category="Noise", keywords=None, snippet="x", lease=False):
    return Incident(
        unit=unit,
        date=date,
        time="",
        highlight=highlight,
        category=category,
        keywords=keywords if keywords is not None else ["noise complaint"],
        snippet=snippet,
        lease_relevant=lease,
    )


def test_groups_multiple_incidents_into_one_unit_row():
    rows = aggregate_by_unit([
        inc("12A", "2026-08-08", "yellow"),
        inc("12A", "2026-08-09", "red"),
    ])
    assert len(rows) == 1
    assert rows[0].occurrences == 2


def test_first_violation_is_earliest_date_regardless_of_input_order():
    rows = aggregate_by_unit([
        inc("12A", "2026-08-09", "yellow"),
        inc("12A", "2026-08-02", "yellow"),
    ])
    assert rows[0].first_violation_date == "2026-08-02"
    assert rows[0].latest_violation_date == "2026-08-09"


def test_worst_highlight_wins_over_milder_ones():
    rows = aggregate_by_unit([
        inc("4B", "2026-08-08", "none"),
        inc("4B", "2026-08-08", "yellow"),
        inc("4B", "2026-08-08", "red"),
    ])
    assert rows[0].worst_highlight == "red"


def test_red_escalates_immediately():
    rows = aggregate_by_unit([inc("4B", "2026-08-08", "red")])
    assert rows[0].triage == "escalate"


def test_single_yellow_is_watch_but_recurrence_escalates():
    once = aggregate_by_unit([inc("8C", "2026-08-08", "yellow")])
    assert once[0].triage == "watch"

    twice = aggregate_by_unit([
        inc("8C", "2026-08-08", "yellow"),
        inc("8C", "2026-08-09", "yellow"),
    ])
    assert twice[0].triage == "escalate", "a repeated yellow should escalate"


def test_unhighlighted_is_note_only():
    rows = aggregate_by_unit([inc("9A", "2026-08-08", "none")])
    assert rows[0].triage == "note_only"


def test_units_sort_numerically_not_lexically():
    rows = aggregate_by_unit([
        inc("12A", "2026-08-08", "yellow"),
        inc("4B", "2026-08-08", "yellow"),
        inc("104", "2026-08-08", "yellow"),
    ])
    assert [r.unit for r in rows] == ["4B", "12A", "104"]


def test_common_area_and_unknown_sort_last():
    rows = aggregate_by_unit([
        inc("Common area", "2026-08-08", "none"),
        inc("Unknown", "2026-08-08", "yellow"),
        inc("12A", "2026-08-08", "yellow"),
    ])
    assert rows[0].unit == "12A"
    assert set(r.unit for r in rows[1:]) == {"Common area", "Unknown"}


def test_keywords_dedupe_case_insensitively_preserving_order():
    rows = aggregate_by_unit([
        inc("12A", "2026-08-08", "yellow", keywords=["Noise Complaint", "loud music"]),
        inc("12A", "2026-08-09", "red", keywords=["noise complaint", "repeat"]),
    ])
    assert rows[0].keywords == ["Noise Complaint", "loud music", "repeat"]


def test_lease_relevance_is_true_if_any_incident_is():
    rows = aggregate_by_unit([
        inc("12A", "2026-08-08", "yellow", lease=False),
        inc("12A", "2026-08-09", "yellow", lease=True),
    ])
    assert rows[0].lease_relevant is True


def test_missing_dates_do_not_crash_aggregation():
    rows = aggregate_by_unit([inc("12A", "", "yellow")])
    assert rows[0].first_violation_date == ""
    assert rows[0].occurrences == 1


def test_blank_unit_falls_back_to_unknown():
    rows = aggregate_by_unit([inc("", "2026-08-08", "yellow")])
    assert rows[0].unit == "Unknown"
