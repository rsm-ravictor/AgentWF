# UI Experience Redesign — Design

**Date:** 2026-08-09
**Scope:** `static/index.html`, `static/style.css`, `static/app.js` only. No backend changes.

## Goal

Take the existing three-screen prototype (login → dashboard → use cases) to the best
possible demo experience: domain-accurate content, a refined visual system with dark
mode, believable simulated workflow runs, and solid accessibility.

## Problems with the current UI

1. **Domain mismatch.** The use cases shown (Lease Compare, Tenant Verify, Document
   Validate…) are generic and do not match the five Multifamily Phase 1 workflows
   defined in CONTEXT.md.
2. **Missing requirement.** CONTEXT.md requires a visible Phase 2 email-ingestion
   placeholder in the UI. There is none.
3. **The seven core folder categories** (Vendor Insurances, Renters Insurance, Lease
   Agreements, Checklists, Breach Agreement Notices, Daily Activity Reports, AAT
   Company Requirements) are invisible in the UI despite being the repository's
   organizing structure.
4. **Polish gaps.** Emoji icons, no dark mode, no focus-visible/aria-live support,
   unlabeled process steps, refresh loses the session, single-style toasts, no
   drag-and-drop upload, no empty/loading states.

## Design

### Information architecture (unchanged shell, richer content)

- **Login** — division picker (Multifamily / Office-Retail) + credentials. Office/Retail
  is selectable but badged "Phase 2 preview" per CONTEXT.md phasing.
- **Dashboard** — greeting; KPI stat-tile row; "Workflows" card grid (the five Phase 1
  workflows); "Repository folders" strip (the seven core categories with doc counts);
  "Email ingestion — coming in Phase 2" placeholder card; recent-activity list.
- **Workflows screen** (renamed from "Use Cases") — left panel: selected workflow
  details, required documents, property/unit search, manual upload with drag-and-drop;
  right workspace: labeled step track specific to each workflow, live activity log,
  results (found/missing), human-in-the-loop email + sign-off.

### Domain content

Workflows and their step tracks come straight from CONTEXT.md:

| Workflow | Steps |
|---|---|
| Vendor Insurance | Fetch docs → Redact → Compare vs AAT requirements → Verdict → Store |
| Renter's Insurance | Fetch lease → Generate checklist → Compare submission → Approve/Draft email → Store |
| Lease & File Checklist | Prepare docs → Build checklist → Verify received → Sign-off queue → Archive |
| Breach Notice | Retrieve lease → Draft notice → Check prior breaches → Management review → Log |
| Security Report | Ingest daily report → Review flags → Classify severity → Escalate/Note → Log |

Each workflow maps to its source folder category so the repository structure is visible.

### Visual system

- Keep the light, Gemini-adjacent aesthetic; refine tokens (spacing scale, radii,
  shadows) and typography (system font stack + "Google Sans"/Roboto fallback chain).
- **Dark mode**: `prefers-color-scheme` default plus a header toggle, persisted in
  `localStorage`; all colors via CSS custom properties.
- **Inline SVG icon set** replaces emoji (shield, refresh, file-text, alert, clipboard,
  mail, folder, moon/sun…), `currentColor` so they theme automatically.
- Stat tiles follow the dataviz contract: sentence-case label, semibold value,
  optional signed delta colored by direction; status colors reserved for status.

### Behavior

- Simulated flows stay simulated but gain believability: per-workflow step labels,
  an appending timestamped activity log during runs (aria-live polite), variable step
  timing, per-document found/missing chips with icons.
- Drag-and-drop anywhere on the upload zone; file chip shown once attached; attached
  uploads count toward "found" documents on the next fetch.
- Session (user, division) persisted in `sessionStorage` — refresh keeps you signed in;
  sign-out clears it.
- Toasts typed success / info / error with icon and matching accent.
- Empty states: workflow workspace shows a hint card until a workflow is selected.

### Accessibility

- `:focus-visible` rings on all interactive elements; buttons are `<button>`, the
  division picker is a radiogroup pattern; step track exposes `aria-current="step"`;
  status text and log use `aria-live="polite"`; `prefers-reduced-motion` disables
  animations; color never the sole channel (icons + text accompany status colors).

### Error handling

Front-end only: guard states (no division / no username / no workflow selected) surface
inline errors or toasts, as now, but with typed styling.

### Testing / verification

No JS test infra exists; verification is by running the FastAPI server and driving the
UI with Playwright: login (both divisions), dashboard render, workflow selection, fetch,
full simulated run, upload, dark-mode toggle, mobile viewport — with screenshots.
