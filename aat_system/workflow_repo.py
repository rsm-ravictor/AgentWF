"""Workflow definitions, required documents, and record-keeping.

A workflow definition is an ordered list of steps. That list is the only source
of truth behind the use case page: the colour-coded diagram on the left, the
narrative walkthrough on the right, and the track the runner walks through are
all rendered from the same rows. Editing the narrative rewrites the rows, so the
three views cannot drift into three different answers.

Also here:

* **The catalog** — which folder a workflow reads and which documents it needs.
  Each required document declares the filename keywords that count as a match,
  so present/missing is a deterministic answer rather than a guess.
* **Records** — one row per completed run, exported as the workflow's record
  file and rolled up on the reference page.
* **Revisions** — every version a definition has ever had, so a definition that
  broke something can be read back and restored. A definition decides what a run
  executes, which makes an edit a change to the system's behaviour; the change
  log is the way back.
"""

import csv
import io
import json
import mimetypes
import re
from datetime import datetime
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import workflow_catalog
from .config import ARCHIVE_ROOT, Division, division_key as _division_key
from .models import Document, Folder, WorkflowRecord, WorkflowRevision, WorkflowStep

# Extensions that count as a "record file" a user would want to open rather than
# treat as evidence to be graded.
RECORD_EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".csv", ".ods")

AAT_REQUIREMENTS_FOLDER = "AAT Company Requirements/Documents"

# Step kinds. The kind colours the node in the diagram and decides what the
# runner actually does when it reaches that step (see workflow_runner).
STEP_KINDS = {
    "intake": "Gathers the documents the workflow needs",
    "analysis": "Reads and grades what was gathered",
    "draft": "Writes the correspondence the outcome calls for",
    "decision": "Applies the pass/fail rule",
    "human": "Hands the outcome to a person",
    "record": "Writes the result to the record file",
    "note": "Descriptive step with no automated action",
}

# How a version of a definition came to be. Shown in the change log so a restore
# after a bad edit reads differently from the edit itself.
REVISION_SOURCES = {
    "seed": "Shipped default",
    "edit": "Edited",
    "reset": "Restored defaults",
    "restore": "Rolled back",
}

# ---------------------------------------------------------------------------
# Catalog
#
# The use cases below are the current Phase 1 Multifamily set. They are expected
# to change; the page shell that renders them is not. Adding a use case here is
# all it takes for it to appear on the dashboard, in the top bar, and on the
# reference page.
# ---------------------------------------------------------------------------

WORKFLOW_CATALOG = {
    "vendor-insurance": {
        "title": "Vendor Insurance",
        "folder": "Vendor Insurances",
        "purpose": "Check a vendor's certificate of insurance against AAT's requirements before they work on site.",
        "documents": [
            {"name": "Vendor insurance certificate", "match": ["coi", "certificate", "insurance"]},
            {
                "name": "AAT requirements document",
                "match": ["requirement", "aat"],
                "folder": AAT_REQUIREMENTS_FOLDER,
            },
        ],
    },
    "renters-insurance": {
        "title": "Renter's Insurance",
        "folder": "Renters Insurance",
        "purpose": "Build a tenant's coverage checklist from the lease and grade what the tenant submits against it.",
        "documents": [
            {"name": "Lease agreement", "match": ["lease"], "folder": "Lease Agreements"},
            {"name": "Tenant checklist", "match": ["checklist"], "folder": "Checklists"},
            {"name": "Submitted insurance policy", "match": ["policy", "insurance", "renter"]},
        ],
    },
    "lease-checklist": {
        "title": "Lease & File Checklist",
        "folder": "Lease Agreements",
        "purpose": "Confirm a tenant file is complete, executed and dated before it is archived.",
        "documents": [
            {"name": "Lease agreement", "match": ["lease"]},
            {"name": "Addenda / riders", "match": ["addend", "rider", "amendment"]},
            {"name": "File checklist", "match": ["checklist"], "folder": "Checklists"},
        ],
    },
    "breach-notice": {
        "title": "Breach Notice",
        "folder": "Breach Agreement Notices",
        "purpose": "Draft a notice of breach citing the lease sections violated, with the unit's prior history attached.",
        "documents": [
            {"name": "Tenant lease", "match": ["lease"], "folder": "Lease Agreements"},
            {"name": "Violation report", "match": ["violation", "incident", "activity"]},
            {"name": "Prior breach history", "match": ["breach", "notice", "history"]},
        ],
    },
    "security-report": {
        "title": "Daily Activity Report",
        "folder": "Daily Activity Reports",
        "purpose": "Review a security report, log what was flagged, and raise anything that looks like a lease breach.",
        "documents": [
            {
                "name": "Daily activity report",
                "match": ["dar", "activity", "report"],
            },
        ],
    },
}

# ---------------------------------------------------------------------------
# Office/Retail catalog
#
# The active set, and the whole of it: two insurance use cases carried over from
# the residential work, and Clause Search, which is new here.
#
# The two insurance ones reuse the shape of the residential vendor and renter
# workflows — same spine, retargeted at office and retail holders. Clause Search
# does not: it starts from an incident, not from a document someone submitted,
# and it ends in drafted correspondence rather than a pass or a fail.
# ---------------------------------------------------------------------------

