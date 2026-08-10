"""Daily Activity Report (DAR) extraction and triage.

A DAR is a security patrol log. Whoever writes it highlights the rows that matter:
yellow for something to watch, red for something severe. This module reads the
report, pulls out the highlighted incidents, and groups them by unit so a manager
sees per-unit history rather than a flat log.

Two deliberate splits:

- The model extracts incidents and reads highlight colors (judgment + vision).
  It does not count, sort, or aggregate.
- Code groups by unit, finds the first violation date, and counts occurrences
  (arithmetic — deterministic, and wrong to delegate).
"""

import base64
import os
import re
from collections import OrderedDict
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel, Field

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")

# What each highlight colour means operationally. Mirrors the Security Report
# triage rules in CONTEXT.md: minor nonrecurring -> note, severe -> management,
# lease breach -> notice.
HIGHLIGHT_SEMANTICS = {
    "red": "severe — escalate to management, may warrant a breach notice",
    "yellow": "watch — log it; escalate if it recurs",
    "none": "not highlighted — informational only",
}


class Incident(BaseModel):
    """One highlighted line item from the report."""

    unit: str = Field(
        description="Apartment or unit number exactly as written (e.g. '12A', '304', "
        "'Bldg 2 / 8C'). Use 'Common area' for incidents with no unit, or 'Unknown' "
        "if a unit is referenced but illegible."
    )
    date: str = Field(
        description="Date of the incident in YYYY-MM-DD. If the row has no own date, "
        "use the report's date. If neither is legible, use an empty string."
    )
    time: str = Field(description="Time of the incident as written, or empty string if absent.")
    highlight: Literal["red", "yellow", "none"] = Field(
        description="The highlight colour on this row in the document. 'red' for red or "
        "pink/orange-red fills, 'yellow' for yellow or amber fills, 'none' if unhighlighted."
    )
    category: str = Field(
        description="Short category for grouping: 'Noise', 'Pet', 'Trash', 'Parking', "
        "'Trespass', 'Damage', 'Smoking', 'Unauthorized occupant', 'Package theft', "
        "'Maintenance', or another short label if none fit."
    )
    keywords: List[str] = Field(
        description="Two to five short tags describing the incident, in the reporter's own "
        "terms where possible (e.g. 'noise complaint', 'trash on balcony', 'dog on premise')."
    )
    snippet: str = Field(
        description="The relevant text from the report, quoted or lightly trimmed. Keep it "
        "short — one or two sentences — but do not paraphrase away specifics."
    )
    lease_relevant: bool = Field(
        description="True if this plausibly violates a standard residential lease term "
        "(pets, noise, occupancy, smoking, property damage, unauthorized use)."
    )


class DarExtraction(BaseModel):
    """Everything read off one report."""

    property_name: str = Field(description="Property or community name, or empty string.")
    report_date: str = Field(description="Report date in YYYY-MM-DD, or empty string.")
    shift_or_range: str = Field(description="Shift or time range covered, or empty string.")
    reporting_officer: str = Field(description="Officer or source named on the report, or empty string.")
    highlights_detected: bool = Field(
        description="True if you could actually see colour highlighting in the document. "
        "False if the document appears to be plain text with no visible highlighting — "
        "this matters, so be honest about it."
    )
    incidents: List[Incident] = Field(
        description="One entry per highlighted row, plus any unhighlighted row that clearly "
        "describes a lease violation or safety issue. Do not invent rows."
    )
    notes: str = Field(
        description="Anything a reviewer should know that the rows do not capture: "
        "illegible sections, ambiguous highlighting, pages skipped, or an empty report."
    )


class UnitRow(BaseModel):
    """One aggregated row per unit — the view the user asked for."""

    unit: str
    first_violation_date: str
    latest_violation_date: str
    occurrences: int
    worst_highlight: Literal["red", "yellow", "none"]
    triage: Literal["escalate", "watch", "note_only"]
    categories: List[str]
    keywords: List[str]
    snippets: List[str]
    lease_relevant: bool
    incidents: List[Incident]


