(function () {
  const qs = (s, el) => (el || document).querySelector(s);
  const qsa = (s, el) => Array.from((el || document).querySelectorAll(s));

  // Phase 1 Multifamily workflows (see CONTEXT.md)
  const workflows = {
    'vendor-insurance': {
      title: 'Vendor Insurance',
      desc: 'Compare vendor insurance certificates against AAT company requirements and flag gaps in coverage.',
      icon: 'i-shield',
      folder: 'Vendor Insurances',
      docs: ['Vendor insurance certificate', 'AAT requirements document'],
      steps: ['Fetch docs', 'Redact', 'Compare', 'Verdict', 'Store'],
    },
    'renters-insurance': {
      title: "Renter's Insurance",
      desc: 'Generate a tenant checklist from the lease, compare the submitted policy, and approve or draft a corrective email.',
      icon: 'i-umbrella',
      folder: 'Renters Insurance',
      docs: ['Lease agreement', 'Tenant checklist', 'Submitted insurance policy'],
      steps: ['Fetch lease', 'Build checklist', 'Compare submission', 'Approve / email', 'Store'],
    },
    'lease-checklist': {
      title: 'Lease & File Checklist',
      desc: 'Prepare lease documents, verify every required file is received and matched, then queue for human sign-off.',
      icon: 'i-clipboard',
      folder: 'Lease Agreements',
      docs: ['Lease agreement', 'Addenda / riders', 'File checklist'],
      steps: ['Prepare docs', 'Build checklist', 'Verify received', 'Sign-off queue', 'Archive'],
    },
    'breach-notice': {
      title: 'Breach Notice',
      desc: 'Draft a notice of breach citing specific lease sections, include prior-breach history, and queue for review.',
      icon: 'i-alert',
      folder: 'Breach Agreement Notices',
      docs: ['Tenant lease', 'Violation report', 'Prior breach history'],
      steps: ['Retrieve lease', 'Draft notice', 'Check history', 'Mgmt review', 'Log breach'],
    },
    'security-report': {
      title: 'Daily Activity Report',
      desc: 'Read a DAR, pull out every row highlighted yellow or red, and group the incidents by unit.',
      icon: 'i-flag',
      folder: 'Daily Activity Reports',
      docs: ['Daily activity report (PDF or image, with highlighting intact)'],
      steps: ['Read report', 'Find highlights', 'Extract incidents', 'Group by unit', 'Triage'],
      dar: true, // uses the DAR extractor, not the rubric grader
    },
  };

  const folders = [
    { name: 'Vendor Insurances', count: 18 },
    { name: 'Renters Insurance', count: 24 },
    { name: 'Lease Agreements', count: 41 },
    { name: 'Checklists', count: 15 },
    { name: 'Breach Agreement Notices', count: 6 },
    { name: 'Daily Activity Reports', count: 22 },
    { name: 'AAT Company Requirements', count: 6 },
  ];

  const recentActivity = [
    { icon: 'i-shield', text: 'Vendor Insurance verdict stored for RES-014 — compliant', time: '12 min ago' },
    { icon: 'i-alert', text: 'Breach notice for unit 8C queued for management review', time: '1 hr ago' },
    { icon: 'i-file', text: '4 documents redacted and filed to Lease Agreements', time: '3 hr ago' },
    { icon: 'i-umbrella', text: 'Corrective email drafted for tenant policy at RES-006 / 3B', time: 'Yesterday' },
    { icon: 'i-clock', text: '3 leases flagged as expiring within 30 days', time: 'Yesterday' },
  ];

  const divisionLabels = { retail: 'Office / Retail', mf: 'Multifamily' };

  const state = {
    division: null,
    userName: null,
    profile: null,          // role, permissions and folder scope, resolved server-side
    selectedWorkflow: null,
    foundDocs: [],
    missingDocs: [],
    uploads: [],
    lastFile: null,
    lastDar: null,
    register: null,
    running: false,
    approvals: [],          // pending cases, read from /approvals
    approvalCounts: {},
    expandedGroup: null,    // which use-case group is open on the dashboard
    expandedApproval: null,
    reviewingApproval: null,
    overviews: [],          // one summary payload per use case, for the grid
    detail: null,           // the open use case's own overview payload
    sop: null,
    sopEditing: false,
    repo: { folders: [], folder: '', documents: [], total: 0 },
  };

  // ---------------- API ----------------
  async function api(path, options) {
    const res = await fetch(path, options);
    let payload = null;
    try {
      payload = await res.json();
    } catch {
      payload = null;
    }
    if (!res.ok) {
      throw new Error((payload && payload.detail) || `Request failed (${res.status})`);
    }
    return payload;
  }

  const can = (permission) => !!(state.profile && state.profile.permissions.includes(permission));

  const EMAIL_TEMPLATE = 'Hello,\n\nWe attempted to fetch the required documents for this workflow. Missing: [MISSING]. Please advise or provide them at your earliest convenience.\n\nThanks,\nAAT Agent';

  // ---------------- Helpers ----------------
  function iconSvg(id, cls) {
    return `<svg class="icon${cls ? ' ' + cls : ''}" aria-hidden="true"><use href="#${id}"/></svg>`;
  }

  function toast(message, type) {
    const wrap = qs('#toast-wrap');
    if (!wrap) return;
    const el = document.createElement('div');
    el.className = `toast toast-${type || 'info'}`;
    const icon = type === 'success' ? 'i-check' : type === 'error' ? 'i-x' : 'i-info';
    el.innerHTML = `${iconSvg(icon)}<span></span>`;
    el.lastElementChild.textContent = message;
    wrap.appendChild(el);
    setTimeout(() => el.remove(), 3600);
  }

  // ---------------- Theme ----------------
  const themeToggle = qs('#theme-toggle');
  const savedTheme = localStorage.getItem('aat-theme');
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;

  themeToggle.addEventListener('click', () => {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const current = document.documentElement.dataset.theme || (prefersDark ? 'dark' : 'light');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('aat-theme', next);
  });

  // ---------------- Session ----------------
  function saveSession() {
    sessionStorage.setItem(
      'aat-session',
      JSON.stringify({ division: state.division, userName: state.userName, email: state.email })
    );
  }

  function restoreSession() {
    try {
      const raw = sessionStorage.getItem('aat-session');
      if (!raw) return null;
      const saved = JSON.parse(raw);
      if (!saved.division || !saved.email) return null;
      return saved;
    } catch {
      return null;
    }
  }

  // The role is resolved server-side from the email, not asserted by the client.
  async function resolveProfile(email, division) {
    const payload = await api('/session/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, division }),
    });
    state.profile = payload.profile;
    state.email = payload.profile.email;
    state.userName = payload.profile.name;
    return state.profile;
  }

  // ---------------- Login ----------------
  const divisionOptions = qsa('.division-option');
  divisionOptions.forEach((btn) => {
    btn.addEventListener('click', () => {
      divisionOptions.forEach((b) => {
        const selected = b === btn;
        b.classList.toggle('selected', selected);
        b.setAttribute('aria-checked', String(selected));
      });
      state.division = btn.dataset.division;
      qs('#login-error').textContent = '';
    });
  });

  const loginForm = qs('#login-form');
  const loginError = qs('#login-error');

  const loginSubmit = qs('#login-submit');

  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const user = qs('#login-user').value.trim();
    if (!state.division) { loginError.textContent = 'Choose a division to continue.'; return; }
    if (!user) { loginError.textContent = 'Enter a username.'; return; }
    loginError.textContent = '';
    loginSubmit.disabled = true;
    try {
      await resolveProfile(user, state.division);
      saveSession();
      await enterApp();
    } catch (err) {
      loginError.textContent = err.message;
    } finally {
      loginSubmit.disabled = false;
    }
  });

  async function enterApp() {
    qs('#screen-login').classList.add('hidden');
    qs('#app').classList.remove('hidden');
    qs('.js-division-badge').textContent = divisionLabels[state.division] || state.division;
    renderIdentity();
    renderWorkflowBar();
    switchView('dashboard');
    await loadCatalog();
    await refreshDashboard();
    if (state.division === 'retail') {
      toast('Office / Retail mirrors Multifamily in Phase 2 — showing the Phase 1 preview.', 'info');
    }
  }

  function renderIdentity() {
    const p = state.profile || {};
    qs('.js-user-name').textContent = p.name || state.userName || '—';
    qs('.js-user-role').textContent = p.role_label || '—';
    qs('.js-user-initials').textContent = (p.name || '?')
      .split(/\s+/).slice(0, 2).map((w) => w[0] || '').join('').toUpperCase();
    qs('#ud-admin').classList.toggle('hidden', !can('manage_users'));
  }

  // ---------------- User menu ----------------
  const userMenuBtn = qs('#user-menu-btn');
  const userDropdown = qs('#user-dropdown');

  function closeUserMenu() {
    userDropdown.classList.add('hidden');
    userMenuBtn.setAttribute('aria-expanded', 'false');
  }

  userMenuBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = userDropdown.classList.toggle('hidden');
    userMenuBtn.setAttribute('aria-expanded', String(!open));
  });
  document.addEventListener('click', closeUserMenu);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeUserMenu(); });
  qsa('.ud-item[data-target]', userDropdown).forEach((item) => {
    item.addEventListener('click', () => { closeUserMenu(); switchView(item.dataset.target); });
  });

  qs('#signout-btn').addEventListener('click', () => {
    sessionStorage.removeItem('aat-session');
    closeUserMenu();
    state.division = null;
    state.userName = null;
    state.email = null;
    state.profile = null;
    state.selectedWorkflow = null;
    state.uploads = [];
    state.approvals = [];
    divisionOptions.forEach((b) => { b.classList.remove('selected'); b.setAttribute('aria-checked', 'false'); });
    qs('#login-user').value = '';
    qs('#login-pass').value = '';
    loginError.textContent = '';
    qs('#app').classList.add('hidden');
    qs('#screen-login').classList.remove('hidden');
  });

  // ---------------- View switching ----------------
  qsa('.nav-tab').forEach((tab) => {
    tab.addEventListener('click', () => switchView(tab.dataset.target));
  });

  const VIEWS = ['dashboard', 'workflows', 'usecase', 'repository', 'register', 'profile', 'admin'];

  function switchView(name) {
    VIEWS.forEach((v) => qs(`#view-${v}`).classList.toggle('hidden', v !== name));
    // The use case detail page is a child of Workflows, so that tab stays lit.
    const tab = name === 'usecase' ? 'workflows' : name;
    qsa('.nav-tab').forEach((t) => t.classList.toggle('active', t.dataset.target === tab));
    if (name === 'register') loadRegister();
    if (name === 'workflows') loadUseCaseOverviews();
    if (name === 'repository') loadRepository();
    if (name === 'profile') renderProfile();
    if (name === 'admin') loadAdmin();
    window.scrollTo({ top: 0, behavior: 'auto' });
  }

  // ---------------- Data loading ----------------
  // The backend owns which documents each workflow requires, so the checklist is
  // checked against something real rather than a list duplicated in the client.
  async function loadCatalog() {
    try {
      const payload = await api('/workflows/catalog');
      payload.workflows.forEach((wf) => {
        if (!workflows[wf.id]) return;
        workflows[wf.id].docs = wf.documents.map((d) => d.name);
        workflows[wf.id].steps = wf.steps;
        workflows[wf.id].folder = wf.folder;
      });
    } catch {
      // Keep the built-in definitions if the catalog is unreachable.
    }
  }

  async function refreshDashboard() {
    try {
      const summary = await api(`/dashboard/summary?division=${state.division}`);
      state.approvals = summary.approvals || [];
      state.approvalCounts = summary.approval_counts || {};
      state.summary = summary;
      folders.length = 0;
      (summary.folders || []).forEach((f) => folders.push(f));
      qs('#stat-documents').textContent = summary.documents_total;
      qs('#stat-leases').textContent = summary.leases_expiring_soon;
    } catch (err) {
      toast(`Could not load dashboard data: ${err.message}`, 'error');
    }
    renderDashboard();
  }

  // ---------------- Dashboard ----------------
  function renderDashboard() {
    const hour = new Date().getHours();
    const greetingWord = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
    qs('#dashboard-greeting').textContent = `${greetingWord}, ${state.userName}`;
    qs('#dashboard-subtitle').textContent = `${divisionLabels[state.division]} · here's what's happening across your repository today.`;

    qs('#stat-workflows').textContent = Object.keys(workflows).length;
    if (state.summary) {
      qs('#stat-documents').textContent = state.summary.documents_total;
      qs('#stat-leases').textContent = state.summary.leases_expiring_soon;
    }
    qs('#stat-pending').textContent = state.approvals.length;

    renderApprovals();

    const grid = qs('#review-grid');
    grid.innerHTML = '';
    Object.entries(workflows).forEach(([id, wf]) => {
      const card = document.createElement('div');
      card.className = 'review-card';
      card.innerHTML = `
        <div class="rc-icon">${iconSvg(wf.icon)}</div>
        <h3>${wf.title}</h3>
        <p>${wf.desc}</p>
        <div class="review-meta">${wf.docs.length} required document${wf.docs.length > 1 ? 's' : ''} · ${wf.folder}</div>
        <button class="btn btn-secondary btn-sm" data-review="${id}">Open</button>
      `;
      grid.appendChild(card);
    });
    qsa('[data-review]', grid).forEach((btn) => {
      btn.addEventListener('click', () => selectWorkflow(btn.dataset.review));
    });

    // Chips open the Repository Folder page filtered to that folder.
    const strip = qs('#folder-strip');
    strip.innerHTML = '';
    folders.forEach((f) => {
      const chip = document.createElement('button');
      chip.className = 'folder-chip';
      chip.type = 'button';
      chip.innerHTML = `${iconSvg('i-folder')}<span class="f-name"></span><span class="f-count">${f.count} docs</span>`;
      chip.querySelector('.f-name').textContent = f.name;
      chip.addEventListener('click', () => {
        state.repo.folder = f.name;
        switchView('repository');
      });
      strip.appendChild(chip);
    });

    const list = qs('#activity-list');
    list.innerHTML = '';
    recentActivity.forEach((item) => {
      const li = document.createElement('li');
      li.innerHTML = `${iconSvg(item.icon)}<span class="a-text"></span><span class="a-time"></span>`;
      li.querySelector('.a-text').textContent = item.text;
      li.querySelector('.a-time').textContent = item.time;
      list.appendChild(li);
    });
  }

  // ---------------- Approvals queue ----------------
  // Grouped by use case rather than one flat list: with five workflows feeding
  // one queue, a flat list buries the workflow you actually came here for.
  function renderApprovals() {
    const wrap = qs('#approval-groups');
    wrap.innerHTML = '';

    const grouped = new Map();
    state.approvals.forEach((ap) => {
      if (!grouped.has(ap.workflow)) grouped.set(ap.workflow, []);
      grouped.get(ap.workflow).push(ap);
    });

    const totalCases = state.approvals.length;
    const groupCount = grouped.size;
    const idleCount = Object.keys(workflows).length - groupCount;
    qs('#approvals-summary').textContent = totalCases
      ? `${totalCases} case${totalCases === 1 ? '' : 's'} across ${groupCount} use case${groupCount === 1 ? '' : 's'}` +
        (idleCount > 0 ? ` · ${idleCount} clear` : '')
      : 'Grouped by use case — open a group to work through it';

    qs('#clear-samples').classList.toggle('hidden', !state.approvals.some((a) => a.source === 'sample'));

    if (!totalCases) {
      wrap.innerHTML = `<div class="approval-empty">${iconSvg('i-check')}<span>Nothing awaiting your approval. You're all caught up.</span></div>`;
      return;
    }

    // Keep group order stable — follow the workflow catalog, not insertion order.
    Object.keys(workflows).forEach((wfId) => {
      const items = grouped.get(wfId);
      if (!items || !items.length) return;

      const wf = workflows[wfId];
      const open = state.expandedGroup === wfId;
      const missingCases = items.filter((i) => i.missing.length).length;

      const section = document.createElement('section');
      section.className = `ap-group${open ? ' expanded' : ''}`;

      const head = document.createElement('button');
      head.className = 'ap-group-head';
      head.setAttribute('aria-expanded', String(open));
      head.innerHTML = `
        <span class="apg-icon">${iconSvg(wf.icon)}</span>
        <span class="apg-main">
          <span class="apg-title"></span>
          <span class="apg-sub"></span>
        </span>
        <span class="apg-count">${items.length}</span>
        <span class="ap-chevron">${iconSvg('i-chevron')}</span>
      `;
      head.querySelector('.apg-title').textContent = wf.title;
      head.querySelector('.apg-sub').textContent = missingCases
        ? `${missingCases} of ${items.length} waiting on missing documents`
        : `All ${items.length === 1 ? 'documents' : 'cases'} complete — ready to sign off`;
      head.addEventListener('click', () => {
        state.expandedGroup = open ? null : wfId;
        renderApprovals();
      });

      const body = document.createElement('div');
      body.className = 'ap-group-body';
      if (!open) body.classList.add('hidden');

      const list = document.createElement('ul');
      list.className = 'approval-list';
      items.forEach((ap) => list.appendChild(buildApprovalItem(ap, wf)));
      body.appendChild(list);

      section.appendChild(head);
      section.appendChild(body);
      wrap.appendChild(section);
    });

    qsa('[data-ap-open]', wrap).forEach((btn) => btn.addEventListener('click', () => openApprovalInWorkflow(Number(btn.dataset.apOpen))));
    qsa('[data-ap-approve]', wrap).forEach((btn) => btn.addEventListener('click', () => resolveApproval(Number(btn.dataset.apApprove), 'approved')));
    qsa('[data-ap-return]', wrap).forEach((btn) => btn.addEventListener('click', () => resolveApproval(Number(btn.dataset.apReturn), 'returned')));
  }

  // Rendered in two places — the dashboard queue and the use case detail page.
  // Both are in the DOM at once, so ids are scoped, and each re-renders itself
  // on expand rather than the other one.
  function buildApprovalItem(ap, wf, opts) {
    const { scope = 'dash', rerender = renderApprovals } = opts || {};
    const expanded = state.expandedApproval === ap.id;
    const li = document.createElement('li');
    li.className = `approval-item${expanded ? ' expanded' : ''}`;

    const head = document.createElement('button');
    head.className = 'approval-head';
    head.setAttribute('aria-expanded', String(expanded));
    head.setAttribute('aria-controls', `ap-body-${scope}-${ap.id}`);
    head.innerHTML = `
      <span class="ap-ref"></span>
      <span class="ap-main">
        <span class="ap-subject"></span>
        <span class="ap-meta"></span>
      </span>
      ${ap.source === 'sample' ? '<span class="ap-sample">sample</span>' : ''}
      <span class="ap-flag">${ap.missing.length ? `${ap.missing.length} missing` : 'Ready'}</span>
      <span class="ap-chevron">${iconSvg('i-chevron')}</span>
    `;
    head.querySelector('.ap-ref').textContent = ap.reference;
    head.querySelector('.ap-subject').textContent = ap.subject;
    head.querySelector('.ap-meta').textContent =
      [ap.property, ap.unit].filter(Boolean).join(' / ') + (ap.raised ? ` · ${ap.raised}` : '');
    head.querySelector('.ap-flag').classList.add(ap.missing.length ? 'flag-warn' : 'flag-ok');
    head.addEventListener('click', () => {
      state.expandedApproval = expanded ? null : ap.id;
      rerender();
    });

    const body = document.createElement('div');
    body.className = 'approval-body';
    body.id = `ap-body-${scope}-${ap.id}`;
    if (!expanded) body.classList.add('hidden');

    const docItem = (text, icon) => `<li>${iconSvg(icon)}<span>${esc(text)}</span></li>`;
    const approveDisabled = can('approve_workflow') ? '' : 'disabled title="Your role cannot sign off."';
    body.innerHTML = `
      <div class="ap-reason"><strong>Why it needs you:</strong> <span class="ap-reason-text"></span></div>
      <div class="ap-docs">
        <div>
          <h4>Found</h4>
          <ul class="found-list">${ap.found.map((d) => docItem(d, 'i-check')).join('') || docItem('None', 'i-info')}</ul>
        </div>
        <div>
          <h4>Missing</h4>
          <ul class="missing-list">${ap.missing.map((d) => docItem(d, 'i-x')).join('') || docItem('None', 'i-info')}</ul>
        </div>
      </div>
      <div class="ap-actions">
        <button class="btn btn-primary btn-sm" data-ap-open="${ap.id}">
          Review in workflow <svg class="icon"><use href="#i-arrow-right"/></svg>
        </button>
        <button class="btn btn-secondary btn-sm" data-ap-approve="${ap.id}" ${approveDisabled}>Approve &amp; store</button>
        <button class="btn btn-text btn-sm" data-ap-return="${ap.id}">Send back</button>
      </div>
    `;
    body.querySelector('.ap-reason-text').textContent = ap.reason;

    li.appendChild(head);
    li.appendChild(body);
    return li;
  }

  // Deep-link an approval into the Workflows view, pre-loaded with its context.
  function openApprovalInWorkflow(apId) {
    const ap = state.approvals.find((a) => a.id === apId);
    if (!ap) return;
    if (state.running) return toast('Wait for the current run to finish.', 'error');

    selectWorkflow(ap.workflow);

    propId.value = ap.property;
    unitId.value = ap.unit;
    state.foundDocs = ap.found.slice();
    state.missingDocs = ap.missing.slice();
    state.reviewingApproval = ap.id;

    renderMatches();
    runLog.innerHTML = '';
    logLine(`Opened ${ap.reference} — ${ap.subject}`);
    logLine(`${ap.property} / ${ap.unit} · ${ap.reason}`);
    logLine(`Found ${ap.found.length} of ${workflows[ap.workflow].docs.length} required documents.`, ap.missing.length === 0);
    if (ap.missing.length) logLine(`Missing: ${ap.missing.join(', ')}`);

    statusDesc.textContent = `Reviewing ${ap.reference} — awaiting your decision.`;
    setRunPill('review', 'Needs approval');
    startBtn.disabled = false;

    humanActions.classList.remove('hidden');
    preEmail.value = EMAIL_TEMPLATE.replace('[MISSING]', ap.missing.length ? ap.missing.join(', ') : 'None');

    toast(`Loaded ${ap.reference} for review.`, 'info');
  }

  // Resolving writes a record row server-side, which is what the Workflows
  // mini-dashboard reports as "rows logged".
  async function resolveApproval(apId, outcome) {
    const ap = state.approvals.find((a) => a.id === apId);
    if (!ap) return;
    if (outcome === 'approved' && !can('approve_workflow')) {
      return toast('Your role does not allow signing off.', 'error');
    }

    try {
      await api(`/approvals/${apId}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ outcome, resolved_by: state.userName || '' }),
      });
    } catch (err) {
      return toast(err.message, 'error');
    }

    if (state.expandedApproval === apId) state.expandedApproval = null;
    if (state.reviewingApproval === apId) state.reviewingApproval = null;

    recentActivity.unshift({
      icon: outcome === 'approved' ? 'i-check' : 'i-mail',
      text: outcome === 'approved'
        ? `${ap.reference} approved and stored — ${ap.subject}`
        : `${ap.reference} sent back for correction — ${ap.subject}`,
      time: 'Just now',
    });

    await refreshDashboard();
    if (state.selectedWorkflow === ap.workflow) loadWorkflowOverview(ap.workflow);
    toast(
      outcome === 'approved' ? `${ap.reference} approved and stored.` : `${ap.reference} sent back for correction.`,
      outcome === 'approved' ? 'success' : 'info'
    );
  }

  qs('#clear-samples').addEventListener('click', async () => {
    try {
      const res = await api(`/approvals/samples?division=${state.division}`, { method: 'DELETE' });
      toast(`Removed ${res.removed} sample case${res.removed === 1 ? '' : 's'}.`, 'success');
      await refreshDashboard();
      if (state.selectedWorkflow) loadWorkflowOverview(state.selectedWorkflow);
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  qs('#stat-pending-tile').addEventListener('click', () => {
    switchView('dashboard');
    const section = qs('#approvals-section');
    section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    section.classList.add('flash');
    setTimeout(() => section.classList.remove('flash'), 1200);
  });

  qs('#phase2-notify').addEventListener('click', () => {
    toast("You'll be notified when email ingestion ships in Phase 2.", 'success');
  });

  // ---------------- Workflows screen ----------------
  const ucTitle = qs('#uc-title');
  const ucDesc = qs('#uc-desc');
  const ucFolder = qs('#uc-folder');
  const ucIcon = qs('#uc-icon use');
  const ucDocs = qs('#uc-docs');
  const fetchBtn = qs('#fetch-docs');
  const dropzone = qs('#dropzone');
  const uploadInput = qs('#manual-upload-input');
  const uploadList = qs('#upload-list');
  const startBtn = qs('#start-process');
  const stepsTrack = qs('#uc-status-steps');
  const statusDesc = qs('#uc-status-desc');
  const runPill = qs('#run-pill');
  const runLog = qs('#run-log');
  const matchSummary = qs('#match-summary');
  const matchList = qs('#match-list');
  const humanActions = qs('#human-actions');
  const preEmail = qs('#pre-email');
  const propId = qs('#prop-id');
  const unitId = qs('#unit-id');
  const clientName = qs('#client-name');

  function renderWorkflowBar() {
    const bar = qs('#workflow-bar');
    bar.innerHTML = '';
    Object.entries(workflows).forEach(([id, wf]) => {
      const chip = document.createElement('button');
      chip.className = 'uc-chip';
      chip.dataset.id = id;
      chip.innerHTML = `${iconSvg(wf.icon)}<span></span>`;
      chip.lastElementChild.textContent = wf.title;
      chip.addEventListener('click', () => selectWorkflow(id));
      bar.appendChild(chip);
    });
  }

  function setRunPill(mode, label) {
    runPill.className = `pill pill-${mode}`;
    runPill.textContent = label;
  }

  function logLine(text, ok) {
    const now = new Date();
    const time = now.toTimeString().slice(0, 8);
    const line = document.createElement('div');
    line.className = `log-line${ok ? ' log-ok' : ''}`;
    line.innerHTML = `<span class="log-time">${time}</span><span></span>`;
    line.lastElementChild.textContent = text;
    runLog.appendChild(line);
    runLog.scrollTop = runLog.scrollHeight;
  }

  function selectWorkflow(id) {
    const wf = workflows[id];
    if (!wf || state.running) {
      if (state.running) toast('Wait for the current run to finish.', 'error');
      return;
    }
    state.selectedWorkflow = id;
    state.foundDocs = [];
    state.missingDocs = [];
    state.uploads = [];
    state.reviewingApproval = null;

    qsa('.uc-chip').forEach((c) => c.classList.toggle('selected', c.dataset.id === id));
    ucTitle.textContent = wf.title;
    ucDesc.textContent = wf.desc;
    ucFolder.textContent = `Folder · ${wf.folder}`;
    ucIcon.setAttribute('href', `#${wf.icon}`);

    // Render the checklist from whatever overview data is already in hand, then
    // refresh — selecting a workflow should not blank the panel while it loads.
    const known = (state.overviews || []).find((o) => o.workflow === id);
    renderDocChecklist(known ? known.required_documents : null, wf);
    state.detail = known || null;
    if (known) renderUseCaseDetail();
    else qs('#uc-approvals').innerHTML = '<div class="ai-loading">Loading approvals…</div>';
    loadUseCaseDetail(id);
    loadSop(id);

    uploadList.innerHTML = '';
    clientName.value = '';
    propId.value = '';
    unitId.value = '';
    state.lastFile = null;
    if (analyzeBtn) analyzeBtn.disabled = true;
    if (aiBlock) aiBlock.classList.add('hidden');
    // Running by hand is always available — a search narrows what it runs
    // against, it is not a precondition.
    startBtn.disabled = false;
    matchSummary.textContent = 'No run yet';
    matchList.innerHTML = '';
    humanActions.classList.add('hidden');
    preEmail.value = EMAIL_TEMPLATE;

    renderSteps(wf.steps);
    statusDesc.textContent = 'Idle — fetch the required documents to begin.';
    setRunPill('idle', 'Idle');
    runLog.innerHTML = '<div class="log-line muted">Waiting for a run…</div>';

    switchView('usecase');
  }

  qs('#uc-back').addEventListener('click', () => {
    if (state.running) return toast('Wait for the current run to finish.', 'error');
    switchView('workflows');
  });

  // ---------------- Per-use-case mini-dashboards ----------------
  // Every workflow gets its own card, whether or not it is the one selected, so
  // the page answers "where is work piling up?" without clicking through five tabs.
  const useCaseGrid = qs('#wf-usecases');

  qs('#wf-mini-refresh').addEventListener('click', () => loadUseCaseOverviews());

  async function loadUseCaseOverviews() {
    if (!useCaseGrid.children.length) {
      useCaseGrid.innerHTML = '<div class="ai-loading">Loading use case summaries…</div>';
    }
    try {
      const data = await api(`/workflows/overview?division=${state.division}`);
      state.overviews = data.overviews || [];
      renderUseCases();
      // The selected workflow's checklist is driven by the same payload.
      const mine = state.overviews.find((o) => o.workflow === state.selectedWorkflow);
      if (mine) renderDocChecklist(mine.required_documents, workflows[state.selectedWorkflow]);
    } catch (err) {
      useCaseGrid.innerHTML = '<div class="ai-error"></div>';
      useCaseGrid.firstElementChild.textContent = err.message;
    }
  }

  // Callers that change one workflow's data need both the grid snapshot and the
  // open detail page to catch up, since the two read different endpoints.
  function loadWorkflowOverview(workflowId) {
    const id = workflowId || state.selectedWorkflow;
    return Promise.all([loadUseCaseOverviews(), id ? loadUseCaseDetail(id) : null]);
  }

  function renderUseCases() {
    const overviews = state.overviews || [];
    const totalOpen = overviews.reduce((n, o) => n + o.approvals.length, 0);
    const totalRows = overviews.reduce((n, o) => n + (o.records.rows_logged || 0), 0);
    qs('#wf-mini-summary').textContent =
      `${totalOpen} approval${totalOpen === 1 ? '' : 's'} outstanding · ${totalRows} record row${totalRows === 1 ? '' : 's'} logged`;

    // Snapshot only — name, folder, three numbers. Approvals, record keeping and
    // record files live on the detail page, where there is room to act on them.
    useCaseGrid.innerHTML = overviews
      .map((o) => {
        const wf = workflows[o.workflow] || { icon: 'i-file', title: o.title };
        const docs = o.required_documents;
        const selected = state.selectedWorkflow === o.workflow;
        const complete = docs.present === docs.total;

        return `
        <article class="uc-card${selected ? ' selected' : ''}" data-uc-open="${o.workflow}"
                 tabindex="0" role="button" aria-label="Open ${esc(o.title)}">
          <header class="uc-card-head">
            <span class="uc-card-icon">${iconSvg(wf.icon)}</span>
            <div class="uc-card-title">
              <h3>${esc(o.title)}</h3>
              <span>${esc(o.folder)}</span>
            </div>
            <span class="btn btn-secondary btn-sm uc-open">${selected ? 'Selected' : 'Open'}</span>
          </header>

          <div class="uc-card-stats">
            <div class="ucs ${o.approvals.length ? 'ucs-alert' : ''}">
              <span class="ucs-num">${o.approvals.length}</span>
              <span class="ucs-lbl">outstanding approvals</span>
            </div>
            <div class="ucs">
              <span class="ucs-num">${o.records.rows_logged || 0}</span>
              <span class="ucs-lbl">rows logged</span>
            </div>
            <div class="ucs ${complete ? 'ucs-ok' : 'ucs-warn'}">
              <span class="ucs-num">${docs.present}/${docs.total}</span>
              <span class="ucs-lbl">required docs</span>
            </div>
          </div>
        </article>`;
      })
      .join('');

    qsa('[data-uc-open]', useCaseGrid).forEach((card) => {
      card.addEventListener('click', () => selectWorkflow(card.dataset.ucOpen));
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectWorkflow(card.dataset.ucOpen); }
      });
    });
  }

  // ---------------- Use case detail ----------------
  // Everything the overview card no longer carries: this workflow's approvals,
  // what has been signed off, and the files holding the record.
  async function loadUseCaseDetail(workflowId) {
    try {
      const detail = await api(`/workflows/${workflowId}/overview?division=${state.division}`);
      if (state.selectedWorkflow !== workflowId) return; // user moved on while it loaded
      state.detail = detail;
      renderDocChecklist(detail.required_documents, workflows[workflowId]);
      renderUseCaseDetail();
    } catch (err) {
      qs('#uc-approvals').innerHTML = '<div class="ai-error"></div>';
      qs('#uc-approvals').firstElementChild.textContent = err.message;
    }
  }

  function renderUseCaseDetail() {
    const detail = state.detail;
    if (!detail) return;
    const wf = workflows[detail.workflow];
    const wrap = qs('#uc-approvals');
    const rec = detail.records;

    // --- Outstanding approvals, reusing the dashboard's expandable rows ---
    qs('#uc-ap-count').textContent = detail.approvals.length
      ? `${detail.approvals.length} waiting`
      : 'all clear';
    qs('#uc-ap-count').className = `ws-count ${detail.approvals.length ? 'count-warn' : 'count-ok'}`;

    wrap.innerHTML = '';
    if (!detail.approvals.length) {
      wrap.innerHTML = `<div class="approval-empty">${iconSvg('i-check')}<span>Nothing waiting on a human for this use case.</span></div>`;
    } else {
      const list = document.createElement('ul');
      list.className = 'approval-list';
      detail.approvals.forEach((ap) =>
        list.appendChild(buildApprovalItem(ap, wf, { scope: 'uc', rerender: renderUseCaseDetail }))
      );
      wrap.appendChild(list);
      qsa('[data-ap-open]', wrap).forEach((b) => b.addEventListener('click', () => openApprovalInWorkflow(Number(b.dataset.apOpen))));
      qsa('[data-ap-approve]', wrap).forEach((b) => b.addEventListener('click', () => resolveApproval(Number(b.dataset.apApprove), 'approved')));
      qsa('[data-ap-return]', wrap).forEach((b) => b.addEventListener('click', () => resolveApproval(Number(b.dataset.apReturn), 'returned')));
    }

    // --- Record keeping ---
    qs('#uc-records').innerHTML = `
      <p class="uc-card-note">${
        rec.last_updated
          ? `Last updated ${esc(formatWhen(rec.last_updated))}${rec.last_updated_by ? ` by ${esc(rec.last_updated_by)}` : ''}.`
          : 'No rows recorded yet — sign-offs and send-backs land here.'
      }</p>
      <div class="rec-tally">
        <span class="rec-rows">${rec.rows_logged || 0}</span>
        <span>row${rec.rows_logged === 1 ? '' : 's'} logged</span>
      </div>
      ${Object.keys(rec.by_outcome || {}).length
        ? `<ul class="wfm-outcomes">${Object.entries(rec.by_outcome)
            .map(([k, v]) => `<li><span class="wfo-count">${v}</span><span>${esc(k.replace(/_/g, ' '))}</span></li>`)
            .join('')}</ul>`
        : ''}`;

    // --- Record files ---
    qs('#uc-record-files').innerHTML =
      (detail.record_files || []).map((f) => `<li>
          <a class="wfm-file" href="${esc(f.url)}" target="_blank" rel="noopener">
            ${iconSvg('i-download')}<span class="wff-name">${esc(f.name)}</span>
          </a>
          <span class="wff-meta">${esc(f.label)}${
            f.rows != null ? ` · ${f.rows} row${f.rows === 1 ? '' : 's'}` : ''
          }</span>
        </li>`).join('') || '<li class="wfm-empty">No record files yet.</li>';
  }

  function formatWhen(iso) {
    if (!iso) return '';
    const d = new Date(iso.endsWith('Z') ? iso : `${iso}Z`);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  // ---------------- Required documents checklist ----------------
  // `checked` comes from the backend, which matches filenames in each document's
  // folder. Before it arrives the list still renders, just without status.
  function renderDocChecklist(checked, wf) {
    if (!wf) return;
    const tally = qs('#uc-doc-tally');
    const hint = qs('#uc-doc-hint');
    ucDocs.innerHTML = '';

    if (!checked) {
      tally.textContent = '';
      hint.textContent = '';
      wf.docs.forEach((name) => {
        const li = document.createElement('li');
        li.className = 'doc-item doc-unknown';
        li.innerHTML = `${iconSvg('i-file')}<span class="doc-name"></span>`;
        li.querySelector('.doc-name').textContent = name;
        ucDocs.appendChild(li);
      });
      return;
    }

    tally.textContent = `${checked.present} / ${checked.total}`;
    tally.className = `doc-tally ${checked.present === checked.total ? 'tally-ok' : 'tally-warn'}`;

    checked.items.forEach((item) => {
      const li = document.createElement('li');
      li.className = `doc-item ${item.present ? 'doc-present' : 'doc-missing'}`;
      li.innerHTML = `
        ${iconSvg(item.present ? 'i-check' : 'i-x')}
        <span class="doc-body">
          <span class="doc-name"></span>
          <span class="doc-sub"></span>
        </span>`;
      li.querySelector('.doc-name').textContent = item.name;
      li.querySelector('.doc-sub').textContent = item.present
        ? item.matched_document.filename
        : `not in ${item.folder}`;
      ucDocs.appendChild(li);
    });

    hint.textContent = checked.missing.length
      ? `Missing ${checked.missing.length} of ${checked.total}. Upload them below or fetch from the repository.`
      : 'Everything this workflow needs is in the repository.';
    hint.className = `doc-hint ${checked.missing.length ? 'hint-warn' : 'hint-ok'}`;
  }

  // ---------------- Standing instructions (per-workflow reference doc) ----------------
  const SOP_FIELDS = [
    { key: 'inputs_expected', label: 'Inputs expected', hint: 'What the agent needs before it can start' },
    { key: 'steps_taken', label: 'Steps taken', hint: 'What it does, in order, every run' },
    { key: 'pass_fail_logic', label: 'Pass / fail logic', hint: 'What clears, what fails, what routes to a human' },
    { key: 'escalation_rules', label: 'Escalation rules', hint: 'When a person must be pulled in, and who' },
  ];

  const sopPanel = qs('#sop-panel');
  const sopGrid = qs('#sop-grid');

  async function loadSop(workflowId) {
    sopPanel.classList.remove('hidden');
    state.sopEditing = false;
    sopGrid.innerHTML = '<div class="ai-loading">Loading standing instructions…</div>';
    try {
      const sop = await api(`/workflows/${workflowId}/sop?division=${state.division}`);
      if (state.selectedWorkflow !== workflowId) return;
      state.sop = sop;
      renderSop();
    } catch (err) {
      sopGrid.innerHTML = '<div class="ai-error"></div>';
      sopGrid.firstElementChild.textContent = err.message;
    }
  }

  function renderSop() {
    const sop = state.sop;
    if (!sop) return;
    const editable = can('edit_sop');

    qs('#sop-meta').textContent =
      `What the agent does every time this workflow runs · ` +
      (sop.is_default
        ? 'AAT defaults, not yet customised'
        : `edited ${formatWhen(sop.updated_at)}${sop.updated_by ? ` by ${sop.updated_by}` : ''}`);

    qs('#sop-edit').classList.toggle('hidden', state.sopEditing || !editable);
    qs('#sop-save').classList.toggle('hidden', !state.sopEditing);
    qs('#sop-cancel').classList.toggle('hidden', !state.sopEditing);
    qs('#sop-reset').classList.toggle('hidden', !state.sopEditing || sop.is_default);

    sopGrid.innerHTML = SOP_FIELDS.map((f) => `
      <section class="sop-field">
        <h4>${f.label}</h4>
        <p class="sop-hint">${f.hint}</p>
        ${state.sopEditing
          ? `<textarea data-sop="${f.key}" rows="7">${esc(sop[f.key] || '')}</textarea>`
          : `<div class="sop-text">${esc(sop[f.key] || '').replace(/\n/g, '<br>') || '<span class="muted">Not set.</span>'}</div>`}
      </section>`).join('') +
      (editable ? '' : `<p class="sop-locked">${iconSvg('i-lock')} Your role can read these instructions but not change them.</p>`);
  }

  qs('#sop-edit').addEventListener('click', () => {
    if (!can('edit_sop')) return toast('Your role cannot edit standing instructions.', 'error');
    state.sopEditing = true;
    renderSop();
  });

  qs('#sop-cancel').addEventListener('click', () => {
    state.sopEditing = false;
    renderSop();
  });

  qs('#sop-save').addEventListener('click', async () => {
    const body = { division: state.division, updated_by: state.userName || '' };
    qsa('[data-sop]', sopGrid).forEach((el) => { body[el.dataset.sop] = el.value; });
    try {
      state.sop = await api(`/workflows/${state.selectedWorkflow}/sop`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      state.sopEditing = false;
      renderSop();
      toast('Standing instructions saved.', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  qs('#sop-reset').addEventListener('click', async () => {
    try {
      state.sop = await api(`/workflows/${state.selectedWorkflow}/sop/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ division: state.division, updated_by: state.userName || '' }),
      });
      state.sopEditing = false;
      renderSop();
      toast('Restored the shipped defaults.', 'info');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  function renderSteps(steps) {
    stepsTrack.innerHTML = '';
    steps.forEach((label, i) => {
      const node = document.createElement('div');
      node.className = 'p-node';
      node.innerHTML = `<div class="p-step">${i + 1}</div><div class="p-label"></div>`;
      node.lastElementChild.textContent = label;
      stepsTrack.appendChild(node);
      if (i < steps.length - 1) {
        const conn = document.createElement('div');
        conn.className = 'p-connector';
        stepsTrack.appendChild(conn);
      }
    });
  }

  function resetSteps() {
    qsa('.p-node', stepsTrack).forEach((n) => { n.classList.remove('active', 'done'); n.querySelector('.p-step').removeAttribute('aria-current'); });
    qsa('.p-connector', stepsTrack).forEach((c) => c.classList.remove('done'));
  }

  // ---------------- Fetch simulation ----------------
  fetchBtn.addEventListener('click', () => {
    if (!state.selectedWorkflow) return toast('Select a workflow first.', 'error');
    if (state.running) return toast('Wait for the current run to finish.', 'error');
    const wf = workflows[state.selectedWorkflow];
    const pid = propId.value.trim() || 'RES-001';
    const uid = unitId.value.trim() || '01A';
    const client = clientName.value.trim();
    const who = client ? `${client} · ${pid} / ${uid}` : `${pid} / ${uid}`;
    statusDesc.textContent = `Searching repository for ${who}…`;
    setRunPill('running', 'Searching');
    logLine(`Query: ${wf.folder} · ${client ? `client ${client} · ` : ''}property ${pid} · unit ${uid}`);

    setTimeout(() => {
      const uploadedNames = state.uploads.map((u) => u.doc);
      const foundFromRepo = wf.docs.slice(0, Math.max(0, wf.docs.length - 1));
      state.foundDocs = wf.docs.filter((d) => foundFromRepo.includes(d) || uploadedNames.includes(d));
      state.missingDocs = wf.docs.filter((d) => !state.foundDocs.includes(d));

      renderMatches();
      logLine(`Found ${state.foundDocs.length} of ${wf.docs.length} required documents.`, state.missingDocs.length === 0);
      if (state.missingDocs.length) logLine(`Missing: ${state.missingDocs.join(', ')}`);
      startBtn.disabled = false;
      statusDesc.textContent = 'Ready to start.';
      setRunPill('idle', 'Ready');
    }, 700);
  });

  function renderMatches() {
    const wf = workflows[state.selectedWorkflow];
    matchSummary.textContent = `Found ${state.foundDocs.length} / ${wf.docs.length} required documents`;
    const item = (text, icon, cls) => `<li class="${cls || ''}">${iconSvg(icon)}<span>${text}</span></li>`;
    matchList.innerHTML = `
      <div>
        <h4>Found</h4>
        <ul class="found-list">${state.foundDocs.map((d) => item(d, 'i-check')).join('') || item('None', 'i-info', 'none')}</ul>
      </div>
      <div>
        <h4>Missing</h4>
        <ul class="missing-list">${state.missingDocs.map((d) => item(d, 'i-x')).join('') || item('None', 'i-info', 'none')}</ul>
      </div>
    `;
  }

  // ---------------- Upload ----------------
  dropzone.addEventListener('click', () => uploadInput.click());
  dropzone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); uploadInput.click(); }
  });
  ['dragover', 'dragenter'].forEach((ev) => dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  }));
  ['dragleave', 'drop'].forEach((ev) => dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
  }));
  dropzone.addEventListener('drop', (e) => {
    if (e.dataTransfer.files.length) attachFile(e.dataTransfer.files[0]);
  });
  uploadInput.addEventListener('change', () => {
    if (uploadInput.files[0]) attachFile(uploadInput.files[0]);
    uploadInput.value = '';
  });

  function attachFile(file) {
    if (!state.selectedWorkflow) return toast('Select a workflow first.', 'error');
    const wf = workflows[state.selectedWorkflow];
    const covered = state.uploads.map((u) => u.doc);
    const target = wf.docs.find((d) => !covered.includes(d) && !state.foundDocs.includes(d)) || wf.docs[wf.docs.length - 1];
    state.uploads.push({ name: file.name, doc: target });
    state.lastFile = file;
    const li = document.createElement('li');
    li.innerHTML = `${iconSvg('i-file')}<span></span>`;
    li.lastElementChild.textContent = `${file.name} → ${target}`;
    uploadList.appendChild(li);
    if (analyzeBtn) analyzeBtn.disabled = false;
    toast(`Attached "${file.name}". Analyze it, or re-fetch to include it.`, 'success');
  }

  // ---------------- Real document analysis (Claude) ----------------
  const analyzeBtn = qs('#analyze-doc');
  const aiBlock = qs('#ai-block');
  const aiBody = qs('#ai-body');
  const aiDecision = qs('#ai-decision');
  const aiHint = qs('#ai-hint');

  const DECISION_META = {
    approve: { label: 'Approve', cls: 'pill-done' },
    needs_human_review: { label: 'Needs human review', cls: 'pill-review' },
    reject: { label: 'Reject', cls: 'pill-error' },
  };
  const STATUS_META = {
    met: { icon: 'i-check', cls: 'f-met' },
    not_met: { icon: 'i-x', cls: 'f-not-met' },
    unclear: { icon: 'i-info', cls: 'f-unclear' },
  };

  // Tell the user up front whether the backend has a key configured.
  fetch('/analyze/workflows')
    .then((r) => (r.ok ? r.json() : null))
    .then((info) => {
      if (info && !info.configured && aiHint) {
        aiHint.textContent = 'Set ANTHROPIC_API_KEY in .env and restart the server to enable analysis.';
        aiHint.classList.add('ai-hint-warn');
      }
    })
    .catch(() => {});

  analyzeBtn?.addEventListener('click', async () => {
    if (!state.selectedWorkflow) return toast('Select a workflow first.', 'error');
    if (!state.lastFile) return toast('Attach a document first.', 'error');

    const wf = workflows[state.selectedWorkflow];
    analyzeBtn.disabled = true;
    setRunPill('running', 'Analyzing');
    statusDesc.textContent = `Sending ${state.lastFile.name} to Claude…`;
    logLine(`Analyzing ${state.lastFile.name} against the ${wf.title} rubric.`);
    aiBlock.classList.remove('hidden');
    aiDecision.className = 'pill pill-running';
    aiDecision.textContent = 'Working';
    aiBody.innerHTML = '<div class="ai-loading">Reading the document and grading it against every requirement…</div>';

    const isDar = !!wf.dar;
    const form = new FormData();
    form.append('property_id', propId.value.trim());
    form.append('division', state.division);
    form.append('upload_file', state.lastFile);
    if (!isDar) {
      form.append('workflow', state.selectedWorkflow);
      form.append('unit_id', unitId.value.trim());
    }

    try {
      const res = await fetch(isDar ? '/analyze/dar' : '/analyze', { method: 'POST', body: form });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.detail || `Request failed (${res.status})`);

      if (isDar) {
        state.lastDar = payload;
        renderDar(payload);
        setRunPill('done', 'Extracted');
        const t = payload.totals;
        statusDesc.textContent = `${t.incidents} incident(s) across ${t.units_affected} unit(s).`;
        logLine(`Extracted ${t.incidents} incidents across ${t.units_affected} units; ${t.escalate} to escalate.`, true);
        if (!payload.report.highlights_detected) {
          logLine('No colour highlighting was visible — extracted all violation rows instead.');
        }
        if (payload.saved) {
          logLine(`Saved to the incident log as report #${payload.report_id}.`, true);
        } else if (payload.save_error) {
          logLine(payload.save_error);
          toast(payload.save_error, 'error');
        }
        if (payload.approvals_raised) {
          logLine(`Raised ${payload.approvals_raised} breach-notice case(s) for escalating units.`, true);
        }
      } else {
        renderVerdict(payload.verdict);
        setRunPill('done', 'Analyzed');
        statusDesc.textContent = 'Analysis complete — review the findings.';
        logLine(`Verdict: ${payload.verdict.decision} (${payload.verdict.confidence} confidence).`, true);
        if (payload.approval) {
          logLine(`Queued ${payload.approval.reference} for human approval.`, true);
        }
        humanActions.classList.remove('hidden');
        preEmail.value = buildEmail(payload.verdict);
      }
      // Whatever the run raised should show up in the counters immediately.
      await refreshDashboard();
      loadWorkflowOverview(state.selectedWorkflow);
    } catch (err) {
      aiDecision.className = 'pill pill-error';
      aiDecision.textContent = 'Error';
      aiBody.innerHTML = '<div class="ai-error"></div>';
      aiBody.firstElementChild.textContent = err.message;
      setRunPill('idle', 'Idle');
      statusDesc.textContent = 'Analysis failed.';
      logLine(`Analysis failed: ${err.message}`);
      toast(err.message, 'error');
    } finally {
      analyzeBtn.disabled = false;
    }
  });

  function renderVerdict(v) {
    const meta = DECISION_META[v.decision] || { label: v.decision, cls: 'pill-idle' };
    aiDecision.className = `pill ${meta.cls}`;
    aiDecision.textContent = meta.label;

    const findings = (v.findings || []).map((f) => {
      const s = STATUS_META[f.status] || STATUS_META.unclear;
      return `<li class="${s.cls}">
        ${iconSvg(s.icon)}
        <div>
          <div class="f-req">${esc(f.requirement)}</div>
          ${f.evidence ? `<div class="f-ev">“${esc(f.evidence)}”</div>` : ''}
        </div>
      </li>`;
    }).join('');

    const fields = (v.extracted_fields || []).map((f) =>
      `<tr><td>${esc(f.label)}</td><td>${esc(f.value)}</td></tr>`).join('');

    const missing = (v.missing_information || []).map((m) => `<li>${esc(m)}</li>`).join('');

    aiBody.innerHTML = `
      <div class="ai-meta">
        <span><strong>Document:</strong> ${esc(v.document_type)}</span>
        <span><strong>Confidence:</strong> ${esc(v.confidence)}</span>
        ${v.is_expected_type ? '' : '<span class="ai-warn">Not the expected document type for this workflow</span>'}
      </div>
      <p class="ai-summary">${esc(v.summary)}</p>
      <h4>Reasoning</h4>
      <p class="ai-reasoning">${esc(v.reasoning)}</p>
      <h4>Requirements</h4>
      <ul class="ai-findings">${findings || '<li class="muted">No findings returned.</li>'}</ul>
      ${fields ? `<h4>Extracted</h4><table class="ai-fields"><tbody>${fields}</tbody></table>` : ''}
      ${missing ? `<h4>Missing information</h4><ul class="ai-missing">${missing}</ul>` : ''}
    `;
  }

  // ---------------- DAR: per-unit incident table ----------------
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const TRIAGE_META = {
    escalate: { label: 'Escalate', cls: 'tri-escalate' },
    watch: { label: 'Watch', cls: 'tri-watch' },
    note_only: { label: 'Note only', cls: 'tri-note' },
  };

  function renderDar(payload) {
    const { report, units, totals } = payload;
    aiDecision.className = `pill ${totals.escalate ? 'pill-error' : totals.watch ? 'pill-review' : 'pill-done'}`;
    aiDecision.textContent = totals.escalate
      ? `${totals.escalate} to escalate`
      : totals.watch ? `${totals.watch} to watch` : 'Nothing flagged';

    const header = `
      <div class="ai-meta">
        ${report.property_name ? `<span><strong>Property:</strong> ${esc(report.property_name)}</span>` : ''}
        ${report.report_date ? `<span><strong>Date:</strong> ${esc(report.report_date)}</span>` : ''}
        ${report.shift_or_range ? `<span><strong>Shift:</strong> ${esc(report.shift_or_range)}</span>` : ''}
        ${report.reporting_officer ? `<span><strong>Officer:</strong> ${esc(report.reporting_officer)}</span>` : ''}
      </div>
      ${report.highlights_detected
        ? ''
        : `<div class="dar-warn">${iconSvg('i-info')}<span>No colour highlighting was visible in this document, so every row describing a violation was extracted instead. Upload the original PDF (not a re-print or plain-text export) to triage by highlight colour.</span></div>`}
      <div class="dar-totals">
        <div><span class="dt-num">${totals.units_affected}</span><span class="dt-lbl">units</span></div>
        <div><span class="dt-num">${totals.incidents}</span><span class="dt-lbl">incidents</span></div>
        <div class="dt-esc"><span class="dt-num">${totals.escalate}</span><span class="dt-lbl">escalate</span></div>
        <div class="dt-watch"><span class="dt-num">${totals.watch}</span><span class="dt-lbl">watch</span></div>
        <div><span class="dt-num">${totals.repeat_units}</span><span class="dt-lbl">repeat</span></div>
      </div>`;

    if (!units.length) {
      aiBody.innerHTML = header + '<p class="ai-summary muted">No highlighted incidents found in this report.</p>'
        + (report.notes ? `<h4>Notes</h4><p class="ai-reasoning">${esc(report.notes)}</p>` : '');
      return;
    }

    const rows = units.map((u, i) => {
      const tri = TRIAGE_META[u.triage] || TRIAGE_META.note_only;
      const kw = u.keywords.map((k) => `<span class="kw">${esc(k)}</span>`).join('');
      const snippet = u.snippets[0] || '';
      const extra = u.snippets.length > 1 ? ` +${u.snippets.length - 1} more` : '';
      return `
        <tr class="dar-row hl-${esc(u.worst_highlight)}" data-unit-row="${i}">
          <td class="c-unit">
            <button class="unit-toggle" data-toggle="${i}" aria-expanded="false">
              ${iconSvg('i-chevron', 'ut-chev')}<strong>${esc(u.unit)}</strong>
            </button>
          </td>
          <td class="c-date">${esc(u.first_violation_date) || '—'}</td>
          <td class="c-count">${u.occurrences}${u.occurrences > 1 ? '<span class="repeat-flag">repeat</span>' : ''}</td>
          <td class="c-triage"><span class="tri ${tri.cls}">${tri.label}</span></td>
          <td class="c-kw">${kw}</td>
          <td class="c-snip">${esc(snippet)}<span class="snip-more">${extra}</span></td>
        </tr>
        <tr class="dar-detail hidden" data-detail="${i}">
          <td colspan="6">
            ${u.incidents.map((inc) => `
              <div class="inc hl-${esc(inc.highlight)}">
                <div class="inc-head">
                  <span class="inc-dot"></span>
                  <strong>${esc(inc.category)}</strong>
                  <span class="inc-when">${esc(inc.date)}${inc.time ? ' · ' + esc(inc.time) : ''}</span>
                  ${inc.lease_relevant ? '<span class="inc-lease">lease-relevant</span>' : ''}
                </div>
                <div class="inc-snip">${esc(inc.snippet)}</div>
                <div class="inc-kw">${inc.keywords.map((k) => `<span class="kw">${esc(k)}</span>`).join('')}</div>
              </div>`).join('')}
          </td>
        </tr>`;
    }).join('');

    aiBody.innerHTML = `
      ${header}
      <div class="dar-actions">
        <button class="btn btn-secondary btn-sm" id="dar-csv">Export CSV</button>
        <button class="btn btn-secondary btn-sm" id="dar-expand">Expand all</button>
      </div>
      <div class="dar-scroll">
        <table class="dar-table">
          <thead>
            <tr>
              <th>Unit</th><th>1st violation</th><th>#</th><th>Triage</th><th>Keywords</th><th>Snippet</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${report.notes ? `<h4>Notes</h4><p class="ai-reasoning">${esc(report.notes)}</p>` : ''}
      <p class="dar-legend">
        <span class="lg hl-red"></span> red highlight — escalate
        <span class="lg hl-yellow"></span> yellow — watch, escalates on recurrence
      </p>`;

    // Row expansion
    qsa('[data-toggle]', aiBody).forEach((btn) => btn.addEventListener('click', () => {
      const i = btn.dataset.toggle;
      const detail = qs(`[data-detail="${i}"]`, aiBody);
      const open = !detail.classList.contains('hidden');
      detail.classList.toggle('hidden', open);
      btn.setAttribute('aria-expanded', String(!open));
      btn.classList.toggle('open', !open);
    }));

    qs('#dar-expand', aiBody)?.addEventListener('click', (e) => {
      const details = qsa('.dar-detail', aiBody);
      const anyClosed = details.some((d) => d.classList.contains('hidden'));
      details.forEach((d) => d.classList.toggle('hidden', !anyClosed));
      qsa('[data-toggle]', aiBody).forEach((b) => {
        b.classList.toggle('open', anyClosed);
        b.setAttribute('aria-expanded', String(anyClosed));
      });
      e.currentTarget.textContent = anyClosed ? 'Collapse all' : 'Expand all';
    });

    qs('#dar-csv', aiBody)?.addEventListener('click', () => exportDarCsv(payload));
  }

  function exportDarCsv(payload) {
    const cell = (v) => `"${String(v == null ? '' : v).replace(/"/g, '""')}"`;
    const lines = [
      ['Unit', 'First violation', 'Latest violation', 'Occurrences', 'Triage', 'Highlight', 'Categories', 'Keywords', 'Snippets']
        .map(cell).join(','),
    ];
    payload.units.forEach((u) => {
      lines.push([
        u.unit, u.first_violation_date, u.latest_violation_date, u.occurrences,
        u.triage, u.worst_highlight, u.categories.join('; '),
        u.keywords.join('; '), u.snippets.join(' | '),
      ].map(cell).join(','));
    });
    // Prefix with BOM so Excel reads UTF-8 correctly.
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const stamp = (payload.report.report_date || 'report').replace(/[^0-9A-Za-z-]/g, '');
    a.href = url;
    a.download = `dar-incidents-${stamp}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    toast('CSV downloaded.', 'success');
  }

  // ---------------- Incident log (standing register) ----------------
  const regBody = qs('#reg-body');
  const regReports = qs('#reg-reports');
  const regProperty = qs('#reg-property');

  qs('#reg-refresh')?.addEventListener('click', () => loadRegister());
  regProperty?.addEventListener('keydown', (e) => { if (e.key === 'Enter') loadRegister(); });
  qs('#reg-csv')?.addEventListener('click', () => {
    if (!state.register || !state.register.units.length) return toast('Nothing to export yet.', 'error');
    exportDarCsv(state.register);
  });

  async function loadRegister() {
    if (!regBody) return;
    regBody.innerHTML = '<div class="ai-loading">Loading incident log…</div>';
    const prop = (regProperty?.value || '').trim();
    try {
      const [regRes, repRes] = await Promise.all([
        fetch(`/dar/register${prop ? `?property_id=${encodeURIComponent(prop)}` : ''}`),
        fetch('/dar/reports'),
      ]);
      if (!regRes.ok) throw new Error(`Register request failed (${regRes.status})`);
      const reg = await regRes.json();
      const reps = repRes.ok ? await repRes.json() : { reports: [] };
      // report_date is absent on the register payload; renderDar reads it for the CSV name.
      reg.report = reg.report || { report_date: '' };
      state.register = reg;
      renderRegister(reg);
      renderReportLog(reps.reports || []);
    } catch (err) {
      regBody.innerHTML = '<div class="ai-error"></div>';
      regBody.firstElementChild.textContent = err.message;
    }
  }

  function renderRegister(reg) {
    const { units, totals } = reg;

    if (!units.length) {
      regBody.innerHTML = `
        <div class="reg-empty">
          ${iconSvg('i-clipboard', 'big')}
          <h3>No incidents logged yet</h3>
          <p>Upload a Daily Activity Report from the <strong>Workflows</strong> tab and analyze it.
          Every highlighted incident is stored here, so a unit's first violation date is tracked
          across reports rather than only within one upload.</p>
          <button class="btn btn-primary btn-sm" id="reg-goto-dar">Go to Daily Activity Report</button>
        </div>`;
      qs('#reg-goto-dar', regBody)?.addEventListener('click', () => selectWorkflow('security-report'));
      return;
    }

    const rows = units.map((u, i) => {
      const tri = TRIAGE_META[u.triage] || TRIAGE_META.note_only;
      const kw = u.keywords.map((k) => `<span class="kw">${esc(k)}</span>`).join('');
      return `
        <tr class="dar-row hl-${esc(u.worst_highlight)}">
          <td class="c-unit">
            <button class="unit-toggle" data-rtoggle="${i}" aria-expanded="false">
              ${iconSvg('i-chevron', 'ut-chev')}<strong>${esc(u.unit)}</strong>
            </button>
          </td>
          <td class="c-date">${esc(u.first_violation_date) || '—'}</td>
          <td class="c-date">${esc(u.latest_violation_date) || '—'}</td>
          <td class="c-count">${u.occurrences}${u.occurrences > 1 ? '<span class="repeat-flag">repeat</span>' : ''}</td>
          <td class="c-triage"><span class="tri ${tri.cls}">${tri.label}</span></td>
          <td class="c-kw">${kw}</td>
        </tr>
        <tr class="dar-detail hidden" data-rdetail="${i}">
          <td colspan="6">
            ${u.incidents.map((inc, j) => {
              const src = (u.sources || [])[j];
              return `
              <div class="inc hl-${esc(inc.highlight)}">
                <div class="inc-head">
                  <strong>${esc(inc.category)}</strong>
                  <span class="inc-when">${esc(inc.date)}${inc.time ? ' · ' + esc(inc.time) : ''}</span>
                  ${inc.lease_relevant ? '<span class="inc-lease">lease-relevant</span>' : ''}
                  ${src && src.filename ? `<span class="inc-src">from ${esc(src.filename)}</span>` : ''}
                </div>
                <div class="inc-snip">${esc(inc.snippet)}</div>
                <div class="inc-kw">${inc.keywords.map((k) => `<span class="kw">${esc(k)}</span>`).join('')}</div>
              </div>`;
            }).join('')}
          </td>
        </tr>`;
    }).join('');

    regBody.innerHTML = `
      <div class="dar-totals">
        <div><span class="dt-num">${totals.units_affected}</span><span class="dt-lbl">units</span></div>
        <div><span class="dt-num">${totals.incidents}</span><span class="dt-lbl">incidents</span></div>
        <div class="dt-esc"><span class="dt-num">${totals.escalate}</span><span class="dt-lbl">escalate</span></div>
        <div class="dt-watch"><span class="dt-num">${totals.watch}</span><span class="dt-lbl">watch</span></div>
        <div><span class="dt-num">${totals.repeat_units}</span><span class="dt-lbl">repeat</span></div>
        <div><span class="dt-num">${totals.reports}</span><span class="dt-lbl">reports</span></div>
      </div>
      <div class="dar-scroll">
        <table class="dar-table">
          <thead>
            <tr><th>Unit</th><th>1st violation</th><th>Latest</th><th>#</th><th>Triage</th><th>Keywords</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="dar-legend">
        <span class="lg hl-red"></span> red — escalate
        <span class="lg hl-yellow"></span> yellow — watch, escalates on recurrence
      </p>`;

    qsa('[data-rtoggle]', regBody).forEach((btn) => btn.addEventListener('click', () => {
      const d = qs(`[data-rdetail="${btn.dataset.rtoggle}"]`, regBody);
      const open = !d.classList.contains('hidden');
      d.classList.toggle('hidden', open);
      btn.classList.toggle('open', !open);
      btn.setAttribute('aria-expanded', String(!open));
    }));
  }

  function renderReportLog(reports) {
    qs('#reg-report-count').textContent = reports.length
      ? `${reports.length} report${reports.length === 1 ? '' : 's'}`
      : '';

    if (!reports.length) {
      regReports.innerHTML = '<div class="empty-state">No reports uploaded yet.</div>';
      return;
    }

    regReports.innerHTML = reports.map((r) => `
      <div class="case-row">
        <div class="case-id">${esc(r.report_date || '—')}</div>
        <div class="rep-file">${iconSvg('i-file')}<span>${esc(r.filename)}</span></div>
        <div class="row-spacer">
          <span class="rep-meta">
            ${r.property_name ? esc(r.property_name) + ' · ' : ''}${r.incident_count} incident${r.incident_count === 1 ? '' : 's'}
            ${r.units.length ? ' · units ' + esc(r.units.slice(0, 6).join(', ')) + (r.units.length > 6 ? '…' : '') : ''}
            ${r.highlights_detected ? '' : ' · <span class="rep-nohl">no highlighting detected</span>'}
          </span>
        </div>
        <div class="rep-sev">
          ${r.severity_counts.red ? `<span class="sev sev-red">${r.severity_counts.red}</span>` : ''}
          ${r.severity_counts.yellow ? `<span class="sev sev-yellow">${r.severity_counts.yellow}</span>` : ''}
        </div>
        <div class="row-actions">
          <button class="btn btn-text btn-sm" data-del-report="${r.id}">Remove</button>
        </div>
      </div>`).join('');

    qsa('[data-del-report]', regReports).forEach((btn) => btn.addEventListener('click', async () => {
      const id = btn.dataset.delReport;
      btn.disabled = true;
      try {
        const res = await fetch(`/dar/reports/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`Delete failed (${res.status})`);
        toast('Report removed.', 'success');
        loadRegister();
      } catch (err) {
        toast(err.message, 'error');
        btn.disabled = false;
      }
    }));
  }

  function buildEmail(v) {
    const gaps = (v.missing_information || []).join(', ');
    const unmet = (v.findings || []).filter((f) => f.status !== 'met').map((f) => f.requirement);
    const items = gaps || unmet.join(', ') || 'None';
    return `Hello,\n\nWe reviewed the document you provided. Outstanding items: ${items}.\n\nPlease advise or provide the missing documentation at your earliest convenience.\n\nThanks,\nAAT Agent`;
  }

  // ---------------- Run simulation ----------------
  startBtn.addEventListener('click', () => {
    if (!state.selectedWorkflow || state.running) return;
    const wf = workflows[state.selectedWorkflow];
    state.running = true;
    startBtn.disabled = true;
    fetchBtn.disabled = true;
    resetSteps();
    humanActions.classList.add('hidden');
    runLog.innerHTML = '';
    setRunPill('running', 'Running');
    logLine(`Run started — ${wf.title}`);
    if (!state.foundDocs.length && !state.missingDocs.length) {
      logLine('No search run — using whatever is already on file for this workflow.');
    }

    const nodes = qsa('.p-node', stepsTrack);
    const connectors = qsa('.p-connector', stepsTrack);
    let i = 0;

    const advance = () => {
      if (i > 0) {
        nodes[i - 1].classList.remove('active');
        nodes[i - 1].classList.add('done');
        if (connectors[i - 1]) connectors[i - 1].classList.add('done');
        logLine(`${wf.steps[i - 1]} — done`, true);
      }
      if (i < wf.steps.length) {
        nodes[i].classList.add('active');
        nodes[i].querySelector('.p-step').setAttribute('aria-current', 'step');
        statusDesc.textContent = `${wf.steps[i]}…`;
        i += 1;
        setTimeout(advance, 480 + Math.random() * 420);
      } else {
        finishRun(wf);
      }
    };
    advance();
  });

  function finishRun(wf) {
    state.running = false;
    fetchBtn.disabled = false;
    startBtn.disabled = false; // so it can be run again without reselecting
    statusDesc.textContent = 'Complete — awaiting human review.';
    setRunPill('done', 'Complete');
    logLine('Run complete. Queued for human review.', true);
    humanActions.classList.remove('hidden');
    preEmail.value = EMAIL_TEMPLATE.replace('[MISSING]', state.missingDocs.length ? state.missingDocs.join(', ') : 'None');
    toast(`${wf.title} run complete.`, 'success');
  }

  qs('#send-email').addEventListener('click', () => {
    if (state.reviewingApproval) {
      const id = state.reviewingApproval;
      logLine(`Correction email sent for ${id}.`);
      resolveApproval(id, 'returned');
      humanActions.classList.add('hidden');
      setRunPill('idle', 'Sent back');
      return;
    }
    toast('Email sent (simulated).', 'success');
  });

  qs('#sign-off').addEventListener('click', async () => {
    if (!can('approve_workflow')) return toast('Your role does not allow signing off.', 'error');
    humanActions.classList.add('hidden');
    setRunPill('done', 'Stored');

    if (state.reviewingApproval) {
      const ref = state.reviewingApproval;
      logLine('Approved — documents stored.', true);
      await resolveApproval(ref, 'approved');
      return;
    }

    // A standalone sign-off is still record-keeping, so log it.
    try {
      await api(`/workflows/${state.selectedWorkflow}/records`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          division: state.division,
          outcome: 'signed_off',
          property_id: propId.value.trim(),
          unit: unitId.value.trim(),
          subject: state.lastFile ? state.lastFile.name : workflows[state.selectedWorkflow].title,
          document_name: state.lastFile ? state.lastFile.name : '',
          recorded_by: state.userName || '',
        }),
      });
      logLine('Signed off — documents stored and logged to the record file.', true);
      loadWorkflowOverview(state.selectedWorkflow);
      toast('Signed off and logged to the workflow record.', 'success');
    } catch (err) {
      logLine(`Sign-off recorded locally but not logged: ${err.message}`);
      toast(err.message, 'error');
    }
  });

  // ---------------- Repository folder ----------------
  const repoGrid = qs('#repo-grid');
  const repoDocuments = qs('#repo-documents');
  const repoSearch = qs('#repo-search');

  qs('#repo-refresh').addEventListener('click', () => loadRepository());
  repoSearch.addEventListener('keydown', (e) => { if (e.key === 'Enter') loadRepositoryDocuments(); });

  async function loadRepository() {
    repoGrid.innerHTML = '<div class="ai-loading">Loading folders…</div>';
    try {
      const data = await api(`/repository/folders?division=${state.division}`);
      state.repo.folders = data.folders;
      state.repo.total = data.total;
      renderRepoFolders();
      await loadRepositoryDocuments();
    } catch (err) {
      repoGrid.innerHTML = '<div class="ai-error"></div>';
      repoGrid.firstElementChild.textContent = err.message;
    }
  }

  function renderRepoFolders() {
    const allowed = (state.profile && state.profile.allowed_folders) || [];
    repoGrid.innerHTML = state.repo.folders
      .map((f) => {
        const locked = allowed.length && !allowed.includes(f.name);
        const selected = state.repo.folder === f.name;
        return `
        <button class="repo-card${selected ? ' selected' : ''}${locked ? ' locked' : ''}" data-folder="${esc(f.name)}"
                ${locked ? 'disabled title="Your role does not include this folder."' : ''}>
          <span class="repo-card-top">
            ${iconSvg(locked ? 'i-lock' : 'i-folder')}
            <span class="repo-count">${f.count}</span>
          </span>
          <span class="repo-name">${esc(f.name)}</span>
          <span class="repo-meta">${
            f.workflows.length ? esc(f.workflows.map((w) => w.title).join(', ')) : 'No workflow reads this folder'
          }</span>
          <span class="repo-when">${f.last_upload ? `last upload ${formatWhen(f.last_upload)}` : 'empty'}</span>
        </button>`;
      })
      .join('');

    qsa('[data-folder]', repoGrid).forEach((btn) =>
      btn.addEventListener('click', () => {
        state.repo.folder = state.repo.folder === btn.dataset.folder ? '' : btn.dataset.folder;
        renderRepoFolders();
        loadRepositoryDocuments();
      })
    );
  }

  async function loadRepositoryDocuments() {
    const folder = state.repo.folder;
    const q = repoSearch.value.trim();
    qs('#repo-list-title').textContent = folder || 'All documents';
    repoDocuments.innerHTML = '<div class="ai-loading">Loading…</div>';
    try {
      const params = new URLSearchParams({ division: state.division });
      if (folder) params.set('folder', folder);
      if (q) params.set('q', q);
      const data = await api(`/repository/documents?${params}`);
      state.repo.documents = data.documents;

      qs('#repo-list-count').textContent = data.documents.length
        ? `${data.documents.length} document${data.documents.length === 1 ? '' : 's'}`
        : '';

      repoDocuments.innerHTML = data.documents.length
        ? data.documents
            .map(
              (d) => `<div class="case-row">
                <div class="rep-file">${iconSvg('i-file')}<span>${esc(d.filename)}</span></div>
                <div class="row-spacer"><span class="rep-meta">${esc(d.folder || '—')}${
                  d.uploaded_at ? ` · ${formatWhen(d.uploaded_at)}` : ''
                }</span></div>
                <div class="rep-sev">${
                  d.redacted ? '<span class="sev sev-ok">redacted</span>' : '<span class="sev sev-yellow">raw</span>'
                }</div>
                <div class="row-actions">
                  <a class="btn btn-text btn-sm" href="/repository/documents/${d.id}/download" target="_blank" rel="noopener">Open</a>
                </div>
              </div>`
            )
            .join('')
        : `<div class="empty-state">${
            folder ? `Nothing in ${esc(folder)} yet.` : 'No documents in the repository yet.'
          } Uploads land here once they pass redaction.</div>`;
    } catch (err) {
      repoDocuments.innerHTML = '<div class="ai-error"></div>';
      repoDocuments.firstElementChild.textContent = err.message;
    }
  }

  // ---------------- Profile ----------------
  function renderProfile() {
    const body = qs('#profile-body');
    const p = state.profile;
    if (!p) {
      body.innerHTML = '<div class="empty-state">No session profile loaded.</div>';
      return;
    }

    // The meter counts permissions actually held. Roles are not a strict ladder —
    // a Reviewer signs off but cannot upload, an Agent the reverse — so a rank
    // bar would claim a containment that does not exist.
    const meter = Array.from({ length: p.permission_total }, (_, i) =>
      `<span class="lvl${i < p.permission_count ? ' lvl-on' : ''}"></span>`).join('');

    body.innerHTML = `
      <div class="profile-grid">
        <section class="panel profile-card">
          <div class="profile-head">
            <div class="avatar big">${esc((p.name || '?').split(/\s+/).slice(0, 2).map((w) => w[0] || '').join('').toUpperCase())}</div>
            <div>
              <h2>${esc(p.name)}</h2>
              <p class="profile-email">${esc(p.email)}</p>
            </div>
          </div>
          <dl class="profile-facts">
            <div><dt>Role</dt><dd><span class="role-pill">${esc(p.role_label)}</span></dd></div>
            <div><dt>Division</dt><dd>${esc(p.division)}</dd></div>
            <div><dt>Status</dt><dd>${p.is_active ? '<span class="sev sev-ok">active</span>' : '<span class="sev sev-red">disabled</span>'}</dd></div>
          </dl>
          <div class="access-level">
            <div class="al-head">
              <span>Access granted</span>
              <strong>${p.permission_count} of ${p.permission_total} permissions</strong>
            </div>
            <div class="al-meter">${meter}</div>
            <p class="al-desc">${esc(p.role_description)}</p>
          </div>
        </section>

        <section class="panel">
          <h2>What this role grants</h2>
          <ul class="perm-list">
            ${p.permission_matrix
              .map(
                (perm) => `<li class="${perm.granted ? 'perm-on' : 'perm-off'}">
                  ${iconSvg(perm.granted ? 'i-check' : 'i-x')}<span>${esc(perm.label)}</span>
                </li>`
              )
              .join('')}
          </ul>
        </section>

        <section class="panel">
          <h2>Folders you can reach</h2>
          ${p.allowed_folders.length
            ? `<ul class="uc-doclist">${p.allowed_folders
                .map((f) => `<li>${iconSvg('i-folder')}<span>${esc(f)}</span></li>`)
                .join('')}</ul>`
            : '<p class="muted">This role has no folder access. Ask an administrator to assign one.</p>'}
          <p class="profile-note">Folder scope follows your role. To change it, your role has to change — see an administrator.</p>
        </section>
      </div>`;
  }

  // ---------------- Admin ----------------
  async function loadAdmin() {
    const body = qs('#admin-body');
    if (!can('manage_users')) {
      body.innerHTML = `
        <div class="reg-empty">
          ${iconSvg('i-lock', 'big')}
          <h3>Administration is restricted</h3>
          <p>Your role (<strong>${esc(state.profile ? state.profile.role_label : 'unknown')}</strong>)
          does not grant user management. An administrator or super user can change that from this page.</p>
        </div>`;
      return;
    }

    body.innerHTML = '<div class="ai-loading">Loading users…</div>';
    try {
      const data = await api('/admin/users');
      renderAdmin(data);
    } catch (err) {
      body.innerHTML = '<div class="ai-error"></div>';
      body.firstElementChild.textContent = err.message;
    }
  }

  function renderAdmin(data) {
    const body = qs('#admin-body');
    const roles = data.roles;
    const isSuper = state.profile.role === 'super_user';

    const rows = data.users
      .map((u) => {
        const self = u.id === state.profile.id;
        // Only a super user may touch super-user access, in the UI and on the server.
        const locked = (!isSuper && u.role === 'super_user') || self;
        const options = roles
          .map((r) => {
            const disabled = r.key === 'super_user' && !isSuper ? 'disabled' : '';
            return `<option value="${r.key}" ${r.key === u.role ? 'selected' : ''} ${disabled}>${esc(r.label)}</option>`;
          })
          .join('');
        return `
        <div class="admin-row${u.is_active ? '' : ' inactive'}">
          <div class="adm-who">
            <span class="avatar sm">${esc((u.name || '?').split(/\s+/).slice(0, 2).map((w) => w[0] || '').join('').toUpperCase())}</span>
            <span class="adm-name">
              <strong>${esc(u.name)}</strong>
              <span>${esc(u.email)}</span>
            </span>
          </div>
          <div class="adm-division">${esc(u.division)}</div>
          <div class="adm-role">
            <select data-role-for="${u.id}" ${locked ? 'disabled' : ''}>${options}</select>
            ${self ? '<span class="adm-note">you</span>' : ''}
          </div>
          <div class="adm-perms">${u.permissions.length} permission${u.permissions.length === 1 ? '' : 's'}</div>
          <div class="adm-actions">
            <button class="btn btn-text btn-sm" data-toggle-active="${u.id}" data-next="${u.is_active ? 'false' : 'true'}"
                    ${self ? 'disabled title="You cannot disable your own account."' : ''}>
              ${u.is_active ? 'Disable' : 'Enable'}
            </button>
          </div>
        </div>`;
      })
      .join('');

    body.innerHTML = `
      <section class="panel admin-panel">
        <div class="section-heading">
          <h2>Users</h2>
          <p>Changing a role changes what that person can do immediately.</p>
        </div>
        <div class="admin-table">
          <div class="admin-row admin-head">
            <div>Person</div><div>Division</div><div>Role</div><div>Grants</div><div></div>
          </div>
          ${rows}
        </div>
      </section>

      <div class="section-heading">
        <h2>Roles and permissions</h2>
        <p>What each role grants, least privileged first</p>
      </div>
      <div class="role-grid">
        ${roles
          .map(
            (r) => `<article class="role-card">
              <div class="role-card-head">
                <span class="role-pill">${esc(r.label)}</span>
                <span class="role-level">level ${r.level}</span>
              </div>
              <p>${esc(r.description)}</p>
              <ul class="role-perms">
                ${data.permissions
                  .map(
                    (p) => `<li class="${r.permissions.includes(p.key) ? 'perm-on' : 'perm-off'}">
                      ${iconSvg(r.permissions.includes(p.key) ? 'i-check' : 'i-x')}<span>${esc(p.label)}</span>
                    </li>`
                  )
                  .join('')}
              </ul>
            </article>`
          )
          .join('')}
      </div>`;

    qsa('[data-role-for]', body).forEach((select) =>
      select.addEventListener('change', () => patchUser(select.dataset.roleFor, { role: select.value }))
    );
    qsa('[data-toggle-active]', body).forEach((btn) =>
      btn.addEventListener('click', () =>
        patchUser(btn.dataset.toggleActive, { is_active: btn.dataset.next === 'true' })
      )
    );
  }

  async function patchUser(userId, changes) {
    try {
      const res = await api(`/admin/users/${userId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...changes, acting_user_id: state.profile.id }),
      });
      toast(`${res.user.name} is now ${res.user.role_label}${res.user.is_active ? '' : ' (disabled)'}.`, 'success');
      loadAdmin();
    } catch (err) {
      toast(err.message, 'error');
      loadAdmin(); // put the control back where the server says it should be
    }
  }

  // ---------------- Boot ----------------
  (async function boot() {
    const saved = restoreSession();
    if (!saved) return;
    state.division = saved.division;
    try {
      await resolveProfile(saved.email, saved.division);
      await enterApp();
    } catch {
      sessionStorage.removeItem('aat-session');
    }
  })();
})();