OFFICE_CATALOG = {
    "insurance-certificate-audit": {
        "title": "Insurance Certificate Audit",
        "folder": "Vendor Insurances",
        "purpose": "Check a certificate of insurance against AAT's requirements before the holder works on site.",
        "documents": [
            {"name": "Certificate of insurance", "match": ["coi", "certificate", "insurance"]},
            {
                "name": "AAT requirements document",
                "match": ["requirement", "aat"],
                "folder": AAT_REQUIREMENTS_FOLDER,
            },
        ],
    },
    "coverage-matching": {
        "title": "Insurance Coverage Matching Workflow",
        "folder": "Vendor Insurances",
        "purpose": "Build the required coverage matrix from the governing agreement, then grade the policy on file against it.",
        "documents": [
            {"name": "Governing agreement", "match": ["lease", "agreement", "contract"], "folder": "Lease Agreements"},
            {"name": "Coverage matrix", "match": ["matrix", "checklist", "schedule"], "folder": "Checklists"},
            {"name": "Submitted policy", "match": ["policy", "insurance", "certificate"]},
        ],
    },
    # Runs from an incident rather than from a submission: the notification
    # arrives, the lease it points at is found, the section the conduct breached
    # is quoted out of that lease, and the email goes to a person to send.
    "clause-search": {
        "title": "Clause Search",
        "folder": "Lease Agreements",
        "purpose": "Match an incident report to the lease it concerns, find the section the conduct breached, and draft the notice quoting that section word for word.",
        "documents": [
            {"name": "Tenant lease", "match": ["lease", "agreement", "contract"]},
        ],
        # The four things a run asks for. The first three identify which lease to
        # read; the fourth is the text the clause search is actually run against
        # and the account of the incident the drafted email carries. Typed rather
        # than attached, because an incident is reported by someone describing it
        # long before a report is filed — the original document can still be
        # attached, but waiting for one would be waiting for the wrong thing.
        "run_inputs": [
            {
                "name": "company",
                "label": "Name of company",
                "type": "text",
                "role": "lease_lookup",
                "placeholder": "Northgate Design Co.",
            },
            {
                "name": "property_id",
                "label": "Property",
                "type": "text",
                "role": "lease_lookup",
                "placeholder": "RTL-118",
            },
            {
                "name": "unit",
                "label": "Unit #",
                "type": "text",
                "role": "lease_lookup",
                "placeholder": "Suite 210",
            },
            {
                "name": "incident_summary",
                "label": "Incident summary",
                "type": "textarea",
                "role": "clause_query",
                "placeholder": "What happened, where, and when. Written the way it was reported.",
            },
        ],
    },
}

# ---------------------------------------------------------------------------
# Default definitions
#
# Seeded into workflow_steps on first read, then owned by whoever edits them.
# Each step carries the narrative text shown beside its node: a one-line summary
# and the bullets describing what happens at that step.
# ---------------------------------------------------------------------------

