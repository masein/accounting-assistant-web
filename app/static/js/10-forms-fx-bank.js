
    // Invoice dates: due date defaults to issue date + 30 days (net-30) and
    // follows the issue date until the user edits the due date themselves.
    const INVOICE_NET_DAYS = 30;
    let _invDueManuallySet = false;
    function datePlusDays(iso, days) {
      const d = new Date((iso || '') + 'T00:00:00Z');
      if (isNaN(d.getTime())) return iso;
      d.setUTCDate(d.getUTCDate() + days);
      return d.toISOString().slice(0, 10);
    }
    // ─── Tax rates (effective-dated) ───
    let _taxRateCodes = [];
    async function loadTaxRates() {
      try {
        const res = await fetch(API + '/reports/tax-rates');
        if (!res.ok) return;
        const rates = await res.json();
        _taxRateCodes = [...new Set(rates.map(r => r.code))];
        // Populate the invoice tax-code dropdown.
        const sel = document.getElementById('inv-tax-code');
        if (sel) {
          const cur = sel.value;
          sel.innerHTML = `<option value="">${t('taxCodeNone')}</option>`
            + _taxRateCodes.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
          sel.value = cur;
        }
        // Refresh any open builder-row tax-code selects now that codes loaded.
        if (typeof invTaxCodeOptions === 'function') {
          document.querySelectorAll('#inv-items-body .il-code').forEach(s => {
            const cur = s.value; s.innerHTML = invTaxCodeOptions(cur);
          });
        }
        // Render the admin list.
        const body = document.getElementById('tr-list-body');
        if (body) {
          body.innerHTML = rates.length
            ? rates.map(r => `<tr><td>${escapeHtml(r.code)}</td><td>${escapeHtml(r.jurisdiction)}</td>
                <td>${r.rate}%</td><td>${r.effective_from}</td><td>${r.effective_to || '—'}</td></tr>`).join('')
            : `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:0.5rem;">${t('taxNoRates')}</td></tr>`;
        }
      } catch (e) { /* ignore */ }
    }

    async function autofillInvoiceTaxRate() {
      const code = document.getElementById('inv-tax-code').value;
      const on = document.getElementById('inv-issue').value;
      const rateInput = document.getElementById('inv-tax-rate');
      if (!code || !on) return;
      try {
        const res = await fetch(API + '/reports/tax-rates/effective?code=' + encodeURIComponent(code) + '&on=' + encodeURIComponent(on));
        if (!res.ok) return;
        const data = await res.json();
        if (data.rate != null) rateInput.value = data.rate;
      } catch (e) { /* ignore */ }
    }
    document.getElementById('inv-tax-code').addEventListener('change', autofillInvoiceTaxRate);
    document.getElementById('inv-issue').addEventListener('change', () => {
      if (document.getElementById('inv-tax-code').value) autofillInvoiceTaxRate();
    });

    document.getElementById('tr-save').addEventListener('click', async () => {
      const payload = {
        code: document.getElementById('tr-code').value.trim(),
        jurisdiction: document.getElementById('tr-juris').value.trim() || 'XX',
        rate: parseFloat(document.getElementById('tr-rate').value || '0'),
        effective_from: document.getElementById('tr-from').value,
        effective_to: document.getElementById('tr-to').value || null,
      };
      if (!payload.code || !payload.effective_from) { showAlert(t('taxRateNeedFields'), true); return; }
      try {
        const res = await fetch(API + '/reports/tax-rates', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('taxRateSaveFailed'), true); return; }
        showAlert(t('taxRateSaved'));
        await loadTaxRates();
      } catch (e) { showAlert(t('taxRateSaveFailed'), true); }
    });

    function setInvoiceDateDefaults() {
      const today = new Date().toISOString().slice(0, 10);
      document.getElementById('inv-issue').value = today;
      document.getElementById('inv-due').value = datePlusDays(today, INVOICE_NET_DAYS);
      _invDueManuallySet = false;
    }
    setInvoiceDateDefaults();
    document.getElementById('inv-issue').addEventListener('change', () => {
      const issue = document.getElementById('inv-issue').value;
      if (issue && !_invDueManuallySet) {
        document.getElementById('inv-due').value = datePlusDays(issue, INVOICE_NET_DAYS);
      }
    });
    document.getElementById('inv-due').addEventListener('change', () => {
      _invDueManuallySet = true;
    });

    function updateJalaliHint() {
      const dateEl = document.getElementById('date');
      const hintEl = document.getElementById('date-jalali-hint');
      if (dateEl && hintEl) {
        hintEl.textContent = dateEl.value ? toJalali(dateEl.value) : '';
      }
    }
    document.getElementById('date').addEventListener('change', updateJalaliHint);
    updateJalaliHint();
    document.getElementById('budget-month').value = new Date().toISOString().slice(0, 7);
    if (mgrFromDateEl) mgrFromDateEl.value = new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString().slice(0, 10);
    if (mgrToDateEl) mgrToDateEl.value = new Date().toISOString().slice(0, 10);
    if (invFromDateEl) invFromDateEl.value = new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString().slice(0, 10);
    if (invToDateEl) invToDateEl.value = new Date().toISOString().slice(0, 10);
    syncManagerFilterLabels();
    topNav.addEventListener('click', (e) => {
      const btn = e.target.closest('.nav-btn[data-page]');
      if (!btn) return;
      showPage(btn.getAttribute('data-page'));
    });

    // ── Sidebar / top-bar interactions ──────────────────────────────────
    (function wireShell() {
      const sidebar = document.getElementById('sidebar');
      const overlay = document.getElementById('sidebar-overlay');
      const toggle = document.getElementById('nav-toggle');
      const collapse = document.getElementById('sidebar-collapse');
      // Restore desktop collapsed state.
      if (localStorage.getItem('aa_sb_collapsed') === '1') document.body.classList.add('sb-collapsed');
      if (toggle) toggle.addEventListener('click', () => {
        const open = sidebar.classList.toggle('open');
        if (overlay) overlay.classList.toggle('show', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      if (overlay) overlay.addEventListener('click', closeSidebarDrawer);
      const syncCollapseLabel = () => {
        if (!collapse) return;
        const c = document.body.classList.contains('sb-collapsed');
        collapse.setAttribute('aria-label', t(c ? 'expandSidebar' : 'collapseSidebar'));
      };
      if (collapse) collapse.addEventListener('click', () => {
        const c = document.body.classList.toggle('sb-collapsed');
        localStorage.setItem('aa_sb_collapsed', c ? '1' : '0');
        syncCollapseLabel();
      });
      syncCollapseLabel();  // initial (restores correct label for persisted state)
      // User menu dropdown.
      const userBtn = document.getElementById('user-menu-btn');
      const userPop = document.getElementById('user-pop');
      if (userBtn && userPop) {
        userBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          const open = userPop.classList.toggle('open');
          userBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
        document.addEventListener('click', (e) => {
          if (!userPop.contains(e.target) && e.target !== userBtn) {
            userPop.classList.remove('open'); userBtn.setAttribute('aria-expanded', 'false');
          }
        });
      }
      const tbLang = document.getElementById('topbar-language');
      if (tbLang) tbLang.addEventListener('change', () => applyLanguage(tbLang.value));
      const tbLogout = document.getElementById('topbar-logout');
      if (tbLogout) tbLogout.addEventListener('click', async () => {
        try { await fetch(API + '/auth/logout', { method: 'POST' }); } catch (_) {}
        window.location.href = '/login';
      });
      // Esc closes the mobile drawer.
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSidebarDrawer(); });
    })();

    window.addEventListener('hashchange', () => {
      const p = (location.hash || '#dashboard').slice(1);
      showPage(p);
      loadPageData(p);
    });
    applyLanguage(localStorage.getItem('aa_ui_language') || 'en', false);
    // NOTE: the initial showPage/loadPageData bootstrap lives in 16-boot.js —
    // it must run after every script file so all loaders are declared.
    loadCurrentUser();
    loadAIConfig();
    loadAnthropicConfig();
    loadChatProviderShape();
    loadReportingCurrency();
    loadUsers();
    renderAttachments();
    loadLedger();
    loadEntities();
    loadInvoices();
    loadRecurringRules();
    loadOwnerDashboard();
    loadBudgets();
    loadEntityOptions();
    loadManagerInventoryItems();

    // ─── FX settings panel ───────────────────────────────────────────────
    async function loadFxSettings() {
      const statusEl = document.getElementById('fx-reporting-status');
      const selEl = document.getElementById('fx-reporting-currency');
      try {
        const r = await fetch(API + '/fx/reporting-currency');
        if (r.ok) {
          const data = await r.json();
          if (selEl && data.currency && [...selEl.options].some(o => o.value === data.currency)) {
            selEl.value = data.currency;
          }
          if (statusEl) statusEl.textContent = 'Currently: ' + (data.currency || 'IRR');
        }
      } catch (_) {}
      loadFxRates();
    }

    async function loadFxRates() {
      const wrap = document.getElementById('fx-rates-wrap');
      if (!wrap) return;
      try {
        const r = await fetch(API + '/fx/rates');
        if (!r.ok) { wrap.innerHTML = '<p class="empty-state">Failed to load rates.</p>'; return; }
        const rows = await r.json();
        if (!rows.length) { wrap.innerHTML = '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('fxNoRates')) + '</p>'; return; }
        wrap.innerHTML = '<table class="mini-table"><thead><tr><th>From</th><th>To</th><th>Rate</th><th>Effective</th><th>Note</th><th></th></tr></thead><tbody>' +
          rows.map(row => `<tr>
            <td><span class="ccy-badge ccy-${escapeHtml((row.from_currency||'').toUpperCase())}">${escapeHtml(row.from_currency)}</span></td>
            <td><span class="ccy-badge ccy-${escapeHtml((row.to_currency||'').toUpperCase())}">${escapeHtml(row.to_currency)}</span></td>
            <td class="num">${formatNum(row.rate)}</td>
            <td>${escapeHtml(row.effective_date)}</td>
            <td>${escapeHtml(row.note || '')}</td>
            <td><button class="btn btn-secondary btn-sm fx-del-rate" data-id="${escapeHtml(row.id)}">Delete</button></td>
          </tr>`).join('') +
          '</tbody></table>';
        wrap.querySelectorAll('.fx-del-rate').forEach(btn => {
          btn.addEventListener('click', async () => {
            if (!(await uiConfirm({ message: t('confirmDeleteFxRate'), confirmLabel: t('btnDelete'), danger: true }))) return;
            const id = btn.dataset.id;
            const r2 = await fetch(API + '/fx/rates/' + encodeURIComponent(id), { method: 'DELETE' });
            if (r2.ok) loadFxRates();
          });
        });
      } catch (_) {
        wrap.innerHTML = '<p class="empty-state">Error loading rates.</p>';
      }
    }

    const fxSaveReportingBtn = document.getElementById('fx-save-reporting-btn');
    if (fxSaveReportingBtn) {
      fxSaveReportingBtn.addEventListener('click', async () => {
        const sel = document.getElementById('fx-reporting-currency');
        const status = document.getElementById('fx-reporting-status');
        const value = sel ? sel.value : 'IRR';
        const r = await fetch(API + '/fx/reporting-currency', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ currency: value }),
        });
        if (r.ok) {
          const data = await r.json();
          if (status) status.textContent = 'Saved: ' + data.currency;
          // Refresh cached metadata so dropdowns reflect the new default
          await loadFxMetadata(true);
        } else {
          if (status) status.textContent = 'Failed to save.';
        }
      });
    }
    const fxAddRateBtn = document.getElementById('fx-add-rate-btn');
    if (fxAddRateBtn) {
      fxAddRateBtn.addEventListener('click', async () => {
        const from = document.getElementById('fx-from').value.trim();
        const to = document.getElementById('fx-to').value.trim();
        const rate = parseFloat(document.getElementById('fx-rate').value);
        const eff = document.getElementById('fx-effective').value;
        const note = document.getElementById('fx-note').value.trim() || null;
        if (!from || !to || !(rate > 0) || !eff) {
          showAlert('Fill in from, to, a positive rate and an effective date.', true);
          return;
        }
        const r = await fetch(API + '/fx/rates', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ from_currency: from, to_currency: to, rate, effective_date: eff, note }),
        });
        if (r.ok) {
          document.getElementById('fx-from').value = '';
          document.getElementById('fx-to').value = '';
          document.getElementById('fx-rate').value = '';
          document.getElementById('fx-effective').value = '';
          document.getElementById('fx-note').value = '';
          loadFxRates();
        } else {
          const data = await r.json().catch(() => ({}));
          showAlert(data.detail || 'Failed to save rate.', true);
        }
      });
    }
    loadFxSettings();

    // ─── Reporting locale panel ──────────────────────────────────────────
    // Cached so managerEndpointFor() can route reports to Iran endpoints
    // without an extra fetch on every report run.
    window.__REPORTING_LOCALE = 'default';

    async function loadReportingLocale() {
      const selEl = document.getElementById('reporting-locale-select');
      const statusEl = document.getElementById('reporting-locale-status');
      try {
        const r = await fetch(API + '/admin/reporting-locale');
        if (!r.ok) return;
        const data = await r.json();
        const loc = (data && data.locale) || 'default';
        window.__REPORTING_LOCALE = loc;
        if (selEl && [...selEl.options].some(o => o.value === loc)) selEl.value = loc;
        if (statusEl) statusEl.textContent = 'Current: ' + loc;
      } catch (_) {}
    }

    const reportingLocaleSaveBtn = document.getElementById('reporting-locale-save-btn');
    if (reportingLocaleSaveBtn) {
      reportingLocaleSaveBtn.addEventListener('click', async () => {
        const sel = document.getElementById('reporting-locale-select');
        const status = document.getElementById('reporting-locale-status');
        const value = sel ? sel.value : 'default';
        const r = await fetch(API + '/admin/reporting-locale', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ locale: value }),
        });
        if (r.ok) {
          const data = await r.json();
          window.__REPORTING_LOCALE = data.locale;
          if (status) status.textContent = 'Saved: ' + data.locale;
        } else {
          const data = await r.json().catch(() => ({}));
          if (status) status.textContent = (data.detail || 'Failed to save.');
        }
      });
    }

    loadReportingLocale();
    loadDisplayCalendar();

    const displayCalendarSaveBtn = document.getElementById('display-calendar-save-btn');
    if (displayCalendarSaveBtn) {
      displayCalendarSaveBtn.addEventListener('click', async () => {
        const sel = document.getElementById('display-calendar-select');
        const status = document.getElementById('display-calendar-status');
        const value = sel ? sel.value : 'gregorian';
        try {
          const r = await fetch(API + '/admin/display-calendar', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ calendar: value }),
          });
          if (r.ok) {
            const data = await r.json();
            window.__DISPLAY_CALENDAR = data.calendar;
            if (status) status.textContent = 'Saved: ' + data.calendar;
          } else {
            const data = await r.json().catch(() => ({}));
            if (status) status.textContent = (data.detail || 'Failed to save.');
          }
        } catch (e) {
          if (status) status.textContent = 'Error: ' + e.message;
        }
      });
    }

    // ─── Locale demo data: reset DB and post the curated 2-year journal ──
    async function _resetAndLoadLocaleDemo(locale, button) {
      const statusEl = document.getElementById('reset-demo-status');
      const otherId = locale === 'ir' ? 'reset-demo-uk-btn' : 'reset-demo-ir-btn';
      const otherBtn = document.getElementById(otherId);
      const confirmMsg = locale === 'ir'
        ? (t('confirmResetIranian') || 'Reset the database and load the Iranian demo? All current transactions will be deleted.')
        : (t('confirmResetUk') || 'Reset the database and load the UK demo? All current transactions will be deleted.');
      if (!(await uiConfirm({ message: confirmMsg, confirmLabel: t('btnContinue'), danger: true }))) return;
      button.disabled = true;
      if (otherBtn) otherBtn.disabled = true;
      statusEl.style.color = 'var(--text-muted)';
      statusEl.textContent = 'Resetting database and posting demo entries...';
      try {
        const url = API + '/admin/reset-db?locale=' + encodeURIComponent(locale) + '&with_demo_data=true';
        const res = await fetch(url, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          statusEl.style.color = '#2e7d32';
          statusEl.textContent = `Loaded ${data.demo_entries || 0} entries (${data.accounts_created || 0} accounts) for locale "${data.locale}". Refreshing…`;
          window.__REPORTING_LOCALE = data.locale;
          const sel = document.getElementById('reporting-locale-select');
          if (sel && [...sel.options].some(o => o.value === data.locale)) sel.value = data.locale;
          const localeStatus = document.getElementById('reporting-locale-status');
          if (localeStatus) localeStatus.textContent = 'Current: ' + data.locale;
          // Give the user a moment to read, then reload so every panel re-fetches.
          setTimeout(() => window.location.reload(), 1500);
        } else {
          statusEl.style.color = '#c62828';
          statusEl.textContent = data.detail || 'Reset failed.';
        }
      } catch (e) {
        statusEl.style.color = '#c62828';
        statusEl.textContent = 'Error: ' + e.message;
      } finally {
        button.disabled = false;
        if (otherBtn) otherBtn.disabled = false;
      }
    }

    const resetIrBtn = document.getElementById('reset-demo-ir-btn');
    const resetUkBtn = document.getElementById('reset-demo-uk-btn');
    const resetEmptyBtn = document.getElementById('reset-empty-btn');
    if (resetIrBtn) resetIrBtn.addEventListener('click', () => _resetAndLoadLocaleDemo('ir', resetIrBtn));
    if (resetUkBtn) resetUkBtn.addEventListener('click', () => _resetAndLoadLocaleDemo('uk', resetUkBtn));
    if (resetEmptyBtn) {
      resetEmptyBtn.addEventListener('click', async () => {
        const statusEl = document.getElementById('reset-demo-status');
        const localeSel = document.getElementById('reporting-locale-select');
        // Use the currently-selected locale's chart of accounts (fall back to ir).
        const locale = localeSel && (localeSel.value === 'uk' || localeSel.value === 'ir')
          ? localeSel.value
          : 'ir';
        const msg = t('confirmResetEmpty') ||
          'Wipe ALL business data — every transaction, invoice, entity, ' +
          'inventory item, AI proposal, audit log. Chart of accounts and ' +
          'the admin user will be preserved. This is irreversible. Continue?';
        if (!(await uiConfirm({ message: msg, confirmLabel: t('btnContinue'), danger: true }))) return;
        resetEmptyBtn.disabled = true;
        if (resetIrBtn) resetIrBtn.disabled = true;
        if (resetUkBtn) resetUkBtn.disabled = true;
        statusEl.style.color = 'var(--text-muted)';
        statusEl.textContent = (t('statusResetting') || 'Resetting database…');
        try {
          // with_demo_data defaults to false → empty start, chart only.
          const url = API + '/admin/reset-db?locale=' + encodeURIComponent(locale) +
                      '&with_demo_data=false';
          const r = await fetch(url, { method: 'POST' });
          const data = await r.json().catch(() => ({}));
          if (r.ok) {
            statusEl.style.color = '#059669';
            statusEl.textContent = (t('statusResetDone') || 'Database reset.') +
              ` Chart: ${data.accounts_created || 0} accounts. Reloading…`;
            setTimeout(() => window.location.reload(), 1200);
          } else {
            statusEl.style.color = '#b91c1c';
            statusEl.textContent = data.detail || 'Reset failed.';
          }
        } catch (e) {
          statusEl.style.color = '#b91c1c';
          statusEl.textContent = 'Connection error: ' + e.message;
        } finally {
          resetEmptyBtn.disabled = false;
          if (resetIrBtn) resetIrBtn.disabled = false;
          if (resetUkBtn) resetUkBtn.disabled = false;
        }
      });
    }

    // Kick off FX metadata fetch so currency defaults land before the user interacts.
    loadFxMetadata().then(meta => {
      if (!meta) return;
      const pref = meta.reporting_currency || meta.most_common_currency || 'IRR';
      // Voucher form: pick the reporting currency as default
      const txnSel = document.getElementById('txn-currency');
      if (txnSel && [...txnSel.options].some(o => o.value === pref)) {
        txnSel.value = pref;
      }
      // Manager reports: default to the most common currency in data
      const mgrSel = document.getElementById('mgr-currency');
      if (mgrSel) {
        const mgrPref = meta.most_common_currency || pref;
        if ([...mgrSel.options].some(o => o.value === mgrPref)) {
          mgrSel.value = mgrPref;
        }
      }
      // Excel import form: default to most common currency too
      const impSel = document.getElementById('excel-import-currency');
      if (impSel && [...impSel.options].some(o => o.value === (meta.most_common_currency || pref))) {
        impSel.value = meta.most_common_currency || pref;
      }
    });

    // ═══════ Bank Statement Module ═══════
    const bsAPI = API + '/brain';

    // Reflect real OCR-engine availability in the note: if PyMuPDF isn't
    // installed in the running image, PDF/image scanning can't work until a
    // rebuild — say so instead of implying a Settings tweak would fix it.
    async function refreshOcrNote() {
      const note = document.getElementById('bs-ocr-note');
      if (!note) return;
      try {
        const res = await fetch(bsAPI + '/ocr-health');
        if (!res.ok) return;
        const data = await res.json();
        if (data && data.ocr_available === false) {
          note.textContent = t('bsOcrUnavailable');
          note.style.color = '#c62828';
        } else {
          note.textContent = t('bsOcrNote');
          note.style.color = '#f57f17';
        }
      } catch (_) { /* leave the default note */ }
    }

    async function loadBankStatements() {
      refreshOcrNote();
      try {
        const res = await fetch(bsAPI + '/bank-statements?limit=20');
        if (!res.ok) return;
        const stmts = await res.json();
        const body = document.getElementById('bs-list-body');
        body.innerHTML = '';
        if (!stmts.length) {
          const tr = document.createElement('tr');
          tr.innerHTML = '<td colspan="7" style="text-align:center;color:var(--text-muted);padding:1.5rem;">No bank statements uploaded yet. Upload a CSV, Excel, or scanned PDF above.</td>';
          body.appendChild(tr);
          return;
        }
        stmts.forEach(s => {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${escapeHtml(s.bank_name)}</td><td>${escapeHtml(s.source_filename)}</td>
            <td>${s.source_type}</td><td>${s.total_rows}</td><td>${s.matched_rows || 0}</td>
            <td><span class="badge ${s.status === 'approved' ? 'badge-ok' : ''}">${s.status}</span></td>
            <td><button class="btn btn-secondary btn-sm bs-view-btn" data-id="${s.id}">View</button></td>`;
          body.appendChild(tr);
        });
      } catch (e) { console.warn('Failed to load bank statements:', e); }
    }

    // Ask the user to map detected headers → required roles when the parser
    // can't auto-detect columns. Returns a {role: colIndex} object or null.
    async function promptColumnMapping(headers, requiredFields) {
      const fieldLabel = { date: 'fieldDate', amount: 'fieldAmount', description: 'fieldDescription' };
      const headerList = (headers || []).map((h, i) => `${i}=${h}`).join(', ');
      const max = Math.max(0, (headers || []).length - 1);
      const map = {};
      for (const field of (requiredFields || [])) {
        const label = fieldLabel[field] ? t(fieldLabel[field]) : field;
        const ans = await uiPrompt({
          title: t('bsMapTitle'),
          message: tf('bsMapAsk', { field: label, max, headers: headerList }),
          type: 'number',
        });
        if (ans === null) return null;           // cancelled the whole mapping
        const idx = parseInt(ans, 10);
        if (!isNaN(idx) && idx >= 0 && idx <= max) map[field] = idx;
      }
      // Need at least a date and an amount to import.
      if (!('date' in map) || !('amount' in map)) { showAlert(t('bsMapTitle'), true); return null; }
      return map;
    }

    async function doUploadStatement(file, bankName, { columnMap = null, confirmDuplicate = false } = {}) {
      const statusEl = document.getElementById('bs-upload-status');
      statusEl.style.display = 'block';
      statusEl.textContent = t('aiThinking') || 'Uploading and parsing...';
      statusEl.className = 'alert';
      const form = new FormData();
      form.append('file', file);
      let url = bsAPI + '/bank-statements/upload?bank_name=' + encodeURIComponent(bankName);
      if (columnMap) url += '&column_map=' + encodeURIComponent(JSON.stringify(columnMap));
      if (confirmDuplicate) url += '&confirm_duplicate=true';
      try {
        const res = await fetch(url, { method: 'POST', body: form });
        const data = await readJsonSafe(res);
        if (!res.ok || data._nonJson) {
          statusEl.textContent = (data && data.detail) ? data.detail : t('bsParseFailed');
          statusEl.className = 'alert alert-error';
          return;
        }
        // Unknown layout → ask the user to map columns, then re-upload.
        if (data.needs_mapping) {
          const map = await promptColumnMapping(data.headers, data.required_fields);
          if (!map) { statusEl.style.display = 'none'; return; }
          return doUploadStatement(file, bankName, { columnMap: map, confirmDuplicate });
        }
        // Identical file already imported → confirm before re-importing.
        if (data.duplicate) {
          const ok = await uiConfirm({ title: t('bsDupTitle'), message: t('bsDupConfirmMsg') });
          if (!ok) { statusEl.style.display = 'none'; return; }
          return doUploadStatement(file, bankName, { columnMap, confirmDuplicate: true });
        }
        let msg = tf('bsParsed', { rows: data.total_rows, bank: data.bank_name, type: data.source_type });
        if (data.skipped_rows) msg += ' ' + tf('bsSkipped', { n: data.skipped_rows });
        statusEl.textContent = msg;
        statusEl.className = 'alert';
        loadBankStatements();
      } catch (e) { statusEl.textContent = t('bsParseFailed'); statusEl.className = 'alert alert-error'; }
    }

    document.getElementById('bs-upload-btn').addEventListener('click', async () => {
      const fileInput = document.getElementById('bs-file-input');
      const bankName = document.getElementById('bs-bank-name').value.trim() || 'Unknown';
      if (!fileInput.files.length) { showAlert('Select a file first.', true); return; }
      await doUploadStatement(fileInput.files[0], bankName);
    });

    let currentStatementId = null;

    document.getElementById('bs-list-body').addEventListener('click', async (e) => {
      const btn = e.target.closest('.bs-view-btn');
      if (!btn) return;
      currentStatementId = btn.dataset.id;
      await loadStatementDetail(currentStatementId);
    });

    async function loadStatementDetail(id) {
      try {
        const res = await fetch(bsAPI + '/bank-statements/' + id);
        if (!res.ok) return;
        const stmt = await res.json();
        document.getElementById('bs-detail-title').textContent = `${stmt.bank_name} — ${stmt.source_filename} (${stmt.total_rows} rows)`;
        const body = document.getElementById('bs-rows-body');
        body.innerHTML = '';
        stmt.rows.forEach(r => {
          const confColor = r.confidence >= 0.85 ? '#2e7d32' : r.confidence >= 0.6 ? '#f57f17' : '#c62828';
          const statusBg = r.recon_status === 'matched' ? '#e8f5e9' : r.recon_status === 'duplicate' ? '#fff3e0' : '';
          const tr = document.createElement('tr');
          tr.style.background = statusBg;
          tr.innerHTML = `<td>${r.row_index}</td><td>${r.tx_date}</td><td dir="auto">${escapeHtml(r.description || '')}</td>
            <td style="color:#1565c0;">${r.debit ? r.debit.toLocaleString() : ''}</td>
            <td style="color:#2e7d32;">${r.credit ? r.credit.toLocaleString() : ''}</td>
            <td>${r.balance != null ? r.balance.toLocaleString() : ''}</td>
            <td>${escapeHtml(r.category || '—')}</td>
            <td style="color:${confColor}">${(r.confidence * 100).toFixed(0)}%</td>
            <td>${r.recon_status}</td>
            <td>${r.recon_status === 'unmatched' ? `<button class="btn btn-secondary btn-sm bs-create-btn" data-row-id="${r.id}" data-code="${r.suggested_account_code || ''}">Create</button>` : r.user_approved ? '✓' : `<button class="btn btn-secondary btn-sm bs-approve-btn" data-row-id="${r.id}">Approve</button>`}</td>`;
          body.appendChild(tr);
        });
        document.getElementById('bs-list-wrap').style.display = 'none';
        document.getElementById('bs-detail-wrap').style.display = 'block';
      } catch (e) { showAlert('Failed to load statement: ' + e.message, true); }
    }

    document.getElementById('bs-back-btn').addEventListener('click', () => {
      document.getElementById('bs-detail-wrap').style.display = 'none';
      document.getElementById('bs-list-wrap').style.display = 'block';
    });

    function renderReconSummary(data) {
      const el = document.getElementById('bs-recon-summary');
      el.style.display = 'block';
      el.innerHTML = '';
      const line = document.createElement('div');
      line.textContent = tf('bsReconSummary', {
        matched: data.matched, partial: data.partial, unmatched: data.unmatched,
        duplicates: data.duplicates, auto: data.auto_matched, missing: data.missing_in_bank,
      });
      el.appendChild(line);

      // Exact unreconciled difference — reported as-is, never force-balanced.
      const diff = data.unreconciled_difference || 0;
      const diffEl = document.createElement('div');
      diffEl.style.cssText = 'margin-top:0.35rem;font-weight:600;color:' + (diff === 0 ? '#2e7d32' : '#c62828') + ';';
      diffEl.textContent = t('bsUnreconciledDiff') + ': ' + formatNum(diff) + ' ' + (data.currency || currencyUnit());
      el.appendChild(diffEl);

      // Bank-fee / interest suggestions — confirm-gated "Record" buttons.
      const sugs = data.fee_suggestions || [];
      if (sugs.length) {
        const title = document.createElement('div');
        title.style.cssText = 'margin-top:0.5rem;font-weight:600;';
        title.textContent = t('bsFeeSuggestTitle');
        el.appendChild(title);
        sugs.forEach(s => {
          const row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:center;gap:0.5rem;margin-top:0.3rem;';
          const span = document.createElement('span');
          span.dir = 'auto';
          span.style.flex = '1';
          span.textContent = `${s.tx_date} · ${s.description || ''} · ${formatNum(s.amount)} ${data.currency || currencyUnit()} → ${s.account_code} ${s.account_name}`;
          const btn = document.createElement('button');
          btn.className = 'btn btn-secondary btn-sm';
          btn.textContent = t('bsRecordBtn');
          btn.onclick = () => recordFeeSuggestion(s);
          row.appendChild(span);
          row.appendChild(btn);
          el.appendChild(row);
        });
      }
    }

    async function recordFeeSuggestion(s) {
      const ok = await uiConfirm({
        title: t('bsRecordConfirmTitle'),
        message: tf('bsRecordConfirmMsg', { desc: s.description || '', account: s.account_code + ' ' + s.account_name }),
      });
      if (!ok) return;
      try {
        const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/approve', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approvals: [{ row_id: s.row_id, action: 'create', account_code: s.account_code }] }),
        });
        const data = await res.json();
        if (data.errors && data.errors.length) { showAlert(data.errors.join('; '), true); }
        else { showAlert(tf('bsRecorded', { n: data.created })); }
        // Re-run reconciliation so the now-booked line drops out of suggestions.
        const rr = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/reconcile', { method: 'POST' });
        renderReconSummary(await rr.json());
        await loadStatementDetail(currentStatementId);
      } catch (e) { showAlert('Record failed: ' + e.message, true); }
    }

    document.getElementById('bs-reconcile-btn').addEventListener('click', async () => {
      if (!currentStatementId) return;
      try {
        const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/reconcile', { method: 'POST' });
        renderReconSummary(await res.json());
        await loadStatementDetail(currentStatementId);
      } catch (e) { showAlert('Reconciliation failed: ' + e.message, true); }
    });

    document.getElementById('bs-approve-all-btn').addEventListener('click', async () => {
      if (!currentStatementId) return;
      const btns = document.querySelectorAll('.bs-approve-btn');
      const approvals = Array.from(btns).map(b => ({ row_id: b.dataset.rowId, action: 'approve' }));
      if (!approvals.length) { showAlert('No rows to approve.'); return; }
      try {
        const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/approve', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approvals })
        });
        const data = await res.json();
        showAlert(`Approved: ${data.approved}, Created: ${data.created}. ${data.errors.join('; ')}`);
        await loadStatementDetail(currentStatementId);
      } catch (e) { showAlert('Approval failed: ' + e.message, true); }
    });

    document.getElementById('bs-rows-body').addEventListener('click', async (e) => {
      const createBtn = e.target.closest('.bs-create-btn');
      const approveBtn = e.target.closest('.bs-approve-btn');
      if (createBtn) {
        const rowId = createBtn.dataset.rowId;
        const code = createBtn.dataset.code ||
          await uiPrompt({ title: t('promptAccountCodeTitle'), message: t('promptAccountCodeMsg'), value: '6190' });
        if (!code) return;
        try {
          const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/approve', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approvals: [{ row_id: rowId, action: 'create', account_code: code }] })
          });
          const data = await res.json();
          showAlert(`Created: ${data.created}. ${data.errors.join('; ')}`);
          await loadStatementDetail(currentStatementId);
        } catch (e) { showAlert('Create failed: ' + e.message, true); }
      }
      if (approveBtn) {
        try {
          const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/approve', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approvals: [{ row_id: approveBtn.dataset.rowId, action: 'approve' }] })
          });
          await loadStatementDetail(currentStatementId);
        } catch (e) { showAlert('Approve failed.', true); }
      }
    });
