    const API = ''; // same origin

    // ─── CSP-safe event wiring ────────────────────────────────────────
    // The Content-Security-Policy forbids inline event-handler attributes, so
    // markup — static or built in template strings — names an action instead:
    //   <button data-action="export-table" data-target="prod-content" …>
    // and one delegated listener runs it. Actions are registered with
    // registerAction(name, fn(el, event)) by the chunk that owns them; the
    // generic ones (show/hide/toggle, overlay close) live here.
    const UI_ACTIONS = Object.create(null);
    function registerAction(name, fn) { UI_ACTIONS[name] = fn; }
    function _byIdOrClosest(el, ref) {
      if (!ref) return null;
      return document.getElementById(ref) || el.closest('#' + ref);
    }
    registerAction('hide', (el) => {
      String(el.dataset.target || '').split(/\s+/).filter(Boolean).forEach((id) => {
        const t = _byIdOrClosest(el, id); if (t) t.style.display = 'none';
      });
      String(el.dataset.show || '').split(/\s+/).filter(Boolean).forEach((id) => {
        const t = document.getElementById(id); if (t) t.style.display = 'block';
      });
    });
    registerAction('toggle', (el) => {
      const t = _byIdOrClosest(el, el.dataset.target);
      if (t) t.style.display = t.style.display === 'none' ? 'block' : 'none';
    });
    document.addEventListener('click', (e) => {
      // A dimmed backdrop that closes when clicked outside its card.
      if (e.target instanceof Element && e.target.hasAttribute('data-overlay-close')) {
        e.target.style.display = 'none';
        return;
      }
      const el = e.target instanceof Element ? e.target.closest('[data-action]') : null;
      if (!el || el.disabled || el.getAttribute('aria-disabled') === 'true') return;
      const fn = UI_ACTIONS[el.dataset.action];
      if (typeof fn === 'function') fn(el, e);
    });
    document.addEventListener('change', (e) => {
      const el = e.target instanceof Element ? e.target.closest('[data-change-action]') : null;
      const fn = el && UI_ACTIONS[el.dataset.changeAction];
      if (typeof fn === 'function') fn(el, e);
    });
    // Web fonts load without blocking render (media="print" until now).
    document.querySelectorAll('link[data-async-css]').forEach((l) => { l.media = 'all'; });

    // Register the zoom plugin with Chart.js once both have loaded.
    try {
      if (typeof Chart !== 'undefined' && typeof window !== 'undefined' && window['chartjs-plugin-zoom']) {
        Chart.register(window['chartjs-plugin-zoom']);
      } else if (typeof Chart !== 'undefined' && typeof ChartZoom !== 'undefined') {
        Chart.register(ChartZoom);
      }
    } catch (_) { /* fall through — charts still render without zoom */ }

    // A chart with nothing to show says so: a new company's CEO Mode and
    // dashboard drew blank boxes that looked broken (2026-10-07). Every
    // dataset empty or all zero → «No data yet» in the middle of the plot.
    // chart.$empty tells the browser suite what was drawn.
    try {
      if (typeof Chart !== 'undefined') {
        Chart.register({
          id: 'aaEmptyState',
          afterDraw(chart) {
            const num = (v) => Number(v && typeof v === 'object' ? (v.y ?? v.r ?? v.x) : v);
            chart.$empty = !(chart.data.datasets || []).some((s) => (s.data || []).some((v) => {
              const n = num(v);
              return Number.isFinite(n) && n !== 0;
            }));
            if (!chart.$empty) return;
            const { ctx, width, height } = chart;
            const a = chart.chartArea || { left: 0, right: width, top: 0, bottom: height };
            const root = getComputedStyle(document.documentElement);
            ctx.save();
            ctx.direction = root.direction === 'rtl' ? 'rtl' : 'ltr';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = root.getPropertyValue('--text-muted').trim() || '#64748b';
            ctx.font = '500 14px ' + getComputedStyle(document.body).fontFamily;
            ctx.fillText(t('noDataYet'), (a.left + a.right) / 2, (a.top + a.bottom) / 2);
            ctx.restore();
          },
        });
      }
    } catch (_) { /* charts still render without it */ }

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

    // A range of dates, in the company's calendar: "1405/07/01 → 1405/07/30" as one
    // left-to-right run, so after Persian or Arabic words it neither reverses its
    // dates nor points its arrow back at the start.
    function formatDateRange(from, to) {
      const a = formatDisplayDate(from), b = to ? formatDisplayDate(to) : '…';
      return '\u2066' + a + ' → ' + b + '\u2069';
    }

    // A period key from a report: "1405-07" (Jalali, year < 1700) → "مهر ۱۴۰۵" /
    // "Mehr 1405"; "2026-09" → "Sep 2026"; "1405-Q1" → "بهار ۱۴۰۵" (§3.5).
    const _J_MONTHS = { fa: ['فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند'],
      ar: ['فروردين','أرديبهشت','خرداد','تير','مرداد','شهريور','مهر','آبان','آذر','دي','بهمن','إسفند'],
      en: ['Farvardin','Ordibehesht','Khordad','Tir','Mordad','Shahrivar','Mehr','Aban','Azar','Dey','Bahman','Esfand'] };
    const _SEASONS = { fa: ['بهار','تابستان','پاییز','زمستان'], ar: ['الربيع','الصيف','الخريف','الشتاء'],
      es: ['Primavera','Verano','Otoño','Invierno'], en: ['Spring','Summer','Autumn','Winter'] };
    function formatPeriodKey(key) {
      const s = String(key || '');
      const ui = (typeof currentLanguage !== 'undefined') ? currentLanguage : 'en';
      const months = _J_MONTHS[ui] || _J_MONTHS.en;
      const seasons = _SEASONS[ui] || _SEASONS.en;
      const digits = (x) => ui === 'fa' ? String(x).replace(/[0-9]/g, d => '۰۱۲۳۴۵۶۷۸۹'[d]) : String(x);
      let m = /^(\d{4})-(\d{2})$/.exec(s);
      if (m) {
        const y = +m[1], mo = +m[2];
        if (y < 1700) return months[mo - 1] + ' ' + digits(y);
        // a Gregorian month in the UI's language (Latin digits for Arabic, like the figures)
        const locale = { fa: 'fa-IR-u-ca-gregory', es: 'es-ES', ar: 'ar-u-nu-latn' }[ui] || 'en-GB';
        try { return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString(locale, { month: 'short', year: 'numeric', timeZone: 'UTC' }); }
        catch (_) { return s; }
      }
      m = /^(\d{4})-Q([1-4])$/.exec(s);
      if (m && +m[1] < 1700) return seasons[+m[2] - 1] + ' ' + digits(m[1]);
      m = /^(\d{4})-(Spring|Summer|Autumn|Winter)$/.exec(s);   // a Gregorian (meteorological) season
      if (m) return seasons[_SEASONS.en.indexOf(m[2])] + ' ' + digits(m[1]);
      m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);             // a Jalali week, by its Saturday
      if (m && +m[1] < 1700) return digits(+m[3]) + ' ' + months[+m[2] - 1] + ' ' + digits(m[1]);
      return s;
    }

    // This month's key in the display calendar ("1405-07" or "2026-09").
    function currentMonthKey(d) {
      const dt = d || new Date();
      if ((window.__DISPLAY_CALENDAR || 'gregorian') === 'jalali') {
        const { jy, jm } = gregorianToJalali(dt.getFullYear(), dt.getMonth() + 1, dt.getDate());
        return jy + '-' + String(jm).padStart(2, '0');
      }
      return dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0');
    }

    // A date as YYYY-MM-DD in local time (toISOString is UTC: a day early in Tehran before 03:30).
    function localIsoDate(d) {
      const dt = d || new Date();
      return dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0');
    }

    // The first day of this month in the display calendar, as a Gregorian ISO date.
    function monthStartIso(d) {
      const dt = d || new Date();
      if ((window.__DISPLAY_CALENDAR || 'gregorian') === 'jalali') {
        const { jd } = gregorianToJalali(dt.getFullYear(), dt.getMonth() + 1, dt.getDate());
        return localIsoDate(new Date(dt.getFullYear(), dt.getMonth(), dt.getDate() - (jd - 1)));
      }
      return localIsoDate(new Date(dt.getFullYear(), dt.getMonth(), 1));
    }

    // Month pickers (budgets) in the display calendar: an Iranian company
    // budgets by Jalali month (§3.5). The <input type=month> stays — it holds
    // the key, "1405-07" being a valid value — and a Jalali month list beside it
    // sets it, so code reading .value and its change listeners are unchanged.
    const CALENDAR_MONTH_INPUTS = ['budget-month', 'pd-budget-month'];
    function applyCalendarMonthPickers() {
      const jalali = (window.__DISPLAY_CALENDAR || 'gregorian') === 'jalali';
      CALENDAR_MONTH_INPUTS.forEach((id) => {
        const input = document.getElementById(id);
        if (!input) return;
        let sel = document.getElementById(id + '-jalali');
        if (!jalali) {
          if (sel) sel.remove();
          input.hidden = false;
          if (/^1[0-6]\d\d-/.test(input.value)) input.value = currentMonthKey();
          return;
        }
        if (!/^1[0-6]\d\d-\d\d$/.test(input.value)) input.value = currentMonthKey();
        if (!sel) {
          sel = document.createElement('select');
          sel.id = id + '-jalali';
          const label = document.querySelector('label[for="' + id + '"]');
          if (label) label.setAttribute('for', sel.id);
          input.insertAdjacentElement('afterend', sel);
          sel.addEventListener('change', () => {
            input.value = sel.value;
            input.dispatchEvent(new Event('change', { bubbles: true }));
          });
        }
        const [cy, cm] = currentMonthKey().split('-').map(Number);
        const keys = [];
        for (let i = -12; i <= 12; i++) {
          const idx = cy * 12 + (cm - 1) + i;
          keys.push(Math.floor(idx / 12) + '-' + String(idx % 12 + 1).padStart(2, '0'));
        }
        sel.innerHTML = keys.map(k => '<option value="' + k + '">' + escapeHtml(formatPeriodKey(k)) + '</option>').join('');
        sel.value = keys.includes(input.value) ? input.value : currentMonthKey();
        input.hidden = true;
      });
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
      applyCalendarMonthPickers();
      if (typeof dressDateInputs === 'function') dressDateInputs();   // date fields in the calendar too
      // report ranges still on their default start from this month of the calendar
      ['mgr-from-date', 'inv-from-date'].forEach((id) => {
        const el = document.getElementById(id);
        if (el && el.dataset.auto && el.value === el.dataset.auto) el.value = el.dataset.auto = monthStartIso();
      });
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
      // the page's language, so an error message comes back in it (app/core/messages.py)
      const lang = (typeof currentLanguage !== 'undefined' && currentLanguage) || 'en';
      if (opts.headers instanceof Headers) { if (!opts.headers.has('X-UI-Language')) opts.headers.set('X-UI-Language', lang); }
      else opts.headers = { 'X-UI-Language': lang, ...(opts.headers || {}) };
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
    // the company's locale (ir / uk / default), from /auth/me: what applies to
    // it — tax codes, tax panels, payroll rules — is all an Iranian or a UK
    // company sees, not the other country's
    let companyLocale = '';
    function ownJurisdiction() { return companyLocale === 'ir' ? 'IR' : companyLocale === 'uk' ? 'UK' : ''; }
    // Which roles may SEE each page (nav + client-side gate). The server still
    // enforces — this is cosmetic. Keep in sync with app/core/permissions.py.
    const PAGE_ROLES = {
      dashboard: ['owner', 'cfo', 'accountant', 'viewer'],
      'personal-dashboard': ['personal'],
      commitments: ['owner', 'cfo', 'accountant', 'personal'],
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
      'fixed-assets': ['owner', 'cfo', 'accountant'],
      accounts: ['owner', 'cfo', 'accountant'],
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
        showAlert(t('msgConnectionError') + err.message, true);
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
        showAlert(t('msgConnectionError') + err.message, true);
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
              <th>${escapeHtml(t('apiKeysScopes'))}</th>
              <th>${escapeHtml(t('apiKeysCreated'))}</th><th>${escapeHtml(t('apiKeysLastUsed'))}</th>
              <th>${escapeHtml(t('apiKeysExpiry'))}</th>
              <th>${escapeHtml(t('usersStatus'))}</th><th></th>
            </tr></thead>
            <tbody>${keys.map(k => `
              <tr>
                <td>${escapeHtml(k.label)}</td>
                <td><code>${escapeHtml(k.prefix)}…</code></td>
                <td>${(k.scopes || []).map(s => `<code>${escapeHtml(s)}</code>`).join(' ')}</td>
                <td>${k.created_at ? escapeHtml(k.created_at.slice(0, 10)) : '—'}</td>
                <td>${k.last_used_at ? escapeHtml(k.last_used_at.slice(0, 10)) : '—'}</td>
                <td>${k.expires_at ? escapeHtml(k.expires_at.slice(0, 10)) : escapeHtml(t('apiKeysExpiryNever'))}</td>
                <td>${k.revoked ? escapeHtml(t('apiKeysRevoked')) : (k.expired ? escapeHtml(t('apiKeysExpired')) : escapeHtml(t('usersActive')))}</td>
                <td>${k.revoked ? '' : `<button type="button" class="btn btn-danger btn-sm apikey-revoke-btn" data-id="${escapeHtml(k.id)}" data-label="${escapeHtml(k.label)}">${escapeHtml(t('apiKeysRevoke'))}</button>`}</td>
              </tr>`).join('')}
            </tbody>
          </table>`;
      } catch (_) { /* ignore */ }
    }

    async function createApiKey() {
      const btn = document.getElementById('apikey-create-btn');
      const label = (document.getElementById('apikey-label').value || 'integration').trim();
      const scopes = [...document.querySelectorAll('.apikey-scope:checked')].map(c => c.value);
      if (!scopes.length) { showAlert(t('apiKeysNeedScope'), true); return; }
      const exp = (document.getElementById('apikey-expiry') || {}).value || '365';
      try {
        if (btn) btn.disabled = true;
        const res = await fetch(API + '/admin/api-keys', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ label, scopes, expires_in_days: exp === 'never' ? null : parseInt(exp, 10) }),
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
        showAlert(t('msgConnectionError') + err.message, true);
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
          showAlert(data.detail || t('msgRevokeKeyFailed'), true);
          return;
        }
        showAlert(t('apiKeysRevokedMsg'));
        loadApiKeys();
      } catch (err) { showAlert(t('msgConnectionError') + err.message, true); }
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
    const validPages = new Set(['dashboard', 'personal-dashboard', 'commitments', 'ai-accountant', 'transactions', 'entities', 'invoices', 'recurring', 'ledger', 'manager', 'inventory', 'products', 'payroll', 'equity', 'purchase-orders', 'expenses', 'time', 'settings', 'bank-statements', 'audit', 'cfo', 'ceo', 'companies', 'migration', 'petty-cash', 'fixed-assets', 'accounts']);
    // The Companies console is super-admin only; gated in showPage().
    let isSuperadmin = false;
    const rawFetch = window.fetch.bind(window);

    // Too many requests in a minute: say so once (not "no permission", not a
    // blank panel) — AI budget refusals carry their own code and message.
    let _rateNoticeUntil = 0;
    async function _noticeRateLimit(res) {
      try {
        const body = await res.clone().json();
        if (body && body.code && body.code !== 'rate_limited') return;
        const now = Date.now();
        if (now < _rateNoticeUntil) return;
        const wait = Number(res.headers.get('Retry-After')) || Number(body && body.retry_after) || 30;
        _rateNoticeUntil = now + Math.min(wait, 30) * 1000;
        if (typeof showAlert === 'function' && typeof tf === 'function') showAlert(tf('rateLimitedNotice', { seconds: wait }), true);
      } catch (_) { /* not JSON: nothing to say */ }
    }
    window.fetch = async (...args) => {
      const res = await rawFetch(...args);
      if (res.status === 401) {
        window.location.href = '/login';
        throw new Error(t('msgAuthRequired'));
      }
      if (res.status === 429) _noticeRateLimit(res);
      return res;
    };