DEFAULT_STEPS = {
    "vendor-insurance": [
        {
            "title": "Gather the certificate",
            "kind": "intake",
            "summary": "Pull the vendor's COI and the AAT requirements standard in force.",
            "bullets": [
                "Reads the Vendor Insurances folder for the certificate.",
                "Reads AAT Company Requirements/Documents for the standard to grade against.",
                "Records which file satisfied each requirement, so the check is auditable.",
            ],
        },
        {
            "title": "Redact and read",
            "kind": "analysis",
            "summary": "Strip identifiers, then grade the certificate against the rubric.",
            "bullets": [
                "Identifiers are redacted before the document is read.",
                "Every rubric requirement comes back met, not met, or unclear, with a supporting quote.",
                "Policy number, carrier, limits and policy period are extracted as fields.",
            ],
        },
        {
            "title": "Compare to requirements",
            "kind": "decision",
            "summary": "Pass only on a clean sheet; anything near a threshold goes to a human.",
            "bullets": [
                "A general liability limit below the AAT minimum fails.",
                "AAT named as certificate holder rather than additional insured fails.",
                "An expired or not-yet-effective policy period fails.",
                "Anything within 10% of a threshold routes to review rather than clearing.",
            ],
        },
        {
            "title": "Human sign-off",
            "kind": "human",
            "summary": "A person owns the outcome; the agent never clears a vendor itself.",
            "bullets": [
                "Anything not clean is queued as an approval case naming the gap.",
                "A failed COI for a vendor already on site escalates to the division head the same day.",
                "Expired coverage escalates immediately rather than waiting for the next cycle.",
            ],
        },
        {
            "title": "File the outcome",
            "kind": "record",
            "summary": "Write the decision to the workflow's record file.",
            "bullets": [
                "One row per run: property, subject, outcome, and who signed off.",
                "The record file exports as CSV from the reference page.",
                "Cleared certificates stay filed under Vendor Insurances for the next renewal check.",
            ],
        },
    ],
    "renters-insurance": [
        {
            "title": "Read the lease",
            "kind": "intake",
            "summary": "Pull the executed lease and the checklist generated from it.",
            "bullets": [
                "Finds the lease for the unit under Lease Agreements.",
                "Finds the tenant checklist under Checklists.",
                "Property ID and unit number identify which tenancy this run is about.",
            ],
        },
        {
            "title": "Build the checklist",
            "kind": "analysis",
            "summary": "Turn the lease's insurance clause into a checkable list of coverages.",
            "bullets": [
                "Required limits, endorsements and the coverage period come from the lease itself.",
                "The checklist is what gets sent to the tenant, so it is written in plain terms.",
                "The submitted policy is graded line by line against that list.",
            ],
        },
        {
            "title": "Compare the submission",
            "kind": "decision",
            "summary": "Pass when limits, endorsements and dates all clear the lease minimum.",
            "bullets": [
                "Personal liability below the lease minimum fails.",
                "A missing additional-insured endorsement fails.",
                "A policy expiring before the lease term ends fails.",
            ],
        },
        {
            "title": "Approve or correct",
            "kind": "human",
            "summary": "Approve a compliant policy, or queue a corrective email naming each gap.",
            "bullets": [
                "The corrective email is drafted for a person to send, never sent automatically.",
                "Two rejected submissions from the same tenant escalate to the property manager.",
                "An occupied unit with no policy on file at all escalates immediately — that is a lease breach.",
            ],
        },
        {
            "title": "File against the unit",
            "kind": "record",
            "summary": "Log the outcome so the unit's coverage history is continuous.",
            "bullets": [
                "The record row carries the property, unit and decision.",
                "Renewal checks read the same rows rather than starting from scratch.",
            ],
        },
    ],
    "lease-checklist": [
        {
            "title": "Assemble the file",
            "kind": "intake",
            "summary": "Collect the lease, every addendum, and the division's file checklist.",
            "bullets": [
                "Reads Lease Agreements for the lease and its riders.",
                "Reads Checklists for the file checklist that applies to this lease type.",
                "Anything referenced by the lease but absent from the folder is reported as missing.",
            ],
        },
        {
            "title": "Verify each line",
            "kind": "analysis",
            "summary": "Check every checklist line is present, executed and dated.",
            "bullets": [
                "Signature and date fields are matched against the lease term.",
                "Rent, deposit and term dates are extracted for the record.",
                "An unsigned page or a missing initial is reported with the page it is on.",
            ],
        },
        {
            "title": "Complete or incomplete",
            "kind": "decision",
            "summary": "Complete only when every line is received and executed by all parties.",
            "bullets": [
                "A missing addendum referenced in the lease fails the file.",
                "An undated signature fails; a signature block with no counter-signature fails.",
                "Nothing archives on the agent's own authority.",
            ],
        },
        {
            "title": "Queue for sign-off",
            "kind": "human",
            "summary": "A person confirms the file before it is closed.",
            "bullets": [
                "Incomplete files are queued with the outstanding lines listed.",
                "A file still incomplete 5 business days after move-in escalates to the division head.",
                "A missing signature on the lease itself escalates immediately.",
            ],
        },
        {
            "title": "Archive the file",
            "kind": "record",
            "summary": "Record the sign-off and archive the completed file.",
            "bullets": [
                "The record row is the audit trail for the tenancy.",
                "Archived files stay searchable from the dashboard folder view.",
            ],
        },
    ],
    "breach-notice": [
        {
            "title": "Retrieve the lease",
            "kind": "intake",
            "summary": "Pull the tenant's lease and whatever triggered this notice.",
            "bullets": [
                "Finds the lease for the unit under Lease Agreements.",
                "Finds the violation or activity report that reported the conduct.",
                "Pulls the unit's prior breach notices so history is attached, not re-derived.",
            ],
        },
        {
            "title": "Draft the notice",
            "kind": "analysis",
            "summary": "Write the notice against the lease sections actually breached.",
            "bullets": [
                "Cites the specific lease section by number, not a general clause.",
                "Describes the conduct with dates, taken from the violation report.",
                "States the cure period the lease allows.",
                "Includes the count of prior documented breaches for this unit.",
            ],
        },
        {
            "title": "Check it stands up",
            "kind": "decision",
            "summary": "Ready to send only when the draft is specific and supported.",
            "bullets": [
                "A draft citing no section is held for a person to rewrite.",
                "A draft asserting a breach the lease does not cover is held.",
                "A conflict between the report's dates and the lease term is held.",
            ],
        },
        {
            "title": "Management review",
            "kind": "human",
            "summary": "Notices are never sent by the agent — management sends them.",
            "bullets": [
                "The drafted notice is queued for review with the history attached.",
                "A third documented violation escalates to the division head before sending.",
                "Anything involving safety, weapons or threats escalates immediately, regardless of count.",
            ],
        },
        {
            "title": "Log the breach",
            "kind": "record",
            "summary": "Record the breach so it counts toward the next occurrence.",
            "bullets": [
                "The record row is what the next run reads as prior history.",
                "Property, unit and the sections cited are all captured.",
            ],
        },
    ],
    "security-report": [
        {
            "title": "Take in the report",
            "kind": "intake",
            "summary": "Pull the day's activity report for the property.",
            "bullets": [
                "Reads the Daily Activity Reports folder for the report under review.",
                "Property ID scopes the run to one site.",
                "The original PDF or image is preferred — exports lose formatting that carries meaning.",
            ],
        },
        {
            "title": "Read what was flagged",
            "kind": "analysis",
            "summary": "Work through the report and pull out each flagged item.",
            "bullets": [
                "Each flagged item is read for unit, date, time and what happened.",
                "Items are grouped by unit so a pattern on one unit is visible.",
                "Anything that reads as a lease violation is marked as such.",
            ],
        },
        {
            "title": "Triage",
            "kind": "decision",
            "summary": "Sort each item into note-only, flag, or breach.",
            "bullets": [
                "A minor, non-recurring issue is noted and closed.",
                "A severe first-time issue is flagged for management.",
                "A lease violation is routed to the Breach Notice workflow instead of being closed here.",
            ],
        },
        {
            "title": "Raise what needs a person",
            "kind": "human",
            "summary": "Anything above note-only goes to a person the same day.",
            "bullets": [
                "Severe items are queued for management review immediately.",
                "A repeat item on a unit already noted becomes an escalation.",
            ],
        },
        {
            "title": "Log the incidents",
            "kind": "record",
            "summary": "Write the day's outcome to the record file.",
            "bullets": [
                "The record row keeps the incident history continuous across reports.",
                "Later runs read those rows when deciding whether an issue is recurring.",
            ],
        },
    ],
    # ---- Office/Retail ----
    #
    # Insurance Certificate Audit and Coverage Matching are adapted from the
    # residential vendor and renter workflows: the same shape, retargeted at
    # office and retail holders. Clause Search is written from its own flow —
    # notification in, drafted notice out — and is the only definition here with
    # a Draft step.
    "insurance-certificate-audit": [
        {
            "title": "Gather the certificate",
            "kind": "intake",
            "summary": "Pull the certificate and the AAT requirements standard in force.",
            "bullets": [
                "Reads the certificate folder for the COI under review.",
                "Reads AAT Company Requirements/Documents for the standard to grade against.",
                "Records which file satisfied each requirement, so the check is auditable.",
            ],
        },
        {
            "title": "Redact and read",
            "kind": "analysis",
            "summary": "Strip identifiers, then grade the certificate against the rubric.",
            "bullets": [
                "Identifiers are redacted before the document is read.",
                "Every rubric requirement comes back met, not met, or unclear, with a supporting quote.",
                "Carrier, policy number, limits and policy period are extracted as fields.",
            ],
        },
        {
            "title": "Compare to requirements",
            "kind": "decision",
            "summary": "Pass only on a clean sheet; anything near a threshold goes to a human.",
            "bullets": [
                "A liability limit below the AAT minimum fails.",
                "AAT named as certificate holder rather than additional insured fails.",
                "An expired or not-yet-effective policy period fails.",
                "Anything within 10% of a threshold routes to review rather than clearing.",
            ],
        },
        {
            "title": "Human sign-off",
            "kind": "human",
            "summary": "A person owns the outcome; the agent never clears a holder itself.",
            "bullets": [
                "Anything not clean is queued as an approval case naming the gap.",
                "A failed certificate for a holder already on site escalates the same day.",
                "Expired coverage escalates immediately rather than waiting for the next cycle.",
            ],
        },
        {
            "title": "File the outcome",
            "kind": "record",
            "summary": "Write the decision to the use case's record file.",
            "bullets": [
                "One row per run: property, subject, outcome, and who signed off.",
                "The record file exports as CSV.",
                "Cleared certificates stay filed for the next renewal check.",
            ],
        },
    ],
    "coverage-matching": [
        {
            "title": "Read the governing agreement",
            "kind": "intake",
            "summary": "Pull the agreement that sets the coverage obligation, and the matrix built from it.",
            "bullets": [
                "Finds the lease, contract or agreement that names the required coverage.",
                "Finds the coverage matrix that turns those terms into a checklist.",
                "Property ID and unit identify which tenancy or vendor this run is about.",
            ],
        },
        {
            "title": "Grade the policy against the matrix",
            "kind": "analysis",
            "summary": "Compare what the submitted policy actually provides against each required line.",
            "bullets": [
                "Identifiers are redacted before the policy is read.",
                "Each required coverage line comes back met, not met, or unclear, with a quote.",
                "Limits, endorsements and the policy period are extracted for comparison.",
            ],
        },
        {
            "title": "Match or flag the gap",
            "kind": "decision",
            "summary": "Clear only when every required line is matched by the policy on file.",
            "bullets": [
                "A missing required coverage line fails.",
                "A limit below what the agreement requires fails.",
                "A line the policy does not clearly address is treated as unmet, never as satisfied.",
            ],
        },
        {
            "title": "Hand the gaps to a person",
            "kind": "human",
            "summary": "Queue the mismatch for sign-off, naming each line that did not match.",
            "bullets": [
                "The approval case lists the required line and what the policy actually said.",
                "A gap on an active tenancy or vendor escalates rather than waiting.",
            ],
        },
        {
            "title": "Record the match",
            "kind": "record",
            "summary": "Log which lines matched and which did not.",
            "bullets": [
                "One row per run, exported as the record file.",
                "Later runs read those rows when deciding whether a gap is recurring.",
            ],
        },
    ],
    "clause-search": [
        {
            "title": "Take in the notification",
            "kind": "intake",
            "summary": "Read what was reported, and pull the lease it points at.",
            "bullets": [
                "The notification supplies the incident report and the tenant name, unit and location it concerns.",
                "Reads Daily Activity Reports for the report, and Lease Agreements for the lease.",
                "Property and unit scope the search, so one tenancy is in view rather than the whole folder.",
                "Records which file was read on each side, so the finding can be checked later.",
            ],
        },
        {
            "title": "Match the lease and find the clause",
            "kind": "analysis",
            "summary": "Confirm the lease is the right one, then locate the section the conduct breached.",
            "bullets": [
                "Identifiers are redacted before either document is read.",
                "The tenant name, unit and location on the report are matched against the lease on file — a lease that does not match is reported as unmatched rather than used.",
                "The lease term is checked against the date of the incident, so a clause is only cited while it is in force.",
                "The conduct is then read against the lease section by section, and the section it breaches is quoted with its number.",
                "A section the lease does not clearly prohibit comes back unclear, never as a breach.",
            ],
        },
        {
            "title": "Draft the notice",
            "kind": "draft",
            "summary": "Write the email: the context supplied, the report in summary, then the clause word for word.",
            "bullets": [
                "Opens with the context the notification carried — tenant, unit, location and the date of the incident.",
                "Summarises what the report said, in the report's own terms rather than reworded.",
                "Pastes the breached section verbatim from the lease, with its number, and the cure period it states.",
                "Anything the lease does not support is listed as unresolved instead of being written into the draft.",
                "Nothing is sent. The draft is output, not correspondence.",
            ],
        },
        {
            "title": "Check it stands up",
            "kind": "decision",
            "summary": "Ready for a person only when every quote is grounded in the lease on file.",
            "bullets": [
                "A draft citing no section is held.",
                "A quote that does not appear in the lease as written is held.",
                "Conduct the lease does not actually prohibit is held rather than asserted as a breach.",
                "An incident dated outside the lease term is held.",
            ],
        },
        {
            "title": "Management review",
            "kind": "human",
            "summary": "A person reads the draft and sends it. The agent never sends.",
            "bullets": [
                "The approval case carries the drafted email and the section it quotes.",
                "Anything involving safety, weapons or threats escalates immediately, regardless of what the clause says.",
            ],
        },
        {
            "title": "Log the finding",
            "kind": "record",
            "summary": "Record the search, the section cited, and who confirmed it.",
            "bullets": [
                "One row per search: property, unit, the section cited, and the outcome.",
                "Later runs read those rows when deciding whether the conduct is recurring.",
            ],
        },
    ],

}