SYSTEM_PROMPT = """You read Daily Activity Reports (DARs) for AAT, a property management \
company, and turn them into a per-unit incident table a manager can act on.

The person who wrote the report has already done the triage by highlighting rows. Read the \
highlighting as their judgment:

- RED highlight: severe. Escalate to management; may warrant a lease breach notice.
- YELLOW highlight: worth watching. Log it, and escalate if it recurs.
- No highlight: routine patrol activity — skip it, unless the text plainly describes a lease \
violation or a safety issue the reporter appears to have missed.

Read the highlight colours from the document itself. If a row's fill is ambiguous between \
yellow and red, choose the more severe and say so in notes. If you cannot see any colour \
highlighting at all — for instance the document is plain text, or a scan where fills did not \
survive — set highlights_detected to false and extract every row that describes a violation \
instead. Do not guess at colours that are not there.

Quote the report rather than summarizing it. A manager acting on a noise complaint needs the \
actual wording, times, and unit number, not your paraphrase. Never invent a unit number, a \
date, or an incident that is not in the document; leave a field empty instead. If a unit \
number is referenced but illegible, use 'Unknown' and note it."""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _document_block(file_bytes: bytes, media_type: str) -> dict:
    """Build the content block. PDFs and images go through natively so the model
    can see highlight colours; text is inlined (colour is already lost)."""
    if media_type == "application/pdf":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(file_bytes).decode("utf-8"),
            },
        }
    if media_type in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(file_bytes).decode("utf-8"),
            },
        }
    text = file_bytes.decode("utf-8", errors="replace")
    return {"type": "text", "text": f"<report>\n{text}\n</report>"}


def _unit_sort_key(unit: str):
    """Sort units naturally: 4B before 12A, letters after digits, specials last."""
    if unit in ("Common area", "Unknown", ""):
        return (2, 0, unit)
    match = re.search(r"\d+", unit)
    if match:
        return (0, int(match.group()), unit)
    return (1, 0, unit)


_HIGHLIGHT_RANK = {"none": 0, "yellow": 1, "red": 2}


def _dedupe(values: List[str]) -> List[str]:
    """Case-insensitive dedupe that preserves first-seen casing and order."""
    seen = {}
    for v in values:
        key = v.strip().lower()
        if key and key not in seen:
            seen[key] = v.strip()
    return list(seen.values())


def aggregate_by_unit(incidents: List[Incident]) -> List[UnitRow]:
    """Group incidents into one row per unit. Deterministic — no model involved."""
    grouped: "OrderedDict[str, List[Incident]]" = OrderedDict()
    for inc in incidents:
        grouped.setdefault(inc.unit or "Unknown", []).append(inc)

    rows: List[UnitRow] = []
    for unit, items in grouped.items():
        dates = sorted(d for d in (i.date for i in items) if d)
        worst = max(items, key=lambda i: _HIGHLIGHT_RANK.get(i.highlight, 0)).highlight
        occurrences = len(items)

        # Red escalates immediately. Yellow escalates on recurrence — that is the
        # "minor nonrecurring issues receive notes only" rule from CONTEXT.md.
        if worst == "red":
            triage = "escalate"
        elif worst == "yellow":
            triage = "escalate" if occurrences > 1 else "watch"
        else:
            triage = "note_only"

        rows.append(
            UnitRow(
                unit=unit,
                first_violation_date=dates[0] if dates else "",
                latest_violation_date=dates[-1] if dates else "",
                occurrences=occurrences,
                worst_highlight=worst,
                triage=triage,
                categories=_dedupe([i.category for i in items]),
                keywords=_dedupe([k for i in items for k in i.keywords]),
                snippets=[i.snippet for i in items if i.snippet],
                lease_relevant=any(i.lease_relevant for i in items),
                incidents=items,
            )
        )

    rows.sort(key=lambda r: _unit_sort_key(r.unit))
    return rows


def analyze_dar(
    file_bytes: bytes,
    filename: str,
    media_type: str,
    property_id: Optional[str] = None,
) -> dict:
    """Extract highlighted incidents from a DAR and group them by unit."""
    context = f"File: {filename}"
    if property_id:
        context += f"\nProperty ID on file: {property_id}"

    instructions = (
        f"{context}\n\n"
        "Read this Daily Activity Report. Extract every highlighted row as an incident, "
        "reading the highlight colour from the document. Include unhighlighted rows only "
        "when they plainly describe a lease violation or safety issue.\n\n"
        "For each incident give the unit number, date, highlight colour, a short category, "
        "two to five keywords, and a short quote from the report."
    )

    response = _client().messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    _document_block(file_bytes, media_type),
                    {"type": "text", "text": instructions},
                ],
            }
        ],
        output_format=DarExtraction,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to analyze this report.")

    extraction: DarExtraction = response.parsed_output
    rows = aggregate_by_unit(extraction.incidents)

    return {
        "report": extraction.model_dump(exclude={"incidents"}),
        "units": [r.model_dump() for r in rows],
        "totals": {
            "units_affected": len(rows),
            "incidents": len(extraction.incidents),
            "escalate": sum(1 for r in rows if r.triage == "escalate"),
            "watch": sum(1 for r in rows if r.triage == "watch"),
            "repeat_units": sum(1 for r in rows if r.occurrences > 1),
        },
        "highlight_semantics": HIGHLIGHT_SEMANTICS,
    }
