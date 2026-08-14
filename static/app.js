/* AAT — Access Agent Toolkit
 *
 * Four screens, in the order a user meets them:
 *   1. login      — company + division
 *   2. dashboard  — folders and one tile per use case
 *   3. use case   — diagram (2/3) + narrative (1/3) + run footer
 *   4. reference  — rollup and shared vocabulary
 *
 * The use case screen is built once and reused for every use case. What differs
 * per use case is only the definition it loads, so a new use case needs no new
 * frontend code.
 */
(function () {
  const qs = (s, el) => (el || document).querySelector(s);
  const qsa = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (s) =>
    String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );

  const divisionLabels = {
    mf: 'Residential / Multifamily',
    retail: 'Office / Retail',
    construction: 'Construction',
  };

  // Step kinds: label + the colour the node and narrative section carry.
  const KINDS = {
    intake: 'Intake',
    analysis: 'Analysis',
    decision: 'Decision',
    human: 'Human',
    record: 'Record',
    note: 'Note',
  };

  const state = {
    division: 'mf',
    profile: null,
    summary: null,
    reference: null,
    workflow: null, // { id, detail, steps, editing, draft }
    running: false,
    runStatuses: {},
    attachment: null,
    openFolder: null,
    view: 'dashboard',
    changeLogFilter: '', // '' = every use case in the division
    permissions: null, // the role × permission matrix, as loaded
    settingsSection: 'profiles', // which Settings section is open
    settingsDivision: null, // which division's levels are being configured
    loginAccounts: null, // seeded accounts, fetched once for the login picker
  };

  const can = (permission) => !!(state.profile && state.profile.permissions.includes(permission));

  // ---------------- API ----------------

  async function api(path, options) {
    const res = await fetch(path, options);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch (err) {
        /* response was not JSON — keep the status text */
      }
      throw new Error(detail);
    }
    return res.status === 204 ? null : res.json();
  }

  const withDivision = (path) =>
    path + (path.includes('?') ? '&' : '?') + 'division=' + encodeURIComponent(state.division);

  const jsonBody = (body, method) => ({
    method: method || 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  // ---------------- Helpers ----------------

  const icon = (id, cls) => `<svg class="icon ${cls || ''}"><use href="#${id}"/></svg>`;

  function toast(message, type) {
    const wrap = qs('#toast-wrap');
    const el = document.createElement('div');
    el.className = 'toast ' + (type ? 'toast-' + type : '');
    el.textContent = message;
    wrap.appendChild(el);
    setTimeout(() => el.classList.add('show'), 10);
    setTimeout(() => {
      el.classList.remove('show');
      setTimeout(() => el.remove(), 300);
    }, 3800);
  }

  function formatWhen(iso) {
    if (!iso) return 'never';
    const then = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
    const seconds = Math.floor((Date.now() - then.getTime()) / 1000);
    if (seconds < 90) return 'just now';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' min ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' hr ago';
    const days = Math.floor(seconds / 86400);
    if (days === 1) return 'yesterday';
    if (days < 30) return days + ' days ago';
    return then.toLocaleDateString();
  }

  const initials = (name) =>
    (name || '?')
      .split(' ')
      .filter(Boolean)
      .slice(0, 2)
      .map((p) => p[0].toUpperCase())
      .join('');

  // ---------------- Theme ----------------

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('aat-theme', theme);
  }

  qs('#theme-toggle').addEventListener('click', () => {
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  });

  // ---------------- Session ----------------

  function saveSession() {
    localStorage.setItem(
      'aat-session',
      JSON.stringify({ division: state.division, email: state.profile && state.profile.email })
    );
  }

  async function resolveProfile(email, division) {
    const data = await api('/session/resolve', jsonBody({ email: email, division: division }));
    state.profile = data.profile;
    state.division = data.profile.division_key;
    return data.profile;
  }

  function renderIdentity() {
    const p = state.profile;
    if (!p) return;
    qs('.js-user-name').textContent = p.name;
    // A level is only meaningful with its division, since it does not reach past
    // one: "Super admin, Construction", not "Super admin".
    qs('.js-user-role').textContent = `${p.role_label} · ${divisionLabels[p.division_key] || ''}`;
    qs('.js-user-initials').textContent = initials(p.name);
    qs('.js-division-badge').textContent = divisionLabels[state.division] || p.division;
  }

  // ---------------- Login ----------------

  function selectDivision(key) {
    state.division = key;
    qsa('.division-option').forEach((btn) => {
      const active = btn.dataset.division === key;
      btn.classList.toggle('selected', active);
      btn.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    // The accounts below are the ones belonging to this division — each division
    // has its own super admin, so the list follows the picker.
    renderLoginAccounts();
  }

  qsa('.division-option').forEach((btn) =>
    btn.addEventListener('click', () => selectDivision(btn.dataset.division))
  );

  // The seeded accounts for the chosen division, most privileged first. Which
  // level you sign in as decides what you can do once inside.
  async function renderLoginAccounts() {
    if (!state.loginAccounts) {
      try {
        state.loginAccounts = (await api('/session/accounts')).accounts;
      } catch (err) {
        return; // The typed-username path still works without the quick pick.
      }
    }
    const mine = state.loginAccounts.filter((a) => a.division_key === state.division);
    const real = mine.filter((a) => !a.is_test);
    const test = mine.filter((a) => a.is_test);

    qs('.la-label').textContent = `Or sign in to ${divisionLabels[state.division] || ''} as`;
    qs('#login-accounts').innerHTML =
      real.map(loginAccountHtml).join('') +
      (test.length
        ? `<details class="la-test">
            <summary>Test accounts — one per level (${test.length})</summary>
            <div class="la-list">${test.map(loginAccountHtml).join('')}</div>
          </details>`
        : '');

    qsa('#login-accounts .la-item').forEach((btn) =>
      btn.addEventListener('click', () => {
        qs('#login-user').value = btn.dataset.email;
        qs('#login-form').requestSubmit();
      })
    );
  }

  function loginAccountHtml(a) {
    return `
      <button type="button" class="la-item" data-email="${esc(a.email)}" title="${esc(
      a.role_description
    )}">
        <span class="la-role">${esc(a.role_label)}<span class="la-div">${
      a.can_view_team ? 'oversees others' : 'own work only'
    }</span></span>
        <span class="la-email">${esc(a.name)} · ${esc(a.email)}</span>
        <span class="la-grant ${a.can_edit_workflow ? 'can' : 'cannot'}">${
      a.can_edit_workflow ? 'can edit workflows' : 'cannot edit workflows'
    }</span>
      </button>`;
  }

  qs('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const email = qs('#login-user').value.trim();
    const error = qs('#login-error');
    if (!email) {
      error.textContent = 'Enter a username to continue.';
      return;
    }
    error.textContent = '';
    const button = qs('#login-submit');
    button.disabled = true;
    button.textContent = 'Signing in…';
    try {
      await resolveProfile(email, state.division);
      saveSession();
      await enterApp();
    } catch (err) {
      error.textContent = err.message;
    } finally {
      button.disabled = false;
      button.textContent = 'Sign in';
    }
  });

  async function enterApp() {
    qs('#screen-login').classList.add('hidden');
    qs('#app').classList.remove('hidden');
    renderIdentity();
    switchView('dashboard');
    await refreshDashboard();
  }

  qs('#signout-btn').addEventListener('click', () => {
    localStorage.removeItem('aat-session');
    state.profile = null;
    qs('#app').classList.add('hidden');
    qs('#screen-login').classList.remove('hidden');
    qs('#login-pass').value = '';
    renderLoginAccounts();
  });

  // ---------------- User menu ----------------

  const closeUserMenu = () => {
    qs('#user-dropdown').classList.add('hidden');
    qs('#user-menu-btn').setAttribute('aria-expanded', 'false');
  };

  qs('#user-menu-btn').addEventListener('click', (event) => {
    event.stopPropagation();
    const menu = qs('#user-dropdown');
    const open = menu.classList.toggle('hidden');
    qs('#user-menu-btn').setAttribute('aria-expanded', open ? 'false' : 'true');
  });

  document.addEventListener('click', closeUserMenu);

  qsa('.ud-item[data-target]').forEach((item) =>
    item.addEventListener('click', () => {
      closeUserMenu();
      switchView(item.dataset.target);
    })
  );

  // ---------------- View switching ----------------

  function switchView(name) {
    state.view = name;
    qsa('.view').forEach((view) => view.classList.toggle('hidden', view.id !== 'view-' + name));
    qsa('.nav-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.target === name));
    window.scrollTo({ top: 0, behavior: 'smooth' });

    if (name === 'reference') loadReference();
    if (name === 'profile') renderProfile();
    if (name === 'settings') loadSettings();
  }

  qsa('.nav-tab').forEach((tab) => tab.addEventListener('click', () => switchView(tab.dataset.target)));
  qs('#brand-home').addEventListener('click', () => switchView('dashboard'));
  qs('#uc-back').addEventListener('click', () => switchView('dashboard'));

  // ---------------- 2. Division dashboard ----------------

  async function refreshDashboard() {
    try {
      state.summary = await api(withDivision('/dashboard/summary'));
    } catch (err) {
      toast('Could not load the dashboard: ' + err.message, 'error');
      return;
    }
    renderDashboard();
  }

  function renderDashboard() {
    const data = state.summary;
    if (!data) return;

    const hour = new Date().getHours();
    const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
    qs('#dashboard-greeting').textContent = `${greeting}, ${(state.profile.name || '').split(' ')[0]}`;
    qs('#dashboard-subtitle').textContent = `${divisionLabels[state.division]} — ${data.use_cases.length} use cases, ${data.documents_total} documents on file.`;

    qs('#stat-usecases').textContent = data.use_cases.length;
    qs('#stat-usecases-note').textContent = data.analysis_configured
      ? 'document grading is live'
      : 'no API key — grading disabled';
    qs('#stat-documents').textContent = data.documents_total;
    qs('#stat-leases').textContent = data.leases_expiring_soon;
    qs('#stat-pending').textContent = data.approvals.length;

    renderUseCaseTiles(data.use_cases);
    renderFolders(data.folders);
    renderApprovals(data.approvals);
  }

  // Every tile is the same shape whatever the use case does — only the logic
  // behind it differs. That is the point of the shared shell.
  function renderUseCaseTiles(useCases) {
    qs('#usecase-grid').innerHTML = useCases
      .map(
        (uc) => `
        <button class="usecase-tile" data-workflow="${esc(uc.id)}">
          <div class="ut-top">
            <span class="ut-folder">${icon('i-folder')} ${esc(uc.folder)}</span>
            ${uc.approvals ? `<span class="pill pill-warn">${uc.approvals} to review</span>` : ''}
          </div>
          <h3>${esc(uc.title)}</h3>
          <p>${esc(uc.purpose)}</p>
          <div class="ut-meta">
            <span>${uc.steps} steps</span>
            <span class="${uc.documents_present === uc.documents_total ? 'ok' : 'warn'}">
              ${uc.documents_present}/${uc.documents_total} documents
            </span>
            <span>${uc.records} runs logged</span>
          </div>
          <div class="ut-foot">
            <span>${uc.last_run ? 'Last run ' + formatWhen(uc.last_run) : 'Never run'}</span>
            <span class="ut-open">Open ${icon('i-arrow-right')}</span>
          </div>
        </button>`
      )
      .join('');

    qsa('.usecase-tile').forEach((tile) =>
      tile.addEventListener('click', () => openUseCase(tile.dataset.workflow))
    );
  }

  function renderFolders(folders) {
    qs('#folder-grid').innerHTML = folders
      .map(
        (folder) => `
        <button class="folder-tile ${state.openFolder === folder.name ? 'active' : ''}" data-folder="${esc(folder.name)}">
          ${icon('i-folder', 'ft-icon')}
          <span class="ft-name">${esc(folder.name)}</span>
          <span class="ft-count">${folder.count} ${folder.count === 1 ? 'document' : 'documents'}</span>
          <span class="ft-when">${folder.last_upload ? 'Updated ' + formatWhen(folder.last_upload) : 'Empty'}</span>
        </button>`
      )
      .join('');

    qsa('.folder-tile').forEach((tile) =>
      tile.addEventListener('click', () => openFolder(tile.dataset.folder))
    );
  }

  async function openFolder(name) {
    state.openFolder = name;
    qs('#folder-docs').classList.remove('hidden');
    qs('#fd-title').textContent = name;
    qs('#fd-body').innerHTML = '<div class="loading">Loading documents…</div>';
    qsa('.folder-tile').forEach((t) => t.classList.toggle('active', t.dataset.folder === name));
    await loadFolderDocuments();
  }

  async function loadFolderDocuments() {
    if (!state.openFolder) return;
    const term = qs('#folder-search').value.trim();
    try {
      const data = await api(
        withDivision('/repository/documents?folder=' + encodeURIComponent(state.openFolder)) +
          (term ? '&q=' + encodeURIComponent(term) : '')
      );
      qs('#fd-body').innerHTML = data.documents.length
        ? `<ul class="doclist">${data.documents
            .map(
              (doc) => `
            <li>
              ${icon('i-file')}
              <div class="dl-text">
                <a href="/repository/documents/${doc.id}/download">${esc(doc.filename)}</a>
                <span>${doc.redacted ? 'Redacted · ' : ''}Uploaded ${formatWhen(doc.uploaded_at)}</span>
              </div>
            </li>`
            )
            .join('')}</ul>`
        : `<p class="empty">Nothing in this folder yet${term ? ' matching “' + esc(term) + '”' : ''}.</p>`;
    } catch (err) {
      qs('#fd-body').innerHTML = `<p class="empty">Could not load documents: ${esc(err.message)}</p>`;
    }
  }

  let folderSearchTimer = null;
  qs('#folder-search').addEventListener('input', () => {
    clearTimeout(folderSearchTimer);
    folderSearchTimer = setTimeout(loadFolderDocuments, 250);
  });

  qs('#fd-close').addEventListener('click', () => {
    state.openFolder = null;
    qs('#folder-docs').classList.add('hidden');
    qsa('.folder-tile').forEach((t) => t.classList.remove('active'));
  });

  qs('#stat-pending-tile').addEventListener('click', () =>
    qs('#approvals-section').scrollIntoView({ behavior: 'smooth', block: 'start' })
  );

  // ---------------- Approvals ----------------

  function renderApprovals(approvals) {
    const container = qs('#approval-groups');
    const summary = qs('#approvals-summary');
    const catalog = (state.summary && state.summary.use_cases) || [];

    qs('#clear-samples').classList.toggle('hidden', !approvals.some((a) => a.source === 'sample'));

    if (!approvals.length) {
      container.innerHTML = '<p class="empty">Nothing is waiting on you. Runs that cannot clear on their own land here.</p>';
      summary.textContent = 'Queue is clear';
      return;
    }
    summary.textContent = `${approvals.length} case${approvals.length === 1 ? '' : 's'} across ${
      new Set(approvals.map((a) => a.workflow)).size
    } use case(s)`;

    const groups = {};
    approvals.forEach((ap) => (groups[ap.workflow] = groups[ap.workflow] || []).push(ap));

    container.innerHTML = Object.entries(groups)
      .map(([workflowId, cases]) => {
        const meta = catalog.find((c) => c.id === workflowId);
        return `
        <details class="approval-group" open>
          <summary>
            <span class="ag-title">${esc(meta ? meta.title : workflowId)}</span>
            <span class="ag-count">${cases.length}</span>
            <span class="ag-open" data-open-workflow="${esc(workflowId)}">Open use case ${icon('i-arrow-right')}</span>
          </summary>
          ${cases.map(approvalHtml).join('')}
        </details>`;
      })
      .join('');

    qsa('[data-open-workflow]').forEach((el) =>
      el.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        openUseCase(el.dataset.openWorkflow);
      })
    );
    bindApprovalActions(container);
  }

  function approvalHtml(ap) {
    const approver = can('approve_workflow');
    return `
      <div class="approval" data-approval="${ap.id}">
        <div class="ap-head">
          <span class="ap-ref">${esc(ap.reference)}</span>
          <span class="ap-subject">${esc(ap.subject)}</span>
          ${ap.source === 'sample' ? '<span class="pill pill-quiet">sample</span>' : ''}
          <span class="ap-when">${esc(ap.raised)}</span>
        </div>
        ${ap.property || ap.unit ? `<div class="ap-where">${esc([ap.property, ap.unit].filter(Boolean).join(' · '))}</div>` : ''}
        <p class="ap-reason">${esc(ap.reason)}</p>
        ${
          ap.missing.length
            ? `<ul class="ap-missing">${ap.missing.map((m) => `<li>${icon('i-x')} ${esc(m)}</li>`).join('')}</ul>`
            : ''
        }
        <div class="ap-actions">
          <button class="btn btn-primary btn-sm" data-resolve="approved" ${approver ? '' : 'disabled'}>Approve</button>
          <button class="btn btn-secondary btn-sm" data-resolve="returned" ${approver ? '' : 'disabled'}>Send back</button>
          ${approver ? '' : '<span class="ap-note">Your role cannot sign off.</span>'}
        </div>
      </div>`;
  }

  function bindApprovalActions(root) {
    qsa('[data-resolve]', root).forEach((btn) =>
      btn.addEventListener('click', async () => {
        const id = btn.closest('[data-approval]').dataset.approval;
        btn.disabled = true;
        try {
          await api(
            `/approvals/${id}/resolve`,
            jsonBody({ outcome: btn.dataset.resolve, resolved_by: state.profile.name })
          );
          toast(btn.dataset.resolve === 'approved' ? 'Approved and recorded.' : 'Sent back for correction.', 'ok');
          await refreshDashboard();
          if (state.workflow) await loadUseCase(state.workflow.id, { keepRun: true });
        } catch (err) {
          toast(err.message, 'error');
          btn.disabled = false;
        }
      })
    );
  }

  qs('#clear-samples').addEventListener('click', async () => {
    try {
      const result = await api(withDivision('/approvals/samples'), { method: 'DELETE' });
      toast(`Cleared ${result.removed} sample case(s).`, 'ok');
      await refreshDashboard();
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  // ---------------- 3. Use case detail ----------------

  async function openUseCase(workflowId) {
    switchView('usecase');
    await loadUseCase(workflowId);
  }

  async function loadUseCase(workflowId, opts) {
    const options = opts || {};
    if (!options.keepRun) {
      state.runStatuses = {};
      state.attachment = null;
      qs('#run-file').value = '';
      qs('#run-filename').textContent = 'Attach a PDF, image, or text file';
      qs('#run-log').innerHTML = '';
      qs('#run-outcome').classList.add('hidden');
      qs('#run-status-text').textContent = 'Not started.';
      qs('#run-progress-fill').style.width = '0%';
      setRunPill('idle', 'Idle');
    }

    let detail;
    try {
      detail = await api(withDivision('/workflows/' + encodeURIComponent(workflowId)));
    } catch (err) {
      toast('Could not open that use case: ' + err.message, 'error');
      return;
    }

    state.workflow = {
      id: workflowId,
      detail: detail,
      steps: detail.definition.steps,
      editing: false,
      draft: null,
    };
    renderUseCase();
  }

  function renderUseCase() {
    const wf = state.workflow;
    const def = wf.detail.definition;

    renderUseCaseBar();

    qs('#uc-title').textContent = def.title;
    qs('#uc-purpose').textContent = def.purpose;
    qs('#uc-folder-chip').innerHTML = `${icon('i-folder')} ${esc(def.folder)}`;
    qs('#uc-updated').textContent = def.is_default
      ? 'Shipped definition'
      : `Edited ${formatWhen(def.updated_at)}${def.updated_by ? ' by ' + def.updated_by : ''}`;

    const version = wf.detail.version || 1;
    qs('#uc-version').innerHTML = `${icon('i-book')} Version ${version} — history`;
    qs('#uc-version').title = 'See every version of this workflow, and roll back to one';

    renderDiagram();
    renderNarrative();
    renderUseCaseState();
    renderRunHint();
  }

  function renderUseCaseBar() {
    const cases = (state.summary && state.summary.use_cases) || [];
    qs('#uc-bar').innerHTML =
      cases
        .map(
          (uc) => `
        <button class="uc-bar-item ${uc.id === state.workflow.id ? 'active' : ''}" data-workflow="${esc(uc.id)}">
          ${esc(uc.title)}${uc.approvals ? `<span class="ucb-badge">${uc.approvals}</span>` : ''}
        </button>`
        )
        .join('') +
      `<button class="uc-bar-item uc-bar-ref" data-reference="1">${icon('i-book')} Reference</button>`;

    qsa('#uc-bar [data-workflow]').forEach((btn) =>
      btn.addEventListener('click', () => {
        if (btn.dataset.workflow !== state.workflow.id) loadUseCase(btn.dataset.workflow);
      })
    );
    qs('#uc-bar [data-reference]').addEventListener('click', () => switchView('reference'));
  }

  // ---- The diagram: one node per step, coloured by kind ----

  function diagramHtml(steps, isDraft) {
    return steps
      .map((step, index) => {
        // A draft step has no key yet — it has not been saved — so it carries no
        // run status either. Nothing to look up until it exists server-side.
        const status = (!isDraft && state.runStatuses[step.key]) || '';
        return `
        <div class="node kind-${esc(step.kind)} ${status}" data-key="${esc(step.key || 'draft-' + index)}" title="${esc(step.summary)}">
          <div class="node-top">
            <span class="node-index">${index + 1}</span>
            <span class="node-kind">${esc(KINDS[step.kind] || 'Step')}</span>
          </div>
          <div class="node-title">${esc((step.title || '').trim()) ||
            '<span class="node-untitled">Untitled step</span>'}</div>
          <div class="node-status"></div>
        </div>`;
      })
      .join('<div class="connector" aria-hidden="true"></div>');
  }

  function legendHtml(steps) {
    const used = [];
    steps.forEach((s) => {
      if (!used.includes(s.kind)) used.push(s.kind);
    });
    return used
      .map((kind) => `<span class="legend-item kind-${esc(kind)}">${esc(KINDS[kind] || kind)}</span>`)
      .join('');
  }

  // While the narrative is being edited the diagram draws the draft, not the
  // saved definition — the picture follows the words as they are typed rather
  // than waiting for a save.
  function renderDiagram() {
    const wf = state.workflow;
    const isDraft = !!(wf.editing && wf.draft);
    const steps = isDraft ? wf.draft : wf.steps;
    qs('#uc-diagram').innerHTML = diagramHtml(steps, isDraft);
    qs('#uc-legend').innerHTML = legendHtml(steps);
    qs('#uc-diagram').classList.toggle('is-draft', isDraft);
    qs('#diagram-draft').classList.toggle('hidden', !isDraft);
    if (!qs('#diagram-overlay').classList.contains('hidden')) {
      qs('#overlay-diagram').innerHTML = diagramHtml(steps, isDraft);
      qs('#overlay-legend').innerHTML = legendHtml(steps);
      qs('#overlay-diagram').classList.toggle('is-draft', isDraft);
    }
  }

  // The version chip is the route from "this workflow changed" to the log that
  // says how, filtered to the use case in hand.
  qs('#uc-version').addEventListener('click', () => {
    if (!state.workflow) return;
    state.changeLogFilter = state.workflow.id;
    switchView('reference');
  });

  qs('#diagram-expand').addEventListener('click', () => {
    if (!state.workflow) return;
    qs('#overlay-title').textContent = state.workflow.detail.definition.title + ' — workflow';
    qs('#diagram-overlay').classList.remove('hidden');
    document.body.classList.add('no-scroll');
    renderDiagram();
  });

  function closeOverlay() {
    qs('#diagram-overlay').classList.add('hidden');
    document.body.classList.remove('no-scroll');
  }

  qs('#diagram-close').addEventListener('click', closeOverlay);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !qs('#diagram-overlay').classList.contains('hidden')) closeOverlay();
  });

  // ---- The narrative: one section per node, editable in place ----

  function renderNarrative() {
    const wf = state.workflow;
    const editable = can('edit_workflow');

    // Shown to everyone, disabled for roles that cannot edit: hiding it makes
    // the capability look absent rather than withheld.
    const editBtn = qs('#narr-edit');
    editBtn.classList.toggle('hidden', wf.editing);
    editBtn.disabled = !editable;
    editBtn.title = editable
      ? 'Edit the steps — the diagram follows as you type'
      : 'Your role cannot edit workflow definitions';

    qs('#narr-save').classList.toggle('hidden', !wf.editing);
    qs('#narr-cancel').classList.toggle('hidden', !wf.editing);
    qs('#narr-reset').classList.toggle('hidden', !wf.editing);
    qs('#narr-add').classList.toggle('hidden', !wf.editing);
    qs('#narr-hint').textContent = wf.editing
      ? 'The diagram follows your edits as you type. Saving makes it the workflow: this walkthrough, the diagram, and what the run executes.'
      : editable
      ? 'Each section is a step in the diagram. Editing here rewrites the workflow itself.'
      : 'Each section is a step in the diagram. Your role cannot edit the definition.';

    qs('#uc-narrative').innerHTML = wf.editing
      ? wf.draft.map(narrativeEditHtml).join('')
      : wf.steps.map(narrativeViewHtml).join('');

    if (wf.editing) bindNarrativeEditors();
  }

  function narrativeViewHtml(step, index) {
    return `
      <article class="narr-step kind-${esc(step.kind)}" data-key="${esc(step.key)}">
        <header>
          <span class="narr-index">${index + 1}</span>
          <h3>${esc(step.title)}</h3>
          <span class="narr-kind">${esc(KINDS[step.kind] || 'Step')}</span>
        </header>
        ${step.summary ? `<p class="narr-summary">${esc(step.summary)}</p>` : ''}
        ${step.bullets.length ? `<ul>${step.bullets.map((b) => `<li>${esc(b)}</li>`).join('')}</ul>` : ''}
      </article>`;
  }

  function narrativeEditHtml(step, index) {
    return `
      <article class="narr-step narr-edit kind-${esc(step.kind)}" data-index="${index}">
        <header>
          <span class="narr-index">${index + 1}</span>
          <input class="ne-title" value="${esc(step.title)}" placeholder="Step name" aria-label="Step name" />
          <select class="ne-kind" aria-label="Step type">
            ${Object.entries(KINDS)
              .map(
                ([key, label]) =>
                  `<option value="${key}" ${key === step.kind ? 'selected' : ''}>${label}</option>`
              )
              .join('')}
          </select>
        </header>
        <input class="ne-summary" value="${esc(step.summary)}" placeholder="One line: what happens here" aria-label="Step summary" />
        <textarea class="ne-bullets" rows="4" placeholder="One bullet per line" aria-label="Step detail">${esc(
          step.bullets.join('\n')
        )}</textarea>
        <div class="ne-actions">
          <button class="btn btn-text btn-sm" data-move="-1" ${index === 0 ? 'disabled' : ''}>Move up</button>
          <button class="btn btn-text btn-sm" data-move="1">Move down</button>
          <button class="btn btn-text btn-sm ne-remove" data-remove="1">Remove</button>
        </div>
      </article>`;
  }

  function readDraftFromDom() {
    return qsa('#uc-narrative .narr-edit').map((el) => ({
      key: null,
      title: qs('.ne-title', el).value,
      kind: qs('.ne-kind', el).value,
      summary: qs('.ne-summary', el).value,
      bullets: qs('.ne-bullets', el)
        .value.split('\n')
        .map((b) => b.trim())
        .filter(Boolean),
    }));
  }

  // Push what is in the editor into the draft and redraw the diagram from it.
  // Only the diagram is redrawn — re-rendering the narrative mid-keystroke would
  // take the caret out of the field being typed in.
  function previewDraft() {
    state.workflow.draft = readDraftFromDom();
    renderDiagram();
  }

  function bindNarrativeEditors() {
    qsa('#uc-narrative .ne-title, #uc-narrative .ne-summary, #uc-narrative .ne-bullets').forEach((field) =>
      field.addEventListener('input', previewDraft)
    );

    // The colour strip follows the type as soon as it is changed, so the edit
    // panel shows the same coding the diagram just took on.
    qsa('#uc-narrative .ne-kind').forEach((select) =>
      select.addEventListener('change', () => {
        const article = select.closest('.narr-step');
        article.className = 'narr-step narr-edit kind-' + select.value;
        previewDraft();
      })
    );

    qsa('#uc-narrative [data-move]').forEach((btn) =>
      btn.addEventListener('click', () => {
        const index = Number(btn.closest('.narr-step').dataset.index);
        const to = index + Number(btn.dataset.move);
        const draft = readDraftFromDom();
        if (to < 0 || to >= draft.length) return;
        [draft[index], draft[to]] = [draft[to], draft[index]];
        state.workflow.draft = draft;
        renderEditor();
      })
    );

    qsa('#uc-narrative [data-remove]').forEach((btn) =>
      btn.addEventListener('click', () => {
        const index = Number(btn.closest('.narr-step').dataset.index);
        const draft = readDraftFromDom();
        if (draft.length === 1) return toast('A workflow needs at least one step.', 'error');
        draft.splice(index, 1);
        state.workflow.draft = draft;
        renderEditor();
      })
    );
  }

  // Structural changes — reorder, remove, add, enter or leave edit mode — move
  // both panels at once, so the two never describe different workflows.
  function renderEditor() {
    renderNarrative();
    renderDiagram();
  }

  qs('#narr-edit').addEventListener('click', () => {
    if (!can('edit_workflow')) return toast('Your role cannot edit workflow definitions.', 'error');
    state.workflow.editing = true;
    state.workflow.draft = state.workflow.steps.map((s) => ({
      key: s.key,
      title: s.title,
      kind: s.kind,
      summary: s.summary,
      bullets: s.bullets.slice(),
    }));
    renderEditor();
  });

  qs('#narr-cancel').addEventListener('click', () => {
    state.workflow.editing = false;
    state.workflow.draft = null;
    renderEditor();
  });

  qs('#narr-add').addEventListener('click', () => {
    const draft = readDraftFromDom();
    draft.push({ key: null, title: 'New step', kind: 'note', summary: '', bullets: [] });
    state.workflow.draft = draft;
    renderEditor();
    // Land in the step that was just added rather than making the user scroll.
    const added = qsa('#uc-narrative .narr-edit').pop();
    if (added) {
      added.scrollIntoView({ block: 'nearest' });
      qs('.ne-title', added).select();
    }
  });

  qs('#narr-save').addEventListener('click', async () => {
    const steps = readDraftFromDom();
    if (steps.some((s) => !s.title.trim())) return toast('Every step needs a name.', 'error');

    const button = qs('#narr-save');
    button.disabled = true;
    try {
      const definition = await api(
        `/workflows/${encodeURIComponent(state.workflow.id)}/definition`,
        jsonBody({ division: state.division, steps: steps, updated_by: state.profile.name }, 'PUT')
      );
      applyDefinition(definition);
      toast('Workflow updated — diagram, walkthrough and run are in step.', 'ok');
      refreshDashboard();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      button.disabled = false;
    }
  });

  qs('#narr-reset').addEventListener('click', async () => {
    try {
      const definition = await api(
        `/workflows/${encodeURIComponent(state.workflow.id)}/definition/reset`,
        jsonBody({ division: state.division, updated_by: state.profile.name })
      );
      applyDefinition(definition);
      toast('Restored the shipped definition.', 'ok');
      refreshDashboard();
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  function applyDefinition(definition) {
    state.workflow.detail.definition = definition;
    state.workflow.steps = definition.steps;
    state.workflow.editing = false;
    state.workflow.draft = null;
    state.runStatuses = {};
    renderUseCase();
  }

  // ---- What this use case has on file ----

  function renderUseCaseState() {
    const detail = state.workflow.detail;
    const docs = detail.required_documents;

    qs('#uc-doc-tally').textContent = `${docs.present}/${docs.total} on file`;
    qs('#uc-docs').innerHTML = docs.items
      .map(
        (item) => `
        <li class="${item.present ? 'present' : 'absent'}">
          ${icon(item.present ? 'i-check' : 'i-x')}
          <div class="dl-text">
            <span>${esc(item.name)}</span>
            <span>${
              item.present
                ? esc(item.matched_document.filename) + ' · ' + formatWhen(item.matched_document.uploaded_at)
                : 'Not in ' + esc(item.folder)
            }</span>
          </div>
        </li>`
      )
      .join('');

    qs('#uc-ap-count').textContent = detail.approvals.length
      ? `${detail.approvals.length} open`
      : 'none open';
    qs('#uc-approvals').innerHTML = detail.approvals.length
      ? detail.approvals.map(approvalHtml).join('')
      : '<p class="empty">Nothing outstanding for this use case.</p>';
    bindApprovalActions(qs('#uc-approvals'));

    const records = detail.records;
    qs('#uc-records').innerHTML = `
      <div class="rec-summary">
        <div><strong>${records.rows_logged}</strong><span>rows logged</span></div>
        <div><strong>${formatWhen(records.last_updated)}</strong><span>last updated</span></div>
        <div><strong>${esc(records.last_updated_by || '—')}</strong><span>by</span></div>
      </div>
      ${
        detail.record_rows.length
          ? `<ul class="rec-rows">${detail.record_rows
              .slice(0, 5)
              .map(
                (row) => `
              <li>
                <span class="rec-outcome rec-${esc(row.outcome)}">${esc(row.outcome.replace(/_/g, ' '))}</span>
                <span class="rec-subject">${esc(row.subject || row.property_id || '—')}</span>
                <span class="rec-when">${formatWhen(row.recorded_at)}</span>
              </li>`
              )
              .join('')}</ul>`
          : '<p class="empty">No runs recorded yet.</p>'
      }`;

    qs('#uc-record-files').innerHTML = detail.record_files
      .map(
        (file) => `
        <li>
          ${icon('i-download')}
          <a href="${esc(file.url)}">${esc(file.name)}</a>
          <span>${esc(file.label)}${file.rows != null ? ' · ' + file.rows + ' rows' : ''}</span>
        </li>`
      )
      .join('');
  }

  // ---------------- Execution ----------------

  function setRunPill(mode, label) {
    const pill = qs('#run-pill');
    pill.className = 'pill pill-' + mode;
    pill.textContent = label;
  }

  function renderRunHint() {
    const detail = state.workflow.detail;
    const parts = [];
    if (!can('run_workflow')) parts.push('Your role cannot start a run.');
    if (!detail.analysis_configured) {
      parts.push('ANTHROPIC_API_KEY is not set, so an attached document cannot be graded.');
    } else {
      parts.push(`Attach a document to grade it against this use case's rubric with ${detail.model}.`);
    }
    parts.push('Runs without an attachment report on what is already on file.');
    qs('#run-hint').textContent = parts.join(' ');
    qs('#start-process').disabled = !can('run_workflow');
  }

  qs('#run-filepick').addEventListener('click', () => qs('#run-file').click());
  qs('#run-file').addEventListener('change', (event) => {
    const file = event.target.files[0];
    state.attachment = file || null;
    qs('#run-filename').textContent = file ? file.name : 'Attach a PDF, image, or text file';
  });

  function logLine(text, tone) {
    const log = qs('#run-log');
    const line = document.createElement('div');
    line.className = 'log-line ' + (tone || '');
    line.innerHTML = text;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  }

  function markStep(key, status) {
    state.runStatuses[key] = status;
    qsa(`.node[data-key="${CSS.escape(key)}"]`).forEach((node) => {
      node.classList.remove('running', 'done', 'blocked');
      if (status) node.classList.add(status);
    });
  }

  qs('#start-process').addEventListener('click', startRun);

  async function startRun() {
    if (state.running || !state.workflow) return;
    if (!can('run_workflow')) return toast('Your role cannot start a run.', 'error');
    // A run executes the saved definition. Starting one while the diagram is
    // showing unsaved edits would report progress against steps that do not
    // exist yet.
    if (state.workflow.editing)
      return toast('Save or cancel your edits first — the diagram is showing unsaved changes.', 'error');

    const steps = state.workflow.steps;
    state.running = true;
    state.runStatuses = {};
    renderDiagram();
    qs('#run-log').innerHTML = '';
    qs('#run-outcome').classList.add('hidden');
    qs('#run-progress-fill').style.width = '0%';
    setRunPill('running', 'Running');
    qs('#start-process').disabled = true;

    const form = new FormData();
    form.append('division', state.division);
    form.append('property_id', qs('#run-property').value.trim());
    form.append('unit', qs('#run-unit').value.trim());
    form.append('actor', state.profile.name);
    if (state.attachment) form.append('upload_file', state.attachment);

    let completed = 0;
    try {
      const res = await fetch(`/workflows/${encodeURIComponent(state.workflow.id)}/run`, {
        method: 'POST',
        body: form,
      });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          detail = (await res.json()).detail || detail;
        } catch (err) {
          /* not JSON */
        }
        throw new Error(detail);
      }

      // Newline-delimited JSON: one event per state change, read as it arrives
      // so the status bar moves while the run is still going.
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (!line) continue;
          const event = JSON.parse(line);
          if (event.type === 'step' && event.status !== 'running') completed += 1;
          handleRunEvent(event, completed, steps.length);
        }
      }
    } catch (err) {
      setRunPill('error', 'Failed');
      qs('#run-status-text').textContent = 'Run failed: ' + err.message;
      logLine(esc(err.message), 'bad');
      toast(err.message, 'error');
    } finally {
      state.running = false;
      qs('#start-process').disabled = !can('run_workflow');
    }
  }

  function handleRunEvent(event, completed, total) {
    if (event.type === 'step' && event.status === 'running') {
      markStep(event.key, 'running');
      qs('#run-status-text').textContent = `${event.title}…`;
      return;
    }

    if (event.type === 'step') {
      markStep(event.key, event.status === 'blocked' ? 'blocked' : 'done');
      qs('#run-progress-fill').style.width = Math.round((completed / total) * 100) + '%';
      qs('#run-status-text').textContent = `${event.title} — ${event.detail}`;
      logLine(
        `<strong>${esc(event.title)}</strong> ${esc(event.detail)}` +
          (event.facts && event.facts.length
            ? `<ul>${event.facts.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>`
            : ''),
        event.status === 'blocked' ? 'warn' : 'ok'
      );
      return;
    }

    if (event.type === 'outcome') {
      renderOutcome(event);
      return;
    }

    if (event.type === 'error') {
      setRunPill('error', 'Failed');
      qs('#run-status-text').textContent = event.message;
      logLine(esc(event.message), 'bad');
    }
  }

  function renderOutcome(outcome) {
    const cleared = outcome.status === 'cleared';
    setRunPill(cleared ? 'ok' : 'warn', cleared ? 'Cleared' : 'Needs review');
    qs('#run-status-text').textContent = outcome.headline;
    qs('#run-progress-fill').style.width = '100%';

    const verdict = outcome.verdict;
    qs('#run-outcome').innerHTML = `
      <div class="outcome-head ${cleared ? 'ok' : 'warn'}">
        ${icon(cleared ? 'i-check' : 'i-x')}
        <h3>${esc(outcome.headline)}</h3>
      </div>
      ${
        outcome.blockers.length
          ? `<div class="outcome-block">
              <h4>What is holding it up</h4>
              <ul>${outcome.blockers.map((b) => `<li>${esc(b)}</li>`).join('')}</ul>
            </div>`
          : ''
      }
      ${
        verdict
          ? `<div class="outcome-block">
              <h4>Document verdict — ${esc(verdict.decision.replace(/_/g, ' '))} (${esc(verdict.confidence)} confidence)</h4>
              <p>${esc(verdict.summary)}</p>
              <ul class="findings">${verdict.findings
                .map(
                  (f) => `
                  <li class="finding f-${esc(f.status)}">
                    <span class="f-status">${esc(f.status.replace('_', ' '))}</span>
                    <span class="f-req">${esc(f.requirement)}</span>
                    ${f.evidence ? `<span class="f-evidence">“${esc(f.evidence)}”</span>` : ''}
                  </li>`
                )
                .join('')}</ul>
              ${
                verdict.extracted_fields.length
                  ? `<div class="fields">${verdict.extracted_fields
                      .map((f) => `<span><em>${esc(f.label)}</em> ${esc(f.value)}</span>`)
                      .join('')}</div>`
                  : ''
              }
            </div>`
          : ''
      }
      ${outcome.analysis_error ? `<p class="outcome-error">${esc(outcome.analysis_error)}</p>` : ''}`;
    qs('#run-outcome').classList.remove('hidden');

    // The run wrote rows — reload so approvals, records and the tiles agree.
    refreshDashboard();
    loadUseCase(state.workflow.id, { keepRun: true });
  }

  // ---------------- 4. Reference ----------------

  async function loadReference() {
    const rollup = qs('#ref-rollup');
    rollup.innerHTML = '<div class="loading">Loading reference…</div>';
    try {
      state.reference = await api(withDivision('/reference'));
    } catch (err) {
      rollup.innerHTML = `<p class="empty">Could not load the reference: ${esc(err.message)}</p>`;
      return;
    }
    renderReference();
  }

  function renderReference() {
    const data = state.reference;

    qs('#ref-rollup').innerHTML = data.use_cases
      .map(
        (uc) => `
        <section class="ref-card">
          <div class="ref-card-head">
            <div>
              <h3>${esc(uc.title)}</h3>
              <p>${esc(uc.purpose)}</p>
            </div>
            <div class="ref-card-actions">
              <button class="btn btn-secondary btn-sm" data-open-ref="${esc(uc.id)}">Open</button>
              <a class="btn btn-text btn-sm" href="${esc(uc.record_file)}">${icon('i-download')} Records</a>
            </div>
          </div>
          <div class="ref-meta">
            <span>${icon('i-folder')} ${esc(uc.folder)}</span>
            <span>${uc.steps.length} steps</span>
            <span>${uc.records.rows_logged} runs logged</span>
            <span class="${uc.approvals ? 'warn' : ''}">${uc.approvals} awaiting review</span>
          </div>
          <ol class="ref-steps">
            ${uc.steps
              .map(
                (step) => `
              <li class="kind-${esc(step.kind)}">
                <strong>${esc(step.title)}</strong>
                <span>${esc(step.summary)}</span>
              </li>`
              )
              .join('')}
          </ol>
          ${
            uc.rubric.length
              ? `<details class="ref-rubric">
                  <summary>Rubric — what a document is graded against (${uc.rubric.length})</summary>
                  <ul>${uc.rubric.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
                </details>`
              : ''
          }
        </section>`
      )
      .join('');

    qsa('[data-open-ref]').forEach((btn) =>
      btn.addEventListener('click', () => openUseCase(btn.dataset.openRef))
    );

    renderChangeLog();

    qs('#ref-kinds').innerHTML = Object.entries(data.step_kinds)
      .map(
        ([key, description]) => `
        <div class="kind-card kind-${esc(key)}">
          <h4>${esc(KINDS[key] || key)}</h4>
          <p>${esc(description)}</p>
        </div>`
      )
      .join('');

    qs('#ref-glossary').innerHTML = data.glossary
      .map(
        (entry) => `
        <div class="glossary-entry">
          <dt>${esc(entry.term)}</dt>
          <dd>${esc(entry.definition)}</dd>
        </div>`
      )
      .join('');
  }

  // ---- Change log: every version a definition has had, and the way back ----

  function renderChangeLog() {
    const data = state.reference;
    const log = data.change_log || [];
    const filter = qs('#changelog-filter');

    // Keep the filter's options in step with what the log actually contains.
    const seen = [];
    log.forEach((entry) => {
      if (!seen.some((s) => s.id === entry.workflow_id))
        seen.push({ id: entry.workflow_id, title: entry.workflow_title });
    });
    const chosen = state.changeLogFilter || '';
    filter.innerHTML =
      '<option value="">All use cases</option>' +
      seen
        .map(
          (s) =>
            `<option value="${esc(s.id)}" ${s.id === chosen ? 'selected' : ''}>${esc(s.title)}</option>`
        )
        .join('');

    const shown = chosen ? log.filter((e) => e.workflow_id === chosen) : log;
    const canRestore = can('edit_workflow');

    qs('#ref-changelog').innerHTML = shown.length
      ? shown.map((entry) => changeLogRowHtml(entry, canRestore)).join('')
      : `<p class="empty">No definition changes recorded yet. Every save, reset and rollback
          from here on is logged, with the version to return to.</p>`;

    qsa('#ref-changelog [data-restore]').forEach((btn) =>
      btn.addEventListener('click', () => restoreRevision(btn.dataset.restore, Number(btn.dataset.version)))
    );
  }

  function changeLogRowHtml(entry, canRestore) {
    const sourcePill =
      { seed: 'pill-quiet', edit: 'pill-running', reset: 'pill-warn', restore: 'pill-warn' }[
        entry.source
      ] || 'pill-quiet';
    return `
      <article class="cl-row ${entry.is_current ? 'current' : ''}">
        <div class="cl-main">
          <div class="cl-top">
            <span class="cl-version">v${entry.version}</span>
            <strong>${esc(entry.workflow_title)}</strong>
            <span class="pill ${sourcePill}">${esc(entry.source_label)}</span>
            ${entry.is_current ? '<span class="pill pill-ok">Live now</span>' : ''}
            ${
              entry.restored_from
                ? `<span class="chip chip-quiet">back to v${entry.restored_from}</span>`
                : ''
            }
          </div>
          <p class="cl-note">${esc(entry.note)}</p>
          <p class="cl-who">
            ${entry.step_count} step${entry.step_count === 1 ? '' : 's'} ·
            ${esc(entry.created_by || 'unattributed')} · ${formatWhen(entry.created_at)}
          </p>
          <details class="cl-steps">
            <summary>The ${entry.step_count} step${entry.step_count === 1 ? '' : 's'} in this version</summary>
            <ol>
              ${entry.steps
                .map(
                  (s) =>
                    `<li class="kind-${esc(s.kind)}"><strong>${esc(s.title)}</strong>
                      <span>${esc(KINDS[s.kind] || s.kind)}</span></li>`
                )
                .join('')}
            </ol>
          </details>
        </div>
        ${
          entry.is_current
            ? ''
            : `<button class="btn btn-secondary btn-sm cl-restore" data-restore="${esc(
                entry.workflow_id
              )}" data-version="${entry.version}" ${canRestore ? '' : 'disabled'} title="${
                canRestore
                  ? 'Make this version the live definition again'
                  : 'Your role cannot edit workflow definitions'
              }">${icon('i-arrow-left')} Roll back to v${entry.version}</button>`
        }
      </article>`;
  }

  async function restoreRevision(workflowId, version) {
    if (!can('edit_workflow')) return toast('Your role cannot edit workflow definitions.', 'error');
    const entry = (state.reference.change_log || []).find(
      (e) => e.workflow_id === workflowId && e.version === version
    );
    const title = entry ? entry.workflow_title : workflowId;
    if (
      !window.confirm(
        `Roll ${title} back to version ${version}?\n\nThe current version stays in the log, ` +
          'so this can be undone. Runs after this use the restored steps.'
      )
    )
      return;

    try {
      const definition = await api(
        `/workflows/${encodeURIComponent(workflowId)}/revisions/${version}/restore`,
        jsonBody({ division: state.division, updated_by: state.profile.name })
      );
      toast(`${title} is back to version ${version}.`, 'ok');
      // The open use case, the dashboard tiles and the log itself all read the
      // definition that just changed.
      if (state.workflow && state.workflow.id === workflowId) applyDefinition(definition);
      await Promise.all([loadReference(), refreshDashboard()]);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  qs('#changelog-filter').addEventListener('change', (event) => {
    state.changeLogFilter = event.target.value;
    renderChangeLog();
  });

  // ---------------- Profile ----------------

  function renderProfile() {
    const p = state.profile;
    if (!p) return;
    qs('#profile-body').innerHTML = `
      <div class="profile-grid">
        <section class="panel">
          <div class="profile-id">
            <span class="avatar large">${esc(initials(p.name))}</span>
            <div>
              <h2>${esc(p.name)}</h2>
              <p>${esc(p.email)}</p>
              <span class="chip">${esc(p.role_label)}</span>
              <span class="chip chip-quiet">${esc(p.division)}</span>
            </div>
          </div>
          <p class="profile-desc">${esc(p.role_description)}</p>
          <div class="meter">
            <div class="meter-bar"><span style="width:${(p.permission_count / p.permission_total) * 100}%"></span></div>
            <span>${p.permission_count} of ${p.permission_total} permissions</span>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head"><h2>Permissions</h2></div>
          <ul class="perm-list">
            ${p.permission_matrix
              .map(
                (perm) => `
              <li class="${perm.granted ? 'granted' : 'denied'}">
                ${icon(perm.granted ? 'i-check' : 'i-x')} ${esc(perm.label)}
              </li>`
              )
              .join('')}
          </ul>
        </section>

        <section class="panel">
          <div class="panel-head"><h2>Folders you can reach</h2></div>
          <ul class="perm-list">
            ${p.allowed_folders.map((f) => `<li class="granted">${icon('i-folder')} ${esc(f)}</li>`).join('')}
          </ul>
        </section>
      </div>`;
  }

  // ---------------- Settings: profiles and role permissions ----------------

  qsa('.settings-tab').forEach((tab) =>
    tab.addEventListener('click', () => showSettingsSection(tab.dataset.section))
  );

  function showSettingsSection(name) {
    state.settingsSection = name;
    qsa('.settings-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.section === name));
    qsa('.settings-section').forEach((section) =>
      section.classList.toggle('hidden', section.id !== 'settings-' + name)
    );
  }

  async function loadSettings() {
    showSettingsSection(state.settingsSection || 'profiles');
    // Settings administers one division at a time, because a level only means
    // something inside one: your own division unless you look at another.
    const div = state.settingsDivision || state.division;
    state.settingsDivision = div;

    const roster = qs('#settings-roster');
    roster.innerHTML = '<div class="loading">Loading profiles…</div>';
    try {
      const [users, permissions] = await Promise.all([
        api('/admin/users?division=' + encodeURIComponent(div)),
        api('/permissions?division=' + encodeURIComponent(div)),
      ]);
      state.permissions = permissions;
      renderSettingsDivisions(permissions.divisions || []);
      renderCreateProfile(users);
      renderRoster(users);
      renderPermissions();
    } catch (err) {
      roster.innerHTML = `<p class="empty">Could not load settings: ${esc(err.message)}</p>`;
    }
  }

  // Which division is being administered. Shown as a switcher because the answer
  // changes both lists below it: its people, and what its levels may do.
  function renderSettingsDivisions(divisions) {
    const host = qs('#settings-divisions');
    const mine = state.profile ? state.profile.division_key : '';
    host.innerHTML = divisions
      .map(
        (d) => `
        <button type="button" class="sd-tab ${d.key === state.settingsDivision ? 'active' : ''}"
          data-division="${esc(d.key)}">
          ${esc(d.label)}${d.key === mine ? '<span class="sd-mine">yours</span>' : ''}
        </button>`
      )
      .join('');

    qsa('#settings-divisions .sd-tab').forEach((tab) =>
      tab.addEventListener('click', () => {
        state.settingsDivision = tab.dataset.division;
        loadSettings();
      })
    );
  }

  // ---- Create a profile: the account, its division, and the role it holds ----

  function renderCreateProfile(data) {
    const canManage = state.profile.can_manage_users;
    const roleOptions = data.roles
      .map(
        (role) =>
          `<option value="${esc(role.key)}" ${role.key === 'general' ? 'selected' : ''}>${esc(
            role.label
          )}</option>`
      )
      .join('');

    qs('#settings-create').innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <div class="panel-head-title">
            <h2>Create a profile</h2>
            ${canManage ? '' : '<span class="pill pill-warn">Needs “Manage users”</span>'}
          </div>
        </div>
        <p class="panel-hint">
          The level decides what the account may do <em>in this division</em> — set what that means
          under <button type="button" class="link-btn" data-goto-permissions="1">Role
          permissions</button>.
          ${
            canManage
              ? 'Any password works at sign-in; the username sets the access level.'
              : 'Your level cannot create accounts. Grant it <strong>Manage users</strong> under Role permissions, then come back.'
          }
        </p>
        <form class="create-form" id="create-profile-form">
          <div class="field">
            <label for="cp-name">Full name</label>
            <input id="cp-name" placeholder="Jordan Blake" ${canManage ? '' : 'disabled'} />
          </div>
          <div class="field">
            <label for="cp-email">Email</label>
            <input id="cp-email" type="email" placeholder="jordan@aat.com" ${canManage ? '' : 'disabled'} />
          </div>
          <div class="field">
            <label for="cp-division">Division</label>
            <select id="cp-division" ${canManage ? '' : 'disabled'}>
              ${(data.divisions || [])
                .map(
                  (d) =>
                    `<option value="${esc(d.key)}" ${
                      d.key === state.settingsDivision ? 'selected' : ''
                    }>${esc(d.label)}</option>`
                )
                .join('')}
            </select>
          </div>
          <div class="field">
            <label for="cp-role">Level</label>
            <select id="cp-role" ${canManage ? '' : 'disabled'}>${roleOptions}</select>
          </div>
          <div class="field field-wide">
            <label for="cp-password">Password <span class="opt">(optional)</span></label>
            <input id="cp-password" type="password" placeholder="Defaults to “prototype”" ${
              canManage ? '' : 'disabled'
            } />
          </div>
          <button type="submit" class="btn btn-primary" ${canManage ? '' : 'disabled'}>
            ${icon('i-plus')} Create profile
          </button>
        </form>
        <div class="cp-grants" id="cp-grants"></div>
      </section>`;

    const showGrants = () => {
      const role = data.roles.find((r) => r.key === qs('#cp-role').value);
      if (!role) return;
      qs('#cp-grants').innerHTML = `
        <p class="cp-grants-head">${esc(role.label)} — ${esc(role.description)}</p>
        <ul class="perm-list inline">
          ${data.permissions
            .map((perm) => {
              const granted = role.permissions.includes(perm.key);
              return `<li class="${granted ? 'granted' : 'denied'}">${icon(
                granted ? 'i-check' : 'i-x'
              )} ${esc(perm.label)}</li>`;
            })
            .join('')}
        </ul>`;
    };
    showGrants();
    qs('#cp-role').addEventListener('change', showGrants);
    qs('#settings-create [data-goto-permissions]').addEventListener('click', () =>
      showSettingsSection('permissions')
    );
    qs('#create-profile-form').addEventListener('submit', createProfile);
  }

  async function createProfile(event) {
    event.preventDefault();
    const email = qs('#cp-email').value.trim();
    if (!email) return toast('An email address is required.', 'error');

    try {
      const created = await api(
        '/admin/users',
        jsonBody({
          name: qs('#cp-name').value.trim(),
          email: email,
          division: qs('#cp-division').value,
          role: qs('#cp-role').value,
          password: qs('#cp-password').value,
          acting_user_id: state.profile.id,
        })
      );
      toast(`${created.user.name} added as ${created.user.role_label}.`, 'ok');
      await loadSettings();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // ---- The roster ----

  function renderRoster(data) {
    const canManage = state.profile.can_manage_users;
    qs('#settings-roster').innerHTML = `
      ${
        canManage
          ? ''
          : '<p class="notice">Your role cannot change accounts, so the roster below is read-only.</p>'
      }
      <section class="panel">
        <div class="panel-head"><h2>Profiles</h2><span class="ws-count">${data.users.length}</span></div>
        <div class="table-scroll">
          <table class="data-table">
            <thead><tr><th>Name</th><th>Email</th><th>Division</th><th>Role</th><th>Active</th></tr></thead>
            <tbody>
              ${data.users
                .map(
                  (user) => `
                <tr data-user="${user.id}">
                  <td>${esc(user.name)}</td>
                  <td class="muted">${esc(user.email)}</td>
                  <td>${esc(user.division)}</td>
                  <td>
                    <select data-field="role" ${canManage ? '' : 'disabled'}>
                      ${data.roles
                        .map(
                          (role) =>
                            `<option value="${esc(role.key)}" ${role.key === user.role ? 'selected' : ''}>${esc(
                              role.label
                            )}</option>`
                        )
                        .join('')}
                    </select>
                  </td>
                  <td>
                    <input type="checkbox" data-field="is_active" ${user.is_active ? 'checked' : ''} ${
                    canManage ? '' : 'disabled'
                  } aria-label="Active" />
                  </td>
                </tr>`
                )
                .join('')}
            </tbody>
          </table>
        </div>
      </section>`;

    // What each role grants is not repeated here — that is the Role permissions
    // section, where it is editable rather than merely listed.
    qsa('#settings-roster [data-field]').forEach((input) =>
      input.addEventListener('change', () => {
        const userId = input.closest('[data-user]').dataset.user;
        const field = input.dataset.field;
        patchUser(userId, { [field]: field === 'is_active' ? input.checked : input.value });
      })
    );
  }

  async function patchUser(userId, changes) {
    try {
      await api(
        `/admin/users/${userId}`,
        jsonBody(Object.assign({ acting_user_id: state.profile.id }, changes), 'PATCH')
      );
      toast('Account updated.', 'ok');
      // The acting account may have changed its own reach, and the roster's
      // role menus feed the create form's grant preview.
      if (String(state.profile.id) === String(userId)) await resolveProfile(state.profile.email, state.division);
      renderIdentity();
      loadSettings();
    } catch (err) {
      toast(err.message, 'error');
      loadSettings();
    }
  }

  // ---- Role permissions: what each role may do ----

  function renderPermissions() {
    const data = state.permissions;
    // "You" only means something in your own division: the same level elsewhere
    // is somebody else.
    const mine =
      state.profile && state.profile.division_key === data.division_key ? state.profile.role : '';
    const changed = data.roles.filter((r) => !r.is_default).length;

    qs('#permissions-body').innerHTML = `
      <p class="notice notice-info">
        Prototype build — any signed-in account can change these, so you are never locked out of
        your own system. In production this section belongs behind
        <strong>Assign roles and permissions</strong>.
      </p>

      <section class="panel">
        <div class="panel-head">
          <div class="panel-head-title">
            <h2>${esc(data.division_label || '')} — what each level may do</h2>
            ${
              changed
                ? `<span class="pill pill-warn">${changed} level${
                    changed === 1 ? '' : 's'
                  } changed from default</span>`
                : '<span class="pill pill-quiet">All levels at shipped defaults</span>'
            }
          </div>
          <div class="panel-head-actions">
            <button class="btn btn-primary btn-sm" id="perm-save" disabled>Save changes</button>
            <button class="btn btn-text btn-sm" id="perm-defaults">Restore defaults</button>
          </div>
        </div>
        <p class="panel-hint">
          Tick a box to grant that level the capability <em>in this division only</em> — Construction
          can run its levels differently from Residential. Your own level is highlighted where it is
          your division: granting it <strong>Edit workflow definitions</strong> is what un-greys the
          Edit control on a use case, <strong>Manage users</strong> is what lets you create profiles,
          and <strong>See everyone's activity</strong> is the difference between overseeing a team and
          seeing only your own runs.
        </p>
        <div class="table-scroll">
          <table class="data-table perm-matrix">
            <thead>
              <tr>
                <th class="pm-role">Level</th>
                ${data.permissions.map((p) => `<th><span>${esc(p.label)}</span></th>`).join('')}
              </tr>
            </thead>
            <tbody>
              ${data.roles.map((role) => permissionRowHtml(role, data.permissions, mine)).join('')}
            </tbody>
          </table>
        </div>
      </section>`;

    qsa('#permissions-body .pm-box').forEach((box) =>
      box.addEventListener('change', () => {
        // Enable Save only once something actually differs from what is stored.
        qs('#perm-save').disabled = !collectPermissionEdits().length;
      })
    );
    qs('#perm-save').addEventListener('click', savePermissions);
    qs('#perm-defaults').addEventListener('click', restoreDefaultPermissions);
  }

  function permissionRowHtml(role, permissions, mine) {
    return `
      <tr data-role="${esc(role.key)}" class="${role.key === mine ? 'pm-mine' : ''}">
        <td class="pm-role">
          <strong>${esc(role.label)}</strong>
          ${role.key === mine ? '<span class="chip">You</span>' : ''}
          ${role.is_default ? '' : '<span class="chip chip-quiet">changed</span>'}
          <span class="pm-desc">${esc(role.description)}</span>
        </td>
        ${permissions
          .map(
            (perm) => `
          <td class="pm-cell">
            <input type="checkbox" class="pm-box" data-permission="${esc(perm.key)}"
              ${role.granted.includes(perm.key) ? 'checked' : ''}
              aria-label="${esc(role.label)} — ${esc(perm.label)}" />
          </td>`
          )
          .join('')}
      </tr>`;
  }

  // Roles whose ticked boxes no longer match what the server has stored.
  function collectPermissionEdits() {
    const stored = {};
    state.permissions.roles.forEach((r) => (stored[r.key] = r.granted.slice().sort()));
    return qsa('#permissions-body tr[data-role]')
      .map((row) => ({
        role: row.dataset.role,
        permissions: qsa('.pm-box', row)
          .filter((box) => box.checked)
          .map((box) => box.dataset.permission),
      }))
      .filter((edit) => edit.permissions.slice().sort().join(',') !== (stored[edit.role] || []).join(','));
  }

  async function savePermissions() {
    const edits = collectPermissionEdits();
    if (!edits.length) return;
    const button = qs('#perm-save');
    button.disabled = true;
    try {
      for (const edit of edits) {
        state.permissions = await api(
          `/permissions/${encodeURIComponent(edit.role)}`,
          jsonBody(
            {
              division: state.settingsDivision,
              permissions: edit.permissions,
              updated_by: state.profile.name,
            },
            'PUT'
          )
        );
      }
      await afterPermissionChange(
        `Updated ${edits.length} level${edits.length === 1 ? '' : 's'} in ${
          divisionLabels[state.settingsDivision] || 'this division'
        }.`
      );
    } catch (err) {
      toast(err.message, 'error');
      loadSettings();
    }
  }

  async function restoreDefaultPermissions() {
    try {
      state.permissions = await api(
        '/permissions/reset',
        jsonBody({ division: state.settingsDivision, updated_by: state.profile.name })
      );
      await afterPermissionChange(
        `${divisionLabels[state.settingsDivision] || 'This division'} is back to its shipped permissions.`
      );
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // A permission change can change what the signed-in account may do, so the
  // session has to be re-resolved: otherwise the page would say one thing and
  // every button on every other screen would still be gated on the old answer.
  async function afterPermissionChange(message) {
    if (state.profile) await resolveProfile(state.profile.email, state.division);
    renderIdentity();
    // Re-render the whole page: what a role grants drives the create form's
    // preview and whether the roster is editable, not only the matrix.
    await loadSettings();
    if (state.workflow) renderNarrative();
    toast(message, 'ok');
  }

  // ---------------- Boot ----------------

  (async function boot() {
    applyTheme(
      localStorage.getItem('aat-theme') ||
        (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    );

    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem('aat-session') || 'null');
    } catch (err) {
      saved = null;
    }

    selectDivision((saved && saved.division) || 'mf');
    if (saved && saved.email) {
      try {
        await resolveProfile(saved.email, saved.division || 'mf');
        await enterApp();
        return;
      } catch (err) {
        localStorage.removeItem('aat-session');
      }
    }
    renderLoginAccounts();
  })();
})();