# Shared vocabulary for the reference page. Kept in one place so a term means the
# same thing in every use case narrative rather than being redefined slightly
# differently inside each one.
GLOSSARY = [
    {
        "term": "Additional insured",
        "definition": (
            "AAT named on a vendor's or tenant's policy so the policy responds to claims against "
            "AAT. Stronger than certificate holder, and the wording the rubrics require."
        ),
    },
    {
        "term": "Certificate holder",
        "definition": (
            "A party listed on a certificate as having been sent a copy. It confers no coverage. "
            "A certificate naming AAT only as holder does not satisfy an additional-insured "
            "requirement."
        ),
    },
    {
        "term": "COI",
        "definition": "Certificate of insurance — the one-page summary of a policy's coverages, limits and dates.",
    },
    {
        "term": "Cure period",
        "definition": "The window a lease gives a tenant to fix a breach before further action. A breach notice must state it.",
    },
    {
        "term": "DAR",
        "definition": "Daily activity report — the security log for a property, one per day per site.",
    },
    {
        "term": "Rubric",
        "definition": (
            "The fixed list of requirements a document is graded against for a given use case. "
            "Every requirement comes back met, not met, or unclear, with a quote from the document."
        ),
    },
    {
        "term": "Met / not met / unclear",
        "definition": (
            "The three grades a requirement can receive. 'Unclear' means the document does not say — "
            "it is never treated as satisfied."
        ),
    },
    {
        "term": "Approval case",
        "definition": (
            "A queued item waiting on a person. Raised whenever a run cannot clear on its own, and "
            "closed as approved or sent back."
        ),
    },
    {
        "term": "Human in the loop",
        "definition": (
            "The rule that no outcome is final until a person signs it off. The agent reads, grades "
            "and drafts; it does not clear, send or archive on its own authority."
        ),
    },
    {
        "term": "Record file",
        "definition": "The per-workflow log of completed runs. One row per run, exportable as CSV.",
    },
    {
        "term": "Redaction",
        "definition": "Removing identifiers from an uploaded file before it is read or stored in the repository.",
    },
    {
        "term": "Escalation",
        "definition": "Routing a case above the normal reviewer — to the division head — because of severity or repetition.",
    },
    {
        "term": "Division",
        "definition": (
            "The business line a user signs in under: Multifamily/Residential or Office/Retail. It "
            "scopes every folder, use case and record in the system."
        ),
    },
]


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "step"


