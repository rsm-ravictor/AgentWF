# Office/Retail use cases

The three use cases this space holds, what each one reads, and what it decides.

| Use case | Runs from | Ends in |
| --- | --- | --- |
| [Insurance Certificate Audit](#1-insurance-certificate-audit) | a certificate of insurance | a pass, or a queued gap |
| [Insurance Coverage Matching](#2-insurance-coverage-matching-workflow) | a policy and the agreement that requires it | a matched matrix, or the lines that did not match |
| [Clause Search](#3-clause-search) | an incident report | a drafted notice quoting the section breached |

Two of them came over from the residential work: **Insurance Certificate Audit**
grew out of Vendor Insurance and **Insurance Coverage Matching** out of Renter's
Insurance, both retargeted at office and retail holders. **Clause Search** is
new, and is the only one here written from its own flow rather than adapted from
an existing one.

Nothing else in the Residential/Multifamily set was carried across. Repository
Audit, which briefly sat in this catalog, has been retired — it is still in the
database with its history intact and can be reinstated, but it is not part of
this space.

## What every use case has in common

Each one is a row in the catalog (`workflow_catalog`) and an ordered list of
steps (`workflow_repo`). The steps are the only source of truth: the diagram, the
written walkthrough and the run all render from them, so editing the walkthrough
changes what a run actually does. Every version of a definition is kept, and any
of them can be rolled back to.

A step's **kind** decides what the run does when it reaches it:

| Kind | What happens |
| --- | --- |
| Intake | Checks the required documents against the repository — and reads the ones that are there, so a later step can quote out of them |
| Analysis | Grades the attached document against the requirements, with the on-file documents alongside it. One model call |
| Draft | Writes the correspondence the reading calls for, quoting the documents word for word. A second model call |
| Decision | Applies the pass rule to everything gathered so far |
| Human | Queues an approval case when the run could not clear on its own |
| Record | Writes the run to the use case's record file |

Three rules hold across all three:

- **A requirement the document is silent on is `unclear`, never `met`.** Absence
  is not satisfaction.
- **Nothing clears, sends or archives on the agent's authority.** It reads,
  grades and drafts; a person signs off.
- **Every run produces a record row**, whether it cleared or not.

Runs are scoped to Office/Retail. Property and unit narrow a run to one tenancy
or holder; both are optional, and a run without them reads the whole folder.

---

## 1. Insurance Certificate Audit

`insurance-certificate-audit` · folder: **Vendor Insurances**

Check a certificate of insurance against AAT's requirements before the holder
works on site.

**Reads**

| Document | Folder | Matched on |
| --- | --- | --- |
| Certificate of insurance | Vendor Insurances | `coi`, `certificate`, `insurance` |
| AAT requirements document | AAT Company Requirements/Documents | `requirement`, `aat` |

**Grades against**

1. Certificate is currently active (today falls between the policy effective and expiration dates)
2. General liability limit is at least $2,000,000 per occurrence
3. AAT is named as an additional insured, not merely as certificate holder
4. Workers compensation coverage is present
5. The named insured matches the vendor or tenant on file

**Steps**

1. **Gather the certificate** *(intake)* — the COI, and the AAT requirements standard in force.
2. **Redact and read** *(analysis)* — identifiers stripped, then every requirement graded met / not met / unclear with a supporting quote. Carrier, policy number, limits and policy period come out as fields.
3. **Compare to requirements** *(decision)* — a limit below the minimum fails; certificate-holder-only fails; an expired or not-yet-effective period fails. Anything within 10% of a threshold routes to review rather than clearing.
4. **Human sign-off** *(human)* — a failed certificate for a holder already on site escalates the same day; expired coverage escalates immediately.
5. **File the outcome** *(record)*.

**What holds it up:** a missing requirements document, a limit under the
minimum, the wrong named party, a lapsed period, or anything sitting close
enough to a threshold to be a judgment call.

---

## 2. Insurance Coverage Matching Workflow

`coverage-matching` · folder: **Vendor Insurances**

Build the required coverage matrix from the governing agreement, then grade the
policy on file against it. Where the certificate audit asks "does this meet AAT's
standard", this one asks "does this meet what *this* agreement obliges" — the
standard is different for every tenancy, and it comes out of the agreement.

**Reads**

| Document | Folder | Matched on |
| --- | --- | --- |
| Governing agreement | Lease Agreements | `lease`, `agreement`, `contract` |
| Coverage matrix | Checklists | `matrix`, `checklist`, `schedule` |
| Submitted policy | Vendor Insurances | `policy`, `insurance`, `certificate` |

**Grades against**

1. Policy is currently active (today falls within the coverage period)
2. Every coverage line required by the governing agreement appears on the policy
3. Each limit meets or exceeds the amount the agreement requires
4. AAT or the managing entity is named as required by the agreement
5. The named insured and insured address match the party and premises on the agreement

**Steps**

1. **Read the governing agreement** *(intake)* — the agreement that sets the obligation, and the matrix built from it.
2. **Grade the policy against the matrix** *(analysis)* — each required line comes back met / not met / unclear, with a quote. Limits, endorsements and the policy period are extracted for comparison.
3. **Match or flag the gap** *(decision)* — a missing line fails; a limit below what the agreement requires fails; **a line the policy does not clearly address is treated as unmet, never as satisfied.**
4. **Hand the gaps to a person** *(human)* — the case lists the required line beside what the policy actually said. A gap on an active tenancy or vendor escalates rather than waiting.
5. **Record the match** *(record)* — which lines matched and which did not, so a recurring gap is visible.

**What holds it up:** any required line the policy does not clearly provide, a
short limit, the wrong named party or premises, or a policy period that does not
cover the term.

---

## 3. Clause Search

`clause-search` · folder: **Lease Agreements**

**New in this space.** The others start from a document someone submitted. This
one starts from something that happened.

An incident is reported — the notification carries the incident report and the
name, unit and location it concerns. From that summary the use case finds the
lease it corresponds to, reads that lease for what the incident violated, pulls
that section out, and drafts the email: the context supplied, the report in
summary, and then the breached wording **pasted exactly as the lease words it**.

The email is a draft. It is never sent by the system.

**Reads**

| Document | Folder | Matched on |
| --- | --- | --- |
| Incident report | Daily Activity Reports | `incident`, `notification`, `activity`, `dar`, `report` |
| Tenant lease | Lease Agreements | `lease`, `agreement`, `contract` |

The clause is quoted out of the **lease on file**, not out of the attachment.
That is why the intake step reads the documents it matched rather than only
counting them: if the lease never reaches the model, the only text available to
quote from is the incident report — the wrong document to take a clause out of,
and one it would misquote plausibly.

**Grades against**

1. The incident report identifies the tenant or party, the unit, the location, and the date the incident occurred
2. The lease on file is for that same party, unit and location
3. The lease is signed by both sides and its term covers the date of the incident
4. The conduct the report describes is prohibited by a specific numbered section of the lease
5. That section is quoted exactly as the lease words it, together with any notice or cure period it sets

**Steps**

1. **Take in the notification** *(intake)* — the incident report from Daily Activity Reports, the lease from Lease Agreements. Property and unit scope the search to one tenancy. Both files are read into the run, and each is named in the log.
2. **Match the lease and find the clause** *(analysis)* — identifiers redacted first. The name, unit and location on the report are matched against the lease on file; a lease that does not match is reported as unmatched rather than used. The lease term is checked against the date of the incident, so a clause is only cited while it is in force. The conduct is then read against the lease section by section, and the section it breaches is quoted with its number. Conduct the lease does not clearly prohibit comes back unclear, never as a breach.
3. **Draft the notice** *(draft)* — opens with the context the notification carried, summarises the report in the report's own terms, and pastes the breached section verbatim with its number and any cure period it states. Anything the lease does not support is listed as **unresolved** and left out of the body rather than written around.
4. **Check it stands up** *(decision)* — a draft citing no section is held; a quote that does not appear in the lease as written is held; conduct the lease does not prohibit is held; an incident dated outside the lease term is held.
5. **Management review** *(human)* — the case carries the drafted email and the section it quotes. Anything involving safety, weapons or threats escalates immediately, regardless of what the clause says.
6. **Log the finding** *(record)* — property, unit, the section cited, the outcome. Later runs read those rows when deciding whether the conduct is recurring.

**Two model calls, not one.** The reading and the drafting are asked for
separately on purpose: one is instructed to withhold judgment wherever a document
is silent, the other to write nothing it cannot quote. Asked together, a single
call tends to soften the first to serve the second. The documents go in again
with the drafting call, because the wording has to come off the source text
rather than out of a summary of it.

**Running it:** attach the incident report. The lease is picked up from the
repository, so it has to be on file under Lease Agreements. Without an
attachment there is no reading for the draft step to work from, and the run says
so rather than inventing one — it does not draft from filenames.

**What holds it up:** no lease matching the report; a lease whose term does not
cover the incident date; conduct the lease does not address; nothing that could
be quoted; or anything the drafter had to leave unresolved.

---

## Where each part lives

| Part | File |
| --- | --- |
| Which use cases a division has | `aat_system/workflow_catalog.py` (live), `aat_system/workflow_repo.py` → `OFFICE_CATALOG` (shipped) |
| The steps behind the diagram, the walkthrough and the run | `aat_system/workflow_repo.py` → `DEFAULT_STEPS` |
| The requirements each one grades against | `aat_system/llm_analyzer.py` → `WORKFLOW_RUBRICS` |
| What a run actually does, step by step | `aat_system/workflow_runner.py` |
| The model calls: grading, and drafting | `aat_system/llm_analyzer.py` → `analyze_document`, `draft_notice` |
| Tests for the Clause Search flow | `tests/test_clause_search.py` |

A use case's requirements, folder and intake list are all editable from the UI —
what is in code is only what a division starts from.
