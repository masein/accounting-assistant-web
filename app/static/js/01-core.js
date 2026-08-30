    const API = ''; // same origin

    // Register the zoom plugin with Chart.js once both have loaded.
    try {
      if (typeof Chart !== 'undefined' && typeof window !== 'undefined' && window['chartjs-plugin-zoom']) {
        Chart.register(window['chartjs-plugin-zoom']);
      } else if (typeof Chart !== 'undefined' && typeof ChartZoom !== 'undefined') {
        Chart.register(ChartZoom);
      }
    } catch (_) { /* fall through — charts still render without zoom */ }

    // ─── Display calendar (Gregorian / Jalali) ────────────────────────
    // Stored in AppSetting; loaded once on session start. All dates are
    // persisted as Gregorian (per ISO-8601). Conversion happens only at
    // render time via formatDisplayDate().
    window.__DISPLAY_CALENDAR = 'gregorian';

    // Khayyam algorithm — Gregorian → Jalali. Returns {jy, jm, jd}.
    function gregorianToJalali(gy, gm, gd) {
      const g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
      let jy = gy <= 1600 ? 0 : 979;
      gy -= gy <= 1600 ? 621 : 1600;
      const gy2 = gm > 2 ? gy + 1 : gy;
      let days = (365 * gy) + Math.floor((gy2 + 3) / 4) - Math.floor((gy2 + 99) / 100)
                + Math.floor((gy2 + 399) / 400) - 80 + gd + g_d_m[gm - 1];
      jy += 33 * Math.floor(days / 12053);
      days %= 12053;
      jy += 4 * Math.floor(days / 1461);
      days %= 1461;
      if (days > 365) {
        jy += Math.floor((days - 1) / 365);
        days = (days - 1) % 365;
      }
      const jm = days < 186 ? 1 + Math.floor(days / 31) : 7 + Math.floor((days - 186) / 30);
      const jd = 1 + (days < 186 ? days % 31 : (days - 186) % 30);
      return { jy, jm, jd };
    }

    // Format a date string (ISO YYYY-MM-DD or any Date-parseable form) for
    // display, using the active calendar setting. Returns the original
    // string when it can't be parsed.
    function formatDisplayDate(dateStr) {
      if (dateStr == null || dateStr === '') return '';
      const s = String(dateStr).trim();
      // Already in Persian-digit form? Just return as-is.
      if (/[۰-۹]/.test(s)) return s;
      const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
      let y, mo, d;
      if (m) {
        y = parseInt(m[1], 10); mo = parseInt(m[2], 10); d = parseInt(m[3], 10);
      } else {
        const dt = new Date(s);
        if (isNaN(dt.getTime())) return s;
        y = dt.getFullYear(); mo = dt.getMonth() + 1; d = dt.getDate();
      }
      if ((window.__DISPLAY_CALENDAR || 'gregorian') === 'jalali') {
        const { jy, jm, jd } = gregorianToJalali(y, mo, d);
        return `${jy}/${String(jm).padStart(2,'0')}/${String(jd).padStart(2,'0')}`;
      }
      return `${y}-${String(mo).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    }

    async function loadDisplayCalendar() {
      try {
        const r = await fetch(API + '/admin/display-calendar');
        if (!r.ok) return;
        const data = await r.json();
        window.__DISPLAY_CALENDAR = data.calendar || 'gregorian';
        const sel = document.getElementById('display-calendar-select');
        if (sel) sel.value = window.__DISPLAY_CALENDAR;
      } catch (_) {}
    }

    // Standard zoom-plugin options applied to line / time-series charts.
    function zoomPluginOptions() {
      return {
        zoom: {
          drag: { enabled: true, backgroundColor: 'rgba(15,118,110,0.12)' },
          wheel: { enabled: true, modifierKey: 'shift' },
          pinch: { enabled: true },
          mode: 'x',
        },
        pan: { enabled: true, mode: 'x', modifierKey: 'alt' },
        limits: { x: { min: 'original', max: 'original' } },
      };
    }

    // --- CSRF helper: read the aa_csrf cookie and inject it on every mutating request ---
    function getCsrfToken() {
      const m = document.cookie.match(/(?:^|;\s*)aa_csrf=([^;]+)/);
      return m ? decodeURIComponent(m[1]) : '';
    }
    const _origFetch = window.fetch;
    window.fetch = function(url, opts) {
      opts = opts || {};
      const method = (opts.method || 'GET').toUpperCase();
      if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
        opts.headers = opts.headers || {};
        if (opts.headers instanceof Headers) {
          if (!opts.headers.has('X-CSRF-Token')) opts.headers.set('X-CSRF-Token', getCsrfToken());
        } else {
          if (!opts.headers['X-CSRF-Token']) opts.headers['X-CSRF-Token'] = getCsrfToken();
        }
      }
      return _origFetch.call(this, url, opts);
    };
    const alertEl = document.getElementById('alert');
    const topNav = document.getElementById('top-nav');
    const form = document.getElementById('transaction-form');
    const linesTbody = document.getElementById('lines-tbody');
    const addLineBtn = document.getElementById('add-line');
    const submitBtn = document.getElementById('submit-btn');
    const resultsTbody = document.getElementById('results-tbody');
    const resultsFoot = document.getElementById('results-foot');
    const chatMessagesEl = document.getElementById('chat-messages');
    const chatInput = document.getElementById('chat-input');
    const chatSendBtn = document.getElementById('chat-send');
    const attachmentInput = document.getElementById('attachment-input');
    const attachmentUploadBtn = document.getElementById('attachment-upload-btn');
    const attachmentGrid = document.getElementById('attachment-grid');
    const openAiChatInlineBtn = document.getElementById('open-ai-chat-inline');
    const voucherChatInlineEl = document.getElementById('voucher-chat-inline');
    const invoicesTbody = document.getElementById('invoices-tbody');
    const recurringTbody = document.getElementById('recurring-tbody');
    const budgetWrap = document.getElementById('budget-wrap');
    const aiProviderSelect = document.getElementById('ai-provider-select');
    const aiModelInput = document.getElementById('ai-model-input');
    const aiBaseInput = document.getElementById('ai-base-input');
    const aiKeyInput = document.getElementById('ai-key-input');
    const aiSaveBtn = document.getElementById('ai-save-btn');
    const settingsUserNameEl = document.getElementById('settings-user-name');
    const settingsUserRoleEl = document.getElementById('settings-user-role');
    const settingsSignedPrefixEl = document.getElementById('settings-signed-prefix');
    const logoutBtn = document.getElementById('logout-btn');
    const uiLanguageLabelEl = document.getElementById('ui-language-label');
    const uiLanguageSelectEl = document.getElementById('ui-language-select');
    const saveLanguageBtn = document.getElementById('save-language-btn');
    const newUserUsernameEl = document.getElementById('new-user-username');
    const newUserPasswordEl = document.getElementById('new-user-password');
    const newUserRoleEl = document.getElementById('new-user-role');
    const newUserEntityEl = document.getElementById('new-user-entity');
    // RBAC role helpers (order = privilege, high → low).
    const ROLE_ORDER = ['owner', 'cfo', 'accountant', 'manager', 'employee', 'viewer', 'personal'];
    const ROLE_KEYS = { owner: 'roleOwner', cfo: 'roleCfo', accountant: 'roleAccountant', manager: 'roleManager', employee: 'roleEmployee', viewer: 'roleViewer', personal: 'rolePersonal' };
    function roleLabel(r) { return t(ROLE_KEYS[r] || 'roleEmployee'); }
    let currentRole = 'owner';
    // Which roles may SEE each page (nav + client-side gate). The server still
    // enforces — this is cosmetic. Keep in sync with app/core/permissions.py.
    const PAGE_ROLES = {
      dashboard: ['owner', 'cfo', 'accountant', 'viewer'],
      'personal-dashboard': ['personal'],
      'ai-accountant': ['owner', 'cfo', 'accountant', 'personal'],
      transactions: ['owner', 'cfo', 'accountant', 'personal'],
      invoices: ['owner', 'cfo', 'accountant'],
      time: ['owner', 'cfo', 'accountant', 'employee'],
      expenses: ['owner', 'cfo', 'accountant', 'manager', 'employee'],
      'purchase-orders': ['owner', 'cfo', 'accountant'],
      recurring: ['owner', 'cfo', 'accountant', 'personal'],
      entities: ['owner', 'cfo', 'accountant'],
      products: ['owner', 'cfo', 'accountant'],
      inventory: ['owner', 'cfo', 'accountant'],
      payroll: ['owner', 'cfo', 'accountant'],
      equity: ['owner', 'cfo', 'accountant'],
      'bank-statements': ['owner', 'cfo', 'accountant', 'personal'],
      ledger: ['owner', 'cfo', 'accountant', 'viewer'],
      manager: ['owner', 'cfo', 'accountant', 'viewer'],
      cfo: ['owner', 'cfo'],
      ceo: ['owner', 'cfo'],
      audit: ['owner', 'cfo', 'accountant'],
      settings: ['owner'],
      users: ['owner'],
      migration: ['owner', 'accountant'],
      'petty-cash': ['owner', 'cfo', 'accountant', 'manager', 'employee'],
      // companies is gated separately by isSuperadmin.
    };
    // Where each role lands after login.
    const ROLE_HOME = { owner: 'dashboard', cfo: 'dashboard', accountant: 'dashboard', manager: 'expenses', employee: 'time', viewer: 'dashboard', personal: 'ai-accountant' };
    function roleHome() { return ROLE_HOME[currentRole] || 'dashboard'; }
    function canSeePage(page) {
      if (page === 'companies') return isSuperadmin;
      if (isSuperadmin) return true;              // platform admin sees everything
      const roles = PAGE_ROLES[page];
      return !roles || roles.includes(currentRole);
    }
    // Hide nav buttons (and their empty sections) the current role can't use.
    function applyRoleAccess() {
      document.querySelectorAll('.nav-btn[data-page]').forEach((btn) => {
        const page = btn.getAttribute('data-page');
        if (page === 'companies') return;         // handled by isSuperadmin logic
        btn.style.display = canSeePage(page) ? '' : 'none';
      });
      document.querySelectorAll('.side-nav .nav-section').forEach((sec) => {
        const anyVisible = Array.from(sec.querySelectorAll('.nav-btn')).some((b) => b.style.display !== 'none');
        sec.style.display = anyVisible ? '' : 'none';
      });
      // AI chat quick actions: personal users get money-diary prompts, not
      // balance-sheet/P&L ones.
      const personalMode = currentRole === 'personal';
      document.querySelectorAll('#ai-acct-quick-actions .chip-business')
        .forEach((c) => { c.style.display = personalMode ? 'none' : ''; });
      document.querySelectorAll('#ai-acct-quick-actions .chip-personal')
        .forEach((c) => { c.style.display = personalMode ? '' : 'none'; });
      // Reconciling a statement against existing bookkeeping is an SME job. A
      // personal tenant has nothing to reconcile against — every row is new
      // spending to categorise and post — so these would only confuse.
      document.querySelectorAll('.sme-only')
        .forEach((el) => { el.style.display = personalMode ? 'none' : ''; });
    }
    async function populateEntityLinkOptions() {
      if (!newUserEntityEl) return;
      try {
        const res = await fetch(API + '/entities?type=employee');
        const rows = res.ok ? await res.json().catch(() => []) : [];
        const opts = ['<option value="">' + escapeHtml(t('usersNoLink')) + '</option>']
          .concat((rows || []).map((e) => `<option value="${escapeHtml(e.id)}">${escapeHtml(e.name)}</option>`));
        newUserEntityEl.innerHTML = opts.join('');
      } catch (_) { /* leave the none option */ }
    }

    // --- Daily cash digest settings (Owner) ---
    async function loadDigestSettings() {
      try {
        const res = await fetch(API + '/notifications/digest-settings');
        if (!res.ok) return;
        const s = await res.json().catch(() => ({}));
        const en = document.getElementById('digest-enabled');
        const th = document.getElementById('digest-threshold');
        const rw = document.getElementById('digest-runway');
        const ch = document.getElementById('digest-channel');
        if (en) en.checked = !!s.enabled;
        if (th) th.value = (s.cash_threshold != null ? s.cash_threshold : 0);
        if (rw) rw.value = (s.runway_months != null ? s.runway_months : 3);
        if (ch && s.channel) ch.value = s.channel;
      } catch (_) { /* ignore */ }
    }

    async function saveDigestSettings() {
      const btn = document.getElementById('digest-save-btn');
      const body = {
        enabled: document.getElementById('digest-enabled').checked,
        cash_threshold: parseInt(document.getElementById('digest-threshold').value || '0', 10),
        runway_months: parseFloat(document.getElementById('digest-runway').value || '3'),
        channel: document.getElementById('digest-channel').value,
      };
      try {
        if (btn) btn.disabled = true;
        const res = await fetch(API + '/notifications/digest-settings', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || t('digestSaveError'), true); return; }
        showAlert(t('digestSaved'));
        loadDigestSettings();
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      } finally { if (btn) btn.disabled = false; }
    }

    async function previewDigest() {
      const out = document.getElementById('digest-preview');
      try {
        // deliver=false → build + return the body without sending.
        const res = await fetch(API + '/notifications/daily-digest?deliver=false', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || t('digestPreviewError'), true); return; }
        if (out) { out.textContent = data.body || ''; out.style.display = 'block'; }
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      }
    }

    // --- Company API keys (Owner) ---
    async function loadApiKeys() {
      const wrap = document.getElementById('apikeys-wrap');
      if (!wrap) return;
      try {
        const res = await fetch(API + '/admin/api-keys');
        if (!res.ok) return;
        const keys = await res.json().catch(() => []);
        if (!keys.length) {
          wrap.innerHTML = '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('apiKeysNone')) + '</p>';
          return;
        }
        wrap.innerHTML = `
          <table class="results-table" style="font-size:0.85rem;">
            <thead><tr>
              <th>${escapeHtml(t('apiKeysLabel'))}</th><th>${escapeHtml(t('apiKeysPrefix'))}</th>
              <th>${escapeHtml(t('apiKeysCreated'))}</th><th>${escapeHtml(t('apiKeysLastUsed'))}</th>
              <th>${escapeHtml(t('usersStatus'))}</th><th></th>
            </tr></thead>
            <tbody>${keys.map(k => `
              <tr>
                <td>${escapeHtml(k.label)}</td>
                <td><code>${escapeHtml(k.prefix)}…</code></td>
                <td>${k.created_at ? escapeHtml(k.created_at.slice(0, 10)) : '—'}</td>
                <td>${k.last_used_at ? escapeHtml(k.last_used_at.slice(0, 10)) : '—'}</td>
                <td>${k.revoked ? escapeHtml(t('apiKeysRevoked')) : escapeHtml(t('usersActive'))}</td>
                <td>${k.revoked ? '' : `<button type="button" class="btn btn-danger btn-sm apikey-revoke-btn" data-id="${escapeHtml(k.id)}" data-label="${escapeHtml(k.label)}">${escapeHtml(t('apiKeysRevoke'))}</button>`}</td>
              </tr>`).join('')}
            </tbody>
          </table>`;
      } catch (_) { /* ignore */ }
    }

    async function createApiKey() {
      const btn = document.getElementById('apikey-create-btn');
      const label = (document.getElementById('apikey-label').value || 'integration').trim();
      try {
        if (btn) btn.disabled = true;
        const res = await fetch(API + '/admin/api-keys', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ label }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || t('apiKeysCreateError'), true); return; }
        const reveal = document.getElementById('apikey-reveal');
        const val = document.getElementById('apikey-reveal-value');
        if (val) val.textContent = data.api_key || '';
        if (reveal) reveal.style.display = '';
        document.getElementById('apikey-label').value = '';
        loadApiKeys();
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      } finally { if (btn) btn.disabled = false; }
    }

    document.addEventListener('click', async (ev) => {
      const copy = ev.target.closest && ev.target.closest('#apikey-copy-btn');
      if (copy) {
        const val = document.getElementById('apikey-reveal-value');
        try { await navigator.clipboard.writeText(val ? val.textContent : ''); showAlert(t('apiKeysCopied')); }
        catch (_) { /* clipboard unavailable */ }
        return;
      }
      const rev = ev.target.closest && ev.target.closest('.apikey-revoke-btn');
      if (!rev) return;
      if (!(await uiConfirm({ message: tf('apiKeysConfirmRevoke', { name: rev.dataset.label || '' }), confirmLabel: t('apiKeysRevoke'), danger: true }))) return;
      try {
        const res = await fetch(API + '/admin/api-keys/' + encodeURIComponent(rev.dataset.id), { method: 'DELETE' });
        if (!res.ok && res.status !== 204) {
          const data = await res.json().catch(() => ({}));
          showAlert(data.detail || 'Failed to revoke key.', true);
          return;
        }
        showAlert(t('apiKeysRevokedMsg'));
        loadApiKeys();
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    });
    const createUserBtn = document.getElementById('create-user-btn');
    const usersWrapEl = document.getElementById('users-wrap');
    const ledgerSearchEl = document.getElementById('ledger-search');
    const ledgerSortEl = document.getElementById('ledger-sort');
    const ledgerTopNEl = document.getElementById('ledger-topn');
    const ledgerNonZeroEl = document.getElementById('ledger-nonzero');
    const ledgerKpisEl = document.getElementById('ledger-kpis');
    const mgrReportTypeEl = document.getElementById('mgr-report-type');
    const mgrFromDateEl = document.getElementById('mgr-from-date');
    const mgrToDateEl = document.getElementById('mgr-to-date');
    const mgrAccountCodeEl = document.getElementById('mgr-account-code');
    const mgrRunBtn = document.getElementById('mgr-run-btn');
    const mgrReportJsonEl = document.getElementById('mgr-report-json');
    const mgrReportPreviewEl = document.getElementById('mgr-report-preview');
    const mgrReportChartPanelEl = document.getElementById('mgr-report-chart-panel');
    const mgrReportChartEl = document.getElementById('mgr-report-chart');
    const mgrReportChartTitleEl = document.getElementById('mgr-report-chart-title');
    const mgrExportJsonBtn = document.getElementById('mgr-export-json-btn');
    const mgrExportCsvBtn = document.getElementById('mgr-export-csv-btn');
    const mgrExportPdfBtn = document.getElementById('mgr-export-pdf-btn');
    const mgrFromLabelEl = document.getElementById('mgr-from-label');
    const mgrToLabelEl = document.getElementById('mgr-to-label');
    const mgrAddItemBtn = document.getElementById('mgr-add-item-btn');
    const mgrInvItemNameEl = document.getElementById('mgr-inv-item-name');
    const mgrInvItemSkuEl = document.getElementById('mgr-inv-item-sku');
    const mgrInvItemUnitEl = document.getElementById('mgr-inv-item-unit');
    const mgrMvItemEl = document.getElementById('mgr-mv-item');
    const mgrMvTypeEl = document.getElementById('mgr-mv-type');
    const mgrMvQtyEl = document.getElementById('mgr-mv-qty');
    const mgrMvCostEl = document.getElementById('mgr-mv-cost');
    const mgrAddMvBtn = document.getElementById('mgr-add-mv-btn');
    const invFromDateEl = document.getElementById('inv-from-date');
    const invToDateEl = document.getElementById('inv-to-date');
    const invRunBalanceBtn = document.getElementById('inv-run-balance-btn');
    const invRunMovementBtn = document.getElementById('inv-run-movement-btn');
    const invExportJsonBtn = document.getElementById('inv-export-json-btn');
    const invExportCsvBtn = document.getElementById('inv-export-csv-btn');
    const invExportPdfBtn = document.getElementById('inv-export-pdf-btn');
    const invReportPreviewEl = document.getElementById('inv-report-preview');
    const invReportJsonEl = document.getElementById('inv-report-json');
    const invReportChartPanelEl = document.getElementById('inv-report-chart-panel');
    const invReportChartEl = document.getElementById('inv-report-chart');
    const invReportChartTitleEl = document.getElementById('inv-report-chart-title');

    let chatHistory = [];
    let lastEntityMentions = null;
    let entityOptions = { client: [], bank: [], payee: [], supplier: [] };  // payee uses type "employee"
    let selectedAttachments = [];
    let entityTransactionsCache = [];
    let currentEntityContext = null;
    let managerReportChart = null;
    let lastManagerReport = null;
    let inventoryReportChart = null;
    let lastInventoryReport = null;
    const validPages = new Set(['dashboard', 'personal-dashboard', 'ai-accountant', 'transactions', 'entities', 'invoices', 'recurring', 'ledger', 'manager', 'inventory', 'products', 'payroll', 'equity', 'purchase-orders', 'expenses', 'time', 'settings', 'bank-statements', 'audit', 'cfo', 'ceo', 'companies', 'migration', 'petty-cash']);
    // The Companies console is super-admin only; gated in showPage().
    let isSuperadmin = false;
    const rawFetch = window.fetch.bind(window);

    window.fetch = async (...args) => {
      const res = await rawFetch(...args);
      if (res.status === 401) {
        window.location.href = '/login';
        throw new Error('Authentication required');
      }
      return res;
    };