# What a use case created in the UI starts as: the same five-kind spine the
# shipped use cases follow, worded generically. A new use case is therefore
# runnable and reviewable the moment it exists — its author edits wording
# rather than assembling a workflow from an empty page — and every kind the
# runner knows how to execute is already there to be kept or deleted.
NEW_WORKFLOW_STEPS = [
    {
        "title": "Gather the documents",
        "kind": "intake",
        "summary": "Collect the paperwork this use case grades, and the standard to grade it against.",
        "bullets": [
            "Reads this use case's folder for each document listed as required.",
            "Records which file satisfied each requirement, so the check is auditable.",
        ],
    },
    {
        "title": "Redact and read",
        "kind": "analysis",
        "summary": "Strip identifiers, then grade the document against this use case's requirements.",
        "bullets": [
            "Redacts personal identifiers before anything is sent for analysis.",
            "Grades the document against the requirements set on this use case.",
        ],
    },
    {
        "title": "Apply the rule",
        "kind": "decision",
        "summary": "Decide pass or fail from everything gathered and graded.",
        "bullets": ["Clears only when nothing is missing and every requirement was met."],
    },
    {
        "title": "Hand to an approver",
        "kind": "human",
        "summary": "Queue the outcome for sign-off when the run could not clear on its own.",
        "bullets": ["Raises an approval case naming what blocked the run."],
    },
    {
        "title": "Write the record",
        "kind": "record",
        "summary": "Log the run to this use case's record file.",
        "bullets": ["One row per run, exported as the record file."],
    },
]


def default_steps(workflow_id: str, shipped: bool = False) -> List[dict]:
    """The steps a use case starts life with.

    Shipped use cases have hand-written defaults; one created in the UI gets the
    generic spine, so both have a version 1 to roll back to. Provenance decides
    rather than the slug, because two divisions may use the same slug for
    different use cases.
    """
    if shipped:
        return DEFAULT_STEPS.get(workflow_id) or NEW_WORKFLOW_STEPS
    return NEW_WORKFLOW_STEPS


# ---------------- Definition: the steps behind diagram, narrative and run ----------------

def _step_dict(step: WorkflowStep) -> dict:
    return {
        "key": step.key,
        "position": step.position,
        "title": step.title,
        "kind": step.kind,
        "kind_label": STEP_KINDS.get(step.kind, "Step"),
        "summary": step.summary or "",
        "bullets": [line for line in (step.bullets or "").split("\n") if line.strip()],
    }


def _ordered_steps(db: Session, workflow_id: str, division: Division) -> List[WorkflowStep]:
    return (
        db.query(WorkflowStep)
        .filter(WorkflowStep.workflow_id == workflow_id, WorkflowStep.division == division)
        .order_by(WorkflowStep.position.asc(), WorkflowStep.id.asc())
        .all()
    )


def _write_steps(
    db: Session,
    workflow_id: str,
    division: Division,
    steps: List[dict],
    updated_by: Optional[str] = None,
    source: str = "edit",
    restored_from: Optional[int] = None,
) -> None:
    """Replace the definition wholesale.

    The step list is short and nothing references a step row by id, so replacing
    it is simpler — and safer against half-applied edits — than reconciling adds,
    moves and deletes one at a time.
    """
    db.query(WorkflowStep).filter(
        WorkflowStep.workflow_id == workflow_id, WorkflowStep.division == division
    ).delete(synchronize_session=False)

    used = set()
    now = datetime.utcnow()
    for position, spec in enumerate(steps):
        title = (spec.get("title") or "").strip() or f"Step {position + 1}"
        key = _slug(spec.get("key") or title)
        while key in used:  # two steps may legitimately share a title
            key = f"{key}-{position}"
        used.add(key)

        kind = (spec.get("kind") or "note").strip().lower()
        if kind not in STEP_KINDS:
            kind = "note"

        bullets = spec.get("bullets") or []
        if isinstance(bullets, str):
            bullets = bullets.split("\n")
        bullets = [b.strip() for b in bullets if b and b.strip()]

        db.add(
            WorkflowStep(
                workflow_id=workflow_id,
                division=division,
                position=position,
                key=key,
                title=title,
                kind=kind,
                summary=(spec.get("summary") or "").strip() or None,
                bullets="\n".join(bullets) or None,
                updated_at=now,
                updated_by=updated_by or None,
            )
        )
    db.commit()
    # Every path that changes a definition comes through here, so recording the
    # version here is what makes the history complete rather than best-effort.
    _record_revision(
        db,
        workflow_id,
        division,
        source=source,
        created_by=updated_by,
        restored_from=restored_from,
    )


def _baseline_steps(db: Session, workflow_id: str, division: Division) -> List[dict]:
    """The definition as first seeded — what "restore defaults" goes back to.

    Read from this division's own seed revision rather than from the shipped
    constant, because the catalog is per division now: Retail's use case may
    share a slug with Residential's and have started from different steps.
    Falls back to the shipped defaults for a definition seeded before
    revisions were recorded.
    """
    seeded = (
        db.query(WorkflowRevision)
        .filter(
            WorkflowRevision.workflow_id == workflow_id,
            WorkflowRevision.division == division,
            WorkflowRevision.source == "seed",
        )
        .order_by(WorkflowRevision.version.asc())
        .first()
    )
    if seeded is not None:
        return json.loads(seeded.steps_json)
    # No recorded seed — fall back to what this use case would have started from.
    row = workflow_catalog.entry(db, workflow_id, division, include_archived=True)
    return list(default_steps(workflow_id, row["shipped"]))


def _is_default(db: Session, workflow_id: str, division: Division, steps: List[dict]) -> bool:
    defaults = _baseline_steps(db, workflow_id, division)
    if len(steps) != len(defaults):
        return False
    for current, default in zip(steps, defaults):
        if current["title"] != default["title"] or current["kind"] != default["kind"]:
            return False
        if current["summary"] != (default.get("summary") or ""):
            return False
        if current["bullets"] != list(default.get("bullets") or []):
            return False
    return True


