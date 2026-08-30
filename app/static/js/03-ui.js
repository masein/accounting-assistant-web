
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

    // Map each page to the nav label key so the top-bar title stays localized.
    const PAGE_TITLE_KEY = {
      dashboard: 'navDashboard', 'personal-dashboard': 'pdNav', 'ai-accountant': 'aiAccountantNav', transactions: 'navTransactions',
      invoices: 'navInvoices', time: 'timeNav', expenses: 'expNav', 'purchase-orders': 'poNav',
      recurring: 'navRecurring', entities: 'navEntities', products: 'productsNav', inventory: 'navInventory',
      payroll: 'payrollNav', equity: 'equityNav', 'bank-statements': 'bankStatementsNav', ledger: 'navLedger', manager: 'navManager',
      cfo: 'cfoModeNav', ceo: 'ceoModeNav', audit: 'auditNav', settings: 'navSettings', companies: 'companiesNav', migration: 'migrationNav', 'petty-cash': 'pettyNav',
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
      close.setAttribute('aria-label', 'Close');
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
      return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }

    // Active reporting-currency label. Loaded once on page boot from
    // /fx/reporting-currency, refreshed after every locale-reset. Every
    // widget that appends a currency unit reads from this global instead
    // of hardcoding 'IRR' — keeps Iran/UK/etc. switching consistent.
    window.__REPORTING_CURRENCY = window.__REPORTING_CURRENCY || 'IRR';
    function currencyUnit() { return window.__REPORTING_CURRENCY || 'IRR'; }
    function fmtCurrency(v) { return formatNum(v) + ' ' + currencyUnit(); }
    async function loadReportingCurrency() {
      try {
        const r = await fetch(API + '/fx/reporting-currency');
        if (!r.ok) return;
        const data = await r.json().catch(() => ({}));
        if (data && data.currency) window.__REPORTING_CURRENCY = data.currency;
      } catch (_) { /* offline / no auth — keep cached value */ }
      applyDefaultFormCurrency();
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
    async function loadFxMetadata(force) {
      if (window.__FX_META && !force) return window.__FX_META;
      try {
        const r = await fetch(API + '/fx/metadata');
        if (!r.ok) return null;
        window.__FX_META = await r.json();
        return window.__FX_META;
      } catch (_) {
        return null;
      }
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

    function formatDateDual(isoDate) {
      if (!isoDate) return '';
      const jalali = toJalali(isoDate);
      return jalali ? `${isoDate} (${jalali})` : isoDate;
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
        amount: 'labelAmount',
        revenue: 'fieldRevenue',
        cost: 'fieldCost',
        profit: 'fieldProfit',
        margin_pct: 'fieldMarginPct',
        current: 'fieldCurrent',
        days_31_60: 'fieldDays31_60',
        days_60_plus: 'fieldDays60Plus',
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
      };
      return map[key] ? t(map[key]) : String(name || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }
