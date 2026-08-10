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
      title: 'Security Report',
      desc: 'Review flagged items from daily activity reports, classify severity, and escalate or log incidents.',
      icon: 'i-flag',
      folder: 'Daily Activity Reports',
      docs: ['Daily activity report', 'Incident log'],
      steps: ['Ingest report', 'Review flags', 'Classify severity', 'Escalate / note', 'Log'],
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
    selectedWorkflow: null,
    foundDocs: [],
    missingDocs: [],
    uploads: [],
    running: false,
  };

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
    sessionStorage.setItem('aat-session', JSON.stringify({ division: state.division, userName: state.userName }));
  }

  function restoreSession() {
    try {
      const raw = sessionStorage.getItem('aat-session');
      if (!raw) return false;
      const saved = JSON.parse(raw);
      if (!saved.division || !saved.userName) return false;
      state.division = saved.division;
      state.userName = saved.userName;
      return true;
    } catch {
      return false;
    }
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

  loginForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const user = qs('#login-user').value.trim();
    if (!state.division) { loginError.textContent = 'Choose a division to continue.'; return; }
    if (!user) { loginError.textContent = 'Enter a username.'; return; }
    loginError.textContent = '';
    state.userName = user;
    saveSession();
    enterApp();
  });

  function enterApp() {
    qs('#screen-login').classList.add('hidden');
    qs('#app').classList.remove('hidden');
    qs('.js-division-badge').textContent = divisionLabels[state.division] || state.division;
    qs('.js-user-name').textContent = state.userName;
    renderDashboard();
    renderWorkflowBar();
    switchView('dashboard');
    if (state.division === 'retail') {
      toast('Office / Retail mirrors Multifamily in Phase 2 — showing the Phase 1 preview.', 'info');
    }
  }

  qs('#signout-btn').addEventListener('click', () => {
    sessionStorage.removeItem('aat-session');
    state.division = null;
    state.userName = null;
    state.selectedWorkflow = null;
    state.uploads = [];
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

  function switchView(name) {
    qs('#view-dashboard').classList.toggle('hidden', name !== 'dashboard');
    qs('#view-workflows').classList.toggle('hidden', name !== 'workflows');
    qsa('.nav-tab').forEach((t) => t.classList.toggle('active', t.dataset.target === name));
  }

  // ---------------- Dashboard ----------------
  function renderDashboard() {
    const hour = new Date().getHours();
    const greetingWord = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
    qs('#dashboard-greeting').textContent = `${greetingWord}, ${state.userName}`;
    qs('#dashboard-subtitle').textContent = `${divisionLabels[state.division]} · here's what's happening across your repository today.`;

    qs('#stat-workflows').textContent = Object.keys(workflows).length;
    qs('#stat-documents').textContent = folders.reduce((sum, f) => sum + f.count, 0);
    qs('#stat-leases').textContent = 3;
    qs('#stat-pending').textContent = 2;

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
      btn.addEventListener('click', () => {
        switchView('workflows');
        selectWorkflow(btn.dataset.review);
      });
    });

    const strip = qs('#folder-strip');
    strip.innerHTML = '';
    folders.forEach((f) => {
      const chip = document.createElement('div');
      chip.className = 'folder-chip';
      chip.innerHTML = `${iconSvg('i-folder')}<span class="f-name"></span><span class="f-count">${f.count} docs</span>`;
      chip.querySelector('.f-name').textContent = f.name;
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

    qsa('.uc-chip').forEach((c) => c.classList.toggle('selected', c.dataset.id === id));
    ucTitle.textContent = wf.title;
    ucDesc.textContent = wf.desc;
    ucFolder.textContent = `Folder · ${wf.folder}`;
    ucIcon.setAttribute('href', `#${wf.icon}`);

    ucDocs.innerHTML = '';
    wf.docs.forEach((d) => {
      const li = document.createElement('li');
      li.innerHTML = `${iconSvg('i-file')}<span></span>`;
      li.lastElementChild.textContent = d;
      ucDocs.appendChild(li);
    });

    uploadList.innerHTML = '';
    startBtn.disabled = true;
    matchSummary.textContent = 'No run yet';
    matchList.innerHTML = '';
    humanActions.classList.add('hidden');
    preEmail.value = EMAIL_TEMPLATE;

    qs('#workspace-empty').classList.add('hidden');
    qs('#workspace-body').classList.remove('hidden');
    renderSteps(wf.steps);
    statusDesc.textContent = 'Idle — fetch the required documents to begin.';
    setRunPill('idle', 'Idle');
    runLog.innerHTML = '<div class="log-line muted">Waiting for a run…</div>';
  }

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
    statusDesc.textContent = `Searching repository for ${pid} / ${uid}…`;
    setRunPill('running', 'Searching');
    logLine(`Query: ${wf.folder} · property ${pid} · unit ${uid}`);

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
    const li = document.createElement('li');
    li.innerHTML = `${iconSvg('i-file')}<span></span>`;
    li.lastElementChild.textContent = `${file.name} → ${target}`;
    uploadList.appendChild(li);
    toast(`Attached "${file.name}" as ${target}. Re-fetch to include it.`, 'success');
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
    statusDesc.textContent = 'Complete — awaiting human review.';
    setRunPill('done', 'Complete');
    logLine('Run complete. Queued for human review.', true);
    humanActions.classList.remove('hidden');
    preEmail.value = EMAIL_TEMPLATE.replace('[MISSING]', state.missingDocs.length ? state.missingDocs.join(', ') : 'None');
    toast(`${wf.title} run complete.`, 'success');
  }

  qs('#send-email').addEventListener('click', () => toast('Email sent (simulated).', 'success'));
  qs('#sign-off').addEventListener('click', () => {
    toast('Signed off and stored to the repository (simulated).', 'success');
    humanActions.classList.add('hidden');
    setRunPill('done', 'Stored');
    logLine('Signed off — documents stored.', true);
  });

  // ---------------- Boot ----------------
  if (restoreSession()) enterApp();
})();
