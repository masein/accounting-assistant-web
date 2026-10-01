
    // Hide every open overlay/modal (account drill-down, audit/chart
    // drilldowns, confirm dialogs…). Called on every page change so a modal
    // opened on one page never stays pinned over the next one.
    function closeAllModals() {
      document.querySelectorAll('.modal-overlay, [id$="-modal"]').forEach((el) => {
        if (!el || !el.style) return;
        if (typeof el.__dialogCancel === 'function') { el.__dialogCancel(); return; }
        if (el.style.display !== 'none') el.style.display = 'none';
      });
    }

    // ─── What's new tour: shown once per release, reopenable from Settings ───
    // Steps come from /auth/me (unseen releases) or /auth/whats-new (all).
    // Copy is server-owned in four languages; we pick the current UI language.
    const _wn = { steps: [], idx: 0, markSeen: false };
    function _wnText(obj) {
      const lang = (typeof currentLanguage === 'string' ? currentLanguage : 'en');
      return (obj && (obj[lang] || obj.en)) || '';
    }
    function openWhatsNew(payload, { markSeen = false } = {}) {
      const releases = (payload && payload.releases) || [];
      const steps = [];
      releases.forEach(r => (r.highlights || []).forEach(h => steps.push({ version: r.version, date: r.date, ...h })));
      if (!steps.length) return false;
      _wn.steps = steps; _wn.idx = 0; _wn.markSeen = markSeen;
      const modal = document.getElementById('whats-new-modal');
      if (!modal) return false;
      _wnRender();
      modal.style.display = 'flex';
      return true;
    }
    function _wnRender() {
      const st = _wn.steps[_wn.idx];
      if (!st) return;
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
      set('whats-new-version', tf('wnVersion', { version: st.version, date: st.date || '' }) + ' · ' + tf('wnStep', { n: _wn.idx + 1, total: _wn.steps.length }));
      set('whats-new-step-title', _wnText(st.title));
      set('whats-new-step-body', _wnText(st.body));
      const dots = document.getElementById('whats-new-dots');
      if (dots) {
        dots.innerHTML = _wn.steps.map((_, i) =>
          `<span style="width:8px;height:8px;border-radius:50%;background:${i === _wn.idx ? 'var(--primary)' : 'var(--border)'};"></span>`).join('');
      }
      const back = document.getElementById('whats-new-back');
      const next = document.getElementById('whats-new-next');
      const show = document.getElementById('whats-new-show');
      if (back) back.style.visibility = _wn.idx === 0 ? 'hidden' : 'visible';
      if (next) next.textContent = _wn.idx === _wn.steps.length - 1 ? t('wnDone') : t('wnNext');
      if (show) show.style.display = (st.page && typeof canSeePage === 'function' && canSeePage(st.page)) ? '' : 'none';
    }
    async function _wnFinish() {
      const modal = document.getElementById('whats-new-modal');
      if (modal) modal.style.display = 'none';
      if (_wn.markSeen) {
        _wn.markSeen = false;
        try { await fetch(API + '/auth/whats-new/seen', { method: 'POST' }); } catch (_) { /* retry next login */ }
      }
    }
    (function wireWhatsNew() {
      const next = document.getElementById('whats-new-next');
      const back = document.getElementById('whats-new-back');
      const close = document.getElementById('whats-new-close');
      const show = document.getElementById('whats-new-show');
      const modal = document.getElementById('whats-new-modal');
      if (next) next.addEventListener('click', () => {
        if (_wn.idx >= _wn.steps.length - 1) { _wnFinish(); return; }
        _wn.idx += 1; _wnRender();
      });
      if (back) back.addEventListener('click', () => { if (_wn.idx > 0) { _wn.idx -= 1; _wnRender(); } });
      if (close) close.addEventListener('click', _wnFinish);
      if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) _wnFinish(); });
      if (show) show.addEventListener('click', () => {
        const st = _wn.steps[_wn.idx];
        if (!st || !st.page) return;
        _wnFinish();
        showPage(st.page);
        if (typeof loadPageData === 'function') loadPageData(st.page);
      });
      const settingsBtn = document.getElementById('settings-whats-new-btn');
      if (settingsBtn) settingsBtn.addEventListener('click', async () => {
        try {
          const r = await fetch(API + '/auth/whats-new');
          const data = await r.json().catch(() => null);
          if (r.ok && data) openWhatsNew(data, { markSeen: !data.seen });
        } catch (_) { /* offline */ }
      });
    })();

    function showPage(page) {
      closeAllModals();
      // Companies console is super-admin only — a non-super-admin reaching it
      // via the #companies hash falls back to the dashboard (backend also 403s).
      const allowed = validPages.has(page) && canSeePage(page);
      const requested = allowed ? page : roleHome();
      document.querySelectorAll('.card[data-page]').forEach(card => {
        card.classList.toggle('active-page', card.getAttribute('data-page') === requested);
      });
      document.querySelectorAll('.nav-btn[data-page]').forEach(btn => {
        const on = btn.getAttribute('data-page') === requested;
        btn.classList.toggle('active', on);
        if (on) btn.setAttribute('aria-current', 'page'); else btn.removeAttribute('aria-current');
      });
      updatePageTitle(requested);
      closeSidebarDrawer();
      if (location.hash !== '#' + requested) history.replaceState(null, '', '#' + requested);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // The page on screen: showPage marks its card. A page's loaders fetch only
    // while their page is shown — loadPageData runs them when it opens, so a
    // refresh after a save needn't reload a page nobody is looking at.
    function activePage() {
      const card = document.querySelector('.card[data-page].active-page');
      return card ? card.getAttribute('data-page') : null;
    }
    function onPage(...pages) { return pages.includes(activePage()); }

    // Map each page to the nav label key so the top-bar title stays localized.
    const PAGE_TITLE_KEY = {
      dashboard: 'navDashboard', 'personal-dashboard': 'pdNav', commitments: 'cmNav', 'ai-accountant': 'aiAccountantNav', transactions: 'navTransactions',
      invoices: 'navInvoices', time: 'timeNav', expenses: 'expNav', 'purchase-orders': 'poNav',
      recurring: 'navRecurring', entities: 'navEntities', products: 'productsNav', inventory: 'navInventory',
      payroll: 'payrollNav', equity: 'equityNav', 'bank-statements': 'bankStatementsNav', ledger: 'navLedger', manager: 'navManager',
      cfo: 'cfoModeNav', ceo: 'ceoModeNav', audit: 'auditNav', settings: 'navSettings', companies: 'companiesNav', migration: 'migrationNav', 'petty-cash': 'pettyNav', 'fixed-assets': 'assetNav', accounts: 'coaNav',
    };
    function updatePageTitle(page) {
      const el = document.getElementById('page-title');
      if (el) el.textContent = t(PAGE_TITLE_KEY[page] || 'navDashboard');
    }
    function closeSidebarDrawer() {
      const sb = document.getElementById('sidebar');
      const ov = document.getElementById('sidebar-overlay');
      const tg = document.getElementById('nav-toggle');
      if (sb) sb.classList.remove('open');
      if (ov) ov.classList.remove('show');
      if (tg) tg.setAttribute('aria-expanded', 'false');
    }

    function toggleInlineChat() {
      // The inline voucher chat was merged into the dedicated AI Chat page.
      // The "Open AI chat" button now navigates there instead of toggling
      // a sidebar panel.
      location.hash = '#ai-accountant';
      if (typeof showPage === 'function') showPage('ai-accountant');
    }

    let _alertTimer = null;
    function showAlert(message, isError = false) {
      if (_alertTimer) { clearTimeout(_alertTimer); _alertTimer = null; }
      alertEl.textContent = '';
      alertEl.className = 'alert alert-' + (isError ? 'error' : 'success');
      alertEl.style.display = 'flex';
      alertEl.style.opacity = '1';
      const span = document.createElement('span');
      span.textContent = message;
      alertEl.appendChild(span);
      const close = document.createElement('button');
      close.className = 'alert-close';
      close.setAttribute('type', 'button');
      close.setAttribute('aria-label', t('btnClose'));
      close.textContent = '×';
      const dismiss = () => { alertEl.style.display = 'none'; if (_alertTimer) { clearTimeout(_alertTimer); _alertTimer = null; } };
      close.onclick = dismiss;
      alertEl.appendChild(close);
      _alertTimer = setTimeout(() => {
        alertEl.style.transition = 'opacity 0.4s ease';
        alertEl.style.opacity = '0';
        setTimeout(() => { alertEl.style.display = 'none'; alertEl.style.transition = ''; }, 400);
      }, 5000);
    }

    // Briefly highlight a freshly inserted row/element and scroll it into
    // view so the user can confirm the save landed.
    function flashRow(el) {
      if (!el) return;
      el.classList.remove('row-flash');
      void el.offsetWidth; // restart the animation if the class was present
      el.classList.add('row-flash');
      if (typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
      setTimeout(() => el.classList.remove('row-flash'), 2600);
    }

    // An enum value's label: t(prefix + value) when the pack has it, else the
    // value itself (underscores as spaces) — never the bare key.
    function enumLabel(prefix, value) {
      const v = String(value == null ? '' : value);
      const s = t(prefix + v);
      return s !== prefix + v ? s : v.replace(/_/g, ' ');
    }

    // t() with {token} substitution: tf('confirmDeleteUser', {name: 'bob'}).
    function tf(key, params) {
      let s = t(key);
      Object.entries(params || {}).forEach(([k, v]) => {
        s = s.split('{' + k + '}').join(String(v));
      });
      return s;
    }

    // Parse a fetch Response as JSON without throwing on non-JSON bodies.
    // A server 500 returns plain text ("Internal Server Error"); calling
    // res.json() on that throws "Unexpected token 'I'…". This returns {} (or
    // {_nonJson:true} so callers can show a friendly message) instead.
    async function readJsonSafe(res) {
      const ct = (res.headers.get('content-type') || '').toLowerCase();
      if (!ct.includes('json')) {
        return { _nonJson: true };
      }
      try { return await res.json(); } catch (_) { return {}; }
    }

    // Promise-based in-app replacements for native confirm()/prompt().
    // Resolve false/null when dismissed (cancel button, ×, overlay click, or
    // a page navigation closing every modal via closeAllModals()).
    function uiConfirm(opts) {
      const o = opts || {};
      return new Promise((resolve) => {
        const modal = document.getElementById('ui-confirm-modal');
        const okBtn = document.getElementById('ui-confirm-ok');
        const cancelBtn = document.getElementById('ui-confirm-cancel');
        const closeBtn = document.getElementById('ui-confirm-close');
        const done = (val) => {
          modal.__dialogCancel = null;
          modal.style.display = 'none';
          resolve(val);
        };
        document.getElementById('ui-confirm-title').textContent = o.title || t('confirmTitle');
        document.getElementById('ui-confirm-message').textContent = o.message || '';
        okBtn.textContent = o.confirmLabel || t('btnConfirm');
        okBtn.className = 'btn ' + (o.danger ? 'btn-danger' : 'btn-primary');
        cancelBtn.textContent = o.cancelLabel || t('btnCancel');
        cancelBtn.style.display = o.hideCancel ? 'none' : '';
        okBtn.onclick = () => done(true);
        cancelBtn.onclick = () => done(false);
        closeBtn.onclick = () => done(false);
        modal.onclick = (e) => { if (e.target === modal) done(false); };
        modal.__dialogCancel = () => done(false);
        modal.style.display = 'flex';
        okBtn.focus();
      });
    }

    function uiPrompt(opts) {
      const o = opts || {};
      return new Promise((resolve) => {
        const modal = document.getElementById('ui-prompt-modal');
        const input = document.getElementById('ui-prompt-input');
        const okBtn = document.getElementById('ui-prompt-ok');
        const cancelBtn = document.getElementById('ui-prompt-cancel');
        const closeBtn = document.getElementById('ui-prompt-close');
        const done = (val) => {
          modal.__dialogCancel = null;
          modal.style.display = 'none';
          input.onkeydown = null;
          resolve(val);
        };
        document.getElementById('ui-prompt-title').textContent = o.title || t('confirmTitle');
        document.getElementById('ui-prompt-label').textContent = o.message || '';
        input.type = o.type || 'text';
        input.value = o.value != null ? String(o.value) : '';
        input.placeholder = o.placeholder || '';
        // Show the eye toggle only for password prompts, masked by default.
        const pwBtn = input.parentElement && input.parentElement.querySelector('.pw-toggle');
        if (pwBtn) {
          if (o.type === 'password') {
            pwBtn.style.display = '';
            pwBtn.setAttribute('aria-pressed', 'false');
            pwBtn.setAttribute('aria-label', t('showPassword'));
            pwBtn.innerHTML = _EYE;
          } else {
            pwBtn.style.display = 'none';
          }
        }
        okBtn.textContent = o.confirmLabel || t('btnConfirm');
        cancelBtn.textContent = o.cancelLabel || t('btnCancel');
        okBtn.onclick = () => done(input.value);
        cancelBtn.onclick = () => done(null);
        closeBtn.onclick = () => done(null);
        modal.onclick = (e) => { if (e.target === modal) done(null); };
        input.onkeydown = (e) => {
          if (e.key === 'Enter') { e.preventDefault(); done(input.value); }
          if (e.key === 'Escape') { e.preventDefault(); done(null); }
        };
        modal.__dialogCancel = () => done(null);
        modal.style.display = 'flex';
        input.focus();
        if (input.type === 'text' || input.type === 'password') input.select();
      });
    }

    function formatNum(n) {
      if (n === 0) return '0';
      // Group the whole part only: 1.4224 used to come out as "1.4,224".
      const s = String(n);
      const dot = s.indexOf('.');
      const head = dot < 0 ? s : s.slice(0, dot);
      return head.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (dot < 0 ? '' : s.slice(dot));
    }
    // An exchange rate: every digit of a large one (a gold coin in rials), six
    // significant ones of a tiny one (rials into dollars), never 1e-7.
    function formatRate(n) {
      const v = Number(n);
      if (!isFinite(v)) return String(n);
      return Math.abs(v) >= 1
        ? v.toLocaleString('en-US', { maximumFractionDigits: 6 })
        : v.toLocaleString('en-US', { maximumSignificantDigits: 6 });
    }

    // Active reporting-currency label. Loaded once on page boot from
    // /fx/reporting-currency, refreshed after every locale-reset. Every
    // widget that appends a currency unit reads from this global instead
    // of hardcoding 'IRR' — keeps Iran/UK/etc. switching consistent.
    window.__REPORTING_CURRENCY = window.__REPORTING_CURRENCY || 'IRR';
    function currencyUnit() { return window.__REPORTING_CURRENCY || 'IRR'; }
    function fmtCurrency(v) { return formatNum(v) + ' ' + currencyUnit(); }
    // Fetched once per page load (sign-in; a dashboard opened while that is out
    // shares it) — the dashboards used to refetch it on every open. Saving it
    // in Settings updates the cached value; ``force`` refetches.
    let _reportingCurrencyInflight = null;
    let _reportingCurrencyLoaded = false;
    function loadReportingCurrency(force) {
      if (_reportingCurrencyLoaded && !force && !_reportingCurrencyInflight) return Promise.resolve();
      if (!_reportingCurrencyInflight) {
        _reportingCurrencyInflight = (async () => {
          try {
            const r = await fetch(API + '/fx/reporting-currency');
            if (r.ok) {
              const data = await r.json().catch(() => ({}));
              if (data && data.currency) window.__REPORTING_CURRENCY = data.currency;
              _reportingCurrencyLoaded = true;
            }
          } catch (_) { /* offline / no auth — keep cached value */ }
          applyDefaultFormCurrency();
        })().finally(() => { _reportingCurrencyInflight = null; });
      }
      return _reportingCurrencyInflight;
    }

    // Pre-select the company (reporting) currency on the create forms instead
    // of the hardcoded IRR first option. Fallback chain: reporting currency →
    // most common transaction currency → IRR.
    function preferredFormCurrency() {
      const meta = window.__FX_META;
      return window.__REPORTING_CURRENCY ||
        (meta && (meta.reporting_currency || meta.most_common_currency)) || 'IRR';
    }

    function applyDefaultFormCurrency() {
      const pref = String(preferredFormCurrency()).toUpperCase();
      ['inv-currency', 'txn-currency'].forEach((id) => {
        const sel = document.getElementById(id);
        if (sel && [...sel.options].some((o) => o.value === pref)) sel.value = pref;
      });
    }

    // Map ISO code → short symbol for inline display.
    const CURRENCY_SYMBOLS = {
      IRR: '\u0631\u06cc\u0627\u0644', // ریال
      IRT: '\u062a\u0648\u0645\u0627\u0646', // تومان
      USD: '$',
      EUR: '\u20ac',
      GBP: '\u00a3',
      AED: 'AED',
      TRY: '\u20ba',
    };

    function currencySymbol(ccy) {
      if (!ccy) return '';
      const s = CURRENCY_SYMBOLS[String(ccy).toUpperCase()];
      return s || String(ccy).toUpperCase();
    }

    // Persian "all amounts are in X" note for the Iranian statements. Reflects
    // the active reporting currency (ریال vs تومان) instead of always saying
    // ریال — otherwise a Toman-reporting company sees the wrong unit.
    const IRAN_UNIT_FA = { IRR: 'ریال' /* ریال */, IRT: 'تومان' /* تومان */ };
    function iranAmountsNote() {
      const ccy = String(window.__REPORTING_CURRENCY || 'IRR').toUpperCase();
      const unit = IRAN_UNIT_FA[ccy] || currencySymbol(ccy);
      return 'کلیه مبالغ به ' + unit + ' است'; // کلیه مبالغ به {unit} است
    }

    function formatMoney(n, ccy) {
      const num = formatNum(n || 0);
      if (!ccy) return num;
      const sym = currencySymbol(ccy);
      // Symbols that look like prefixes (e.g. $, €, £) go before; words/codes after.
      const prefixes = new Set(['$', '\u20ac', '\u00a3', '\u00a5']);
      if (prefixes.has(sym)) return sym + num;
      return num + ' ' + sym;
    }

    // Per-session cache of FX metadata from /fx/metadata
    window.__FX_META = window.__FX_META || null;
    // Per-currency views: a report shows ONE currency (the reporting currency
    // by default) and names the others the books contain. Amounts in
    // different currencies are never added together; the note offers each
    // other currency as its own view.
    function baseCurrencyCode() {
      return (window.__FX_META && window.__FX_META.reporting_currency) || currencyUnit();
    }
    // "ALL": every currency at its base-currency value (roadmap §4.6) — the
    // rate each entry was posted at. Entries with no rate yet are left out and
    // counted in /fx/metadata.unconverted.
    function baseViewNoteText() {
      const base = baseCurrencyCode();
      let txt = t('currencyBaseViewNote').replace('{base}', base);
      const u = window.__FX_META && window.__FX_META.unconverted;
      if (u && u.count) {
        txt += ' ' + t('currencyBaseViewPending').replace('{n}', String(u.count)).replace('{currencies}', (u.currencies || []).join(', '));
      }
      return txt;
    }
    function renderCurrencyViewNote(el, data, onPick) {
      if (!el) return;
      const others = (data && Array.isArray(data.other_currencies)) ? data.other_currencies : [];
      if (!data || !data.currency || !others.length) { el.style.display = 'none'; el.innerHTML = ''; return; }
      const combined = data.currency === 'ALL';
      // "All, in GBP" when the books hold only GBP is the GBP view: nothing to explain
      if (combined && others.length === 1 && others[0] === baseCurrencyCode()) {
        el.style.display = 'none'; el.innerHTML = ''; return;
      }
      const note = combined ? baseViewNoteText()
        : t('currencyViewNote').replace('{currency}', data.currency).replace('{others}', others.join(', '));
      const picks = combined ? others : ['ALL'].concat(others);
      const buttons = picks.map(c => '<button type="button" class="btn btn-secondary btn-sm ccy-view-switch" data-ccy="' + escapeHtml(c) + '">'
        + escapeHtml(c === 'ALL' ? t('currencyAllInBase').replace('{base}', baseCurrencyCode())
          : t('currencyViewOnly').replace('{currency}', c)) + '</button>').join(' ');
      el.innerHTML = escapeHtml(note) + ' <span style="display:inline-flex;gap:0.35rem;flex-wrap:wrap;vertical-align:middle;">' + buttons + '</span>';
      el.style.display = '';
      el.querySelectorAll('.ccy-view-switch').forEach(b => b.addEventListener('click', () => { if (onPick) onPick(b.dataset.ccy); }));
    }

    // Callers that arrive while a request is out share it (four used to go
    // out at sign-in); ``force`` skips the cached copy, not the one in flight.
    let _fxMetaInflight = null;
    async function loadFxMetadata(force) {
      if (window.__FX_META && !force) return window.__FX_META;
      if (!_fxMetaInflight) {
        _fxMetaInflight = (async () => {
          try {
            const r = await fetch(API + '/fx/metadata');
            if (!r.ok) return null;
            window.__FX_META = await r.json();
            applyReportCurrencyDefault(window.__FX_META);
            return window.__FX_META;
          } catch (_) {
            return null;
          }
        })().finally(() => { _fxMetaInflight = null; });
      }
      return _fxMetaInflight;
    }

    // The reports' currency select (#mgr-currency): several currencies in the
    // books → all of them at base value (roadmap §4.6); one → that one. Set
    // here, with the metadata, so the dashboard — which reads the select on
    // its first paint — never sees the markup's "All" default before it is
    // settled. Only once: after that the choice is the user's.
    function applyReportCurrencyDefault(meta) {
      const sel = document.getElementById('mgr-currency');
      if (!sel || !meta) return;
      const pref = meta.reporting_currency || meta.most_common_currency || 'IRR';
      const allOpt = [...sel.options].find(o => o.value === 'ALL');
      if (allOpt) allOpt.textContent = t('currencyAllInBase').replace('{base}', pref);
      if (sel.dataset.defaulted) return;
      sel.dataset.defaulted = '1';
      const used = Array.isArray(meta.used_currencies) ? meta.used_currencies : [];
      const want = used.length > 1 ? 'ALL' : (meta.most_common_currency || pref);
      if ([...sel.options].some(o => o.value === want)) sel.value = want;
    }

    function toJalali(isoDate) {
      if (!isoDate) return '';
      const parts = String(isoDate).split('-');
      if (parts.length !== 3) return isoDate;
      // Uses the single gregorianToJalali defined earlier, which returns an
      // object { jy, jm, jd }. (A second, array-returning duplicate of this
      // function used to live here and silently shadowed the object version,
      // breaking every consumer that destructured { jy, jm, jd } — including
      // formatDisplayDate, which then rendered "undefined/undefined/undefined".)
      const { jy, jm, jd } = gregorianToJalali(+parts[0], +parts[1], +parts[2]);
      return jy + '/' + String(jm).padStart(2, '0') + '/' + String(jd).padStart(2, '0');
    }

    // Once "2026-09-28 (1405/07/06)" whatever the company chose: a Jalali
    // company read the Gregorian date first, a UK one got a Jalali date it
    // never asked for. A date now follows Settings → display calendar.
    function formatDateDual(isoDate) {
      return formatDisplayDate(isoDate);
    }

    function formatKpiValue(v, unit) {
      if (v == null) return t('na');
      if (unit === 'months' && Number(v) < 0) return t('na');
      if (unit === 'months') return v + ' ' + t('monthsShort');
      if (unit === '%') return String(v) + '%';
      // Everything else with a unit is a currency code (IRR, GBP, USD,
      // EUR, …). Display the formatted amount followed by the code.
      if (unit && String(unit).length > 0 && unit !== '%') {
        return formatNum(v) + ' ' + unit;
      }
      return String(v);
    }
    function escapeHtml(s) {
      if (s == null) return '';
      const t = String(s);
      return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function trf(key, vars = {}) {
      let txt = t(key);
      Object.keys(vars).forEach((k) => {
        txt = txt.replace(new RegExp('\\{' + k + '\\}', 'g'), String(vars[k]));
      });
      return txt;
    }
    // A control in a table cell takes its name from its column header: the line
    // editors (journal lines, invoice lines, instalments, opening balances, PO
    // lines…) build inputs without a <label>, which left a screen reader saying
    // "edit text" forty times. Names this pass set are refreshed on the next one,
    // so a language switch re-reads the translated header.
    function nameTableControls(root) {
      (root || document).querySelectorAll('td input, td select, td textarea').forEach((el) => {
        if (el.type === 'hidden' || (el.labels && el.labels.length)) return;
        if (el.hasAttribute('aria-label') && !el.dataset.autoName) return;      // named by its renderer
        const td = el.closest('td');
        const table = td && td.closest('table');
        const head = table && table.tHead && table.tHead.rows[0];
        const th = head && head.cells[td.cellIndex];
        const name = th ? th.textContent.trim() : '';
        if (name && el.getAttribute('aria-label') !== name) { el.setAttribute('aria-label', name); el.dataset.autoName = '1'; }
      });
    }
    // A file field speaks the browser's language, not the page's: Chrome writes
    // "Choose Files" / "No file chosen" on it whatever lang and dir say, so a
    // Persian user met English beside every upload. Each visible file input is
    // dressed in a chip and the chosen names in the user's language; the input
    // stays where it was, transparent and on top, so it is still what is
    // clicked, focused, dropped on, and read by scripts and tests. (One a script
    // opens from its own button carries display:none and is left alone.)
    const _fileInputValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    function paintFilePick(input) {
      const wrap = input.parentElement;
      if (!wrap || !wrap.classList.contains('file-pick')) return;
      const n = input.files ? input.files.length : 0;
      const label = t(input.multiple ? 'fileChooseMany' : 'fileChooseOne');
      const chosen = !n ? t('fileNoneChosen') : n === 1 ? input.files[0].name : tf('fileManyChosen', { count: n });
      // only on a change: the observer that calls this watches text too
      const btn = wrap.querySelector('.file-pick-btn'), name = wrap.querySelector('.file-pick-name');
      if (btn.textContent !== label) btn.textContent = label;
      if (name.textContent !== chosen) { name.textContent = chosen; name.title = n > 1 ? [...input.files].map((f) => f.name).join('\n') : ''; }
      wrap.classList.toggle('has-files', n > 0);
    }
    function dressFileInputs() {
      document.querySelectorAll('input[type="file"]').forEach((input) => {
        if (input.dataset.filePick) { paintFilePick(input); return; }   // a language switch repaints
        if (input.style.display === 'none' || input.hidden) return;
        input.dataset.filePick = '1';
        const wrap = document.createElement('span');
        wrap.className = 'file-pick';
        wrap.style.cssText = input.style.cssText;   // its layout (max-width, flex, margin) moves to the box
        input.style.cssText = '';
        const btn = document.createElement('span');
        btn.className = 'file-pick-btn';
        const name = document.createElement('span');
        name.className = 'file-pick-name';
        btn.setAttribute('aria-hidden', 'true');
        name.setAttribute('aria-hidden', 'true');
        input.parentNode.insertBefore(wrap, input);
        wrap.append(input, btn, name);
        input.addEventListener('change', () => paintFilePick(input));
        // a script clearing the field (input.value = '') fires no event
        Object.defineProperty(input, 'value', {
          configurable: true,
          get() { return _fileInputValue.get.call(this); },
          set(v) { _fileInputValue.set.call(this, v); paintFilePick(this); },
        });
        paintFilePick(input);
      });
    }
    (function watchTableControls() {
      let queued = false;
      const run = () => { queued = false; try { nameTableControls(); } catch (_) {} try { dressFileInputs(); } catch (_) {} };
      new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(run); } })
        .observe(document.body, { childList: true, subtree: true, characterData: true });
    })();

    // Persian (۰–۹) and Arabic-Indic (٠–٩) digits in a number field: Chrome
    // drops them, so an amount typed on a Persian keyboard simply vanished. They
    // go in as 0–9 — typed, entered through an input method, or pasted (with
    // its thousands separators dropped and ٫ as the decimal point).
    function asciiDigits(text) {
      return String(text)
        .replace(/[۰-۹]/g, (d) => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d))
        .replace(/[٠-٩]/g, (d) => '٠١٢٣٤٥٦٧٨٩'.indexOf(d))
        .replace(/٫/g, '.').replace(/[٬,\s]/g, '');
    }
    (function acceptPersianDigits() {
      const NON_ASCII = /[۰-۹٠-٩٫٬]/;
      const numberField = (el) => el instanceof HTMLInputElement && el.type === 'number' && !el.readOnly && !el.disabled;
      const put = (el, text) => {
        if (!document.execCommand('insertText', false, text)) {      // the field takes it where the caret is
          el.value = (el.value || '') + text;
          el.dispatchEvent(new Event('input', { bubbles: true }));
        }
      };
      document.addEventListener('keydown', (e) => {
        if (!numberField(e.target) || e.ctrlKey || e.metaKey || e.altKey || e.key.length !== 1 || !NON_ASCII.test(e.key)) return;
        e.preventDefault();
        put(e.target, asciiDigits(e.key));
      }, true);
      document.addEventListener('beforeinput', (e) => {
        if (!numberField(e.target) || !e.data || !NON_ASCII.test(e.data)) return;
        e.preventDefault();
        put(e.target, asciiDigits(e.data));
      }, true);
      document.addEventListener('paste', (e) => {
        if (!numberField(e.target)) return;
        const text = (e.clipboardData && e.clipboardData.getData('text')) || '';
        if (!NON_ASCII.test(text) && !/[,\s]/.test(text.trim())) return;
        e.preventDefault();
        put(e.target, asciiDigits(text));
      }, true);
    })();

    // ي/ى and ی, ك and ک are the same letter to a reader (an Arabic keyboard,
    // a bank's export): a search compares both sides folded. Stored text is kept.
    function foldFa(text) {
      return String(text || '').replace(/[يى]/g, 'ی').replace(/ك/g, 'ک').toLowerCase();
    }

    function localizeDynamicText(value) {
      if (value == null) return '';
      const s = String(value).trim();
      const map = {
        'No data yet.': 'noDataYet',
        'No active alerts.': 'noActiveAlerts',
        'Risk': 'risk',
        'OK': 'ok',
        'Book quality risk': 'alertBookQualityRisk',
        'Cash runway is short': 'alertCashRunwayShort',
        'Overdue receivables': 'alertOverdueReceivables',
        'Overdue payables': 'alertOverduePayables',
        'Expense spike': 'alertExpenseSpike',
        'Missing reference': 'issueMissingReference',
        'Transactions without entity': 'issueTransactionsWithoutEntity',
        'Expense transactions without attachment': 'issueExpenseWithoutAttachment',
        'Lines without description': 'issueLinesWithoutDescription',
        'References captured': 'checkReferencesCaptured',
        'Entities linked': 'checkEntitiesLinked',
        'Expense attachments available': 'checkExpenseAttachmentsAvailable',
        'Line descriptions complete': 'checkLineDescriptionsComplete',
        'Unassigned client': 'unassignedClient',
        'Unassigned vendor': 'unassignedVendor',
        // CFO / CEO KPI-card labels (returned by the backend or built in the
        // CEO renderer). Mapped here so both executive dashboards localise.
        'Total Revenue (12m)': 'cfoKpiTotalRevenue',
        'Avg Monthly Revenue': 'cfoKpiAvgMonthlyRevenue',
        'Net Profit (12m)': 'cfoKpiNetProfit',
        'Net Margin': 'cfoKpiNetMargin',
        'Cash on Hand': 'cfoKpiCashOnHand',
        'Monthly Burn Rate': 'cfoKpiMonthlyBurnRate',
        'Cash Runway': 'cashRunway',
        'Accounts Receivable': 'ceoAR',
        'Accounts Payable': 'ceoAP',
        'Expense MoM Change': 'cfoKpiExpenseMoM',
        'Cash Position': 'ceoKpiCashPosition',
        'Burn Rate': 'burnRate',
        'Liability Ratio': 'ceoKpiLiabilityRatio',
      };
      if (map[s]) return t(map[s]);
      let m = s.match(/^Estimated runway is\s+([0-9.]+)\s+months based on recent burn rate\.$/i);
      if (m) return trf('alertRunwayMessage', { months: m[1] });
      m = s.match(/^Overdue AR is\s+([0-9,]+)\.\s*Follow up collections\.$/i);
      if (m) return trf('alertOverdueArMessage', { amount: m[1] });
      m = s.match(/^Overdue AP is\s+([0-9,]+)\.\s*Plan vendor payments\.$/i);
      if (m) return trf('alertOverdueApMessage', { amount: m[1] });
      m = s.match(/^Data quality score is\s+([0-9]+\/100)\.\s*Resolve missing references\/entities\/attachments\.$/i);
      if (m) return trf('alertDataQualityMessage', { score: m[1] });
      m = s.match(/^This month expenses are significantly above recent average\.$/i);
      if (m) return t('alertExpenseSpikeMessage');
      m = s.match(/^([0-9]+)\/([0-9]+)\s+transactions have reference\.$/i);
      if (m) return trf('checkRefsDetail', { done: m[1], total: m[2] });
      m = s.match(/^([0-9]+)\/([0-9]+)\s+transactions have entity links\.$/i);
      if (m) return trf('checkEntitiesDetail', { done: m[1], total: m[2] });
      m = s.match(/^([0-9]+)\/([0-9]+)\s+expense transactions have attachments\.$/i);
      if (m) return trf('checkAttachmentsDetail', { done: m[1], total: m[2] });
      m = s.match(/^([0-9]+)\/([0-9]+)\s+lines have descriptions\.$/i);
      if (m) return trf('checkLineDescriptionsDetail', { done: m[1], total: m[2] });
      return s;
    }
    function localizeReportFieldName(name) {
      const key = String(name || '').toLowerCase();
      const map = {
        transaction_id: 'fieldTransactionId',
        date: 'labelDate',
        reference: 'labelReference',
        description: 'labelDescription',
        total_debit: 'fieldTotalDebit',
        total_credit: 'fieldTotalCredit',
        account_code: 'tableAccountCode',
        account_name: 'fieldAccount',
        debit: 'tableDebit',
        credit: 'tableCredit',
        line_description: 'tableLineDescription',
        section: 'fieldSection',
        balance: 'fieldBalance',
        metric: 'fieldMetric',
        value: 'fieldValue',
        item_name: 'fieldItemName',
        sku: 'labelSKU',
        quantity: 'labelQuantity',
        unit_cost: 'fieldUnitCost',
        inventory_value: 'fieldInventoryValue',
        movement_date: 'fieldMovementDate',
        movement_type: 'labelMovementType',
        product_name: 'fieldProduct',
        invoice_number: 'fieldInvoice',
        sales_amount: 'fieldSalesAmount',
        purchase_amount: 'fieldPurchaseAmount',
        count: 'fieldCount',
        issue_date: 'labelIssueDate',
        due_date: 'labelDueDate',
        status: 'usersStatus',
        amount: 'labelAmount',
        revenue: 'fieldRevenue',
        cost: 'fieldCost',
        profit: 'fieldProfit',
        margin_pct: 'fieldMarginPct',
        current: 'fieldCurrent',
        days_31_60: 'fieldDays31_60',
        days_60_plus: 'fieldDays60Plus',
        days_61_90: 'fieldDays61_90',
        days_90_plus: 'fieldDays90Plus',
        entity_type: 'fieldEntityType',
        role: 'fieldRole',
        total: 'tableTotal',
        week_start: 'fieldWeek',
        projected_inflow: 'fieldInflow',
        projected_outflow: 'fieldOutflow',
        projected_net: 'fieldNet',
        projected_cash: 'fieldCash',
        risk: 'fieldRisk',
        estimated_cost: 'fieldEstimatedCost',
        entity_names: 'fieldEntities',
        entity_name: 'fieldEntityName',
        invoice_count: 'fieldInvoiceCount',
        client_count: 'fieldClientCount',
        supplier_count: 'fieldSupplierCount',
        top_client: 'fieldTopClient',
        name: 'labelName',
        code: 'tableCode',
        client: 'labelClient',
        vendor: 'labelSupplier',
        category: 'labelCategory',
      };
      return map[key] ? t(map[key]) : String(name || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    // ═══════ Two-factor sign-in (account menu → security dialog) ═══════
    // Every role reaches it from the account menu; the server does the work
    // (/auth/2fa*), this only walks the user through set-up and turn-off.
    const _tfa = { user: null };
    function setTwoFactorHint(user) {
      _tfa.user = user || null;
      const dot = document.getElementById('topbar-security-dot');
      if (dot) dot.style.display = (user && user.two_factor_recommended && !user.two_factor_enabled) ? '' : 'none';
    }
    function _tfaShow(section) {
      ['tfa-off', 'tfa-scan', 'tfa-codes', 'tfa-on'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.style.display = id === section ? '' : 'none';
      });
      _tfaError('');
    }
    function _tfaError(msg) {
      const el = document.getElementById('tfa-error');
      if (!el) return;
      el.textContent = msg || '';
      el.style.display = msg ? '' : 'none';
    }
    async function _tfaPost(path, body) {
      const res = await fetch(API + path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : t('tfaFailed'));
      return data;
    }
    function _tfaRenderStatus(st) {
      if (!st.enabled) {
        const rec = document.getElementById('tfa-recommend');
        if (rec) rec.style.display = (_tfa.user && _tfa.user.two_factor_recommended) ? '' : 'none';
        document.getElementById('tfa-setup-password').value = '';
        _tfaShow('tfa-off');
        return;
      }
      const since = st.enabled_at ? String(st.enabled_at).slice(0, 10) : '';
      document.getElementById('tfa-on-status').textContent = tf('tfaOnSince', { date: since });
      document.getElementById('tfa-on-left').textContent = tf('tfaCodesLeft', { n: st.recovery_codes_left });
      document.getElementById('tfa-on-code').value = '';
      document.getElementById('tfa-off-password').value = '';
      _tfaShow('tfa-on');
    }
    function _tfaShowCodes(codes) {
      _tfa.codes = codes || [];
      document.getElementById('tfa-codes-list').textContent = _tfa.codes.join('\n');
      _tfaShow('tfa-codes');
    }
    async function openSecurityModal() {
      const modal = document.getElementById('security-modal');
      if (!modal) return;
      modal.style.display = 'flex';
      try {
        const res = await fetch(API + '/auth/2fa');
        const st = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(st.detail || t('tfaFailed'));
        _tfaRenderStatus(st);
      } catch (err) {
        _tfaShow('');
        _tfaError(err.message);
      }
    }
    function _tfaAfterChange(enabled) {
      if (_tfa.user) _tfa.user.two_factor_enabled = enabled;
      setTwoFactorHint(_tfa.user);
    }
    // ─── Telegram / Bale (roadmap §5.7) ─────────────────────────────────
    let _msgStatus = null;
    async function refreshMessengerButton() {
      const btn = document.getElementById('messenger-btn');
      if (!btn) return;
      try {
        const res = await fetch(API + '/ai-accountant/messenger');
        _msgStatus = res.ok ? await res.json() : null;
      } catch (_) { _msgStatus = null; }
      const any = _msgStatus && Object.values(_msgStatus.platforms || {}).some(p => p.connected);
      btn.style.display = any ? '' : 'none';
    }
    function renderMessengerModal() {
      const st = _msgStatus || { platforms: {}, links: [] };
      const plat = document.getElementById('messenger-platforms');
      plat.innerHTML = Object.entries(st.platforms).filter(([, p]) => p.connected).map(([key, p]) =>
        `<button type="button" class="btn btn-primary btn-sm msg-connect" data-platform="${escapeHtml(key)}">${escapeHtml(tf('msgConnect', { name: p.name }))}</button>`).join('');
      plat.querySelectorAll('.msg-connect').forEach(b => b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const res = await fetch(API + '/ai-accountant/messenger/link', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ platform: b.dataset.platform }) });
          const d = await res.json().catch(() => ({}));
          if (!res.ok) { showAlert(d.detail || t('msgFailed'), true); return; }
          const box = document.getElementById('messenger-code');
          box.innerHTML = '<div>' + escapeHtml(tf('msgOpenLink', { bot: '@' + d.bot })) + '</div>'
            + `<a class="btn btn-primary btn-sm" href="${escapeHtml(d.url)}" target="_blank" rel="noopener">${escapeHtml(tf('msgOpen', { name: st.platforms[b.dataset.platform].name }))}</a>`
            + '<div class="fx-hint" style="margin-top:0.45rem;">' + escapeHtml(t('msgOrSend')) + ' <code dir="ltr">/start ' + escapeHtml(d.code) + '</code> · '
            + escapeHtml(t('msgExpires')) + '</div>';
          box.hidden = false;
        } finally { b.disabled = false; }
      }));
      const links = document.getElementById('messenger-links');
      links.innerHTML = (st.links || []).length ? st.links.map(l => `<div class="msg-link"><span><strong>${escapeHtml(l.name)}</strong> · <bdi>${escapeHtml(l.chat_name || '')}</bdi></span>
          <button type="button" class="btn btn-secondary btn-sm msg-unlink" data-id="${escapeHtml(l.id)}">${escapeHtml(t('msgUnlink'))}</button></div>`).join('')
        : '<p class="fx-hint">' + escapeHtml(t('msgNoLinks')) + '</p>';
      links.querySelectorAll('.msg-unlink').forEach(b => b.addEventListener('click', async () => {
        if (!(await uiConfirm({ message: t('msgUnlinkConfirm'), confirmLabel: t('msgUnlink'), danger: true }))) return;
        const res = await fetch(API + '/ai-accountant/messenger/links/' + encodeURIComponent(b.dataset.id), { method: 'DELETE' });
        if (res.ok) { await refreshMessengerButton(); renderMessengerModal(); }
      }));
    }
    (function wireMessenger() {
      const modal = document.getElementById('messenger-modal');
      const btn = document.getElementById('messenger-btn');
      if (!modal || !btn) return;
      const close = () => { modal.style.display = 'none'; document.getElementById('messenger-code').hidden = true; };
      document.getElementById('messenger-modal-close').addEventListener('click', close);
      modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
      btn.addEventListener('click', async () => {
        const pop = document.getElementById('user-pop');
        if (pop) pop.classList.remove('open');
        await refreshMessengerButton();
        renderMessengerModal();
        modal.style.display = 'flex';
      });
    })();

    // Platform admin: connect the bots (Settings → Messenger bots).
    async function loadMessengerBots() {
      const card = document.getElementById('msg-bots-card');
      if (!card) return;
      const res = await fetch(API + '/admin/messenger-bots').catch(() => null);
      if (!res || !res.ok) { card.style.display = 'none'; return; }
      const d = await res.json();
      card.style.display = '';
      document.getElementById('msg-bots-url').textContent = tf('msgBotsUrl', { url: d.public_url || '' });
      const list = document.getElementById('msg-bots-list');
      list.innerHTML = Object.entries(d.bots || {}).map(([key, b]) => `<div class="msg-bot" data-platform="${escapeHtml(key)}">
          <strong>${escapeHtml(b.name)}</strong>
          ${b.connected
            ? `<span>${escapeHtml(tf('msgBotConnected', { bot: '@' + (b.username || '') }))}</span>
               <button type="button" class="btn btn-secondary btn-sm msg-bot-off">${escapeHtml(t('msgBotDisconnect'))}</button>`
            : `<input type="password" class="msg-bot-token" autocomplete="off" dir="ltr" placeholder="123456:ABC…" aria-label="${escapeHtml(tf('msgBotToken', { name: b.name }))}">
               <button type="button" class="btn btn-primary btn-sm msg-bot-on">${escapeHtml(t('msgBotConnect'))}</button>`}
        </div>`).join('');
      list.querySelectorAll('.msg-bot').forEach(row => {
        const platform = row.dataset.platform;
        const on = row.querySelector('.msg-bot-on');
        if (on) on.addEventListener('click', async () => {
          const token = row.querySelector('.msg-bot-token').value.trim();
          if (!token) return;
          on.disabled = true;
          try {
            const r = await fetch(API + '/admin/messenger-bots/' + platform, { method: 'PUT',
              headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
            const out = await r.json().catch(() => ({}));
            if (!r.ok) { showAlert(typeof out.detail === 'string' ? out.detail : t('msgFailed'), true); return; }
            showAlert(tf('msgBotConnected', { bot: '@' + (out.username || '') }));
            loadMessengerBots(); refreshMessengerButton();
          } finally { on.disabled = false; }
        });
        const off = row.querySelector('.msg-bot-off');
        if (off) off.addEventListener('click', async () => {
          if (!(await uiConfirm({ message: t('msgBotDisconnectConfirm'), confirmLabel: t('msgBotDisconnect'), danger: true }))) return;
          await fetch(API + '/admin/messenger-bots/' + platform, { method: 'DELETE' });
          loadMessengerBots(); refreshMessengerButton();
        });
      });
    }

    (function wireTwoFactor() {
      const modal = document.getElementById('security-modal');
      if (!modal) return;
      const close = () => { modal.style.display = 'none'; _tfa.codes = null; };
      document.getElementById('security-modal-close').addEventListener('click', close);
      modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
      const menuBtn = document.getElementById('topbar-security');
      if (menuBtn) menuBtn.addEventListener('click', () => {
        const pop = document.getElementById('user-pop');
        if (pop) pop.classList.remove('open');
        openSecurityModal();
      });

      document.getElementById('tfa-start').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const password = document.getElementById('tfa-setup-password').value;
        if (!password) { _tfaError(t('tfaNeedPassword')); return; }
        btn.disabled = true;
        try {
          const data = await _tfaPost('/auth/2fa/setup', { password });
          document.getElementById('tfa-qr').src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(data.qr_svg);
          document.getElementById('tfa-qr').alt = t('tfaQrAlt');
          document.getElementById('tfa-secret').textContent = (data.secret || '').replace(/(.{4})/g, '$1 ').trim();
          document.getElementById('tfa-enable-code').value = '';
          _tfaShow('tfa-scan');
          document.getElementById('tfa-enable-code').focus();
        } catch (err) { _tfaError(err.message); } finally { btn.disabled = false; }
      });

      document.getElementById('tfa-confirm').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const code = document.getElementById('tfa-enable-code').value.trim();
        if (!code) { _tfaError(t('tfaNeedCode')); return; }
        btn.disabled = true;
        try {
          const data = await _tfaPost('/auth/2fa/enable', { code });
          _tfaAfterChange(true);
          _tfaShowCodes(data.recovery_codes);
        } catch (err) { _tfaError(err.message); } finally { btn.disabled = false; }
      });
      document.getElementById('tfa-enable-code').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); document.getElementById('tfa-confirm').click(); }
      });

      document.getElementById('tfa-codes-copy').addEventListener('click', async () => {
        try { await navigator.clipboard.writeText((_tfa.codes || []).join('\n')); showAlert(t('tfaCopied')); }
        catch (_) { _tfaError(t('tfaCopyFailed')); }
      });
      document.getElementById('tfa-codes-download').addEventListener('click', () => {
        const who = (_tfa.user && _tfa.user.username) || 'account';
        const text = t('tfaFileHeader') + ' — ' + who + '\n\n' + (_tfa.codes || []).join('\n') + '\n';
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
        a.download = 'recovery-codes-' + who + '.txt';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      });
      document.getElementById('tfa-codes-done').addEventListener('click', () => { _tfa.codes = null; openSecurityModal(); });

      document.getElementById('tfa-new-codes').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const code = document.getElementById('tfa-on-code').value.trim();
        if (!code) { _tfaError(t('tfaNeedAppCode')); return; }
        btn.disabled = true;
        try { _tfaShowCodes((await _tfaPost('/auth/2fa/recovery-codes', { code })).recovery_codes); }
        catch (err) { _tfaError(err.message); } finally { btn.disabled = false; }
      });

      document.getElementById('tfa-disable').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const code = document.getElementById('tfa-on-code').value.trim();
        const password = document.getElementById('tfa-off-password').value;
        if (!code || !password) { _tfaError(t('tfaNeedBoth')); return; }
        if (!(await uiConfirm({ message: t('tfaConfirmOff'), confirmLabel: t('tfaTurnOff'), danger: true }))) return;
        modal.style.display = 'flex';  // uiConfirm closes over it; keep ours open
        btn.disabled = true;
        try {
          const st = await _tfaPost('/auth/2fa/disable', { password, code });
          _tfaAfterChange(false);
          _tfaRenderStatus(st);
          showAlert(t('tfaTurnedOff'));
        } catch (err) { _tfaError(err.message); } finally { btn.disabled = false; }
      });
    })();

    // ═══════ Installable app (roadmap §4.10) ═══════
    // The service worker needs a secure context (https, or localhost in dev).
    let _installPrompt = null;
    (function setupInstallableApp() {
      if ('serviceWorker' in navigator && window.isSecureContext) {
        window.addEventListener('load', () => {
          navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => { /* the app works without it */ });
        });
      }
      const btn = document.getElementById('install-app-btn');
      window.addEventListener('beforeinstallprompt', (ev) => {
        ev.preventDefault();                 // offer it from the account menu instead of a banner
        _installPrompt = ev;
        if (btn) btn.style.display = '';
      });
      window.addEventListener('appinstalled', () => {
        _installPrompt = null;
        if (btn) btn.style.display = 'none';
      });
      if (btn) btn.addEventListener('click', async () => {
        if (!_installPrompt) return;
        _installPrompt.prompt();
        try { await _installPrompt.userChoice; } catch (_) { /* dismissed */ }
        _installPrompt = null;
        btn.style.display = 'none';
      });
    })();

    // ═══════ Push notifications (roadmap §4.10, part 2) ═══════
    function _pushKeyBytes(b64) {
      const pad = '='.repeat((4 - (b64.length % 4)) % 4);
      const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
      return Uint8Array.from(raw, (c) => c.charCodeAt(0));
    }
    async function _pushRegistration() {
      return ('serviceWorker' in navigator) ? navigator.serviceWorker.getRegistration('/') : null;
    }
    async function refreshPushButton() {
      const btn = document.getElementById('push-toggle-btn');
      if (!btn) return;
      const supported = window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window
        && 'Notification' in window;
      const reg = supported ? await _pushRegistration() : null;
      if (!reg || Notification.permission === 'denied') { btn.style.display = 'none'; return; }
      const sub = await reg.pushManager.getSubscription();
      btn.textContent = t(sub ? 'pushTurnOff' : 'pushTurnOn');
      btn.dataset.on = sub ? '1' : '';
      btn.style.display = '';
    }
    (function setupPush() {
      const btn = document.getElementById('push-toggle-btn');
      if (!btn) return;
      btn.addEventListener('click', async () => {
        const reg = await _pushRegistration();
        if (!reg) return;
        try {
          if (btn.dataset.on) {
            const sub = await reg.pushManager.getSubscription();
            if (sub) {
              await fetch(API + '/notifications/push/subscriptions', { method: 'DELETE',
                headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ endpoint: sub.endpoint }) });
              await sub.unsubscribe();
            }
          } else {
            if ((await Notification.requestPermission()) !== 'granted') { refreshPushButton(); return; }
            const { public_key: key } = await (await fetch(API + '/notifications/push/key')).json();
            const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: _pushKeyBytes(key) });
            const res = await fetch(API + '/notifications/push/subscriptions', { method: 'POST',
              headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(sub.toJSON()) });
            if (!res.ok) { await sub.unsubscribe(); showAlert(t('pushFailed'), true); }
            else { fetch(API + '/notifications/push/test', { method: 'POST' }).catch(() => {}); }
          }
        } catch (_) {
          showAlert(t('pushFailed'), true);
        }
        refreshPushButton();
      });
      if ('serviceWorker' in navigator && window.isSecureContext) {
        navigator.serviceWorker.ready.then(() => refreshPushButton()).catch(() => {});
      }
    })();
