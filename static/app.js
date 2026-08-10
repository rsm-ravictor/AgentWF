(function () {
  const qs = (s, el) => (el || document).querySelector(s);
  const qsa = (s, el) => Array.from((el || document).querySelectorAll(s));

  const usecases = {
    'lease-compare': { title: 'Lease Compare', desc: 'Compare tenant lease with repository master.', icon: '📄', docs: ['Lease Agreement', 'Rider/Addendum'] },
    'renewal-prep': { title: 'Renewal Prep', desc: 'Gather existing lease, tenant contact, and ledger.', icon: '🔄', docs: ['Lease Agreement', 'Payment Ledger', 'Contact Form'] },
    'insurance-check': { title: 'Insurance Check', desc: 'Verify active insurance certificates.', icon: '🛡️', docs: ['Insurance Certificate', 'Agent Letter'] },
    'tenant-verify': { title: 'Tenant Verify', desc: 'Confirm tenant identity and prior leases.', icon: '🪪', docs: ['ID/Driver License', 'Previous Lease'] },
    'doc-validate': { title: 'Document Validate', desc: 'Run validation checks on uploaded docs.', icon: '✅', docs: ['Upload Document'] },
  };

  const divisionLabels = { retail: 'Retail / Office', mf: 'Multifamily' };

  const state = {
    division: null,
    userName: null,
    selectedUsecase: null,
    foundDocs: [],
    missingDocs: [],
  };

  function toast(message) {
    const wrap = qs('#toast-wrap');
    if (!wrap) return;
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = message;
    wrap.appendChild(el);
    setTimeout(() => el.remove(), 3200);
  }

  // ---------------- Login screen ----------------
  const divisionOptions = qsa('.division-option');
  divisionOptions.forEach((btn) => {
    btn.addEventListener('click', () => {
      divisionOptions.forEach((b) => b.classList.toggle('selected', b === btn));
      state.division = btn.dataset.division;
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
    enterApp();
  });

  function enterApp() {
    qs('#screen-login').classList.add('hidden');
    qsa('.js-division-badge').forEach((el) => (el.textContent = divisionLabels[state.division] || state.division));
    qsa('.js-user-name').forEach((el) => (el.textContent = state.userName));
    renderDashboard();
    switchScreen('dashboard');
  }

  qsa('.signout-btn').forEach((btn) => btn.addEventListener('click', signOut));

  function signOut() {
    state.division = null;
    state.userName = null;
    state.selectedUsecase = null;
    divisionOptions.forEach((b) => b.classList.remove('selected'));
    qs('#login-user').value = '';
    qs('#login-pass').value = '';
    qs('#login-error').textContent = '';
    qsa('.screen').forEach((s) => s.classList.add('hidden'));
    qs('#screen-login').classList.remove('hidden');
  }

  // ---------------- Screen switching ----------------
  qsa('.nav-tab').forEach((tab) => {
    tab.addEventListener('click', () => switchScreen(tab.dataset.target));
  });

  function switchScreen(name) {
    qs('#screen-dashboard').classList.toggle('hidden', name !== 'dashboard');
    qs('#screen-usecases').classList.toggle('hidden', name !== 'usecases');
    qsa('.nav-tab').forEach((t) => t.classList.toggle('active', t.dataset.target === name));
  }

  // ---------------- Dashboard ----------------
  function renderDashboard() {
    const hour = 12; // static greeting period; avoids relying on client clock for a demo
    const greetingWord = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
    qs('#dashboard-greeting').textContent = `${greetingWord}, ${state.userName}`;
    qs('#dashboard-subtitle').textContent = `${divisionLabels[state.division]} · here's what's happening across your repository today.`;

    const ucCount = Object.keys(usecases).length;
    const totalDocs = Object.values(usecases).reduce((sum, uc) => sum + uc.docs.length, 0);
    qs('#stat-usecases').textContent = ucCount;
    qs('#stat-documents').textContent = totalDocs * 4;
    qs('#stat-leases').textContent = 3;
    qs('#stat-pending').textContent = 2;

    const grid = qs('#review-grid');
    grid.innerHTML = '';
    Object.entries(usecases).forEach(([id, uc]) => {
      const card = document.createElement('div');
      card.className = 'review-card';
      card.innerHTML = `
        <div class="rc-icon">${uc.icon}</div>
        <h3>${uc.title}</h3>
        <p>${uc.desc}</p>
        <div class="review-meta">${uc.docs.length} required document${uc.docs.length > 1 ? 's' : ''}</div>
        <button class="btn btn-secondary btn-sm" data-review="${id}">Review</button>
      `;
      grid.appendChild(card);
    });
    qsa('[data-review]', grid).forEach((btn) => {
      btn.addEventListener('click', () => {
        switchScreen('usecases');
        selectUsecase(btn.dataset.review);
      });
    });
  }

  // ---------------- Use cases screen ----------------
  const ucChips = qsa('.uc-chip');
  const ucTitle = qs('#uc-title');
  const ucDesc = qs('#uc-desc');
  const ucDocs = qs('#uc-docs');
  const fetchBtn = qs('#fetch-docs');
  const manualUploadBtn = qs('#manual-upload');
  const manualUploadInput = qs('#manual-upload-input');
  const startBtn = qs('#start-process');
  const statusSteps = qsa('.p-step');
  const statusConnectors = qsa('.p-connector');
  const statusDesc = qs('#uc-status-desc');
  const matchSummary = qs('#match-summary');
  const matchList = qs('#match-list');
  const humanActions = qs('#human-actions');
  const preEmail = qs('#pre-email');
  const propId = qs('#prop-id');
  const unitId = qs('#unit-id');

  ucChips.forEach((chip) => chip.addEventListener('click', () => selectUsecase(chip.dataset.id)));

  function selectUsecase(id) {
    const uc = usecases[id];
    if (!uc) return;
    state.selectedUsecase = id;
    state.foundDocs = [];
    state.missingDocs = [];

    ucChips.forEach((c) => c.classList.toggle('selected', c.dataset.id === id));
    ucTitle.textContent = uc.title;
    ucDesc.textContent = uc.desc;
    ucDocs.innerHTML = '';
    uc.docs.forEach((d) => {
      const li = document.createElement('li');
      li.textContent = d;
      ucDocs.appendChild(li);
    });

    startBtn.disabled = true;
    matchSummary.textContent = 'No run yet';
    matchList.innerHTML = '';
    humanActions.classList.add('hidden');
    resetSteps();
    statusDesc.textContent = 'Idle';
  }

  function resetSteps() {
    statusSteps.forEach((s) => s.classList.remove('active', 'done'));
    statusConnectors.forEach((c) => c.classList.remove('done'));
  }

  fetchBtn.addEventListener('click', () => {
    if (!state.selectedUsecase) return toast('Select a use case first.');
    const pid = propId.value || 'RES-001';
    const uid = unitId.value || '01A';
    statusDesc.textContent = `Searching repository for ${pid} / ${uid}…`;
    setTimeout(() => {
      const uc = usecases[state.selectedUsecase];
      state.foundDocs = uc.docs.slice(0, Math.max(0, uc.docs.length - 1));
      state.missingDocs = uc.docs.slice(state.foundDocs.length);
      matchSummary.textContent = `Found ${state.foundDocs.length} / ${uc.docs.length} required documents`;
      matchList.innerHTML = `
        <div>
          <h4>Found</h4>
          <ul class="found-list">${state.foundDocs.map((d) => `<li>${d}</li>`).join('') || '<li>None</li>'}</ul>
        </div>
        <div>
          <h4>Missing</h4>
          <ul class="missing-list">${state.missingDocs.map((d) => `<li>${d}</li>`).join('') || '<li>None</li>'}</ul>
        </div>
      `;
      startBtn.disabled = false;
      statusDesc.textContent = 'Ready to start';
    }, 600);
  });

  manualUploadBtn.addEventListener('click', () => manualUploadInput.click());
  manualUploadInput.addEventListener('change', () => {
    const file = manualUploadInput.files[0];
    if (file) toast(`Selected "${file.name}" — ready to attach to this use case.`);
  });

  startBtn.addEventListener('click', () => {
    if (!state.selectedUsecase) return toast('Select a use case first.');
    resetSteps();
    const steps = ['Search', 'Open', 'Extract', 'Compare', 'Store'];
    let i = 0;
    const timer = setInterval(() => {
      if (i > 0) {
        statusSteps[i - 1].classList.remove('active');
        statusSteps[i - 1].classList.add('done');
        if (statusConnectors[i - 1]) statusConnectors[i - 1].classList.add('done');
      }
      if (i < steps.length) {
        statusSteps[i].classList.add('active');
        statusDesc.textContent = steps[i];
        i += 1;
      } else {
        clearInterval(timer);
        statusDesc.textContent = 'Complete';
        humanActions.classList.remove('hidden');
        preEmail.value = (preEmail.value || '').replace('[MISSING]', state.missingDocs.length ? state.missingDocs.join(', ') : 'None');
      }
    }, 550);
  });

  qs('#send-email').addEventListener('click', () => toast('Email sent (simulated).'));
  qs('#sign-off').addEventListener('click', () => toast('Signed off and stored (simulated).'));
})();
