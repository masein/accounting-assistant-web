
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
                <td>${r.rate}%</td><td>${escapeHtml(formatDisplayDate(r.effective_from))}</td><td>${escapeHtml(formatDisplayDate(r.effective_to) || '—')}</td></tr>`).join('')
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
      const today = localIsoDate();
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
    document.getElementById('budget-month').value = currentMonthKey();
    document.getElementById('budget-month').addEventListener('change', loadBudgets);
    if (mgrFromDateEl) mgrFromDateEl.value = mgrFromDateEl.dataset.auto = monthStartIso();
    if (mgrToDateEl) mgrToDateEl.value = localIsoDate();
    if (invFromDateEl) invFromDateEl.value = invFromDateEl.dataset.auto = monthStartIso();
    if (invToDateEl) invToDateEl.value = localIsoDate();
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
      if (tbLang) tbLang.addEventListener('change', () => switchLanguage(tbLang.value));
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
      // the page shown — a role sent to its home instead of p loads that one
      loadPageData(activePage() || p);
    });
    applyLanguage(localStorage.getItem('aa_ui_language') || 'en', false);
    // Boot fetches the shell only: who is signed in (role, company, language),
    // the reporting currency, and — below — the reporting locale, the display
    // calendar and the currency defaults every form uses. Each page's data
    // comes from loadPageData when that page is shown; the first one is
    // loaded by 16-boot.js once userReady settles, because which page a role
    // lands on depends on it. (Every page's lists used to load here at each
    // sign-in — about 40 requests, the dashboard twice, and a row of 403s for
    // roles that can't open those pages.)
    const userReady = loadCurrentUser();
    loadReportingCurrency();
    renderAttachments();

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
          if (statusEl) statusEl.textContent = t('msgCurrently') + (data.currency || 'IRR');
        }
      } catch (_) {}
      loadFxRates();
      renderFxUnconverted();
    }

    // Entries with no rate yet (roadmap §4.6). IRR ones in a company whose
    // base is not IRR are most likely pounds (etc.) saved under the old IRR
    // default: offer to relabel them.
    async function renderFxUnconverted() {
      const box = document.getElementById('fx-unconverted');
      if (!box) return;
      const meta = await loadFxMetadata(true);
      const u = meta && meta.unconverted;
      if (!u || !u.count) { box.hidden = true; box.innerHTML = ''; return; }
      const base = meta.reporting_currency || '';
      let html = '<p>' + escapeHtml(tf('fxUnconvertedNote', { n: u.count, currencies: (u.currencies || []).join(', '), base })) + '</p>';
      const suspect = (u.currencies || []).includes('IRR') && base && base !== 'IRR';
      if (suspect) {
        html += '<p>' + escapeHtml(tf('fxRelabelHint', { base })) + '</p>'
          + '<button type="button" class="btn btn-secondary btn-sm" id="fx-relabel-btn">' + escapeHtml(tf('fxRelabelBtn', { base })) + '</button>';
      }
      box.innerHTML = html;
      box.hidden = false;
      const btn = document.getElementById('fx-relabel-btn');
      if (btn) btn.addEventListener('click', async () => {
        const post = (apply) => fetch(API + '/fx/relabel', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ from_currency: 'IRR', apply }),
        }).then(r => r.json().then(d => ({ ok: r.ok, d })));
        try {
          const dry = await post(false);
          if (!dry.ok) { showAlert(dry.d.detail || t('fxFeedsFailed'), true); return; }
          const msg = tf('fxRelabelConfirm', { entries: dry.d.entries, invoices: dry.d.invoices, base: dry.d.to_currency })
            + (dry.d.locked ? ' ' + tf('fxRelabelLocked', { n: dry.d.locked }) : '');
          if (!(await uiConfirm({ message: msg, confirmLabel: tf('fxRelabelBtn', { base: dry.d.to_currency }) }))) return;
          const done = await post(true);
          if (!done.ok) { showAlert(done.d.detail || t('fxFeedsFailed'), true); return; }
          showAlert(tf('fxRelabelDone', { entries: done.d.entries, base: done.d.to_currency }));
          loadFxSettings();
          if (typeof loadLedger === 'function') loadLedger();
        } catch (_) { showAlert(t('fxFeedsFailed'), true); }
      });
    }

    let _fxRatesRows = null;
    let _fxRatesAllShared = false;
    const FX_SHARED_SHOWN = 8;

    async function loadFxRates() {
      const wrap = document.getElementById('fx-rates-wrap');
      if (!wrap) return;
      const history = document.getElementById('fx-rates-history');
      try {
        const r = await fetch(API + '/fx/rates' + (history && history.checked ? '' : '?latest=true'));
        if (!r.ok) { wrap.innerHTML = '<p class="empty-state">' + escapeHtml(t('fxRatesLoadFailed')) + '</p>'; return; }
        _fxRatesRows = await r.json();
        renderFxRates();
      } catch (_) {
        wrap.innerHTML = '<p class="empty-state">' + escapeHtml(t('fxRatesLoadFailed')) + '</p>';
      }
    }

    function renderFxRates() {
      const wrap = document.getElementById('fx-rates-wrap');
      if (!wrap || !_fxRatesRows) return;
      const q = ((document.getElementById('fx-rates-filter') || {}).value || '').trim().toUpperCase();
      const match = (row) => !q || (row.from_currency || '').includes(q) || (row.to_currency || '').includes(q);
      const rows = _fxRatesRows.filter(match);
      if (!_fxRatesRows.length) { wrap.innerHTML = '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('fxNoRates')) + '</p>'; return; }
      // The company's own rates first; the shared ones (dozens, from the
      // daily feeds) folded after a few unless asked for or filtered.
      const own = rows.filter(r => !r.shared);
      const shared = rows.filter(r => r.shared);
      const showShared = (_fxRatesAllShared || q) ? shared : shared.slice(0, FX_SHARED_SHOWN);
      const canDelete = (row) => !row.shared || (typeof isSuperadmin !== 'undefined' && isSuperadmin);
      const line = (row) => `<tr>
            <td><span class="ccy-badge ccy-${escapeHtml((row.from_currency||'').toUpperCase())}">${escapeHtml(row.from_currency)}</span></td>
            <td><span class="ccy-badge ccy-${escapeHtml((row.to_currency||'').toUpperCase())}">${escapeHtml(row.to_currency)}</span></td>
            <td class="num">${escapeHtml(formatRate(row.rate))}</td>
            <td>${escapeHtml(formatDisplayDate(row.effective_date))}</td>
            <td>${row.shared ? '<span class="fx-shared">' + escapeHtml(row.source ? tf('fxSharedFrom', { source: row.source === 'ecb' ? 'ECB' : row.source }) : t('fxShared')) + '</span> ' : ''}${escapeHtml(row.source ? '' : (row.note || ''))}</td>
            <td>${canDelete(row) ? `<button class="btn btn-secondary btn-sm fx-del-rate" data-id="${escapeHtml(row.id)}">${escapeHtml(t('btnDelete'))}</button>` : ''}</td>
          </tr>`;
      const more = shared.length - showShared.length;
      wrap.innerHTML = (rows.length ? '<table class="mini-table"><thead><tr><th>' + escapeHtml(t('fxFrom')) + '</th><th>' + escapeHtml(t('fxTo')) +
          '</th><th>' + escapeHtml(t('fxRateCol')) + '</th><th>' + escapeHtml(t('fxEffective')) + '</th><th>' + escapeHtml(t('fxNote')) + '</th><th></th></tr></thead><tbody>' +
          own.map(line).join('') + showShared.map(line).join('') + '</tbody></table>'
          : '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('fxNoRatesMatch')) + '</p>') +
        (more > 0 ? `<button type="button" class="btn btn-secondary btn-sm" id="fx-rates-more" style="margin-top:0.4rem;">${escapeHtml(tf('fxShowAllShared', { n: shared.length }))}</button>` : '');
      const moreBtn = document.getElementById('fx-rates-more');
      if (moreBtn) moreBtn.addEventListener('click', () => { _fxRatesAllShared = true; renderFxRates(); });
      wrap.querySelectorAll('.fx-del-rate').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!(await uiConfirm({ message: t('confirmDeleteFxRate'), confirmLabel: t('btnDelete'), danger: true }))) return;
          const id = btn.dataset.id;
          const r2 = await fetch(API + '/fx/rates/' + encodeURIComponent(id), { method: 'DELETE' });
          if (r2.ok) loadFxRates();
          else {
            const d = await r2.json().catch(() => ({}));
            showAlert(d.detail || t('fxFeedsFailed'), true);
          }
        });
      });
    }
    const fxRatesFilter = document.getElementById('fx-rates-filter');
    if (fxRatesFilter) fxRatesFilter.addEventListener('input', renderFxRates);
    const fxHistoryToggle = document.getElementById('fx-rates-history');
    if (fxHistoryToggle) fxHistoryToggle.addEventListener('change', loadFxRates);

    // ─── Automatic rates (platform admin; roadmap §4.6) ─────────────────
    let _fxFeeds = null;   // {enabled, ecb, hour, feeds: [{name, url, enabled, items: [...]}]}
    const _fxFound = {};   // feed index → numbers found by the last Test
    let _fxFeedStatus = null;
    function fxRelocalize() {
      if (_fxRatesRows) renderFxRates();
      if (_fxFeeds) { renderRateFeeds(); renderRateFeedStatus(_fxFeedStatus || {}); }
    }

    async function loadRateFeeds() {
      const card = document.getElementById('fx-feeds-card');
      if (!card) return;
      try {
        const res = await fetch(API + '/admin/rate-feeds');
        if (!res.ok) { card.style.display = 'none'; return; }
        const d = await res.json();
        card.style.display = '';
        _fxFeeds = { enabled: !!d.enabled, ecb: !!d.ecb, hour: d.hour, feeds: d.feeds || [] };
        document.getElementById('fx-feeds-enabled').checked = _fxFeeds.enabled;
        document.getElementById('fx-feeds-ecb').checked = _fxFeeds.ecb;
        const hour = document.getElementById('fx-feeds-hour');
        if (!hour.options.length) {
          for (let h = 0; h < 24; h++) hour.add(new Option(String(h).padStart(2, '0') + ':00', String(h)));
        }
        hour.value = String(_fxFeeds.hour);
        document.getElementById('fx-feeds-ecb-list').textContent = (d.ecb_currencies || []).join(' · ');
        renderRateFeeds();
        renderRateFeedStatus(d.status || {});
      } catch (_) { card.style.display = 'none'; }
      loadFxRates();   // shared rows get a Delete button for the platform admin
    }

    // tf() with each value isolated left-to-right: codes, counts and dates keep
    // their order inside a Persian or Arabic sentence.
    function tfBdi(key, vars) {
      return escapeHtml(t(key)).replace(/\{(\w+)\}/g, (m, k) =>
        (k in vars) ? '<bdi dir="ltr">' + escapeHtml(String(vars[k])) + '</bdi>' : m);
    }

    function renderRateFeedStatus(st) {
      _fxFeedStatus = st;
      const el = document.getElementById('fx-feeds-status');
      if (!el) return;
      if (!st.ran_at) { el.textContent = t('fxFeedsNever'); return; }
      const when = new Date(st.ran_at);
      const items = (st.results || []).map(r => {
        const src = '<bdi>' + escapeHtml(r.source === 'ecb' ? 'ECB' : r.source) + '</bdi>';
        if (r.error) return `<li class="err">${src}: ${escapeHtml(t('fxFeedsFailed'))} — <bdi dir="ltr">${escapeHtml(r.error)}</bdi></li>`;
        let line = src + ': ' + tfBdi('fxFeedsResult', { added: r.added || 0, updated: r.updated || 0 });
        if (r.kept) line += ', ' + tfBdi('fxFeedsKept', { kept: r.kept });
        if (r.date) line += ' · ' + tfBdi('fxFeedsRatesOf', { date: r.date });
        if ((r.errors || []).length) line += ` <span class="err">(<bdi dir="ltr">${escapeHtml(r.errors.join('; '))}</bdi>)</span>`;
        return '<li>' + line + '</li>';
      });
      el.innerHTML = tfBdi('fxFeedsLastRun', { when: isNaN(when) ? st.ran_at : when.toLocaleString() }) +
        (items.length ? '<ul>' + items.join('') + '</ul>' : '');
    }

    function renderRateFeeds() {
      const list = document.getElementById('fx-feeds-list');
      if (!list || !_fxFeeds) return;
      list.innerHTML = _fxFeeds.feeds.map((f, i) => `
        <div class="fx-feed" data-i="${i}">
          <div class="fx-feed-top">
            <div><label for="fx-feed-name-${i}">${escapeHtml(t('fxFeedsName'))}</label>
              <input type="text" id="fx-feed-name-${i}" data-k="name" maxlength="60" value="${escapeHtml(f.name || '')}" placeholder="Navasan"></div>
            <div><label for="fx-feed-url-${i}">${escapeHtml(t('fxFeedsUrl'))}</label>
              <input type="url" id="fx-feed-url-${i}" data-k="url" dir="ltr" maxlength="1024" value="${escapeHtml(f.url || '')}" placeholder="https://…?api_key=…"></div>
            <label class="fx-check"><input type="checkbox" data-k="enabled" ${f.enabled !== false ? 'checked' : ''}> ${escapeHtml(t('fxFeedsFeedOn'))}</label>
            <button type="button" class="btn btn-secondary btn-sm fx-feed-test">${escapeHtml(t('fxFeedsTest'))}</button>
            <button type="button" class="btn btn-secondary btn-sm fx-feed-remove">${escapeHtml(t('fxFeedsRemove'))}</button>
          </div>
          <div class="fx-feed-items fx-stack">
            ${(f.items || []).length ? '' : '<p class="fx-hint" style="margin:0 0 0.3rem 0;">' + escapeHtml(t('fxFeedsNoItems')) + '</p>'}
            <table class="mini-table"${(f.items || []).length ? '' : ' hidden'}><thead><tr><th>${escapeHtml(t('fxFeedsUnit'))}</th><th>${escapeHtml(t('fxFeedsPricedIn'))}</th><th>${escapeHtml(t('fxFeedsPath'))}</th><th>×</th><th></th></tr></thead>
            <tbody>${(f.items || []).map((it, j) => `<tr data-j="${j}">
              <td class="fx-code" data-label="${escapeHtml(t('fxFeedsUnit'))}"><input type="text" data-ik="unit" dir="ltr" maxlength="16" value="${escapeHtml(it.unit || '')}" placeholder="GOLDG" aria-label="${escapeHtml(t('fxFeedsUnit'))}"></td>
              <td class="fx-code" data-label="${escapeHtml(t('fxFeedsPricedIn'))}"><input type="text" data-ik="to" dir="ltr" maxlength="16" value="${escapeHtml(it.to || '')}" placeholder="IRR" aria-label="${escapeHtml(t('fxFeedsPricedIn'))}"></td>
              <td class="fx-path" data-label="${escapeHtml(t('fxFeedsPath'))}"><input type="text" data-ik="path" dir="ltr" maxlength="120" value="${escapeHtml(it.path || '')}" placeholder="data.gold18.price" aria-label="${escapeHtml(t('fxFeedsPath'))}"></td>
              <td class="fx-mult" data-label="${escapeHtml(t('fxFeedsMultiply'))}" style="width:5.5rem;"><input type="number" data-ik="multiply" dir="ltr" min="0" step="any" value="${escapeHtml(String(it.multiply ?? 1))}" aria-label="${escapeHtml(t('fxFeedsMultiply'))}"></td>
              <td class="fx-rm" style="width:1%;"><button type="button" class="btn btn-secondary btn-sm fx-item-remove" aria-label="${escapeHtml(t('fxFeedsRemove'))}">✕</button></td>
            </tr>`).join('')}</tbody></table>
            <button type="button" class="btn btn-secondary btn-sm fx-item-add" style="margin-top:0.35rem;">${escapeHtml(t('fxFeedsAddItem'))}</button>
          </div>
          <div class="fx-feed-found" id="fx-feed-found-${i}">${renderFound(i)}</div>
        </div>`).join('');
      list.querySelectorAll('.fx-feed').forEach(card => {
        const i = Number(card.dataset.i);
        const feed = _fxFeeds.feeds[i];
        card.querySelectorAll('[data-k]').forEach(inp => inp.addEventListener('input', () => {
          feed[inp.dataset.k] = inp.type === 'checkbox' ? inp.checked : inp.value;
        }));
        card.querySelectorAll('tr[data-j]').forEach(tr => {
          const it = feed.items[Number(tr.dataset.j)];
          tr.querySelectorAll('[data-ik]').forEach(inp => inp.addEventListener('input', () => {
            const k = inp.dataset.ik;
            it[k] = (k === 'unit' || k === 'to') ? inp.value.toUpperCase().trim() : inp.value.trim();
          }));
          tr.querySelector('.fx-item-remove').addEventListener('click', () => {
            feed.items.splice(Number(tr.dataset.j), 1); renderRateFeeds();
          });
        });
        card.querySelector('.fx-item-add').addEventListener('click', () => {
          feed.items.push({ unit: '', to: 'IRR', path: '', multiply: 1 }); renderRateFeeds();
        });
        card.querySelector('.fx-feed-remove').addEventListener('click', async () => {
          if (!(await uiConfirm({ message: tf('fxFeedsRemoveConfirm', { name: feed.name || '—' }), confirmLabel: t('fxFeedsRemove'), danger: true }))) return;
          _fxFeeds.feeds.splice(i, 1); delete _fxFound[i]; renderRateFeeds();
        });
        card.querySelector('.fx-feed-test').addEventListener('click', async (ev) => {
          const btn = ev.currentTarget;
          btn.disabled = true;
          try {
            const res = await fetch(API + '/admin/rate-feeds/test', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ url: feed.url || '', name: feed.name || null }),
            });
            _fxFound[i] = await res.json().catch(() => ({ ok: false, error: 'error' }));
          } catch (_) { _fxFound[i] = { ok: false, error: 'error' }; }
          btn.disabled = false;
          const box = document.getElementById('fx-feed-found-' + i);
          if (box) { box.innerHTML = renderFound(i); wireFound(i, box); }
        });
        const box = document.getElementById('fx-feed-found-' + i);
        if (box) wireFound(i, box);
      });
    }

    function renderFound(i) {
      const f = _fxFound[i];
      if (!f) return '';
      if (!f.ok) return `<span class="err" style="color:var(--danger);">${escapeHtml(t('fxFeedsFailed'))}: ${escapeHtml(f.error || '')}</span>`;
      if (!(f.numbers || []).length) return escapeHtml(t('fxFeedsNothingFound'));
      return '<div>' + escapeHtml(tf('fxFeedsFound', { n: f.numbers.length })) + '</div><table class="mini-table"><tbody>' +
        f.numbers.map((n, k) => `<tr><td dir="ltr"><code>${escapeHtml(n.path)}</code></td><td class="num">${escapeHtml(formatRate(n.value))}</td>
          <td style="width:1%;"><button type="button" class="btn btn-secondary btn-sm fx-use" data-k="${k}">${escapeHtml(t('fxFeedsUse'))}</button></td></tr>`).join('') +
        '</tbody></table>';
    }

    function wireFound(i, box) {
      box.querySelectorAll('.fx-use').forEach(b => b.addEventListener('click', () => {
        const n = _fxFound[i].numbers[Number(b.dataset.k)];
        _fxFeeds.feeds[i].items.push({ unit: '', to: 'IRR', path: n.path, multiply: 1 });
        renderRateFeeds();
        const rows = document.querySelectorAll(`.fx-feed[data-i="${i}"] tr[data-j] [data-ik="unit"]`);
        if (rows.length) rows[rows.length - 1].focus();
      }));
    }

    (function wireRateFeeds() {
      const add = document.getElementById('fx-feeds-add');
      if (!add) return;
      add.addEventListener('click', () => {
        if (!_fxFeeds) return;
        _fxFeeds.feeds.push({ name: '', url: '', enabled: true, items: [] });
        renderRateFeeds();
      });
      document.getElementById('fx-feeds-save').addEventListener('click', async (ev) => {
        const btn = ev.currentTarget;
        const msg = document.getElementById('fx-feeds-msg');
        if (!_fxFeeds) return;
        const body = {
          enabled: document.getElementById('fx-feeds-enabled').checked,
          ecb: document.getElementById('fx-feeds-ecb').checked,
          hour: Number(document.getElementById('fx-feeds-hour').value || 7),
          feeds: _fxFeeds.feeds.map(f => ({ ...f, items: (f.items || []).map(it => ({ ...it, multiply: Number(it.multiply) || 0 })) })),
        };
        btn.disabled = true;
        try {
          const res = await fetch(API + '/admin/rate-feeds', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
          });
          const d = await res.json().catch(() => ({}));
          if (!res.ok) {
            msg.textContent = typeof d.detail === 'string' ? d.detail : t('fxFeedsFailed');
            return;
          }
          msg.textContent = t('fxFeedsSaved');
          await loadRateFeeds();
        } catch (_) { msg.textContent = t('fxFeedsFailed'); } finally { btn.disabled = false; }
      });
      document.getElementById('fx-feeds-run').addEventListener('click', async (ev) => {
        const btn = ev.currentTarget;
        const msg = document.getElementById('fx-feeds-msg');
        btn.disabled = true;
        msg.textContent = t('fxFeedsRunning');
        try {
          const res = await fetch(API + '/admin/rate-feeds/run', { method: 'POST' });
          const d = await res.json().catch(() => ({}));
          msg.textContent = res.ok ? '' : (typeof d.detail === 'string' ? d.detail : t('fxFeedsFailed'));
          if (res.ok) { renderRateFeedStatus(d); loadFxRates(); if (typeof loadFxMetadata === 'function') loadFxMetadata(true); }
        } catch (_) { msg.textContent = t('fxFeedsFailed'); } finally { btn.disabled = false; }
      });
    })();

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
          if (status) status.textContent = t('msgSavedPrefix') + data.currency;
          // the cached reporting currency (loadReportingCurrency) and metadata
          // follow, so labels and dropdowns reflect the new default
          if (data.currency) window.__REPORTING_CURRENCY = data.currency;
          applyDefaultFormCurrency();
          await loadFxMetadata(true);
        } else {
          if (status) status.textContent = t('settingsSaveFailed');
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
          showAlert(t('msgFxRateFields'), true);
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
          showAlert(data.detail || t('msgFxRateSaveFailed'), true);
        }
      });
    }
    // loadFxSettings runs with the Settings page (loadSettingsPage).

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
        if (statusEl) statusEl.textContent = tf('settingsCurrentValue', { value: optionLabel(selEl, loc) });
      } catch (_) {}
    }

    // "UK FRS 102 Section 1A" (the option as the user reads it), not "uk"
    function optionLabel(sel, value) {
      const o = sel && [...sel.options].find((x) => x.value === value);
      return o ? o.textContent.trim() : String(value || '');
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
          if (status) status.textContent = tf('settingsSavedValue', { value: optionLabel(sel, data.locale) });
        } else {
          const data = await r.json().catch(() => ({}));
          if (status) status.textContent = (data.detail || t('settingsSaveFailed'));
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
            if (status) status.textContent = tf('settingsSavedValue', { value: optionLabel(sel, data.calendar) });
            if (typeof applyCalendarMonthPickers === 'function') applyCalendarMonthPickers();
            if (typeof dressDateInputs === 'function') dressDateInputs();
          } else {
            const data = await r.json().catch(() => ({}));
            if (status) status.textContent = (data.detail || t('settingsSaveFailed'));
          }
        } catch (e) {
          if (status) status.textContent = tf('errorWithMessage', { message: e.message });
        }
      });
    }

    // ─── Locale demo data: reset DB and post the curated 2-year journal ──
    async function _resetAndLoadLocaleDemo(locale, button) {
      const statusEl = document.getElementById('reset-demo-status');
      const otherId = locale === 'ir' ? 'reset-demo-uk-btn' : 'reset-demo-ir-btn';
      const otherBtn = document.getElementById(otherId);
      const confirmMsg = locale === 'ir'
        ? t('confirmResetIranian')
        : t('confirmResetUk');
      if (!(await uiConfirm({ message: confirmMsg, confirmLabel: t('btnContinue'), danger: true }))) return;
      button.disabled = true;
      if (otherBtn) otherBtn.disabled = true;
      statusEl.style.color = 'var(--text-muted)';
      statusEl.textContent = t('demoResetting');
      try {
        const url = API + '/admin/reset-db?locale=' + encodeURIComponent(locale) + '&with_demo_data=true';
        const res = await fetch(url, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          statusEl.style.color = '#2e7d32';
          statusEl.textContent = tf('demoLoaded', { entries: data.demo_entries || 0, accounts: data.accounts_created || 0,
            locale: optionLabel(document.getElementById('reporting-locale-select'), data.locale) });
          window.__REPORTING_LOCALE = data.locale;
          const sel = document.getElementById('reporting-locale-select');
          if (sel && [...sel.options].some(o => o.value === data.locale)) sel.value = data.locale;
          const localeStatus = document.getElementById('reporting-locale-status');
          if (localeStatus) localeStatus.textContent = tf('settingsCurrentValue', { value: optionLabel(sel, data.locale) });
          // Give the user a moment to read, then reload so every panel re-fetches.
          setTimeout(() => window.location.reload(), 1500);
        } else {
          statusEl.style.color = '#c62828';
          statusEl.textContent = data.detail || t('demoResetFailed');
        }
      } catch (e) {
        statusEl.style.color = '#c62828';
        statusEl.textContent = tf('errorWithMessage', { message: e.message });
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
            statusEl.textContent = data.detail || t('demoResetFailed');
          }
        } catch (e) {
          statusEl.style.color = '#b91c1c';
          statusEl.textContent = t('msgConnectionError') + e.message;
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
      // Manager reports' currency: applyReportCurrencyDefault (03-ui.js), run
      // by loadFxMetadata itself.
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
          tr.innerHTML = '<td colspan="7" style="text-align:center;color:var(--text-muted);padding:1.5rem;">' + escapeHtml(t('bsEmpty')) + '</td>';
          body.appendChild(tr);
          return;
        }
        stmts.forEach(s => {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${escapeHtml(s.bank_name)}</td><td>${escapeHtml(s.source_filename)}</td>
            <td>${escapeHtml(s.source_type)}${s.origin === 'email' ? ` <span class="badge" title="${escapeHtml(t('bsViaEmailTitle'))}">${escapeHtml(t('bsViaEmail'))}</span>` : ''}</td><td>${s.total_rows}</td><td>${s.matched_rows || 0}</td>
            <td><span class="badge ${s.status === 'approved' ? 'badge-ok' : ''}">${s.status}</span></td>
            <td><button class="btn btn-secondary btn-sm bs-view-btn" data-id="${s.id}">${escapeHtml(t('btnView'))}</button></td>`;
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

    async function doUploadStatement(file, bankName, { columnMap = null, confirmDuplicate = false, pdfPassword = null } = {}) {
      const statusEl = document.getElementById('bs-upload-status');
      statusEl.style.display = 'block';
      statusEl.textContent = t('xiUploading');
      statusEl.className = 'alert';
      const form = new FormData();
      form.append('file', file);
      if (pdfPassword) form.append('pdf_password', pdfPassword);        // the body, never the URL
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
        // A locked PDF → ask for its password, then send it again with it.
        if (data.needs_password) {
          const pw = await uiPrompt({ title: t('bsPdfPasswordTitle'), type: 'password',
            message: data.password_wrong ? t('bsPdfPasswordWrong') : t('bsPdfPasswordPrompt') });
          if (!pw) { statusEl.style.display = 'none'; return; }
          return doUploadStatement(file, bankName, { columnMap, confirmDuplicate, pdfPassword: pw });
        }
        // Unknown layout → ask the user to map columns, then re-upload.
        if (data.needs_mapping) {
          const map = await promptColumnMapping(data.headers, data.required_fields);
          if (!map) { statusEl.style.display = 'none'; return; }
          return doUploadStatement(file, bankName, { columnMap: map, confirmDuplicate, pdfPassword });
        }
        // Identical file already imported → confirm before re-importing.
        if (data.duplicate) {
          const ok = await uiConfirm({ title: t('bsDupTitle'), message: t('bsDupConfirmMsg') });
          if (!ok) { statusEl.style.display = 'none'; return; }
          return doUploadStatement(file, bankName, { columnMap, confirmDuplicate: true, pdfPassword });
        }
        let msg = tf('bsParsed', { rows: data.total_rows, bank: data.bank_name, type: data.source_type });
        if (data.skipped_rows) msg += ' ' + tf('bsSkipped', { n: data.skipped_rows });
        if (data.duplicate_rows) msg += ' ' + tf('bsAlreadyImported', { n: data.duplicate_rows });
        statusEl.textContent = msg;
        statusEl.className = 'alert';
        loadBankStatements();
      } catch (e) { statusEl.textContent = t('bsParseFailed'); statusEl.className = 'alert alert-error'; }
    }

    document.getElementById('bs-upload-btn').addEventListener('click', async () => {
      const fileInput = document.getElementById('bs-file-input');
      const bankName = document.getElementById('bs-bank-name').value.trim() || 'Unknown';
      if (!fileInput.files.length) { showAlert(t('msgSelectFile'), true); return; }
      await doUploadStatement(fileInput.files[0], bankName);
    });

    let currentStatementId = null;

    // Deep link from the AI chat's statement card: open the Bank statements
    // page on this statement and show its differences with the books.
    async function openStatementFromChat(id, { review = false } = {}) {
      if (!id) return;
      showPage('bank-statements');
      currentStatementId = String(id);
      await loadStatementDetail(currentStatementId);
      if (review) await runStatementReview();
    }

    document.getElementById('bs-list-body').addEventListener('click', async (e) => {
      const btn = e.target.closest('.bs-view-btn');
      if (!btn) return;
      currentStatementId = btn.dataset.id;
      await loadStatementDetail(currentStatementId);
    });

    // Postable accounts for the per-row category picker. Fetched once per
    // page visit; the import's suggestion just preselects an option.
    let _bsAccounts = [];
    async function bsLoadAccountOptions() {
      if (_bsAccounts.length) return;
      try {
        const res = await fetch(API + '/manager-reports/accounts/list');
        if (!res.ok) return;
        _bsAccounts = await res.json();
      } catch (_) { /* offline — falls back to a free-text prompt */ }
    }

    function bsCategoryCell(r) {
      // Settled rows just show what they were filed as; rows still awaiting a
      // decision get a picker preselected to the import's suggestion.
      if (r.recon_status !== 'unmatched' || !_bsAccounts.length) {
        return escapeHtml(r.category || '—');
      }
      // Build the markup directly with a `selected` ATTRIBUTE — setting .value
      // on a detached <select> sets the property, which outerHTML does not
      // serialize, so the suggestion would silently vanish from the picker.
      const chosen = r.suggested_account_code || '';
      const opts = _bsAccounts.map(a =>
        `<option value="${escapeHtml(a.code)}"${a.code === chosen ? ' selected' : ''}>` +
        `${escapeHtml(a.code)} — ${escapeHtml(a.name)}</option>`).join('');
      return `<select class="bs-code-select" data-row-id="${escapeHtml(r.id)}">` +
             `<option value="">${escapeHtml(t('bsPickCategory'))}</option>${opts}</select>`;
    }

    // Which bank account the statement belongs to, and how that was decided —
    // choose it when nothing on the statement tells (statement_import.resolve_bank_account).
    let _bsBankChoices = null;
    const BS_ACCT_SOURCE_KEYS = { chosen: 'bsAcctChosen', account_number: 'bsAcctByNumber', name: 'bsAcctByName',
      bank: 'bsAcctByBank', default: 'bsAcctDefault' };
    async function bsBankChoices() {
      if (_bsBankChoices) return _bsBankChoices;
      try {
        const r = await fetch(bsAPI + '/bank-accounts');
        if (r.ok) _bsBankChoices = (await r.json()).accounts || [];
      } catch (_) { /* the row just stays hidden */ }
      return _bsBankChoices || [];
    }
    async function setStatementBankAccount(stmt, code) {
      const r = await fetch(bsAPI + '/bank-statements/' + stmt.id + '/bank-account', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }) });
      if (r.status === 409) { showAlert(t('bsAcctLocked'), true); renderStatementBankAccount(stmt); return; }
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        showAlert(typeof d.detail === 'string' ? d.detail : t('bsAcctFailed'), true);
        renderStatementBankAccount(stmt);
        return;
      }
      await loadStatementDetail(stmt.id);
    }
    async function renderStatementBankAccount(stmt) {
      const el = document.getElementById('bs-bank-account-row');
      if (!el) return;
      const choices = await bsBankChoices();
      if (!choices.length || !stmt.bank_account_code) { el.style.display = 'none'; return; }
      const opts = choices.map(c => `<option value="${escapeHtml(c.code)}"${c.code === stmt.bank_account_code ? ' selected' : ''}>`
        + `${escapeHtml(c.code)} — ${escapeHtml(c.bank || c.name)}</option>`).join('');
      const unsure = stmt.bank_account_source === 'default' && choices.length > 1;
      el.innerHTML = `<div style="display:flex;gap:0.45rem;align-items:center;flex-wrap:wrap;">
          <label for="bs-bank-account" style="font-weight:600;margin:0;">${escapeHtml(t('bsBankAccount'))}</label>
          <select id="bs-bank-account" style="width:auto;max-width:100%;margin:0;">${opts}</select>
          <span style="color:${unsure ? '#b45309' : 'var(--text-muted)'};">${escapeHtml(t(BS_ACCT_SOURCE_KEYS[stmt.bank_account_source] || 'bsAcctDefault'))}</span>
          ${stmt.bank_account_source === 'chosen' ? `<button type="button" class="btn btn-secondary btn-sm" id="bs-bank-account-auto">${escapeHtml(t('bsAcctAutomatic'))}</button>` : ''}
        </div>`;
      el.style.display = '';
      el.querySelector('#bs-bank-account').addEventListener('change', (e) => setStatementBankAccount(stmt, e.target.value));
      const auto = el.querySelector('#bs-bank-account-auto');
      if (auto) auto.addEventListener('click', () => setStatementBankAccount(stmt, null));
    }

    async function loadStatementDetail(id) {
      try {
        await bsLoadAccountOptions();
        const res = await fetch(bsAPI + '/bank-statements/' + id);
        if (!res.ok) return;
        const stmt = await res.json();
        document.getElementById('bs-detail-title').textContent = `${stmt.bank_name} — ${stmt.source_filename} (${stmt.total_rows} rows)`;
        renderStatementBankAccount(stmt);
        const body = document.getElementById('bs-rows-body');
        body.innerHTML = '';
        stmt.rows.forEach(r => {
          const confColor = r.confidence >= 0.85 ? '#2e7d32' : r.confidence >= 0.6 ? '#f57f17' : '#c62828';
          const statusBg = r.recon_status === 'matched' ? '#e8f5e9' : r.recon_status === 'duplicate' ? '#fff3e0' : '';
          const tr = document.createElement('tr');
          tr.style.background = statusBg;
          tr.innerHTML = `<td>${r.row_index}</td><td>${escapeHtml(formatDisplayDate(r.tx_date))}</td><td dir="auto">${escapeHtml(r.description || '')}</td>
            <td style="color:#1565c0;">${r.debit ? r.debit.toLocaleString() : ''}</td>
            <td style="color:#2e7d32;">${r.credit ? r.credit.toLocaleString() : ''}</td>
            <td>${r.balance != null ? r.balance.toLocaleString() : ''}</td>
            <td>${bsCategoryCell(r)}</td>
            <td style="color:${confColor}">${(r.confidence * 100).toFixed(0)}%</td>
            <td>${escapeHtml(enumLabel('bsRecon_', r.recon_status))}</td>
            <td>${r.recon_status === 'unmatched' ? `<button class="btn btn-secondary btn-sm bs-create-btn" data-row-id="${r.id}" data-code="${r.suggested_account_code || ''}">${escapeHtml(t('bsCreateBtn'))}</button>` : r.user_approved ? '✓' : `<button class="btn btn-secondary btn-sm bs-approve-btn" data-row-id="${r.id}">${escapeHtml(t('btnApprove'))}</button>`}</td>`;
          body.appendChild(tr);
        });
        document.getElementById('bs-list-wrap').style.display = 'none';
        document.getElementById('bs-detail-wrap').style.display = 'block';
      } catch (e) { showAlert(t('msgStatementLoadFailed') + e.message, true); }
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
          span.textContent = `${escapeHtml(formatDisplayDate(s.tx_date))} · ${s.description || ''} · ${formatNum(s.amount)} ${data.currency || currencyUnit()} → ${s.account_code} ${s.account_name}`;
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
      } catch (e) { showAlert(t('msgRecordFailed') + e.message, true); }
    }

    document.getElementById('bs-reconcile-btn').addEventListener('click', async () => {
      if (!currentStatementId) return;
      try {
        const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/reconcile', { method: 'POST' });
        renderReconSummary(await res.json());
        await loadStatementDetail(currentStatementId);
      } catch (e) { showAlert(t('msgReconcileFailed') + e.message, true); }
    });

    // ─── Check against the books: contradictions + one-click fixes ───
    const FINDING_LABEL_KEY = {
      unrecorded: 'bsFindUnrecorded', needs_confirmation: 'bsFindNeedsConfirm',
      amount_mismatch: 'bsFindAmount', missing_in_bank: 'bsFindMissing',
      duplicate: 'bsFindDuplicate', balance_gap: 'bsFindBalance',
    };
    async function runStatementReview() {
      if (!currentStatementId) return;
      const el = document.getElementById('bs-recon-summary');
      try {
        const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/review', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        renderStatementReview(data);
        await loadStatementDetail(currentStatementId);
      } catch (e) {
        el.style.display = 'block';
        el.textContent = t('bsReviewFailed') + ': ' + e.message;
      }
    }
    function renderStatementReview(data) {
      const el = document.getElementById('bs-recon-summary');
      el.style.display = 'block';
      el.innerHTML = '';
      const ccy = data.currency || currencyUnit();
      const title = document.createElement('div');
      title.style.cssText = 'font-weight:600;margin-bottom:0.35rem;';
      title.textContent = t('bsReviewTitle');
      el.appendChild(title);
      const c = data.counts || {};
      const line = document.createElement('div');
      line.textContent = tf('bsReviewSummary', {
        matched: c.matched || 0, unrecorded: c.unrecorded || 0, confirm: c.needs_confirmation || 0,
        mismatch: c.amount_mismatch || 0, missing: c.missing_in_bank || 0, dupes: c.duplicates || 0,
      });
      el.appendChild(line);
      if (data.balance && data.balance.gap != null) {
        const b = data.balance;
        const bl = document.createElement('div');
        bl.style.cssText = 'margin-top:0.3rem;font-weight:600;color:' + (b.gap === 0 ? '#2e7d32' : '#c62828') + ';';
        bl.textContent = tf('bsReviewBalance', {
          bank: formatNum(b.statement_closing), books: formatNum(b.book_balance), gap: formatNum(b.gap), ccy,
        }) + (b.gap !== 0 && b.explained ? ' — ' + t('bsReviewGapExplained') : '');
        el.appendChild(bl);
      }
      if (data.clean) {
        const ok = document.createElement('div');
        ok.style.cssText = 'margin-top:0.4rem;color:#2e7d32;font-weight:600;';
        ok.textContent = t('bsReviewClean');
        el.appendChild(ok);
        return;
      }
      (data.findings || []).forEach(f => {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:0.5rem;margin-top:0.4rem;padding-top:0.35rem;border-top:1px solid rgba(0,0,0,0.06);';
        const badge = document.createElement('span');
        const color = f.severity === 'high' ? '#c62828' : f.severity === 'info' ? '#546e7a' : '#f57f17';
        badge.style.cssText = 'font-size:0.72rem;font-weight:600;padding:0.1rem 0.4rem;border-radius:10px;color:#fff;background:' + color + ';white-space:nowrap;';
        // A duplicate can be a row imported from an EARLIER statement or a row
        // repeated inside THIS statement (possible double charge) — label them apart.
        badge.textContent = t((f.kind === 'duplicate' && f.category === 'same_statement') ? 'bsFindDuplicateSame' : (FINDING_LABEL_KEY[f.kind] || 'bsFindUnrecorded'));
        const span = document.createElement('span');
        span.dir = 'auto';
        span.style.flex = '1';
        let txt = (f.tx_date || '') + ' · ' + (f.description || '') + ' · ' + formatNum(f.amount) + ' ' + ccy;
        if (f.kind === 'amount_mismatch' && f.matched_amount != null) {
          txt += ' — ' + t('bsFindBook') + ': ' + formatNum(f.matched_amount) + ' (' + (f.matched_date || '') + ')';
        } else if (f.kind === 'needs_confirmation' && f.matched_date) {
          txt += ' — ' + t('bsFindBook') + ': ' + (f.matched_date || '') + ' ' + (f.matched_description || '');
        } else if (f.kind === 'unrecorded' && f.suggested_account_code) {
          txt += ' → ' + f.suggested_account_code + ' ' + (f.suggested_account_name || '');
        }
        span.textContent = txt;
        row.appendChild(badge);
        row.appendChild(span);
        if (f.suggested_fix === 'post_row' && f.row_id) {
          const btn = document.createElement('button');
          btn.className = 'btn btn-secondary btn-sm';
          btn.textContent = t('bsFindPostBtn');
          btn.onclick = () => applyFindingAction(f, 'create');
          row.appendChild(btn);
        } else if (f.suggested_fix === 'approve_match' && f.row_id) {
          const btn = document.createElement('button');
          btn.className = 'btn btn-secondary btn-sm';
          btn.textContent = t('bsFindApproveBtn');
          btn.onclick = () => applyFindingAction(f, 'approve');
          row.appendChild(btn);
        }
        el.appendChild(row);
      });
    }
    async function applyFindingAction(f, action) {
      if (action === 'create') {
        const ok = await uiConfirm({
          title: t('bsRecordConfirmTitle'),
          message: tf('bsRecordConfirmMsg', { desc: f.description || '', account: (f.suggested_account_code || '') + ' ' + (f.suggested_account_name || '') }),
        });
        if (!ok) return;
      }
      try {
        const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/approve', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approvals: [{ row_id: f.row_id, action, account_code: f.suggested_account_code || null }] }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        if ((data.errors || []).length) { showAlert(data.errors[0], true); return; }
        showAlert(action === 'create' ? t('bsFindPosted') : t('bsFindApproved'));
        await runStatementReview();
      } catch (e) { showAlert(t('bsReviewFailed') + ': ' + e.message, true); }
    }
    document.getElementById('bs-review-btn').addEventListener('click', runStatementReview);

    document.getElementById('bs-approve-all-btn').addEventListener('click', async () => {
      if (!currentStatementId) return;
      const btns = document.querySelectorAll('.bs-approve-btn');
      const approvals = Array.from(btns).map(b => ({ row_id: b.dataset.rowId, action: 'approve' }));
      if (!approvals.length) { showAlert(t('msgNoRowsToApprove')); return; }
      try {
        const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/approve', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approvals })
        });
        const data = await res.json();
        showAlert(`Approved: ${data.approved}, Created: ${data.created}. ${data.errors.join('; ')}`);
        await loadStatementDetail(currentStatementId);
      } catch (e) { showAlert(t('msgApprovalFailed') + e.message, true); }
    });

    // Bulk-post: create a ledger entry for every unmatched row that has a
    // category chosen (the import preselects one). Rows the user blanked out
    // are left alone, so this is safe to press repeatedly.
    document.getElementById('bs-post-all-btn').addEventListener('click', async () => {
      if (!currentStatementId) return;
      const approvals = Array.from(document.querySelectorAll('.bs-code-select'))
        .filter(sel => sel.value)
        .map(sel => ({ row_id: sel.dataset.rowId, action: 'create', account_code: sel.value }));
      if (!approvals.length) { showAlert(t('bsNothingToPost')); return; }
      if (!await uiConfirm({ title: t('bsPostAllBtn'), message: tf('bsPostAllConfirm', { n: approvals.length }) })) return;
      try {
        const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/approve', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approvals })
        });
        const data = await res.json();
        showAlert(tf('bsPostedCount', { n: data.created }) + (data.errors.length ? ' — ' + data.errors.join('; ') : ''),
                  data.errors.length > 0);
        await loadStatementDetail(currentStatementId);
      } catch (e) { showAlert(t('msgPostFailed') + e.message, true); }
    });

    document.getElementById('bs-rows-body').addEventListener('click', async (e) => {
      const createBtn = e.target.closest('.bs-create-btn');
      const approveBtn = e.target.closest('.bs-approve-btn');
      if (createBtn) {
        const rowId = createBtn.dataset.rowId;
        const sel = createBtn.closest('tr')?.querySelector('.bs-code-select');
        const code = (sel && sel.value) || createBtn.dataset.code ||
          await uiPrompt({ title: t('promptAccountCodeTitle'), message: t('promptAccountCodeMsg'), value: '' });
        if (!code) { showAlert(t('bsPickCategory'), true); return; }
        try {
          const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/approve', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approvals: [{ row_id: rowId, action: 'create', account_code: code }] })
          });
          const data = await res.json();
          showAlert(`Created: ${data.created}. ${data.errors.join('; ')}`);
          await loadStatementDetail(currentStatementId);
        } catch (e) { showAlert(t('msgCreateFailed') + e.message, true); }
      }
      if (approveBtn) {
        try {
          const res = await fetch(bsAPI + '/bank-statements/' + currentStatementId + '/approve', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approvals: [{ row_id: approveBtn.dataset.rowId, action: 'approve' }] })
          });
          await loadStatementDetail(currentStatementId);
        } catch (e) { showAlert(t('msgApproveFailed'), true); }
      }
    });

    // ═══════ Bank SMS capture (app/services/bank_sms.py) ═══════
    (function wireBankSms() {
      const box = document.getElementById('sms-text');
      if (!box) return;
      const note = document.getElementById('sms-preview-note');
      const example = document.getElementById('sms-auto-example');
      if (example) example.textContent = `POST ${location.origin}/api/v1/bank-sms\nAuthorization: Bearer <API key>\nContent-Type: application/json\n\n{"text": "<the SMS as received>"}`;
      let timer = null;
      box.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const text = box.value.trim();
          if (!text) { note.textContent = ''; return; }
          try {
            const r = await fetch(API + '/bank-sms/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) { note.textContent = ''; return; }
            const ok = (d.messages || []).filter(m => !m.problems.length).length;
            note.textContent = tf('smsPreview', { ok, n: (d.messages || []).length });
          } catch (_) { note.textContent = ''; }
        }, 400);
      });
      document.getElementById('sms-add').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const text = box.value.trim();
        const out = document.getElementById('sms-result');
        if (!text) return;
        btn.disabled = true;
        try {
          const r = await fetch(API + '/bank-sms', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) { out.innerHTML = `<div class="tfa-note">${escapeHtml(typeof d.detail === 'string' ? d.detail : t('smsFailed'))}</div>`; return; }
          const parts = [`<div class="tfa-note" style="background:color-mix(in srgb, var(--success, #16a34a) 10%, transparent);border-color:color-mix(in srgb, var(--success, #16a34a) 35%, transparent);">${escapeHtml(tf('smsDone', { added: d.added, dup: d.duplicates }))}</div>`];
          if ((d.gaps || []).length) parts.push(`<div class="tfa-note">${escapeHtml(tf('smsGaps', { n: d.gaps.length }))}</div>`);
          if ((d.unparsed || []).length) {
            parts.push(`<div class="tfa-note"><strong>${escapeHtml(tf('smsUnread', { n: d.unparsed.length }))}</strong><ul style="margin:0.3rem 0 0;padding-inline-start:1.1rem;">${d.unparsed.slice(0, 10).map(u => `<li><span dir="auto">${escapeHtml(u.text.split('\n')[0])}</span> — ${escapeHtml(u.problems.join(', '))}</li>`).join('')}</ul></div>`);
          }
          out.innerHTML = parts.join('');
          if (d.added) { box.value = ''; note.textContent = ''; if (typeof loadBankStatements === 'function') loadBankStatements(); }
        } catch (_) { out.innerHTML = `<div class="tfa-note">${escapeHtml(t('smsFailed'))}</div>`; } finally { btn.disabled = false; }
      });
    })();

    // ═══════ Bank statements by e-mail (app/services/statement_mailbox.py) ═══════
    const MAIL_ERROR_KEYS = { resolve: 'mailErrResolve', private: 'mailErrPrivate', connect: 'mailErrConnect',
      timeout: 'mailErrTimeout', tls: 'mailErrTls', auth: 'mailErrAuth', folder: 'mailErrFolder', protocol: 'mailErrProtocol' };
    const MAIL_OUTCOME_KEYS = { imported: 'mailOutImported', duplicate: 'mailOutDuplicate', needs_mapping: 'mailOutNeedsMapping',
      needs_password: 'mailOutNeedsPassword',
      failed: 'mailOutFailed', no_attachment: 'mailOutNoAttachment', too_large: 'mailOutTooLarge' };
    let _mailCfg = null;
    function _mailEl(id) { return document.getElementById(id); }
    function _mailForm() {
      return {
        host: _mailEl('mail-host').value.trim(), port: parseInt(_mailEl('mail-port').value, 10) || 993,
        username: _mailEl('mail-user').value.trim(), password: _mailEl('mail-password').value,
        folder: _mailEl('mail-folder').value.trim() || 'INBOX', senders: _mailEl('mail-senders').value,
        enabled: _mailEl('mail-enabled').checked, pdf_password: _mailEl('mail-pdf-password').value,
      };
    }
    function mailWhen(iso) {
      const d = iso ? new Date(iso) : null;
      if (!d || isNaN(d.getTime())) return '';
      const p = (n) => String(n).padStart(2, '0');
      // an isolated LTR run: after Persian words "2026-09-29" would otherwise read "29-09-2026"
      return `\u2066${formatDisplayDate(`${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`)} ${p(d.getHours())}:${p(d.getMinutes())}\u2069`;
    }
    function mailError(r) {
      const f = _mailForm();
      const key = MAIL_ERROR_KEYS[r && r.error_code];
      return key ? tf(key, { host: f.host || (_mailCfg && _mailCfg.host) || '', port: f.port, folder: f.folder })
        : ((r && r.error) || t('mailErrProtocol'));
    }
    function _mailNote(text, ok) {
      const style = ok ? ' style="background:color-mix(in srgb, var(--success, #16a34a) 10%, transparent);border-color:color-mix(in srgb, var(--success, #16a34a) 35%, transparent);"' : '';
      _mailEl('mail-result').innerHTML = `<div class="tfa-note"${style}>${escapeHtml(text)}</div>`;
    }
    function renderMailState(cfg) {
      const st = cfg.status || {};
      let text;
      if (!cfg.host) text = t('mailStateOff');
      else if (st.checked_at && st.ok === false) text = tf('mailStateError', { when: mailWhen(st.checked_at), error: mailError(st) });
      else if (st.checked_at) text = tf('mailStateOk', { when: mailWhen(st.checked_at), imported: st.imported || 0 });
      else text = cfg.enabled ? t('mailStateWaiting') : t('mailStatePaused');
      if (st.deferred) text += ' · ' + tf('mailStateDeferred', { n: st.deferred });
      if (cfg.host && !cfg.enabled && st.checked_at) text += ' · ' + t('mailStatePaused');
      _mailEl('mail-summary-state').textContent = '— ' + text;
    }
    function renderMailbox(cfg) {
      _mailCfg = cfg;
      _mailEl('mail-host').value = cfg.host || '';
      _mailEl('mail-port').value = cfg.port || 993;
      _mailEl('mail-user').value = cfg.username || '';
      _mailEl('mail-password').value = '';
      _mailEl('mail-password').placeholder = cfg.has_password ? t('mailPasswordSaved') : '';
      _mailEl('mail-folder').value = cfg.folder || 'INBOX';
      _mailEl('mail-senders').value = (cfg.senders || []).map(s => s.bank_name ? `${s.match}, ${s.bank_name}` : s.match).join('\n');
      _mailEl('mail-enabled').checked = !!cfg.enabled;
      _mailEl('mail-pdf-password').value = '';
      _mailEl('mail-pdf-password').placeholder = cfg.has_pdf_password ? t('mailPasswordSaved') : t('mailPdfPasswordNone');
      _mailEl('mail-pdf-forget').style.display = cfg.has_pdf_password && cfg.can_manage ? '' : 'none';
      _mailEl('mail-form').disabled = !cfg.can_manage;
      document.querySelectorAll('#mail-panel .mail-manage').forEach(b => { b.style.display = cfg.can_manage ? '' : 'none'; });
      _mailEl('mail-view-only').style.display = cfg.can_manage ? 'none' : '';
      _mailEl('mail-check').disabled = !(cfg.host && cfg.has_password && (cfg.senders || []).length);
      renderMailState(cfg);
    }
    async function loadMailLog() {
      try {
        const r = await fetch(API + '/bank-mailbox/messages');
        if (!r.ok) return;
        const rows = (await r.json()).messages || [];
        _mailEl('mail-log-wrap').style.display = rows.length ? '' : 'none';
        _mailEl('mail-log-body').innerHTML = rows.map(m => `<tr>
          <td style="white-space:nowrap;">${escapeHtml(mailWhen(m.received_at || m.checked_at))}</td>
          <td dir="ltr">${escapeHtml(m.sender)}</td>
          <td><bdi>${escapeHtml(m.subject || '')}</bdi></td>
          <td><span class="badge ${m.status === 'imported' ? 'badge-ok' : ''}"${m.detail ? ` title="${escapeHtml(m.detail)}"` : ''}>${escapeHtml(t(MAIL_OUTCOME_KEYS[m.status] || 'mailOutFailed'))}</span></td>
        </tr>`).join('');
      } catch (_) { /* the panel still works without its log */ }
    }
    async function loadMailbox() {
      if (!_mailEl('mail-panel')) return;
      try {
        const r = await fetch(API + '/bank-mailbox');
        if (!r.ok) { _mailEl('mail-panel').style.display = 'none'; return; }
        _mailEl('mail-panel').style.display = '';
        renderMailbox(await r.json());
        loadMailLog();
      } catch (_) { /* offline: leave the panel as it is */ }
    }
    (function wireMailbox() {
      if (!_mailEl('mail-panel')) return;
      const busy = async (btn, fn) => { btn.disabled = true; try { await fn(); } finally { btn.disabled = false; } };
      _mailEl('mail-save').addEventListener('click', (e) => busy(e.currentTarget, async () => {
        const body = _mailForm();
        if (!body.password) delete body.password;
        if (!body.pdf_password) delete body.pdf_password;
        const r = await fetch(API + '/bank-mailbox', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { _mailNote(typeof d.detail === 'string' ? d.detail : t('mailSaveFailed')); return; }
        renderMailbox(d);
        _mailNote(t('mailSaved'), true);
      }));
      _mailEl('mail-pdf-forget').addEventListener('click', (e) => busy(e.currentTarget, async () => {
        const body = { ..._mailForm(), clear_pdf_password: true };
        delete body.password; delete body.pdf_password;
        const r = await fetch(API + '/bank-mailbox', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { _mailNote(typeof d.detail === 'string' ? d.detail : t('mailSaveFailed')); return; }
        renderMailbox(d);
        _mailNote(t('mailPdfPasswordForgotten'), true);
      }));
      _mailEl('mail-test').addEventListener('click', (e) => busy(e.currentTarget, async () => {
        const f = _mailForm();
        const body = { host: f.host, port: f.port, username: f.username, folder: f.folder };
        if (f.password) body.password = f.password;
        const r = await fetch(API + '/bank-mailbox/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { _mailNote(typeof d.detail === 'string' ? d.detail : t('mailErrProtocol')); return; }
        _mailNote(d.ok ? tf('mailTestOk', { n: d.messages, folder: d.folder }) : mailError(d), !!d.ok);
      }));
      _mailEl('mail-check').addEventListener('click', (e) => busy(e.currentTarget, async () => {
        _mailNote(t('mailChecking'), true);
        const r = await fetch(API + '/bank-mailbox/check', { method: 'POST' });
        const d = await r.json().catch(() => ({}));
        if (r.status === 409) { _mailNote(t('mailNotSetUp')); return; }
        if (!r.ok) { _mailNote(typeof d.detail === 'string' ? d.detail : t('mailErrProtocol')); return; }
        _mailNote(d.ok ? tf('mailCheckDone', { found: d.found, imported: d.imported })
          + (d.deferred ? ' ' + tf('mailStateDeferred', { n: d.deferred }) : '') : mailError(d), !!d.ok);
        if (_mailCfg) { _mailCfg.status = d; renderMailState(_mailCfg); }
        loadMailLog();
        if (d.imported && typeof loadBankStatements === 'function') loadBankStatements();
      }));
    })();