def get_definition(db: Session, workflow_id: str, division: Division) -> dict:
    """The workflow's steps, seeding its starting definition on first read."""
    # Retired use cases included: their records and history stay readable.
    wf = workflow_catalog.entry(db, workflow_id, division, include_archived=True)

    rows = _ordered_steps(db, workflow_id, division)
    if not rows:
        _write_steps(
            db,
            workflow_id,
            division,
            default_steps(workflow_id, wf["shipped"]),
            "AAT default",
            source="seed",
        )
        rows = _ordered_steps(db, workflow_id, division)

    steps = [_step_dict(r) for r in rows]
    latest = max((r.updated_at for r in rows if r.updated_at), default=None)
    return {
        "workflow_id": workflow_id,
        "title": wf["title"],
        "folder": wf["folder"],
        "purpose": wf["purpose"],
        "division": division.value,
        "steps": steps,
        "updated_at": latest.isoformat() if latest else None,
        "updated_by": rows[0].updated_by if rows else None,
        "is_default": _is_default(db, workflow_id, division, steps),
        # Carried through so the page can render what this use case requires
        # and grades against without a second lookup.
        "documents": wf["documents"],
        "rubric": wf["rubric"],
        "document_kinds": wf["document_kinds"],
        # What the run form asks for, so the page builds its boxes from the use
        # case rather than from a fixed set that fits only some of them.
        "run_inputs": wf["run_inputs"],
        "archived": wf["archived"],
    }


def seed_definition(
    db: Session, workflow_id: str, division: Division, updated_by: Optional[str] = None
) -> dict:
    """Give a newly created use case its starting definition, from a clean slate.

    Any step rows and revisions already sitting on this (slug, division) are
    cleared first. They can exist without the use case existing: before the
    catalog was per division, reading a use case in any division seeded rows for
    it, and those rows outlived the shared catalog. Inheriting them would start a
    new use case from another division's steps and make "restore defaults" go
    back to them.

    Called only for a use case just created. Editing an existing one goes
    through `update_definition`, which preserves history.
    """
    wf = workflow_catalog.entry(db, workflow_id, division, include_archived=True)

    db.query(WorkflowStep).filter(
        WorkflowStep.workflow_id == workflow_id, WorkflowStep.division == division
    ).delete(synchronize_session=False)
    db.query(WorkflowRevision).filter(
        WorkflowRevision.workflow_id == workflow_id, WorkflowRevision.division == division
    ).delete(synchronize_session=False)
    db.commit()

    _write_steps(
        db,
        workflow_id,
        division,
        default_steps(workflow_id, wf["shipped"]),
        updated_by or "New use case",
        source="seed",
    )
    return get_definition(db, workflow_id, division)


def update_definition(
    db: Session,
    workflow_id: str,
    division: Division,
    steps: List[dict],
    updated_by: Optional[str] = None,
) -> dict:
    """Rewrite the definition from an edited narrative.

    Steps may be added, reordered, retitled or removed. The run walks whatever
    comes back out of here, which is what keeps the narrative honest: a step
    written into the narrative is a step the run actually reports on.
    """
    if not workflow_catalog.exists(db, workflow_id, division, include_archived=True):
        raise ValueError(f"Unknown workflow '{workflow_id}' in {division.value}")
    if not steps:
        raise ValueError("A workflow needs at least one step.")

    # Seed first if this definition has never been read, so version 1 is always
    # the shipped default. Otherwise the first edit to an untouched workflow
    # would become version 1 and leave nothing to roll back to.
    get_definition(db, workflow_id, division)

    _write_steps(db, workflow_id, division, steps, updated_by)
    return get_definition(db, workflow_id, division)


def reset_definition(
    db: Session, workflow_id: str, division: Division, updated_by: Optional[str] = None
) -> dict:
    """Restore the starting steps — the escape hatch after a bad edit."""
    if not workflow_catalog.exists(db, workflow_id, division, include_archived=True):
        raise ValueError(f"Unknown workflow '{workflow_id}' in {division.value}")
    get_definition(db, workflow_id, division)  # baseline on the record first
    _write_steps(
        db,
        workflow_id,
        division,
        _baseline_steps(db, workflow_id, division),
        updated_by or "AAT default",
        source="reset",
    )
    return get_definition(db, workflow_id, division)


# ---------------- Revisions: every version a definition has had ----------------

def _storable_steps(db: Session, workflow_id: str, division: Division) -> List[dict]:
    """The current definition in the shape `_write_steps` accepts.

    Storing it in the write shape is what makes a restore a straight replay of a
    past version rather than a translation that could lose something.
    """
    return [
        {
            "key": row.key,
            "title": row.title,
            "kind": row.kind,
            "summary": row.summary or "",
            "bullets": [line for line in (row.bullets or "").split("\n") if line.strip()],
        }
        for row in _ordered_steps(db, workflow_id, division)
    ]


def _describe_change(previous: Optional[List[dict]], current: List[dict]) -> str:
    """A plain-language summary of what one version changed against the last.

    Written at save time, while both sides are in hand, so the log reads as what
    happened rather than as two lists a person has to compare themselves.
    """
    if previous is None:
        return f"Initial definition — {len(current)} step{'s' if len(current) != 1 else ''}."

    before = {s["title"]: s for s in previous}
    after = {s["title"]: s for s in current}

    added = [t for t in after if t not in before]
    removed = [t for t in before if t not in after]
    retyped = [
        f"“{t}” is now {after[t]['kind'].title()}"
        for t in after
        if t in before and after[t]["kind"] != before[t]["kind"]
    ]
    reworded = [
        t
        for t in after
        if t in before
        and after[t]["kind"] == before[t]["kind"]
        and (after[t]["summary"] != before[t]["summary"] or after[t]["bullets"] != before[t]["bullets"])
    ]
    kept_order_before = [s["title"] for s in previous if s["title"] in after]
    kept_order_after = [s["title"] for s in current if s["title"] in before]
    reordered = kept_order_before != kept_order_after

    parts = []
    if added:
        parts.append("added " + _quoted_list(added))
    if removed:
        parts.append("removed " + _quoted_list(removed))
    if retyped:
        parts.append(_join_clause(retyped))
    if reworded:
        parts.append("reworded " + _quoted_list(reworded))
    if reordered and not (added or removed):
        parts.append("reordered the steps")

    # A save that changed nothing never gets here — `_record_revision` returns
    # early — so `parts` is only empty if a change slipped past every check.
    if not parts:
        return f"Changed — {len(current)} step{'s' if len(current) != 1 else ''}."
    return (parts[0][0].upper() + parts[0][1:]) + ("; " + "; ".join(parts[1:]) if len(parts) > 1 else "") + "."


