# AAT System Context

## Purpose
This project is a scaffold for an asset and lease management system with tiered access permissions, centralized document repository monitoring, redaction processing, and workflow automation.

## Business Divisions
- **Multifamily/Residential**
- **Office/Retail**

Each division has a division head with full access to all workflows and folders within their division. Subgroup owners have limited, role-specific access to assigned folders and workflows.

## Core Requirements
- Centralized repository that tracks all leases and flags expired leases.
- Redaction layer applied before any documents enter the repository or are used by agents.
- Manual folder upload support in Phase 1, with email ingestion deferred to Phase 2.
- A Phase 2 UI placeholder should exist for future email ingestion and automated sorting, but it is not required to work in the prototype.
- External decision workflows should reference an environment-defined API key for downstream service calls.
- Required folder categories:
  - Vendor Insurances
  - Renters Insurance
  - Lease Agreements
  - Checklists
  - Breach Agreement Notices
  - Daily Activity Reports
  - AAT Company Requirements/Documents

## Multifamily/Residential Phase 1 Workflows
1. **Vendor Insurance**
   - Compare vendor insurance documents against AAT requirements.
2. **Renter’s Insurance**
   - Generate a tenant checklist from the lease.
   - Send checklist to tenant.
   - Compare submitted insurance to checklist.
   - Approve if compliant or draft a corrective email if not.
3. **Lease, Addenda, and File Checklist**
   - Prepare lease documents.
   - Create a file checklist.
   - Verify required documents are received and matched.
   - Sign off and queue for human review before archiving.
4. **Notices of Breach of Lease**
   - Retrieve the tenant’s lease on violation.
   - Draft breach notice citing specific lease sections.
   - Check prior breaches and include history.
   - Queue notice for management review and sending.
   - Log breach for future reference.
5. **Security Report Workflow**
   - Triggered by daily activity reports.
   - Review flagged items and log incidents.
   - Minor nonrecurring issues receive notes only.
   - Severe first-time issues flagged for management.
   - Lease breaches generate notices similar to breach workflow.

## Office/Retail Phase 2
- Mirror Phase 1 with the same core structure and automation patterns.
- Adapt folder mappings, document sources, and division-specific requirements for Office/Retail.

## Implementation Notes
- The current scaffold includes FastAPI, SQLAlchemy, document redaction, folder-based routing, and workflow stubs.
- The redaction layer is designed as a pre-processing step for PDFs and uploaded files.
- Phase 2 reuses the same architecture with division-specific adjustments rather than a separate rebuild.
- Email inbox ingestion is a Phase 2 capability; Phase 1 relies on manual upload and folder permission controls.