def _quoted_list(titles: List[str], limit: int = 3) -> str:
    shown = [f"“{t}”" for t in titles[:limit]]
    extra = len(titles) - len(shown)
    joined = _join_clause(shown)
    return joined + (f" and {extra} more" if extra > 0 else "")


def _join_clause(items: List[str]) -> str:
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def _record_revision(
    db: Session,
    workflow_id: str,
    division: Division,
    source: str = "edit",
    created_by: Optional[str] = None,
    restored_from: Optional[int] = None,
) -> WorkflowRevision:
    """Save the definition as it now stands as the next version.

    A save that left the steps exactly as they were records nothing: the log is
    there to answer "what changed and when", and a version that changed nothing
    only buries the ones that did.
    """
    steps = _storable_steps(db, workflow_id, division)
    latest = (
        db.query(WorkflowRevision)
        .filter(WorkflowRevision.workflow_id == workflow_id, WorkflowRevision.division == division)
        .order_by(WorkflowRevision.version.desc())
        .first()
    )
    previous_steps = json.loads(latest.steps_json) if latest else None
    if previous_steps == steps:
        return latest

    revision = WorkflowRevision(
        workflow_id=workflow_id,
        division=division,
        version=(latest.version + 1) if latest else 1,
        steps_json=json.dumps(steps),
        step_count=len(steps),
        source=source,
        note=_describe_change(previous_steps, steps),
        restored_from=restored_from,
        created_by=created_by or None,
    )
    db.add(revision)
    db.commit()
    db.refresh(revision)
    return revision


def _revision_dict(
    revision: WorkflowRevision, current_version: int, title: Optional[str] = None
) -> dict:
    steps = json.loads(revision.steps_json)
    return {
        "version": revision.version,
        "workflow_id": revision.workflow_id,
        "workflow_title": title or revision.workflow_id,
        "source": revision.source,
        "source_label": REVISION_SOURCES.get(revision.source, "Saved"),
        "note": revision.note or "",
        "restored_from": revision.restored_from,
        "step_count": revision.step_count,
        "steps": [
            {
                "title": s["title"],
                "kind": s["kind"],
                "kind_label": STEP_KINDS.get(s["kind"], "Step"),
            }
            for s in steps
        ],
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
        "created_by": revision.created_by,
        "is_current": revision.version == current_version,
    }


def current_version(db: Session, workflow_id: str, division: Division) -> int:
    return (
        db.query(func.max(WorkflowRevision.version))
        .filter(WorkflowRevision.workflow_id == workflow_id, WorkflowRevision.division == division)
        .scalar()
        or 0
    )


def list_revisions(
    db: Session, workflow_id: str, division: Division, limit: int = 50
) -> List[dict]:
    """Every version of one workflow's definition, newest first."""
    rows = (
        db.query(WorkflowRevision)
        .filter(WorkflowRevision.workflow_id == workflow_id, WorkflowRevision.division == division)
        .order_by(WorkflowRevision.version.desc())
        .limit(limit)
        .all()
    )
    latest = rows[0].version if rows else 0
    names = workflow_catalog.titles(db, division)
    return [_revision_dict(r, latest, names.get(r.workflow_id)) for r in rows]


def change_log(db: Session, division: Division, limit: int = 60) -> List[dict]:
    """Definition changes across every workflow in the division, newest first.

    This is what the Reference page shows: one division-wide history, so "when
    did this system's behaviour last change" is one place to look rather than
    five.
    """
    # Retired use cases included: their history is still history.
    names = workflow_catalog.titles(db, division)
    if not names:
        return []  # a division with no use cases has no history to show

    current = {wf_id: current_version(db, wf_id, division) for wf_id in names}
    rows = (
        db.query(WorkflowRevision)
        .filter(
            WorkflowRevision.division == division,
            # Scoped to use cases this division actually has. Revisions can
            # outlive their use case — before the catalog was per division,
            # reading one seeded rows in every division — and those would
            # otherwise show here as history for something not in the division.
            WorkflowRevision.workflow_id.in_(names.keys()),
        )
        .order_by(WorkflowRevision.created_at.desc(), WorkflowRevision.id.desc())
        .limit(limit)
        .all()
    )
    return [
        _revision_dict(r, current.get(r.workflow_id, 0), names.get(r.workflow_id))
        for r in rows
    ]


def restore_revision(
    db: Session,
    workflow_id: str,
    division: Division,
    version: int,
    updated_by: Optional[str] = None,
) -> dict:
    """Put a past version back as the live definition.

    The restore is itself a new version rather than a rewind: nothing is deleted
    from the history, so restoring a bad restore is possible too.
    """
    if not workflow_catalog.exists(db, workflow_id, division, include_archived=True):
        raise ValueError(f"Unknown workflow '{workflow_id}' in {division.value}")

    revision = (
        db.query(WorkflowRevision)
        .filter(
            WorkflowRevision.workflow_id == workflow_id,
            WorkflowRevision.division == division,
            WorkflowRevision.version == version,
        )
        .first()
    )
    if revision is None:
        raise ValueError(f"Version {version} of '{workflow_id}' does not exist.")

    steps = json.loads(revision.steps_json)
    if not steps:
        raise ValueError(f"Version {version} of '{workflow_id}' has no steps to restore.")

    _write_steps(
        db, workflow_id, division, steps, updated_by, source="restore", restored_from=version
    )
    return get_definition(db, workflow_id, division)


# ---------------- Records ----------------

def log_record(
    db: Session,
    workflow_id: str,
    division: Division,
    outcome: str,
    property_id: str = "",
    unit: str = "",
    subject: str = "",
    decision_note: str = "",
    document_name: str = "",
    recorded_by: str = "",
) -> WorkflowRecord:
    """Write one record-keeping row for a completed run."""
    record = WorkflowRecord(
        workflow_id=workflow_id,
        division=division,
        property_id=property_id or None,
        unit=unit or None,
        subject=subject or None,
        outcome=outcome,
        decision_note=decision_note or None,
        document_name=document_name or None,
        recorded_by=recorded_by or None,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def list_records(db: Session, workflow_id: str, division: Division, limit: int = 200) -> List[dict]:
    rows = (
        db.query(WorkflowRecord)
        .filter(WorkflowRecord.workflow_id == workflow_id, WorkflowRecord.division == division)
        .order_by(WorkflowRecord.recorded_at.desc(), WorkflowRecord.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "property_id": r.property_id or "",
            "unit": r.unit or "",
            "subject": r.subject or "",
            "outcome": r.outcome,
            "decision_note": r.decision_note or "",
            "document_name": r.document_name or "",
            "recorded_by": r.recorded_by or "",
            "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
        }
        for r in rows
    ]


def record_summary(db: Session, workflow_id: str, division: Division) -> dict:
    """Rows logged and when they last moved."""
    base = db.query(WorkflowRecord).filter(
        WorkflowRecord.workflow_id == workflow_id, WorkflowRecord.division == division
    )
    total = base.count()
    last = base.order_by(WorkflowRecord.recorded_at.desc(), WorkflowRecord.id.desc()).first()
    by_outcome = dict(
        db.query(WorkflowRecord.outcome, func.count(WorkflowRecord.id))
        .filter(WorkflowRecord.workflow_id == workflow_id, WorkflowRecord.division == division)
        .group_by(WorkflowRecord.outcome)
        .all()
    )
    return {
        "rows_logged": total,
        "last_updated": last.recorded_at.isoformat() if last and last.recorded_at else None,
        "last_updated_by": last.recorded_by if last else None,
        "last_subject": (last.subject or last.property_id or "") if last else "",
        "by_outcome": by_outcome,
    }


def records_csv(db: Session, workflow_id: str, division: Division) -> str:
    """The workflow's record file, as CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(
        ["Recorded at", "Property", "Unit", "Subject", "Outcome", "Document", "Recorded by", "Note"]
    )
    for r in list_records(db, workflow_id, division, limit=5000):
        writer.writerow(
            [
                r["recorded_at"] or "",
                r["property_id"],
                r["unit"],
                r["subject"],
                r["outcome"],
                r["document_name"],
                r["recorded_by"],
                r["decision_note"],
            ]
        )
    return buffer.getvalue()


# ---------------- Documents ----------------

def folder_documents(db: Session, division: Division, folder_name: str) -> List[Document]:
    """Every document in one folder, newest first."""
    return (
        db.query(Document)
        .join(Folder)
        .filter(Folder.division == division, Folder.name == folder_name)
        .order_by(Document.uploaded_at.desc())
        .all()
    )


# Kept as the private name this module has always used internally.
_folder_documents = folder_documents


def required_documents(db: Session, workflow_id: str, division: Division) -> dict:
    """Per-required-document present/missing, with the file that satisfied it.

    Matching is on filename keywords declared on the use case, so the answer is
    reproducible and the UI can show *which* file counted.
    """
    wf = workflow_catalog.entry(db, workflow_id, division, include_archived=True)

    cache: dict = {}
    items = []
    for spec in wf["documents"]:
        folder_name = spec.get("folder", wf["folder"])
        if folder_name not in cache:
            cache[folder_name] = _folder_documents(db, division, folder_name)

        match = None
        for doc in cache[folder_name]:
            lowered = (doc.filename or "").lower()
            if any(keyword in lowered for keyword in spec["match"]):
                match = doc
                break

        items.append(
            {
                "name": spec["name"],
                "folder": folder_name,
                "present": match is not None,
                "matched_document": (
                    {
                        "id": match.id,
                        "filename": match.filename,
                        "uploaded_at": match.uploaded_at.isoformat() if match.uploaded_at else None,
                        "redacted": match.redacted_at is not None,
                    }
                    if match
                    else None
                ),
            }
        )

    present = sum(1 for i in items if i["present"])
    return {
        "items": items,
        "present": present,
        "total": len(items),
        "missing": [i["name"] for i in items if not i["present"]],
    }


def archived_file(db: Session, document_id: int) -> Optional[dict]:
    """One archived document's bytes, so a step can read what is on file.

    `required_documents` answers whether a document is present. A step that has
    to quote out of it — Clause Search reads the clause out of the lease itself —
    needs the file, not the filename.

    Returns None when the row exists but the file does not. A repository that has
    lost a file should report that as a gap in the run, not raise out of it.
    """
    document = db.get(Document, document_id)
    if document is None or document.folder is None:
        return None
    path = (
        ARCHIVE_ROOT
        / document.folder.division.value
        / document.folder.name
        / document.filename
    )
    if not path.exists():
        return None
    media_type, _ = mimetypes.guess_type(document.filename)
    return {
        "id": document.id,
        "filename": document.filename,
        "folder": document.folder.name,
        "media_type": media_type or "application/octet-stream",
        "content": path.read_bytes(),
    }


def record_files(db: Session, workflow_id: str, division: Division) -> List[dict]:
    """Spreadsheets and record files tied to this workflow, openable in place.

    The generated record file is always listed first — it is derived from
    workflow_records and therefore always current. Real uploads follow.
    """
    wf = workflow_catalog.entry(db, workflow_id, division, include_archived=True)

    summary = record_summary(db, workflow_id, division)
    files = [
        {
            "kind": "generated",
            "name": f"{workflow_id}-records.csv",
            "label": "Workflow record log",
            "rows": summary["rows_logged"],
            "updated_at": summary["last_updated"],
            "url": f"/workflows/{workflow_id}/records.csv?division={division_key(division)}",
        }
    ]

    folders = {wf["folder"]} | {d.get("folder", wf["folder"]) for d in wf["documents"]}
    for folder_name in sorted(folders):
        for doc in _folder_documents(db, division, folder_name):
            if not (doc.filename or "").lower().endswith(RECORD_EXTENSIONS):
                continue
            files.append(
                {
                    "kind": "uploaded",
                    "name": doc.filename,
                    "label": folder_name,
                    "rows": None,
                    "updated_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                    "url": f"/repository/documents/{doc.id}/download",
                }
            )
    return files


def division_key(division: Division) -> str:
    """Kept as a re-export so callers here need not reach into config."""
    return _division_key(division)
