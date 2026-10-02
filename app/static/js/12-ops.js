    // Load the data a page needs. Called from BOTH the nav-button click
    // handler and hash navigation (hashchange / deep-link / back-forward), so
    // a page reached via the URL hash — not just a click — still fetches its
    // data (otherwise e.g. CFO/CEO cards render their empty "—" placeholders).
    // Boot fetches nothing page-specific: this is the ONLY place a page's
    // data loads from when it opens (tests/test_boot_page_data.py).
    function loadPageData(page) {
      if (page === 'dashboard') { initDashTabs(); loadOwnerDashboard(); dashTabRefresh('spend'); }
      if (page === 'personal-dashboard') { loadPersonalDashboard(); }
      if (page === 'commitments') { loadCommitments(); }
      if (page === 'ai-accountant') { if (typeof aiChatInit === 'function') aiChatInit(); }
      if (page === 'transactions') { loadEntityOptions(); }
      if (page === 'entities') { loadEntities(); }
      if (page === 'invoices') { loadInvoices(); invInitBuilder(); loadEntityOptions(); }
      if (page === 'recurring') { loadRecurringRules(); recurringInitPage(); }
      if (page === 'bank-statements') { loadBankStatements(); loadMailbox(); }
      if (page === 'ledger') { loadLedger(); }
      if (page === 'audit') { loadAuditLogs(); }
      if (page === 'cfo') { loadCFOReport(); }
      if (page === 'ceo') { loadCEOReport(); }
      if (page === 'inventory') { loadPriceMgmtItems(); loadStockPanel(); loadManagerInventoryItems(); }
      if (page === 'manager') { loadAccountDatalist(); loadProductEntityDatalist(); loadClosePack(); }
      if (page === 'products') { loadProductsCatalog(); }
      if (page === 'payroll') { loadPayroll(); }
      if (page === 'equity') { loadEquity(); }
      if (page === 'purchase-orders') { loadPurchaseOrders(); }
      if (page === 'expenses') { loadExpenses(); }
      if (page === 'time') { loadTimeTab(); }
      if (page === 'settings') { loadSettingsPage(); }
      if (page === 'companies') { loadCompanies(); }
      if (page === 'migration') { migrationInitPage(); }
      if (page === 'petty-cash') { pettyInitPage(); }
      if (page === 'fixed-assets') { loadFixedAssets(); }
      if (page === 'accounts') { loadChartOfAccounts(); }
    }


    // ═══════ Migration from another accounting system ═══════
    let _migrationToken = null;
    let _migrationPendingCache = [];
    function _migFieldLabel(f) {
      const map = { address: 'migFieldAddress', phone: 'migFieldPhone', iban: 'migFieldIban', account_number: 'migFieldAccountNumber', email: 'migFieldEmail' };
      return map[f] ? t(map[f]) : f;
    }
    function migrationInitPage() { migrationLoadPending(); }
    async function migrationUpload() {
      const filesEl = document.getElementById('migration-files');
      const statusEl = document.getElementById('migration-status');
      if (!filesEl.files.length) { statusEl.textContent = t('migrationFilesLabel'); return; }
      const fd = new FormData();
      Array.from(filesEl.files).slice(0, 4).forEach((f) => fd.append('files', f));
      statusEl.textContent = '…';
      document.getElementById('migration-result').style.display = 'none';
      try {
        const res = await fetch(API + '/migration/import/preview', { method: 'POST', body: fd });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(typeof err.detail === 'string' ? err.detail : res.statusText);
        }
        const body = await res.json();
        _migrationToken = body.token;
        const dateEl = document.getElementById('migration-opening-date');
        if (!dateEl.value && body.default_opening_date) dateEl.value = body.default_opening_date;
        _migrationRenderPreview(body);
        statusEl.textContent = '';
      } catch (e) {
        statusEl.textContent = e.message || String(e);
      }
    }
    function _cpTypesLabel(types) {
      if (!types) return '';
      const map = { client: 'migCpTypeClient', supplier: 'migCpTypeSupplier', employee: 'migCpTypeEmployee' };
      const parts = Object.keys(types).map((k) => types[k] + ' ' + t(map[k] || k));
      return parts.length ? ' (' + parts.join(' · ') + ')' : '';
    }
    function _migrationRenderPreview(body) {
      const s = body.summary || {};
      const tiers = s.tiers || {};
      const split = s.tafsili_split || {};
      const opening = s.opening || {};
      const v = s.validation || {};
      const fmt = (n) => (typeof n === 'number' ? n.toLocaleString() : n);
      const tierLabels = { group: t('migrationTierGroups'), kol: t('migrationTierKol'), moein: t('migrationTierMoein'), tafsili: t('migrationTierTafsili') };
      let html = '<div style="display:flex;gap:1.5rem;flex-wrap:wrap;font-size:0.9rem;">';
      Object.keys(tiers).forEach((k) => {
        html += `<div><strong>${fmt(tiers[k])}</strong> ${escapeHtml(tierLabels[k] || k)}</div>`;
      });
      html += `<div><strong>${fmt(split.bank_accounts || 0)}</strong> ${escapeHtml(t('migrationBankAccounts'))}</div>`;
      html += `<div><strong>${fmt(split.counterparties || 0)}</strong> ${escapeHtml(t('migrationCounterparties'))}${escapeHtml(_cpTypesLabel(s.counterparty_types))}</div>`;
      html += '</div>';
      const okColor = 'var(--success,#28a745)', badColor = 'var(--danger,#dc3545)';
      html += `<div style="margin-top:0.5rem;font-size:0.9rem;">${escapeHtml(t('migrationOpeningTotals'))}: `
        + `<strong>${fmt(opening.total_debit || 0)}</strong> / <strong>${fmt(opening.total_credit || 0)}</strong> — `
        + (opening.balanced
            ? `<span style="color:${okColor};">${escapeHtml(t('migrationBalancedYes'))}</span>`
            : `<span style="color:${badColor};">${escapeHtml(t('migrationBalancedNo'))} (${fmt(Math.abs(opening.difference || 0))})</span>`)
        + '</div>';
      let issues = '';
      if ((v.errors || []).length) {
        issues += `<div style="color:${badColor};font-size:0.85rem;"><strong>${escapeHtml(t('migrationErrorsLabel'))}:</strong><ul style="margin:0.25rem 0 0 1.25rem;">`
          + v.errors.map((e) => `<li>${escapeHtml(e)}</li>`).join('') + '</ul></div>';
      }
      if ((v.warnings || []).length) {
        issues += `<div style="color:var(--text-muted);font-size:0.85rem;margin-top:0.25rem;"><strong>${escapeHtml(t('migrationWarningsLabel'))}:</strong><ul style="margin:0.25rem 0 0 1.25rem;">`
          + v.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('') + '</ul></div>';
      }
      document.getElementById('migration-preview-summary').innerHTML = html;
      document.getElementById('migration-preview-issues').innerHTML = issues;
      document.getElementById('migration-confirm-btn').disabled = (v.errors || []).length > 0;
      document.getElementById('migration-preview').style.display = 'block';
    }
    async function migrationConfirm() {
      if (!_migrationToken) return;
      const statusEl = document.getElementById('migration-status');
      const btn = document.getElementById('migration-confirm-btn');
      btn.disabled = true;
      statusEl.textContent = '…';
      try {
        const payload = { token: _migrationToken };
        const dateVal = document.getElementById('migration-opening-date').value;
        if (dateVal) payload.opening_date = dateVal;
        const res = await fetch(API + '/migration/import/confirm', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          const d = err.detail;
          throw new Error(typeof d === 'string' ? d : (d && d.message) || res.statusText);
        }
        const body = await res.json();
        const r = body.result || {};
        const chart = r.chart || {};
        const ents = r.entities || {};
        const oj = r.opening_journal || {};
        const created = ['group', 'kol', 'moein'].reduce((a, k) => a + ((chart[k] || {}).created || 0), 0);
        const updated = ['group', 'kol', 'moein'].reduce((a, k) => a + ((chart[k] || {}).updated || 0), 0);
        const entCreated = (ents.banks_created || 0) + (ents.counterparties_created || 0);
        let html = `<div style="background:var(--bg-success,#d4edda);border:1px solid var(--success,#28a745);border-radius:8px;padding:1rem;font-size:0.9rem;">`
          + `<strong>${escapeHtml(t('migrationApplied'))}</strong><br>`
          + `${created} ${escapeHtml(t('migrationAccountsCreated'))}, ${updated} ${escapeHtml(t('migrationAccountsUpdated'))}, `
          + `${entCreated} ${escapeHtml(t('migrationEntitiesCreated'))}.<br>`
          + `${escapeHtml(t('migrationJournalPosted'))}: ${escapeHtml(formatDisplayDate(oj.opening_date) || '')}`
          + (oj.suspense_amount ? ` — ${escapeHtml(t('migrationBalancedNo'))} (${Number(oj.suspense_amount).toLocaleString()})` : '')
          + (oj.replaced_previous ? ` ${escapeHtml(t('migrationJournalReplaced'))}` : '')
          + `</div>`;
        const resEl = document.getElementById('migration-result');
        resEl.innerHTML = html;
        resEl.style.display = 'block';
        document.getElementById('migration-preview').style.display = 'none';
        statusEl.textContent = '';
        migrationLoadPending();
      } catch (e) {
        statusEl.textContent = e.message || String(e);
        btn.disabled = false;
      }
    }
    async function migrationLoadPending() {
      const el = document.getElementById('migration-queue');
      if (!el) return;
      try {
        const res = await fetch(API + '/migration/pending');
        if (!res.ok) { el.innerHTML = ''; return; }
        const rows = await res.json();
        _migrationPendingCache = rows;
        if (!rows.length) {
          el.innerHTML = `<p style="color:var(--text-muted);font-size:0.85rem;">${escapeHtml(t('migrationQueueEmpty'))}</p>`;
          return;
        }
        el.innerHTML = rows.map((r) => {
          const missing = (r.missing_fields || []).map(_migFieldLabel).join('، ');
          const flags = (r.review_flags || []).includes('type_ambiguous')
            ? `<span class="chip" style="font-size:0.75rem;">${escapeHtml(t('migrationReviewType'))}</span>` : '';
          return `<div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap;border:1px solid var(--border);border-radius:8px;padding:0.5rem 0.75rem;margin-bottom:0.5rem;">`
            + `<strong>${escapeHtml(r.entity_name)}</strong>`
            + `<span style="color:var(--text-muted);font-size:0.8rem;">${escapeHtml(r.entity_type)}${r.source_code ? ' · ' + escapeHtml(r.source_code) : ''}</span>`
            + (missing ? `<span style="font-size:0.8rem;">${escapeHtml(t('migrationMissing'))}: ${escapeHtml(missing)}</span>` : '')
            + flags
            + `<span style="margin-inline-start:auto;display:flex;gap:0.4rem;">`
            + `<button type="button" class="btn btn-secondary btn-sm" data-action="migration-ask-ai" data-id="${escapeHtml(String(r.id))}">${escapeHtml(t('migrationAiBtn'))}</button>`
            + `<button type="button" class="btn btn-secondary btn-sm" data-action="migration-resolve" data-id="${escapeHtml(String(r.id))}">${escapeHtml(t('migrationResolveBtn'))}</button>`
            + `<button type="button" class="btn btn-secondary btn-sm" data-action="migration-dismiss" data-id="${escapeHtml(String(r.id))}">${escapeHtml(t('migrationDismissBtn'))}</button>`
            + `</span></div>`;
        }).join('');
      } catch (_) { el.innerHTML = ''; }
    }
    async function migrationResolve(id) {
      try {
        const res = await fetch(API + '/migration/pending/' + id + '/resolve', { method: 'POST' });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          const d = err.detail;
          const missing = d && d.missing_fields ? d.missing_fields.map(_migFieldLabel).join('، ') : '';
          showAlert((d && d.message ? d.message : t('migrationMissing')) + (missing ? ': ' + missing : ''), true);
        }
      } catch (_) {}
      migrationLoadPending();
    }
    async function migrationDismiss(id) {
      try { await fetch(API + '/migration/pending/' + id + '/dismiss', { method: 'POST' }); } catch (_) {}
      migrationLoadPending();
    }
    function migrationAskAI(id) {
      const rec = _migrationPendingCache.find((r) => r.id === id);
      if (!rec) return;
      const missing = (rec.missing_fields || []).map(_migFieldLabel).join('، ');
      const msg = t('migrationAiPrompt')
        .replaceAll('{type}', rec.entity_type).replaceAll('{name}', rec.entity_name)
        .replaceAll('{missing}', missing).replaceAll('{id}', rec.entity_id);
      // opens the chat (restoring its latest session first) and sends
      if (typeof window.aiChatAsk === 'function') window.aiChatAsk(msg);
    }


    // ═══════ Recurring: manual form + run-due ═══════
    async function _recLoadSelectors() {
      try {
        // every account money sits in at a bank: the banks on file, the chart's
        // bank account and the ones opened under it (a chart-made 111001 was missing)
        const [banksRes, acctsRes] = await Promise.all([
          fetch(API + '/brain/bank-accounts'), fetch(API + '/accounts'),
        ]);
        const banks = banksRes.ok ? ((await banksRes.json()).accounts || []) : [];
        const accts = acctsRes.ok ? await acctsRes.json() : [];
        const bankSel = document.getElementById('rec-bank');
        if (bankSel) {
          const label = (b) => (b.bank && !String(b.name).includes(b.bank) ? b.bank + ' — ' : '') + b.name + ' (' + b.code + ')';
          bankSel.innerHTML = banks.map(b => `<option value="${escapeHtml(b.code)}">${escapeHtml(label(b))}</option>`).join('')
            || `<option value="">${escapeHtml(t('recNoBanks'))}</option>`;
        }
        const counterSel = document.getElementById('rec-counter');
        const pettySel = document.getElementById('petty-exp-cat');
        const opts = accts
          .map(a => `<option value="${escapeHtml(a.code)}">${escapeHtml(a.code)} — ${escapeHtml(a.name)}</option>`).join('');
        if (counterSel) counterSel.innerHTML = opts;
        if (pettySel) pettySel.innerHTML = `<option value="">${escapeHtml(t('pettyCatAuto'))}</option>` + opts;
      } catch (_) { /* selectors stay empty */ }
    }
    async function recurringInitPage() {
      _recLoadSelectors();
      const st = document.getElementById('rec-form-status');
      try {
        const run = await fetch(API + '/recurring/run-due', { method: 'POST' });
        if (run.ok) {
          const data = await run.json();
          if (st && (data.posted || []).length) {
            st.textContent = tf('recPostedNow', { n: data.posted.length });
          }
        }
      } catch (_) {}
      loadRecurringRules();
      loadDetectedRecurring();
    }
    (function wireRecurringForm() {
      const btn = document.getElementById('rec-manual-create');
      if (!btn) return;
      btn.addEventListener('click', async () => {
        const st = document.getElementById('rec-form-status');
        const name = document.getElementById('rec-name').value.trim();
        const amount = parseInt(document.getElementById('rec-amount').value, 10);
        const start = document.getElementById('rec-start').value;
        if (!name || !amount || !start) { st.textContent = t('recFormMissing'); return; }
        const payload = {
          name,
          direction: document.getElementById('rec-direction').value,
          frequency: document.getElementById('rec-frequency').value,
          amount,
          start_date: start,
          next_run_date: start,
          end_date: document.getElementById('rec-end').value || null,
          bank_account_code: document.getElementById('rec-bank').value || null,
          counter_account_code: document.getElementById('rec-counter').value || null,
          auto_post: document.getElementById('rec-autopost').checked,
        };
        try {
          const res = await fetch(API + '/recurring', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { st.textContent = (typeof data.detail === 'string' ? data.detail : t('recFormFailed')); return; }
          st.textContent = t('recFormSaved');
          document.getElementById('rec-name').value = '';
          document.getElementById('rec-amount').value = '';
          recurringInitPage();
        } catch (e) { st.textContent = e.message; }
      });
      const runBtn = document.getElementById('rec-run-due');
      if (runBtn) runBtn.addEventListener('click', recurringInitPage);
    })();

    // ═══════ Notifications bell + reminders ═══════
    let _notifyOpen = false;
    async function notifyRefresh() {
      try {
        const res = await fetch(API + '/notifications/feed');
        if (!res.ok) return;
        const items = await res.json();
        const unread = items.filter(i => !i.read).length;
        const badge = document.getElementById('notify-badge');
        if (badge) {
          badge.style.display = unread ? 'block' : 'none';
          badge.textContent = unread > 99 ? '99+' : String(unread);
        }
        const list = document.getElementById('notify-list');
        if (!list) return;
        if (!items.length) {
          list.innerHTML = `<div class="empty-state" style="font-size:0.85rem;">${escapeHtml(t('notifyEmpty'))}</div>`;
          return;
        }
        const colors = { high: 'var(--danger,#dc3545)', warning: '#d97706', info: 'var(--text-muted)' };
        list.innerHTML = items.map(i => `
          <div class="notify-item" data-id="${i.id}" data-page="${escapeHtml(i.link_page || '')}"
               style="display:flex; gap:0.5rem; padding:0.4rem 0.3rem; border-radius:6px; cursor:pointer; ${i.read ? 'opacity:0.65;' : ''}">
            <span style="width:8px; height:8px; margin-top:6px; border-radius:50%; flex-shrink:0; background:${colors[i.level] || colors.info};"></span>
            <span style="flex:1; min-width:0;">
              <span style="display:block; font-size:0.85rem; font-weight:${i.read ? '400' : '600'};">${escapeHtml(i.title)}</span>
              <span style="display:block; font-size:0.75rem; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis;">${escapeHtml(i.message || '')}</span>
            </span>
          </div>`).join('');
        list.querySelectorAll('.notify-item').forEach(el => {
          el.addEventListener('click', async () => {
            await fetch(API + '/notifications/feed/' + el.dataset.id + '/read', { method: 'POST' }).catch(() => {});
            const page = el.dataset.page;
            if (page && typeof showPage === 'function') {
              showPage(page);
              if (typeof loadPageData === 'function') loadPageData(page);
              _toggleNotify(false);
            }
            notifyRefresh();
          });
        });
      } catch (_) {}
    }
    async function remRefresh() {
      try {
        const res = await fetch(API + '/notifications/reminders');
        if (!res.ok) return;
        const rows = await res.json();
        const list = document.getElementById('rem-list');
        if (!list) return;
        list.innerHTML = rows.length ? rows.map(r => `
          <div style="display:flex; gap:0.4rem; align-items:center; padding:0.25rem 0.3rem; font-size:0.82rem;">
            <span style="flex:1; ${r.status === 'paused' ? 'opacity:0.5;' : ''}">${escapeHtml(r.title)} · ${escapeHtml(formatDisplayDate(r.due_date))}${r.repeat !== 'none' ? ' ↻' : ''}</span>
            <button type="button" class="rem-toggle" data-id="${r.id}" data-status="${r.status}" style="border:none;background:none;cursor:pointer;">${r.status === 'paused' ? '▶' : '⏸'}</button>
            <button type="button" class="rem-del" data-id="${r.id}" style="border:none;background:none;cursor:pointer;">🗑</button>
          </div>`).join('')
          : `<div style="font-size:0.78rem; color:var(--text-muted);">${escapeHtml(t('remEmpty'))}</div>`;
        list.querySelectorAll('.rem-toggle').forEach(b => b.addEventListener('click', async () => {
          await fetch(API + '/notifications/reminders/' + b.dataset.id, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: b.dataset.status === 'paused' ? 'active' : 'paused' }),
          });
          remRefresh(); notifyRefresh();
        }));
        list.querySelectorAll('.rem-del').forEach(b => b.addEventListener('click', async () => {
          await fetch(API + '/notifications/reminders/' + b.dataset.id, { method: 'DELETE' });
          remRefresh(); notifyRefresh();
        }));
      } catch (_) {}
    }
    function _toggleNotify(open) {
      const pop = document.getElementById('notify-pop');
      if (!pop) return;
      _notifyOpen = open === undefined ? !_notifyOpen : open;
      pop.style.display = _notifyOpen ? 'block' : 'none';
      if (_notifyOpen) { notifyRefresh(); remRefresh(); }
    }
    (function wireNotify() {
      const bell = document.getElementById('notify-bell-btn');
      if (!bell) return;
      bell.addEventListener('click', (e) => { e.stopPropagation(); _toggleNotify(); });
      document.addEventListener('click', (e) => {
        const pop = document.getElementById('notify-pop');
        if (_notifyOpen && pop && !pop.contains(e.target)) _toggleNotify(false);
      });
      const readAll = document.getElementById('notify-read-all');
      if (readAll) readAll.addEventListener('click', async () => {
        await fetch(API + '/notifications/feed/read-all', { method: 'POST' }).catch(() => {});
        notifyRefresh();
      });
      const addBtn = document.getElementById('rem-new-add');
      if (addBtn) addBtn.addEventListener('click', async () => {
        const title = document.getElementById('rem-new-title').value.trim();
        const due = document.getElementById('rem-new-date').value;
        if (!title || !due) return;
        await fetch(API + '/notifications/reminders', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title, due_date: due,
            repeat: document.getElementById('rem-new-repeat').value, days_before: 3 }),
        }).catch(() => {});
        document.getElementById('rem-new-title').value = '';
        remRefresh(); notifyRefresh();
      });
      // badge refresh on load + every 90s
      notifyRefresh();
      setInterval(notifyRefresh, 90000);
    })();

    // ═══════ Petty cash page ═══════
    let _pettyOwnAccount = null;
    let _pettyAttachment = null;
    function _pettyIsManager() { return ['owner', 'cfo', 'accountant'].includes(currentRole); }
    async function pettyInitPage() {
      _recLoadSelectors();
      const adminSec = document.getElementById('petty-admin-section');
      if (adminSec) adminSec.style.display = _pettyIsManager() ? 'block' : 'none';
      try {
        const res = await fetch(API + '/petty-cash/accounts');
        if (!res.ok) return;
        const accounts = await res.json();
        // my own account = the one whose user_id matches me (admins may also hold one)
        const meRes = await fetch(API + '/auth/me').catch(() => null);
        const me = meRes && meRes.ok ? await meRes.json() : {};
        const myId = (me.user && me.user.id) || me.user_id || me.id || null;
        _pettyOwnAccount = accounts.find(a => a.user_id === myId) ||
                           (!_pettyIsManager() ? accounts[0] : null);
        _pettyRenderOwn();
        if (_pettyIsManager()) _pettyRenderAdmin(accounts);
      } catch (_) {}
    }
    async function _pettyRenderOwn() {
      const none = document.getElementById('petty-own-none');
      const wrap = document.getElementById('petty-own-wrap');
      if (!_pettyOwnAccount) { none.style.display = 'block'; wrap.style.display = 'none'; return; }
      none.style.display = 'none'; wrap.style.display = 'block';
      try {
        const res = await fetch(API + '/petty-cash/accounts/' + _pettyOwnAccount.id);
        if (!res.ok) return;
        const acc = await res.json();
        document.getElementById('petty-own-balance').textContent = formatMoney(acc.balance, baseCurrencyCode());
        const kinds = { deposit: t('pettyKindDeposit'), expense: t('pettyKindExpense'), adjustment: t('pettyKindAdjust') };
        const stats = { pending: t('pettyStPending'), approved: t('pettyStApproved'), rejected: t('pettyStRejected') };
        document.getElementById('petty-own-tbody').innerHTML = (acc.transactions || []).map(x => `
          <tr><td>${escapeHtml((x.created_at || '').slice(0, 10))}</td>
              <td>${escapeHtml(kinds[x.kind] || x.kind)}</td>
              <td class="num">${formatMoney(x.signed_amount, baseCurrencyCode())}</td>
              <td>${escapeHtml(x.description || '')}</td>
              <td>${escapeHtml(stats[x.status] || x.status)}</td></tr>`).join('')
          || `<tr><td colspan="5" class="empty-state">—</td></tr>`;
      } catch (_) {}
    }
    async function _pettyRenderAdmin(accounts) {
      const tbody = document.getElementById('petty-admin-tbody');
      tbody.innerHTML = accounts.map(a => `
        <tr><td>${escapeHtml(a.holder_name)}</td>
            <td class="num">${formatMoney(a.balance, baseCurrencyCode())}</td>
            <td class="num">${a.pending_expenses || 0}</td>
            <td>
              <button type="button" class="btn btn-secondary btn-sm petty-deposit" data-id="${a.id}" data-name="${escapeHtml(a.holder_name)}">${escapeHtml(t('pettyDepositBtn'))}</button>
              <button type="button" class="btn btn-secondary btn-sm petty-adjust" data-id="${a.id}" data-name="${escapeHtml(a.holder_name)}">${escapeHtml(t('pettyAdjustBtn'))}</button>
            </td></tr>`).join('')
        || `<tr><td colspan="4" class="empty-state">—</td></tr>`;
      tbody.querySelectorAll('.petty-deposit').forEach(b => b.addEventListener('click', async () => {
        // the app's own dialogs (the browser's speak its language, not the page's); Persian digits count
        const typed = await uiPrompt({ title: t('pettyDepositBtn'), message: tf('pettyDepositPrompt', { currency: currencySymbol(baseCurrencyCode()) }) + ' — ' + b.dataset.name });
        const amount = parseInt(asciiDigits(typed || ''), 10);
        if (!amount || amount <= 0) return;
        const bank = await uiPrompt({ title: t('pettyDepositBtn'), message: t('pettyDepositBankPrompt'),
                                      value: document.getElementById('rec-bank')?.value || '1110' });
        if (!bank) return;
        const res = await fetch(API + '/petty-cash/accounts/' + b.dataset.id + '/deposit', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount, bank_account_code: asciiDigits(bank) }),
        });
        if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(d.detail || t('msgFailed'), true); }
        pettyInitPage();
      }));
      tbody.querySelectorAll('.petty-adjust').forEach(b => b.addEventListener('click', async () => {
        const typed = await uiPrompt({ title: t('pettyAdjustBtn'), message: tf('pettyAdjustPrompt', { currency: currencySymbol(baseCurrencyCode()) }) + ' — ' + b.dataset.name });
        const signed = parseInt(asciiDigits(typed || ''), 10);
        if (!signed) return;
        const counter = await uiPrompt({ title: t('pettyAdjustBtn'), message: t('pettyDepositBankPrompt'), value: '1110' });
        if (!counter) return;
        const res = await fetch(API + '/petty-cash/accounts/' + b.dataset.id + '/adjust', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ signed_amount: signed, counter_account_code: asciiDigits(counter),
                                 description: t('pettyAdjustBtn') }),
        });
        if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(d.detail || t('msgFailed'), true); }
        pettyInitPage();
      }));
      // pending approvals list
      const wrap = document.getElementById('petty-pending-wrap');
      const pend = [];
      for (const a of accounts.filter(x => x.pending_expenses > 0)) {
        const det = await (await fetch(API + '/petty-cash/accounts/' + a.id)).json();
        (det.transactions || []).filter(x => x.status === 'pending' && x.kind === 'expense')
          .forEach(x => pend.push({ ...x, holder: a.holder_name }));
      }
      wrap.innerHTML = pend.length ? `
        <strong style="font-size:0.9rem;">${escapeHtml(t('pettyPendingTitle'))}</strong>
        ${pend.map(x => `
          <div style="display:flex; gap:0.6rem; align-items:center; border:1px solid var(--border); border-radius:6px; padding:0.4rem 0.6rem; margin-top:0.3rem; font-size:0.85rem;">
            <span style="flex:1;">${escapeHtml(x.holder)} — ${formatMoney(x.amount, baseCurrencyCode())} · ${escapeHtml(x.description || '')}</span>
            <button type="button" class="btn btn-primary btn-sm petty-approve" data-id="${x.id}">${escapeHtml(t('pettyApproveBtn'))}</button>
            <button type="button" class="btn btn-danger btn-sm petty-reject" data-id="${x.id}">${escapeHtml(t('pettyRejectBtn'))}</button>
          </div>`).join('')}` : '';
      wrap.querySelectorAll('.petty-approve').forEach(b => b.addEventListener('click', async () => {
        await fetch(API + '/petty-cash/expenses/' + b.dataset.id + '/approve', { method: 'POST' });
        pettyInitPage(); notifyRefresh();
      }));
      wrap.querySelectorAll('.petty-reject').forEach(b => b.addEventListener('click', async () => {
        await fetch(API + '/petty-cash/expenses/' + b.dataset.id + '/reject', { method: 'POST' });
        pettyInitPage(); notifyRefresh();
      }));
    }
    (function wirePettyForms() {
      const createBtn = document.getElementById('petty-create-account');
      if (createBtn) createBtn.addEventListener('click', async () => {
        const st = document.getElementById('petty-admin-status');
        const username = document.getElementById('petty-new-username').value.trim();
        if (!username) { st.textContent = t('pettyNewUser'); return; }
        const res = await fetch(API + '/petty-cash/accounts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username,
            holder_name: document.getElementById('petty-new-holder').value.trim() || null }),
        });
        const d = await res.json().catch(() => ({}));
        st.textContent = res.ok ? t('pettyCreated') : (typeof d.detail === 'string' ? d.detail : t('msgFailed'));
        if (res.ok) { document.getElementById('petty-new-username').value = ''; pettyInitPage(); }
      });
      const attachBtn = document.getElementById('petty-exp-attach');
      const fileEl = document.getElementById('petty-exp-file');
      if (attachBtn && fileEl) {
        attachBtn.addEventListener('click', () => fileEl.click());
        fileEl.addEventListener('change', async () => {
          const f = fileEl.files && fileEl.files[0];
          if (!f) return;
          const fd = new FormData();
          fd.append('file', f);
          const res = await fetch(API + '/transactions/attachments', { method: 'POST', body: fd });
          if (res.ok) {
            _pettyAttachment = await res.json();
            document.getElementById('petty-exp-attach-name').textContent = _pettyAttachment.file_name;
          }
          fileEl.value = '';
        });
      }
      const submitBtn = document.getElementById('petty-exp-submit');
      if (submitBtn) submitBtn.addEventListener('click', async () => {
        if (!_pettyOwnAccount) return;
        const amount = parseInt(document.getElementById('petty-exp-amount').value, 10);
        const desc = document.getElementById('petty-exp-desc').value.trim();
        if (!amount || !desc) { showAlert(t('recFormMissing'), true); return; }
        const res = await fetch(API + '/petty-cash/accounts/' + _pettyOwnAccount.id + '/expenses', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount, description: desc,
            category_account_code: document.getElementById('petty-exp-cat').value || null,
            attachment_id: _pettyAttachment ? _pettyAttachment.id : null }),
        });
        if (res.ok) {
          document.getElementById('petty-exp-amount').value = '';
          document.getElementById('petty-exp-desc').value = '';
          document.getElementById('petty-exp-attach-name').textContent = '';
          _pettyAttachment = null;
          _pettyRenderOwn(); pettyInitPage();
        } else {
          const d = await res.json().catch(() => ({}));
          showAlert(typeof d.detail === 'string' ? d.detail : t('msgFailed'), true);
        }
      });
    })();

    // ═══════ Shareholders & Equity ═══════
    let _equityCurrency = '';
    function _eqFmt(n) { return formatNum(n) + (_equityCurrency ? ' ' + _equityCurrency : ''); }
    async function _equityShareholderOptions() {
      const res = await fetch(API + '/entities?type=shareholder');
      const rows = res.ok ? await res.json().catch(() => []) : [];
      const opts = '<option value="">' + escapeHtml(t('equitySelectShareholder')) + '</option>'
        + (rows || []).map(e => `<option value="${escapeHtml(e.id)}">${escapeHtml(e.name)}</option>`).join('');
      ['equity-sh-entity', 'equity-contrib-entity', 'equity-ca-entity', 'equity-pay-entity'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = opts;
      });
      return (rows || []);
    }
    async function loadEquity() {
      if (!document.getElementById('equity-captable-body')) return;
      _equityWire();
      // the unit amounts are in: the company's own currency (it said "the smallest unit, e.g. Rials",
      // which to a UK company read as pence)
      const hint = document.getElementById('equity-amount-hint');
      if (hint) {
        const ccy = baseCurrencyCode(), sym = currencySymbol(ccy);
        hint.textContent = tf('equityAmountHint', { currency: sym && sym !== ccy ? ccy + ' (' + sym + ')' : ccy });
      }
      const today = localIsoDate(new Date());
      ['equity-contrib-date', 'equity-div-date', 'equity-ci-date', 'equity-ca-date', 'equity-pay-date'].forEach(id => {
        const el = document.getElementById(id); if (el && !el.value) el.value = today;
      });
      await _equityShareholderOptions();
      try {
        const res = await fetch(API + '/equity/cap-table');
        if (!res.ok) throw new Error(res.statusText);
        const data = await res.json();
        _equityCurrency = data.currency || '';
        document.getElementById('equity-registered-capital').textContent = _eqFmt(data.registered_capital || 0);
        document.getElementById('equity-total-paidin').textContent = _eqFmt(data.total_paid_in || 0);
        document.getElementById('equity-total-percent').textContent = (data.total_percent || 0) + '%';
        const body = document.getElementById('equity-captable-body');
        if (!data.rows || !data.rows.length) {
          body.innerHTML = '<tr><td colspan="7" class="empty-state">' + escapeHtml(t('equityNoShareholders')) + '</td></tr>';
          return;
        }
        body.innerHTML = data.rows.map(r => `
          <tr class="equity-row" data-entity-id="${escapeHtml(r.entity_id)}" data-name="${escapeHtml(r.entity_name || '')}" style="cursor:pointer;">
            <td>${escapeHtml(r.entity_name || '—')}</td>
            <td class="num">${r.percent != null ? r.percent + '%' : '—'}</td>
            <td class="num">${r.shares != null ? formatNum(r.shares) : '—'}</td>
            <td class="num">${formatNum(r.paid_in)}</td>
            <td class="num">${formatNum(r.dividends_declared)}</td>
            <td class="num ${r.dividends_outstanding > 0 ? 'ledger-negative' : ''}">${formatNum(r.dividends_outstanding)}</td>
            <td><button type="button" class="btn btn-secondary btn-sm equity-del" data-id="${escapeHtml(r.id)}" data-name="${escapeHtml(r.entity_name || '')}">${escapeHtml(t('btnDelete'))}</button></td>
          </tr>`).join('');
      } catch (err) {
        document.getElementById('equity-captable-body').innerHTML =
          '<tr><td colspan="7" class="empty-state">' + escapeHtml(t('equityLoadError')) + '</td></tr>';
      }
    }
    async function _equityPost(url, payload, okKey) {
      const res = await fetch(API + url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { showAlert(data.detail || t('equityPostError'), true); return null; }
      showAlert(t(okKey) + (data.summary_lines && data.summary_lines.length ? ' — ' + data.summary_lines.join(' · ') : ''));
      loadEquity();
      return data;
    }
    function _equityWire() {
      const add = document.getElementById('equity-sh-add');
      if (!add || add._wired) return; add._wired = true;
      add.addEventListener('click', async () => {
        const entity_id = document.getElementById('equity-sh-entity').value;
        if (!entity_id) { showAlert(t('equitySelectShareholder'), true); return; }
        const percent = parseFloat(document.getElementById('equity-sh-percent').value) || null;
        const shares = parseInt(document.getElementById('equity-sh-shares').value) || null;
        const share_class = document.getElementById('equity-sh-class').value;
        const res = await fetch(API + '/equity/shareholdings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ entity_id, percent, shares, share_class }) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || t('equityPostError'), true); return; }
        showAlert(t('equityShareholderAdded')); loadEquity();
      });
      document.getElementById('equity-contrib-post').addEventListener('click', () => {
        const entity_id = document.getElementById('equity-contrib-entity').value;
        if (!entity_id) { showAlert(t('equitySelectShareholder'), true); return; }
        _equityPost('/equity/contribution', {
          entity_id, amount: parseInt(document.getElementById('equity-contrib-amount').value) || 0,
          date: document.getElementById('equity-contrib-date').value,
          to_capital: document.getElementById('equity-contrib-tocapital').checked,
        }, 'equityPostedContribution');
      });
      document.getElementById('equity-div-post').addEventListener('click', () => {
        _equityPost('/equity/dividend/declare', {
          total_amount: parseInt(document.getElementById('equity-div-amount').value) || 0,
          date: document.getElementById('equity-div-date').value,
        }, 'equityPostedDividend');
      });
      document.getElementById('equity-ci-post').addEventListener('click', () => {
        _equityPost('/equity/capital-increase', {
          amount: parseInt(document.getElementById('equity-ci-amount').value) || 0,
          source: document.getElementById('equity-ci-source').value,
          date: document.getElementById('equity-ci-date').value,
        }, 'equityPostedCapital');
      });
      document.getElementById('equity-ca-post').addEventListener('click', () => {
        const entity_id = document.getElementById('equity-ca-entity').value;
        if (!entity_id) { showAlert(t('equitySelectShareholder'), true); return; }
        _equityPost('/equity/current-account', {
          entity_id, amount: parseInt(document.getElementById('equity-ca-amount').value) || 0,
          direction: document.getElementById('equity-ca-direction').value,
          date: document.getElementById('equity-ca-date').value,
        }, 'equityPostedCurrentAccount');
      });
      document.getElementById('equity-pay-post').addEventListener('click', () => {
        const entity_id = document.getElementById('equity-pay-entity').value;
        if (!entity_id) { showAlert(t('equitySelectShareholder'), true); return; }
        _equityPost('/equity/dividend/pay', {
          entity_id, amount: parseInt(document.getElementById('equity-pay-amount').value) || 0,
          date: document.getElementById('equity-pay-date').value,
        }, 'equityPostedPayment');
      });
      // cap-table row → per-shareholder ledger; delete button
      document.getElementById('equity-captable-body').addEventListener('click', async (e) => {
        const del = e.target.closest('.equity-del');
        if (del) {
          e.stopPropagation();
          if (!(await uiConfirm({ message: tf('equityConfirmDelete', { name: del.dataset.name }), danger: true }))) return;
          const res = await fetch(API + '/equity/shareholdings/' + encodeURIComponent(del.dataset.id), { method: 'DELETE' });
          if (res.ok) { showAlert(t('equityShareholderRemoved')); loadEquity(); }
          else { const d = await res.json().catch(() => ({})); showAlert(d.detail || t('equityPostError'), true); }
          return;
        }
        const row = e.target.closest('.equity-row');
        if (row) openShareholderLedger(row.dataset.entityId, row.dataset.name);
      });
    }
    async function openShareholderLedger(entityId, name) {
      const modal = document.getElementById('account-modal');
      const body = document.getElementById('account-modal-body');
      const title = document.getElementById('account-modal-title');
      title.textContent = (name || '') + ' — ' + t('equityLedgerTitle');
      body.innerHTML = '<p class="empty-state">' + t('loading') + '</p>';
      modal.style.display = 'flex';
      try {
        const yr = new Date().getFullYear();
        const res = await fetch(API + '/manager-reports/operational/person-running-balance?role=shareholder'
          + '&entity_id=' + encodeURIComponent(entityId)
          + '&from_date=' + (yr - 1) + '-01-01&to_date=' + yr + '-12-31');
        if (!res.ok) throw new Error(res.statusText);
        const data = await res.json();
        const rows = (data.rows || []).map(r => `
          <tr><td>${escapeHtml(formatDisplayDate(r.date || ''))}</td><td>${escapeHtml(r.description || r.reference || '')}</td>
          <td class="num">${r.debit_effect ? formatNum(r.debit_effect) : ''}</td>
          <td class="num">${r.credit_effect ? formatNum(r.credit_effect) : ''}</td>
          <td class="num">${formatNum(r.running_balance)}</td></tr>`).join('');
        body.innerHTML = `
          <p style="margin:0 0 0.6rem;color:var(--text-muted);font-size:0.84rem;">${escapeHtml(t('equityLedgerHint'))} · ${escapeHtml(t('equityColOutstandingClaim'))}: <strong>${formatNum(data.closing_balance)}</strong></p>
          <div style="max-height:400px;overflow:auto;"><table class="detail-table"><thead><tr>
            <th>${escapeHtml(t('labelDate'))}</th><th>${escapeHtml(t('labelDescription'))}</th>
            <th class="num">${escapeHtml(t('equityColDebit'))}</th><th class="num">${escapeHtml(t('equityColCredit'))}</th>
            <th class="num">${escapeHtml(t('equityColBalance'))}</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="5" class="empty-state">' + escapeHtml(t('equityLedgerEmpty')) + '</td></tr>'}</tbody></table></div>`;
      } catch (err) {
        body.innerHTML = '<p class="empty-state">' + escapeHtml(t('equityLoadError')) + '</p>';
      }
    }

    // ═══════ Installments (اقساط) & cheques (چک) ═══════
    // A cheque's life (roadmap §3.4): in hand → deposited → cleared / bounced →
    // deposited again or returned; passed on; Sayad registration. Each step
    // is one POST; the server decides what it posts (app/services/cheques.py).
    let _cmRows = [];
    let _cmBanks = [];
    let _cmAccts = [];

    async function cmFillAccounts() {
      const sels = [document.getElementById('cm-p-acct'), document.getElementById('cm-c-acct')];
      if (!sels[0] || sels[0].options.length) return;
      try {
        const [accRes, bankRes] = await Promise.all([
          fetch(API + '/manager-reports/accounts/list'), fetch(API + '/entities?type=bank')]);
        if (accRes.ok) _cmAccts = await accRes.json();
        if (bankRes.ok) _cmBanks = (await bankRes.json()).filter(b => b.code);
        // Settling moves money against a liability or a receivable, not an
        // expense — offer only those, plus a blank for tracking-only items.
        const opts = `<option value="">${escapeHtml(t('cmNoPosting'))}</option>` + _cmAccts
          .filter(a => (a.code || '').startsWith('2') || (a.code || '').startsWith('1'))
          .map(a => `<option value="${escapeHtml(a.code)}">${escapeHtml(a.code)} — ${escapeHtml(a.name)}</option>`)
          .join('');
        sels.forEach(s => { if (s) s.innerHTML = opts; });
      } catch (_) { /* offline */ }
    }

    // Open invoices (received cheque) or bills (issued) a new cheque can pay.
    async function cmFillInvoices(selId, kind) {
      const sel = document.getElementById(selId);
      if (!sel) return;
      sel.innerHTML = `<option value="">${escapeHtml(t('cmNoInvoice'))}</option>`;
      try {
        const res = await fetch(API + '/invoices?kind=' + kind);
        if (!res.ok) return;
        const rows = (await res.json()).filter(r => r.balance_due > 0 && !['draft', 'voided', 'canceled'].includes(r.status));
        sel.innerHTML += rows.map(r => `<option value="${escapeHtml(r.id)}">${escapeHtml(r.number)} — ${escapeHtml(formatNum(r.balance_due))}</option>`).join('');
      } catch (_) { /* offline */ }
    }

    function cmStatusChip(row) {
      const today = localIsoDate();
      const chip = (level, key) => '<span class="alert-chip ' + level + '">' + escapeHtml(t(key)) + '</span>';
      const cheque = row.kind === 'cheque';
      let out;
      if (row.status === 'settled') out = chip('low', cheque ? (row.direction === 'receive' ? 'cmClearedIn' : 'cmClearedOut') : 'cmSettled');
      else if (row.status === 'bounced') out = chip('high', 'cmBounced');
      else if (row.status === 'returned') out = chip('low', 'cmReturned');
      else if (row.status === 'endorsed') out = chip('low', 'cmEndorsed');
      else if (row.status === 'deposited') out = chip('medium', 'cmDeposited');
      else if (row.due_date < today) out = chip('high', 'cmOverdue');
      else out = chip('medium', cheque ? (row.direction === 'receive' ? 'cmInHand' : 'cmIssued') : 'cmPending');
      if (row.needs_sayad) out += ' <span class="alert-chip high" title="' + escapeHtml(t('cmSayadHint')) + '">' + escapeHtml(t('cmSayadMissing')) + '</span>';
      return out;
    }

    // The steps a row can take next.
    function cmSteps(r) {
      if (r.kind !== 'cheque') return r.status === 'pending' ? ['settle'] : [];
      const rec = r.direction === 'receive';
      const steps = {
        pending: rec ? ['deposit', 'clear', 'bounce', 'endorse', 'return'] : ['clear', 'bounce', 'return'],
        deposited: ['clear', 'bounce'],
        bounced: rec ? ['redeposit', 'clear', 'return'] : ['clear', 'return'],
      }[r.status] || [];
      if (['pending', 'deposited'].includes(r.status) && !r.sayad_registered_on && companyLocale !== 'uk') steps.push('sayad');
      if (!rec && ['pending', 'bounced'].includes(r.status)) steps.unshift('print');   // we write the ones we issue
      return steps;
    }
    const CM_STEP_LABEL = { settle: 'cmSettle', deposit: 'cmActDeposit', redeposit: 'cmActDepositAgain', clear: 'cmActClear',
      bounce: 'cmActBounce', return: 'cmActReturn', endorse: 'cmActEndorse', sayad: 'cmActSayad', print: 'cmActPrint' };

    function cmRowHtml(r) {
      const seq = (r.sequence && r.plan_total) ? ` (${r.sequence}/${r.plan_total})` : '';
      const who = r.direction === 'pay' ? t('cmDirPay') : t('cmDirReceive');
      const meta = [];
      if (r.reference) meta.push('#' + r.reference);
      if (r.sayad_id) meta.push(t('cmFieldSayad') + ' ' + r.sayad_id);
      if (r.bank_name) meta.push(r.bank_name);
      if (r.invoice_number) meta.push(tf('cmPaysInvoice', { n: r.invoice_number }));
      if (r.endorsed_to) meta.push(tf('cmPassedTo', { to: r.endorsed_to }));
      const steps = cmSteps(r);
      const actions = (steps.length
        ? `<select class="cm-step" data-id="${escapeHtml(r.id)}" aria-label="${escapeHtml(t('cmColActions'))}">
             <option value="">${escapeHtml(t('cmActions'))}</option>
             ${steps.map(st => `<option value="${st}">${escapeHtml(t(CM_STEP_LABEL[st]))}</option>`).join('')}
           </select>` : '<span aria-hidden="true">✓</span>')
        + (r.kind === 'cheque' ? `<button type="button" class="btn btn-secondary btn-sm cm-history" data-id="${escapeHtml(r.id)}" aria-expanded="false">${escapeHtml(t('cmActHistory'))}</button>` : '');
      return `<tr data-row="${escapeHtml(r.id)}">
        <td class="cm-due" data-label="${escapeHtml(t('cmColDue'))}">${escapeHtml(formatDisplayDate(r.due_date))}</td>
        <td class="cm-what" dir="auto">${escapeHtml(r.title)}${escapeHtml(seq)} <span style="color:var(--text-muted); font-size:0.8rem;">${escapeHtml(who)}</span>
          ${meta.length ? `<div class="cm-meta" dir="auto">${escapeHtml(meta.join(' · '))}</div>` : ''}</td>
        <td class="cm-amount" data-label="${escapeHtml(t('cmColAmount'))}">${escapeHtml(formatNum(r.amount))}</td>
        <td class="cm-status">${cmStatusChip(r)}</td>
        <td class="cm-act"><div class="cm-actions">${actions}</div></td>
      </tr>`;
    }

    function cmRender() {
      const body = document.getElementById('cm-rows');
      if (!body) return;
      const f = document.getElementById('cm-filter')?.value || 'open';
      const open = ['pending', 'deposited', 'bounced'];
      const rows = _cmRows.filter(r => f === 'all' ? true : f === 'open' ? open.includes(r.status) : r.kind === f);
      body.innerHTML = rows.length ? rows.map(cmRowHtml).join('')
        : `<tr><td colspan="5" class="empty-state" style="padding:0.6rem;">${escapeHtml(t('cmNone'))}</td></tr>`;
    }

    async function loadCommitments() {
      const body = document.getElementById('cm-rows');
      if (!body) return;
      await cmFillAccounts();
      cmLoadLayout();
      const on = document.getElementById('cm-c-on');
      if (on && !on.value) on.value = localIsoDate();
      cmFillInvoices('cm-c-inv', document.getElementById('cm-c-dir')?.value === 'receive' ? 'sales' : 'purchase');
      try {
        const [rows, sum] = await Promise.all([
          (await fetch(API + '/commitments')).json(),
          (await fetch(API + '/commitments/summary')).json(),
        ]);
        const kpi = (label, value) => `<div class="kpi-card"><div class="label">${escapeHtml(t(label))}</div><div class="value">${value}</div></div>`;
        const money = (n) => `${escapeHtml(formatNum(n))} ${escapeHtml(currencyUnit())}`;
        document.getElementById('cm-summary').innerHTML =
          kpi('cmYouOwe', money(sum.payable)) + kpi('cmOwedToYou', money(sum.receivable))
          + kpi('cmNextDue', sum.next_due_date ? escapeHtml(formatDisplayDate(sum.next_due_date)) : '—')
          + (sum.cheques_in_hand ? kpi('cmInHandTotal', money(sum.cheques_in_hand)) : '')
          + (sum.cheques_at_bank ? kpi('cmAtBankTotal', money(sum.cheques_at_bank)) : '')
          + (sum.cheques_bounced ? kpi('cmBouncedTotal', money(sum.cheques_bounced)) : '');
        _cmRows = Array.isArray(rows) ? rows : [];
        cmRender();
      } catch (_) {
        body.innerHTML = `<tr><td colspan="5" class="empty-state">${escapeHtml(t('cmNone'))}</td></tr>`;
      }
    }

    async function cmShowHistory(btn) {
      const tr = btn.closest('tr');
      const next = tr.nextElementSibling;
      if (next && next.classList.contains('cm-history-row')) {
        next.remove(); btn.setAttribute('aria-expanded', 'false'); return;
      }
      btn.setAttribute('aria-expanded', 'true');
      const row = document.createElement('tr');
      row.className = 'cm-history-row';
      row.innerHTML = `<td colspan="5" style="background:var(--surface-2, #f8fafc);">…</td>`;
      tr.insertAdjacentElement('afterend', row);
      try {
        const data = await (await fetch(API + `/commitments/${btn.dataset.id}/history`)).json();
        const ev = data.events || [];
        row.firstElementChild.innerHTML = ev.length
          ? '<ol class="cm-history" style="margin:0; padding-inline-start:1.2rem; font-size:0.85rem;">' + ev.map(e =>
              `<li>${escapeHtml(formatDisplayDate(e.on))} — ${escapeHtml(t('cmEv_' + e.action) || e.action)}${e.note ? ' <span dir="auto" style="color:var(--text-muted);">(' + escapeHtml(e.note) + ')</span>' : ''}${e.transaction_id ? ' <span style="color:var(--text-muted); font-size:0.75rem;">· ' + escapeHtml(t('cmEvPosted')) + '</span>' : ''}</li>`).join('') + '</ol>'
          : escapeHtml(t('cmNoHistory'));
      } catch (_) { row.firstElementChild.textContent = t('cmNoHistory'); }
    }

    // One dialog for every step; only the fields a step needs are shown.
    function cmStepDialog(row, step) {
      const modal = document.getElementById('cm-step-modal');
      const show = (id, on) => { const el = document.getElementById(id); if (el) el.style.display = on ? '' : 'none'; };
      document.getElementById('cm-step-title').textContent = t(CM_STEP_LABEL[step]);
      document.getElementById('cm-step-what').textContent = `${row.title} — ${formatNum(row.amount)} ${currencyUnit()}`;
      // a cheque is dated the day it falls due
      document.getElementById('cm-step-date').value = step === 'print' ? row.due_date : localIsoDate();
      const bankSel = document.getElementById('cm-step-bank');
      bankSel.innerHTML = `<option value="">${escapeHtml(t('cmDefaultBank'))}</option>` + _cmBanks
        .map(b => `<option value="${escapeHtml(b.code)}">${escapeHtml(b.name)} (${escapeHtml(b.code)})</option>`).join('');
      if (row.deposit_account_code) bankSel.value = row.deposit_account_code;
      document.getElementById('cm-step-sayad').value = row.sayad_id || '';
      document.getElementById('cm-step-to').value = '';
      document.getElementById('cm-step-note').value = '';
      document.getElementById('cm-step-acct').innerHTML = `<option value="">—</option>` + _cmAccts
        .filter(a => /^[12]/.test(a.code || ''))
        .map(a => `<option value="${escapeHtml(a.code)}">${escapeHtml(a.code)} — ${escapeHtml(a.name)}</option>`).join('');
      show('cm-step-bank-row', ['deposit', 'redeposit', 'clear', 'settle'].includes(step));
      show('cm-step-sayad-row', step === 'sayad');
      show('cm-step-bill-row', step === 'endorse');
      show('cm-step-to-row', step === 'endorse');
      show('cm-step-acct-row', step === 'endorse');
      show('cm-step-note-row', ['bounce', 'return'].includes(step));
      show('cm-step-payee-row', step === 'print');
      show('cm-step-nid-row', step === 'print');
      show('cm-step-guide-row', step === 'print');
      document.getElementById('cm-step-payee').value = row.counterparty || '';
      document.getElementById('cm-step-nid').value = '';
      document.getElementById('cm-step-guide').checked = false;
      document.getElementById('cm-step-hint').textContent = t('cmHint_' + step) || '';
      if (step === 'endorse') cmFillInvoices('cm-step-bill', 'purchase');
      return new Promise((resolve) => {
        const done = (val) => { modal.__dialogCancel = null; modal.style.display = 'none'; resolve(val); };
        document.getElementById('cm-step-ok').onclick = () => done({
          on: document.getElementById('cm-step-date').value || null,
          bank_account_code: bankSel.value || null,
          sayad_id: document.getElementById('cm-step-sayad').value.trim() || null,
          to: document.getElementById('cm-step-to').value.trim(),
          account_code: document.getElementById('cm-step-acct').value || null,
          invoice_id: document.getElementById('cm-step-bill').value || null,
          note: document.getElementById('cm-step-note').value.trim() || null,
          payee: document.getElementById('cm-step-payee').value.trim() || null,
          national_id: document.getElementById('cm-step-nid').value.trim() || null,
          guide: document.getElementById('cm-step-guide').checked,
        });
        document.getElementById('cm-step-cancel').onclick = () => done(null);
        document.getElementById('cm-step-close').onclick = () => done(null);
        modal.onclick = (e) => { if (e.target === modal) done(null); };
        modal.__dialogCancel = () => done(null);
        modal.style.display = 'flex';
        document.getElementById('cm-step-ok').focus();
      });
    }

    // A PDF from a POST, in a tab opened while the click still counts as the
    // user's (a tab opened after the request would be blocked as a pop-up).
    async function cmOpenPdf(url, body) {
      const win = window.open('', '_blank');
      try {
        const res = await fetch(API + url, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          if (win) win.close();
          showAlert(typeof d.detail === 'string' ? d.detail : t('cmStepFailed'), true);
          return false;
        }
        const href = URL.createObjectURL(await res.blob());
        if (win) win.location.href = href;
        else { showAlert(t('cmPrintBlocked'), true); }
        setTimeout(() => URL.revokeObjectURL(href), 60_000);
        return true;
      } catch (_) {
        if (win) win.close();
        showAlert(t('cmStepFailed'), true);
        return false;
      }
    }

    async function cmRunStep(id, step) {
      const row = _cmRows.find(r => r.id === id);
      if (!row) return;
      const v = await cmStepDialog(row, step);
      if (!v) return;
      if (step === 'print') {
        const ok = await cmOpenPdf(`/commitments/${id}/print`,
          { on: v.on, payee: v.payee, national_id: v.national_id, guide: v.guide });
        if (ok && !v.guide) loadCommitments();          // the print is in its history
        return;
      }
      const route = { settle: 'settle', clear: 'settle', deposit: 'deposit', redeposit: 'deposit', bounce: 'bounce',
        return: 'return', endorse: 'endorse', sayad: 'sayad' }[step];
      const body = {
        settle: { on: v.on, post: true, bank_account_code: v.bank_account_code },
        clear: { on: v.on, post: true, bank_account_code: v.bank_account_code },
        deposit: { on: v.on, bank_account_code: v.bank_account_code },
        redeposit: { on: v.on, bank_account_code: v.bank_account_code },
        bounce: { on: v.on, note: v.note }, return: { on: v.on, note: v.note },
        endorse: { on: v.on, to: v.to, account_code: v.account_code, invoice_id: v.invoice_id },
        sayad: { on: v.on, sayad_id: v.sayad_id },
      }[step];
      try {
        const res = await fetch(API + `/commitments/${id}/${route}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(typeof d.detail === 'string' ? d.detail : t('cmStepFailed'), true); return; }
        showAlert(t('cmStepDone'));
        await loadCommitments();
        if (typeof notifyRefresh === 'function') notifyRefresh();
      } catch (_) { showAlert(t('cmStepFailed'), true); }
    }

    // ── Cheque print layout (§3.4): where each field lands on the leaf ──
    const CM_PL_FIELDS = ['date', 'date_words', 'payee', 'national_id', 'amount_words', 'amount'];
    function cmFillLayout(l) {
      ['width', 'height', 'offset_x', 'offset_y', 'font_size'].forEach(k => {
        const el = document.getElementById('cm-pl-' + k);
        if (el) el.value = l[k];
      });
      const body = document.querySelector('#cm-pl-fields tbody');
      if (!body) return;
      body.innerHTML = CM_PL_FIELDS.filter(f => l.fields[f]).map(f => {
        const b = l.fields[f];
        const cell = (k) => `<td><input type="number" step="0.5" data-field="${f}" data-k="${k}" value="${escapeHtml(String(b[k]))}" style="width:6rem;"></td>`;
        return `<tr><td>${escapeHtml(t('cmPf_' + f))}</td>${cell('x')}${cell('y')}${cell('w')}</tr>`;
      }).join('');
    }
    async function cmLoadLayout() {
      if (!document.getElementById('cm-print-panel')) return;
      try {
        const res = await fetch(API + '/commitments/print-layout');
        if (res.ok) cmFillLayout(await res.json());
      } catch (_) { /* offline */ }
    }
    function cmReadLayout() {
      const num = (id) => Number(document.getElementById('cm-pl-' + id).value);
      const fields = {};
      document.querySelectorAll('#cm-pl-fields input[data-field]').forEach(inp => {
        (fields[inp.dataset.field] = fields[inp.dataset.field] || {})[inp.dataset.k] = Number(inp.value);
      });
      return { width: num('width'), height: num('height'), offset_x: num('offset_x'), offset_y: num('offset_y'),
               font_size: num('font_size'), fields };
    }

    (function wireCommitments() {
      document.getElementById('cm-pl-save')?.addEventListener('click', async () => {
        try {
          const res = await fetch(API + '/commitments/print-layout', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cmReadLayout()) });
          const d = await res.json().catch(() => ({}));
          if (!res.ok) { showAlert(typeof d.detail === 'string' ? d.detail : t('cmStepFailed'), true); return; }
          cmFillLayout(d);
          showAlert(t('cmPrintSaved'));
        } catch (_) { showAlert(t('cmStepFailed'), true); }
      });
      document.getElementById('cm-pl-test')?.addEventListener('click', () => cmOpenPdf('/commitments/print-test', {}));
      document.getElementById('cm-pl-reset')?.addEventListener('click', async () => {
        try {
          const res = await fetch(API + '/commitments/print-layout/reset', { method: 'POST' });
          if (res.ok) { cmFillLayout(await res.json()); showAlert(t('cmPrintSaved')); }
        } catch (_) { showAlert(t('cmStepFailed'), true); }
      });

      const planBtn = document.getElementById('cm-p-save');
      if (planBtn) planBtn.addEventListener('click', async () => {
        const body = {
          title: (document.getElementById('cm-p-title').value || '').trim(),
          total_amount: Number(document.getElementById('cm-p-total').value || 0),
          count: Number(document.getElementById('cm-p-count').value || 0),
          first_due: document.getElementById('cm-p-first').value,
          direction: document.getElementById('cm-p-dir').value,
          counter_account_code: document.getElementById('cm-p-acct').value || null,
        };
        if (!body.title || !body.total_amount || !body.count || !body.first_due) {
          showAlert(t('cmMissingFields'), true); return;
        }
        try {
          const res = await fetch(API + '/commitments/installments', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(d.detail || t('msgFailed'), true); return; }
          showAlert(tf('cmPlanCreated', { n: body.count }));
          document.getElementById('cm-p-title').value = '';
          await loadCommitments();
        } catch (_) { showAlert('error', true); }
      });

      const dirSel = document.getElementById('cm-c-dir');
      if (dirSel) dirSel.addEventListener('change', () => cmFillInvoices('cm-c-inv', dirSel.value === 'receive' ? 'sales' : 'purchase'));
      const invSel = document.getElementById('cm-c-inv');
      if (invSel) invSel.addEventListener('change', () => {
        // an invoice decides the account (its receivable / payable)
        const acct = document.getElementById('cm-c-acct');
        if (acct) { acct.disabled = !!invSel.value; if (invSel.value) acct.value = ''; }
      });

      const chequeBtn = document.getElementById('cm-c-save');
      if (chequeBtn) chequeBtn.addEventListener('click', async () => {
        const body = {
          title: (document.getElementById('cm-c-title').value || '').trim(),
          amount: Number(document.getElementById('cm-c-amount').value || 0),
          due_date: document.getElementById('cm-c-due').value,
          direction: document.getElementById('cm-c-dir').value,
          reference: document.getElementById('cm-c-ref').value || null,
          sayad_id: (document.getElementById('cm-c-sayad').value || '').trim() || null,
          bank_name: document.getElementById('cm-c-bank').value || null,
          invoice_id: document.getElementById('cm-c-inv').value || null,
          counter_account_code: document.getElementById('cm-c-inv').value ? null : (document.getElementById('cm-c-acct').value || null),
          on: document.getElementById('cm-c-on').value || null,
        };
        if (!body.title || !body.amount || !body.due_date) { showAlert(t('cmMissingFields'), true); return; }
        try {
          const res = await fetch(API + '/commitments/cheques', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(typeof d.detail === 'string' ? d.detail : t('msgFailed'), true); return; }
          showAlert(t('cmChequeAdded'));
          ['cm-c-title', 'cm-c-amount', 'cm-c-ref', 'cm-c-sayad'].forEach(id => { document.getElementById(id).value = ''; });
          document.getElementById('cm-c-inv').value = '';
          document.getElementById('cm-c-acct').disabled = false;
          await loadCommitments();
        } catch (_) { showAlert('error', true); }
      });

      const filter = document.getElementById('cm-filter');
      if (filter) filter.addEventListener('change', cmRender);

      const rows = document.getElementById('cm-rows');
      if (rows) {
        rows.addEventListener('change', (e) => {
          const sel = e.target.closest('.cm-step');
          if (!sel || !sel.value) return;
          const step = sel.value;
          sel.value = '';
          cmRunStep(sel.dataset.id, step);
        });
        rows.addEventListener('click', (e) => {
          const btn = e.target.closest('.cm-history');
          if (btn) cmShowHistory(btn);
        });
      }
    })();

    // ═══════ Detected recurring payments ═══════
    // Suggestions only: the panel hides itself when there's nothing to say, and
    // creating a rule is always an explicit click.
    async function loadDetectedRecurring() {
      const wrap = document.getElementById('rec-detected-wrap');
      const list = document.getElementById('rec-detected-list');
      if (!wrap || !list) return;
      try {
        const rows = await (await fetch(API + '/recurring/detected')).json();
        if (!Array.isArray(rows) || !rows.length) { wrap.style.display = 'none'; return; }
        wrap.style.display = '';
        list.innerHTML = rows.map(r => {
          const freq = t('freq_' + r.frequency) || r.frequency;
          const approx = r.amount_varies ? '≈ ' : '';
          return `<div style="display:flex; justify-content:space-between; align-items:center; gap:0.5rem; padding:0.4rem 0; border-top:1px solid var(--border);">
            <div>
              <strong dir="auto">${escapeHtml(r.description)}</strong>
              <div style="font-size:0.8rem; color:var(--text-muted);">
                ${escapeHtml(approx + formatNum(r.typical_amount))} ${escapeHtml(currencyUnit())} ·
                ${escapeHtml(freq)} · ${escapeHtml(tf('rdSeenTimes', { n: r.occurrences }))} ·
                ${escapeHtml(tf('rdNextAbout', { d: formatDisplayDate(r.next_expected) }))}
              </div>
            </div>
            <button class="btn btn-primary btn-sm rd-create"
              data-payload="${escapeHtml(JSON.stringify(r))}">${escapeHtml(t('rdCreateRule'))}</button>
          </div>`;
        }).join('');
      } catch (_) { wrap.style.display = 'none'; }
    }

    (function wireDetectedRecurring() {
      const list = document.getElementById('rec-detected-list');
      if (!list) return;
      list.addEventListener('click', async (e) => {
        const btn = e.target.closest('.rd-create');
        if (!btn) return;
        let d;
        try { d = JSON.parse(btn.dataset.payload); } catch (_) { return; }
        // auto_post stays OFF: the app should not start posting entries by
        // itself off a guess. The user enables it once they trust the rule.
        const body = {
          name: d.description, direction: d.direction, frequency: d.frequency,
          amount: d.typical_amount, start_date: d.next_expected,
          next_run_date: d.next_expected,
          bank_account_code: d.bank_account_code || null,
          counter_account_code: d.counter_account_code || null,
          auto_post: false,
        };
        try {
          const res = await fetch(API + '/recurring', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) });
          if (!res.ok) { const j = await res.json().catch(() => ({})); showAlert(j.detail || t('msgFailed'), true); return; }
          showAlert(t('rdRuleCreated'));
          await loadRecurringRules();
          await loadDetectedRecurring();
        } catch (_) { showAlert('error', true); }
      });
    })();

    registerAction('migration-upload', () => migrationUpload());
    registerAction('migration-confirm', () => migrationConfirm());
    registerAction('migration-ask-ai', (el) => migrationAskAI(el.dataset.id));
    registerAction('migration-resolve', (el) => migrationResolve(el.dataset.id));
    registerAction('migration-dismiss', (el) => migrationDismiss(el.dataset.id));


    // ═══════ Fixed-asset register (roadmap §4.3) ═══════
    let _assetCategories = null;
    function assetCategoryLabel(key) {
      const k = 'assetCat_' + key;
      const v = t(k);
      return v === k ? key : v;
    }
    function assetTermsText(a) {
      return a.method === 'declining_balance'
        ? tf('assetTermsDeclining', { rate: (Number(a.rate_bps || 0) / 100).toString() })
        : tf('assetTermsStraight', { months: a.life_months || 0 });
    }
    function assetStatusChip(a) {
      if (a.status === 'disposed') return '<span class="alert-chip low">' + escapeHtml(t('assetStatusDisposed')) + '</span>';
      if (a.fully_depreciated) return '<span class="alert-chip low">' + escapeHtml(t('assetStatusDone')) + '</span>';
      return '<span class="alert-chip medium">' + escapeHtml(t('assetStatusActive')) + '</span>';
    }

    async function assetLoadCategories() {
      if (_assetCategories) return _assetCategories;
      const res = await fetch(API + '/fixed-assets/categories');
      if (!res.ok) throw new Error('categories');
      _assetCategories = await res.json();
      const sel = document.getElementById('asset-category');
      sel.innerHTML = _assetCategories.categories.map((c) =>
        '<option value="' + escapeHtml(c.key) + '">' + escapeHtml(assetCategoryLabel(c.key)) + '</option>').join('');
      sel.value = _assetCategories.categories[0].key;
      assetApplyCategory();
      return _assetCategories;
    }

    function assetApplyCategory() {
      if (!_assetCategories) return;
      const key = document.getElementById('asset-category').value;
      const c = _assetCategories.categories.find((x) => x.key === key);
      if (!c) return;
      document.getElementById('asset-method').value = c.method;
      document.getElementById('asset-life').value = c.life_months || '';
      document.getElementById('asset-rate').value = c.rate_bps ? (c.rate_bps / 100) : '';
      assetApplyMethod();
      const hint = document.getElementById('asset-category-hint');
      hint.textContent = c.statutory ? t('assetStatutoryHint') : (key === 'other' ? t('assetOtherHint') : t('assetPolicyHint'));
    }

    function assetApplyMethod() {
      const declining = document.getElementById('asset-method').value === 'declining_balance';
      document.getElementById('asset-life-wrap').style.display = declining ? 'none' : '';
      document.getElementById('asset-rate-wrap').style.display = declining ? '' : 'none';
    }

    async function assetFillSuppliers() {
      const sel = document.getElementById('asset-supplier');
      if (!sel || sel.dataset.loaded) return;
      try {
        const rows = await (await fetch(API + '/entities?type=supplier')).json();
        sel.innerHTML = '<option value="">—</option>' + (rows || []).map((e) =>
          '<option value="' + escapeHtml(e.id) + '">' + escapeHtml(e.name) + '</option>').join('');
        sel.dataset.loaded = '1';
      } catch (_) { sel.innerHTML = '<option value="">—</option>'; }
    }

    function assetMoneyByCurrency(map, field) {
      return Object.entries(map || {}).map(([ccy, v]) => formatNum(field ? v[field] : v) + ' ' + ccy).join(' · ') || '0';
    }

    async function loadFixedAssets() {
      const wrap = document.getElementById('asset-register');
      if (!wrap) return;
      try { await assetLoadCategories(); } catch (_) { /* the form stays usable without presets */ }
      assetFillSuppliers();
      try {
        const res = await fetch(API + '/fixed-assets');
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || t('msgFailed'));
        document.getElementById('asset-summary').innerHTML = [
          ['assetKpiCost', assetMoneyByCurrency(data.totals, 'cost')],
          ['assetKpiAccumulated', assetMoneyByCurrency(data.totals, 'accumulated')],
          ['assetKpiNbv', assetMoneyByCurrency(data.totals, 'net_book_value')],
        ].map(([k, v]) => '<div class="kpi-card"><div class="label">' + escapeHtml(t(k)) + '</div><div class="value">'
          + escapeHtml(v) + '</div></div>').join('');
        const due = document.getElementById('asset-due');
        if (data.due && data.due.months) {
          due.style.display = '';
          due.textContent = tf('assetDueBanner', { n: data.due.months, oldest: data.due.oldest,
                                                   amount: assetMoneyByCurrency(data.due.amount) });
        } else {
          due.style.display = 'none';
        }
        const rows = data.assets || [];
        if (!rows.length) {
          wrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('assetNone')) + '</p>';
          return;
        }
        const head = ['assetColNumber', 'assetColName', 'assetFieldCategory', 'assetColInService', 'assetColTerms',
                      'assetFieldCost', 'assetColAccumulated', 'assetColNbv', 'assetColStatus', '']
          .map((k) => '<th>' + (k ? escapeHtml(t(k)) : '') + '</th>').join('');
        wrap.innerHTML = '<table class="mini-table"><thead><tr>' + head + '</tr></thead><tbody>' + rows.map((a) => '<tr>'
          + '<td>' + escapeHtml(a.number) + '</td><td dir="auto">' + escapeHtml(a.name) + '</td>'
          + '<td>' + escapeHtml(assetCategoryLabel(a.category)) + '</td>'
          + '<td>' + escapeHtml(formatDisplayDate(a.in_service_on)) + '</td>'
          + '<td>' + escapeHtml(assetTermsText(a)) + '</td>'
          + '<td>' + escapeHtml(formatNum(a.cost)) + '</td><td>' + escapeHtml(formatNum(a.accumulated)) + '</td>'
          + '<td>' + escapeHtml(formatNum(a.net_book_value)) + '</td><td>' + assetStatusChip(a) + '</td>'
          + '<td><button type="button" class="btn btn-secondary btn-sm asset-open" data-id="' + escapeHtml(a.id) + '">'
          + escapeHtml(t('assetDetails')) + '</button></td></tr>').join('') + '</tbody></table>';
        wrap.querySelectorAll('.asset-open').forEach((b) => b.addEventListener('click', () => assetShowDetail(b.dataset.id)));
      } catch (_) {
        wrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('assetLoadError')) + '</p>';
      }
    }

    async function assetShowDetail(id) {
      const box = document.getElementById('asset-detail');
      box.style.display = '';
      box.innerHTML = '<p class="fc-note">' + escapeHtml(t('loading')) + '</p>';
      try {
        const res = await fetch(API + '/fixed-assets/' + encodeURIComponent(id));
        const a = await res.json();
        if (!res.ok) throw new Error(a.detail || t('msgFailed'));
        const sched = (a.schedule || []).map((m) => '<tr><td>' + escapeHtml(m.label) + '</td><td>' + escapeHtml(formatNum(m.amount))
          + '</td><td>' + escapeHtml(formatNum(m.closing_nbv)) + '</td><td>' + (m.posted ? '✓' : '') + '</td></tr>').join('');
        const dispose = a.status === 'active'
          ? '<h4>' + escapeHtml(t('assetDisposeTitle')) + '</h4><div class="row">'
            + '<div><label for="asset-dispose-on">' + escapeHtml(t('assetDisposeOn')) + '</label><input type="date" id="asset-dispose-on"></div>'
            + '<div><label for="asset-dispose-proceeds">' + escapeHtml(t('assetDisposeProceeds')) + '</label><input type="number" id="asset-dispose-proceeds" min="0" step="1" value="0"></div></div>'
            + '<div style="display:flex; gap:0.5rem;"><button type="button" class="btn btn-secondary btn-sm" id="asset-dispose-preview">' + escapeHtml(t('assetRunPreview')) + '</button>'
            + '<button type="button" class="btn btn-danger btn-sm" id="asset-dispose-post">' + escapeHtml(t('assetDisposePost')) + '</button></div>'
            + '<div id="asset-dispose-result"></div>'
          : '<p class="fc-note">' + escapeHtml(tf('assetDisposedOn', { date: formatDisplayDate(a.disposed_on), amount: formatNum(a.disposal_proceeds || 0) })) + '</p>';
        box.innerHTML = '<div style="display:flex; justify-content:space-between; gap:0.5rem; align-items:center;">'
          + '<h3 style="margin:0;" dir="auto">' + escapeHtml(a.number + ' — ' + a.name) + '</h3>'
          + '<button type="button" class="btn btn-secondary btn-sm" id="asset-detail-close">' + escapeHtml(t('assetClose')) + '</button></div>'
          + '<p class="fc-note">' + escapeHtml(assetCategoryLabel(a.category) + ' · ' + assetTermsText(a) + ' · '
            + tf('assetStartsFrom', { date: formatDisplayDate(a.depreciation_start) })) + '</p>'
          + dispose
          + '<details style="margin-top:0.6rem;"><summary>' + escapeHtml(tf('assetScheduleTitle', { n: (a.schedule || []).length })) + '</summary>'
          + '<table class="mini-table"><thead><tr><th>' + escapeHtml(t('assetColMonth')) + '</th><th>' + escapeHtml(t('assetColCharge'))
          + '</th><th>' + escapeHtml(t('assetColNbv')) + '</th><th>' + escapeHtml(t('assetColPosted')) + '</th></tr></thead><tbody>'
          + sched + '</tbody></table></details>';
        document.getElementById('asset-detail-close').addEventListener('click', () => { box.style.display = 'none'; box.innerHTML = ''; });
        if (a.status === 'active') {
          const onEl = document.getElementById('asset-dispose-on');
          onEl.value = localIsoDate(new Date());
          const send = async (preview) => {
            const out = document.getElementById('asset-dispose-result');
            const body = { on: onEl.value, proceeds: Number(document.getElementById('asset-dispose-proceeds').value || 0) };
            if (!body.on) { out.innerHTML = '<p class="fc-note">' + escapeHtml(t('assetDisposeNeedDate')) + '</p>'; return; }
            if (!preview && !(await uiConfirm({ message: t('assetDisposeConfirm'), confirmLabel: t('assetDisposePost'), danger: true }))) return;
            try {
              const r = await fetch(API + '/fixed-assets/' + encodeURIComponent(a.id) + '/dispose' + (preview ? '/preview' : ''), {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
              const d = await r.json();
              if (!r.ok) throw new Error(d.detail || t('msgFailed'));
              out.innerHTML = '<p class="fc-note">' + escapeHtml(tf(d.gain ? 'assetDisposeGain' : 'assetDisposeLoss', {
                nbv: formatNum(d.net_book_value), proceeds: formatNum(d.proceeds), amount: formatNum(d.gain || d.loss) })) + '</p>';
              if (!preview) { loadFixedAssets(); assetShowDetail(a.id); }
            } catch (err) {
              out.innerHTML = '<p class="fc-note fc-risk-text">' + escapeHtml(err.message || t('assetLoadError')) + '</p>';
            }
          };
          document.getElementById('asset-dispose-preview').addEventListener('click', () => send(true));
          document.getElementById('asset-dispose-post').addEventListener('click', () => send(false));
        }
      } catch (err) {
        box.innerHTML = '<p class="fc-note fc-risk-text">' + escapeHtml(err.message || t('assetLoadError')) + '</p>';
      }
    }

    async function assetRun(preview) {
      const out = document.getElementById('asset-run-result');
      const through = document.getElementById('asset-run-through').value || null;
      try {
        const res = preview
          ? await fetch(API + '/fixed-assets/depreciation-run' + (through ? '?through=' + encodeURIComponent(through) : ''))
          : await fetch(API + '/fixed-assets/depreciation-run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                                  body: JSON.stringify({ through }) });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || 'run');
        if (!d.journals.length) { out.innerHTML = '<p class="fc-note">' + escapeHtml(t('assetRunNothing')) + '</p>'; return; }
        out.innerHTML = '<p class="fc-note">' + escapeHtml(t(preview ? 'assetRunWould' : 'assetRunDone')) + '</p>'
          + '<table class="mini-table"><thead><tr><th>' + escapeHtml(t('assetColMonth')) + '</th><th>' + escapeHtml(t('assetColDate'))
          + '</th><th>' + escapeHtml(t('assetColAssets')) + '</th><th>' + escapeHtml(t('assetColCharge')) + '</th></tr></thead><tbody>'
          + d.journals.map((j) => '<tr><td>' + escapeHtml(j.months.join(', ')) + '</td><td>' + escapeHtml(formatDisplayDate(j.date))
            + '</td><td>' + escapeHtml(String(j.assets)) + '</td><td>' + escapeHtml(formatNum(j.amount) + ' ' + j.currency) + '</td></tr>').join('')
          + '</tbody></table>';
        if (!preview) loadFixedAssets();
      } catch (err) {
        out.innerHTML = '<p class="fc-note fc-risk-text">' + escapeHtml(err.message || t('assetLoadError')) + '</p>';
      }
    }

    async function assetSave() {
      const msg = document.getElementById('asset-save-msg');
      const val = (id) => document.getElementById(id).value;
      const method = val('asset-method');
      const body = {
        name: val('asset-name').trim(), category: val('asset-category') || 'other',
        cost: Number(val('asset-cost') || 0), residual: Number(val('asset-residual') || 0),
        acquired_on: val('asset-acquired'), in_service_on: val('asset-in-service') || null, method,
        life_months: method === 'straight_line' ? (Number(val('asset-life')) || null) : null,
        rate_bps: method === 'declining_balance' ? (Math.round(Number(val('asset-rate')) * 100) || null) : null,
        acquisition: val('asset-acquisition'), entity_id: val('asset-supplier') || null,
        serial_number: val('asset-serial').trim() || null, location: val('asset-location').trim() || null,
        opening_accumulated: Number(val('asset-opening') || 0), opening_date: val('asset-opening-date') || null,
      };
      if (!body.name || !body.cost || !body.acquired_on) { msg.textContent = t('assetSaveMissing'); return; }
      try {
        const res = await fetch(API + '/fixed-assets', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                         body: JSON.stringify(body) });
        const d = await res.json();
        if (!res.ok) throw new Error(typeof d.detail === 'string' ? d.detail : t('assetLoadError'));
        msg.textContent = tf('assetSaved', { number: d.number });
        ['asset-name', 'asset-cost', 'asset-serial', 'asset-location', 'asset-opening-date'].forEach((id) => { document.getElementById(id).value = ''; });
        document.getElementById('asset-residual').value = '0';
        document.getElementById('asset-opening').value = '0';
        loadFixedAssets();
      } catch (err) { msg.textContent = err.message; }
    }

    (function wireFixedAssets() {
      const save = document.getElementById('asset-save');
      if (!save) return;
      save.addEventListener('click', assetSave);
      document.getElementById('asset-category').addEventListener('change', assetApplyCategory);
      document.getElementById('asset-method').addEventListener('change', assetApplyMethod);
      document.getElementById('asset-run-preview').addEventListener('click', () => assetRun(true));
      document.getElementById('asset-run-post').addEventListener('click', async () => {
        if (await uiConfirm({ message: t('assetRunConfirm'), confirmLabel: t('assetRunPost') })) assetRun(false);
      });
    })();

    // ═══════ Chart of accounts (roadmap §4.5) ═══════
    let _coaTree = [];
    function coaFlatten(nodes, depth, out) {
      nodes.forEach((n) => { out.push({ ...n, depth }); coaFlatten(n.children || [], depth + 1, out); });
      return out;
    }

    function coaRender() {
      const box = document.getElementById('coa-tree');
      const q = foldFa((document.getElementById('coa-filter').value || '').trim());
      const rows = coaFlatten(_coaTree, 0, []).filter((n) => !q || n.code.includes(q) || foldFa(n.name).includes(q));
      if (!rows.length) { box.innerHTML = '<p class="fc-note">' + escapeHtml(t('coaNone')) + '</p>'; return; }
      const head = ['coaCode', 'coaName', 'coaLevel', 'coaBalance', 'coaStatus', ''].map((k) => '<th>' + (k ? escapeHtml(t(k)) : '') + '</th>').join('');
      box.innerHTML = '<table class="mini-table"><thead><tr>' + head + '</tr></thead><tbody>' + rows.map((n) => {
        const act = (name, label, cls) => '<button type="button" class="btn btn-' + (cls || 'secondary') + ' btn-sm" data-coa="' + name + '" data-id="' + escapeHtml(n.id) + '">' + escapeHtml(t(label)) + '</button>';
        // the everyday action on the row, the rest under ⋯ (3–4 buttons on every row were noise)
        const more = [
          act('rename', 'coaRename'),
          n.protected ? '' : (n.is_active ? act('off', 'coaDeactivate') : act('on', 'coaReactivate')),
          n.protected ? '' : act('delete', 'coaDelete', 'danger'),
        ].join('');
        const actions = (n.level !== 'DETAIL' && n.is_active ? act('child', 'coaAddChild') + ' ' : '')
          + '<details class="row-menu"><summary class="btn btn-secondary btn-sm" aria-label="' + escapeHtml(t('moreActions')) + '" title="'
          + escapeHtml(t('moreActions')) + '">⋯</summary><div class="row-menu-list">' + more + '</div></details>';
        return '<tr' + (n.is_active ? '' : ' style="opacity:0.55;"') + '><td dir="ltr" style="padding-inline-start:' + (0.4 + n.depth * 1.1) + 'rem;">' + escapeHtml(n.code) + '</td>'
          + '<td dir="auto">' + escapeHtml(n.name) + '</td><td>' + escapeHtml(t('coaLevel_' + n.level)) + '</td>'
          + '<td>' + escapeHtml(formatNum(n.total)) + '</td>'
          + '<td>' + (n.is_active ? '' : '<span class="alert-chip low">' + escapeHtml(t('coaInactive')) + '</span>')
          + (n.protected ? ' <span class="fc-note">' + escapeHtml(t('coaProtected')) + '</span>' : '') + '</td>'
          + '<td class="row-actions">' + actions + '</td></tr>';
      }).join('') + '</tbody></table>';
    }

    async function loadChartOfAccounts() {
      const box = document.getElementById('coa-tree');
      if (!box) return;
      try {
        const inactive = document.getElementById('coa-show-inactive').checked;
        const res = await fetch(API + '/accounts/tree?include_inactive=' + (inactive ? 'true' : 'false'));
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || t('msgFailed'));
        _coaTree = data.accounts || [];
        coaRender();
        coaLoadOpening();
      } catch (_) {
        box.innerHTML = '<p class="fc-note">' + escapeHtml(t('coaLoadError')) + '</p>';
      }
    }

    function coaNode(id) { return coaFlatten(_coaTree, 0, []).find((n) => n.id === id); }

    async function coaSend(url, method, body) {
      const res = await fetch(API + url, { method, headers: { 'Content-Type': 'application/json' },
                                           body: body ? JSON.stringify(body) : undefined });
      const d = res.status === 204 ? {} : await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof d.detail === 'string' ? d.detail : t('coaLoadError'));
      return d;
    }

    async function coaAction(kind, id) {
      const n = coaNode(id);
      if (!n) return;
      const msg = document.getElementById('coa-new-msg');
      try {
        if (kind === 'child') {
          document.getElementById('coa-new-parent').value = n.code;
          await coaSuggest();
          document.getElementById('coa-new-name').focus();
          return;
        }
        if (kind === 'rename') {
          const name = await uiPrompt({ title: t('coaRename'), message: n.code, value: n.name, confirmLabel: t('coaRename') });
          if (name == null || !name.trim() || name.trim() === n.name) return;
          await coaSend('/accounts/' + encodeURIComponent(id), 'PATCH', { name: name.trim() });
        } else if (kind === 'off' || kind === 'on') {
          await coaSend('/accounts/' + encodeURIComponent(id), 'PATCH', { is_active: kind === 'on' });
        } else if (kind === 'delete') {
          if (!(await uiConfirm({ message: tf('coaDeleteConfirm', { code: n.code, name: n.name }), confirmLabel: t('coaDelete'), danger: true }))) return;
          await coaSend('/accounts/' + encodeURIComponent(id), 'DELETE');
        }
        msg.textContent = '';
        loadChartOfAccounts();
      } catch (err) {
        msg.textContent = err.message;
        document.getElementById('coa-new-msg').scrollIntoView({ block: 'nearest' });
      }
    }

    async function coaSuggest() {
      const parent = (document.getElementById('coa-new-parent').value || '').trim();
      if (!parent) return;
      try {
        const d = await coaSend('/accounts/suggest-code/' + encodeURIComponent(parent), 'GET');
        document.getElementById('coa-new-code').value = d.code;
      } catch (_) { /* an unknown parent is reported on save */ }
    }

    async function coaCreate() {
      const msg = document.getElementById('coa-new-msg');
      const body = { name: (document.getElementById('coa-new-name').value || '').trim(),
                     parent_code: (document.getElementById('coa-new-parent').value || '').trim() || null,
                     code: (document.getElementById('coa-new-code').value || '').trim() || null };
      if (!body.name) { msg.textContent = t('coaNeedName'); return; }
      try {
        const d = await coaSend('/accounts', 'POST', body);
        msg.textContent = tf('coaAdded', { code: d.code, name: d.name });
        document.getElementById('coa-new-name').value = '';
        document.getElementById('coa-new-code').value = '';
        loadChartOfAccounts();
      } catch (err) { msg.textContent = err.message; }
    }

    async function coaLoadOpening() {
      const grid = document.getElementById('coa-opening-grid');
      try {
        const cur = await coaSend('/accounts/opening-balances', 'GET');
        document.getElementById('coa-opening-date').value = cur.date || '';
        const have = {};
        (cur.lines || []).forEach((ln) => { have[ln.account_code] = ln; });
        const postable = coaFlatten(_coaTree, 0, []).filter((n) => n.level !== 'GROUP' && n.is_active);
        grid.innerHTML = '<table class="mini-table"><thead><tr><th>' + escapeHtml(t('coaCode')) + '</th><th>' + escapeHtml(t('coaName'))
          + '</th><th>' + escapeHtml(t('coaDebit')) + '</th><th>' + escapeHtml(t('coaCredit')) + '</th></tr></thead><tbody>'
          + postable.map((n) => {
            const ln = have[n.code] || {};
            return '<tr><td dir="ltr">' + escapeHtml(n.code) + '</td><td dir="auto">' + escapeHtml(n.name) + '</td>'
              + '<td><input type="number" min="0" step="1" class="coa-dr" data-code="' + escapeHtml(n.code) + '" value="' + (ln.debit || '') + '" style="width:8rem;"></td>'
              + '<td><input type="number" min="0" step="1" class="coa-cr" data-code="' + escapeHtml(n.code) + '" value="' + (ln.credit || '') + '" style="width:8rem;"></td></tr>';
          }).join('') + '</tbody></table>';
        grid.querySelectorAll('input').forEach((inp) => inp.addEventListener('input', coaOpeningTotals));
        coaOpeningTotals();
        if (cur.source === 'migration') document.getElementById('coa-opening-msg').textContent = t('coaOpeningFromMigration');
      } catch (_) { grid.innerHTML = ''; }
    }

    function coaOpeningLines() {
      const byCode = {};
      document.querySelectorAll('#coa-opening-grid input').forEach((inp) => {
        const v = Math.round(Number(inp.value || 0));
        if (!v) return;
        const row = byCode[inp.dataset.code] || (byCode[inp.dataset.code] = { account_code: inp.dataset.code, debit: 0, credit: 0 });
        row[inp.classList.contains('coa-dr') ? 'debit' : 'credit'] += v;
      });
      return Object.values(byCode);
    }

    function coaOpeningTotals() {
      const lines = coaOpeningLines();
      const dr = lines.reduce((a, l) => a + l.debit, 0);
      const cr = lines.reduce((a, l) => a + l.credit, 0);
      document.getElementById('coa-opening-totals').textContent = tf(dr === cr ? 'coaOpeningBalanced' : 'coaOpeningDiff',
        { debit: formatNum(dr), credit: formatNum(cr), diff: formatNum(Math.abs(dr - cr)) });
    }

    async function coaSaveOpening() {
      const msg = document.getElementById('coa-opening-msg');
      const on = document.getElementById('coa-opening-date').value;
      if (!on) { msg.textContent = t('coaOpeningNeedDate'); return; }
      const lines = coaOpeningLines();
      if (lines.some((l) => l.debit && l.credit)) { msg.textContent = t('coaOpeningBothSides'); return; }
      if (!(await uiConfirm({ message: t('coaOpeningConfirm'), confirmLabel: t('coaOpeningSave') }))) return;
      try {
        const d = await coaSend('/accounts/opening-balances', 'PUT', { on, lines });
        msg.textContent = d.adjustment ? tf('coaOpeningSavedAdj', { amount: formatNum(Math.abs(d.adjustment)) }) : t('coaOpeningSaved');
        loadChartOfAccounts();
      } catch (err) { msg.textContent = err.message; }
    }

    (function wireChartOfAccounts() {
      const tree = document.getElementById('coa-tree');
      if (!tree) return;
      tree.addEventListener('click', (ev) => {
        const b = ev.target.closest('button[data-coa]');
        if (b) coaAction(b.dataset.coa, b.dataset.id);
      });
      document.getElementById('coa-filter').addEventListener('input', coaRender);
      document.getElementById('coa-show-inactive').addEventListener('change', loadChartOfAccounts);
      document.getElementById('coa-new-parent').addEventListener('change', coaSuggest);
      document.getElementById('coa-new-save').addEventListener('click', coaCreate);
      document.getElementById('coa-opening-save').addEventListener('click', coaSaveOpening);
    })();

    // ═══════ Historical journals from another system (roadmap §4.11) ═══════
    let _ji = null;                 // the last preview: token, preset, columns, accounts…
    let _jiChart = null;
    const JI_STATUS = ['ready', 'unmapped_account', 'unbalanced', 'closed_period', 'future', 'already_imported'];

    async function jiChart() {
      if (_jiChart) return _jiChart;
      const rows = await (await fetch(API + '/accounts?limit=500')).json();
      _jiChart = (rows || []).filter((a) => a.level !== 'GROUP');
      return _jiChart;
    }

    function jiPresetName(key) {
      const o = document.querySelector('#ji-preset option[value="' + key + '"]');
      return o ? o.textContent.trim() : key;
    }

    function jiRender(r) {
      const box = document.getElementById('ji-result');
      const opt = (i, h, sel) => '<option value="' + i + '"' + (sel ? ' selected' : '') + '>' + escapeHtml(h || ('#' + (i + 1))) + '</option>';
      const colPick = (field) => '<label class="ji-col"><span>' + escapeHtml(t('jiField_' + field)) + '</span><select data-ji-field="' + field + '">'
        + '<option value="-1">—</option>' + (r.headers || []).map((h, i) => opt(i, h, r.columns[field] === i)).join('') + '</select></label>';
      const counts = JI_STATUS.filter((k) => r.counts && r.counts[k]).map((k) =>
        '<span class="alert-chip ' + (k === 'ready' ? 'low' : 'medium') + '">' + escapeHtml(tf('jiStatus_' + k, { n: r.counts[k] })) + '</span>').join(' ');
      let html = '<p class="fc-note">' + escapeHtml(tf('jiSummary', { vouchers: formatNum(r.voucher_count), lines: formatNum(r.line_count),
        from: r.from ? formatDisplayDate(r.from) : '—', to: r.to ? formatDisplayDate(r.to) : '—', preset: jiPresetName(r.preset) })) + '</p>';
      if (counts) html += '<div style="display:flex; gap:0.35rem; flex-wrap:wrap; margin:0.3rem 0;">' + counts + '</div>';
      if (r.rounded_lines) html += '<p class="fc-note">' + escapeHtml(tf('jiRounded', { n: r.rounded_lines })) + '</p>';
      if ((r.skipped_rows || []).length) html += '<p class="fc-note">' + escapeHtml(tf('jiSkippedRows', { n: r.skipped_rows.length })) + '</p>';
      if ((r.needs || []).length) html += '<p class="fc-risk-text">' + escapeHtml(t('jiNeeds')) + '</p>';
      html += '<details class="asset-opening"' + ((r.needs || []).length ? ' open' : '') + '><summary>' + escapeHtml(tf('jiColumns', { row: r.header_row })) + '</summary>'
        + '<div class="ji-cols">' + (r.fields || []).map(colPick).join('') + '</div>'
        + '<button type="button" class="btn btn-secondary btn-sm" id="ji-reread">' + escapeHtml(t('jiReread')) + '</button></details>';
      if ((r.accounts || []).length) {
        html += '<h4 style="margin:0.8rem 0 0.3rem;">' + escapeHtml(t('jiAccounts')) + '</h4><div style="overflow-x:auto;"><table class="mini-table"><thead><tr><th>'
          + escapeHtml(t('jiSourceAccount')) + '</th><th>' + escapeHtml(t('coaDebit')) + '</th><th>' + escapeHtml(t('coaCredit')) + '</th><th>'
          + escapeHtml(t('jiMapsTo')) + '</th></tr></thead><tbody>' + r.accounts.map((a) => {
            const pick = a.mapped_to || (a.suggestions[0] && a.suggestions[0].code) || '';
            const options = '<option value="">' + escapeHtml(t('jiChoose')) + '</option>' + (_jiChart || []).map((c) =>
              '<option value="' + escapeHtml(c.code) + '"' + (c.code === pick ? ' selected' : '') + '>' + escapeHtml(c.code + ' — ' + c.name) + '</option>').join('');
            return '<tr><td dir="auto">' + escapeHtml([a.code, a.name].filter(Boolean).join(' — ')) + (a.how ? ' <span class="fc-note">(' + escapeHtml(t('jiHow_' + a.how)) + ')</span>' : '') + '</td>'
              + '<td>' + escapeHtml(formatNum(a.debit)) + '</td><td>' + escapeHtml(formatNum(a.credit)) + '</td>'
              + '<td><select data-ji-account="' + escapeHtml(a.key) + '">' + options + '</select></td></tr>';
          }).join('') + '</tbody></table></div>';
      }
      const problems = (r.vouchers || []).filter((v) => v.problem).slice(0, 30);
      if (problems.length) {
        html += '<h4 style="margin:0.8rem 0 0.3rem;">' + escapeHtml(t('jiProblems')) + '</h4><table class="mini-table"><tbody>' + problems.map((v) =>
          '<tr><td>' + escapeHtml(v.number) + '</td><td>' + escapeHtml(formatDisplayDate(v.date)) + '</td><td>' + escapeHtml(formatNum(v.debit))
          + ' / ' + escapeHtml(formatNum(v.credit)) + '</td><td>' + escapeHtml(t('jiProblem_' + v.problem)) + '</td></tr>').join('') + '</tbody></table>';
      }
      html += '<div style="display:flex; gap:0.5rem; margin-top:0.8rem;"><button type="button" class="btn btn-secondary" id="ji-recheck">' + escapeHtml(t('jiRecheck'))
        + '</button><button type="button" class="btn btn-primary" id="ji-apply"' + ((r.counts && r.counts.ready) ? '' : ' disabled') + '>'
        + escapeHtml(tf('jiApply', { n: (r.counts && r.counts.ready) || 0 })) + '</button></div><p id="ji-msg" class="fc-note"></p>';
      box.innerHTML = html;
      document.getElementById('ji-reread').addEventListener('click', () => jiReview());
      document.getElementById('ji-recheck').addEventListener('click', () => jiReview());
      document.getElementById('ji-apply').addEventListener('click', jiApply);
    }

    function jiCollect() {
      const columns = {};
      document.querySelectorAll('#ji-result select[data-ji-field]').forEach((s) => {
        const v = parseInt(s.value, 10);
        columns[s.dataset.jiField] = v >= 0 ? v : null;
      });
      const account_map = {};
      document.querySelectorAll('#ji-result select[data-ji-account]').forEach((s) => { if (s.value) account_map[s.dataset.jiAccount] = s.value; });
      return { token: _ji.token, preset: document.getElementById('ji-preset').value, columns, account_map };
    }

    async function jiSend(url, body, isForm) {
      const res = await fetch(API + url, isForm ? { method: 'POST', body } :
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof d.detail === 'string' ? d.detail : t('jiFailed'));
      return d;
    }

    async function jiPreview() {
      const file = document.getElementById('ji-file').files[0];
      const box = document.getElementById('ji-result');
      if (!file) { box.innerHTML = '<p class="fc-note">' + escapeHtml(t('jiPickFile')) + '</p>'; return; }
      box.innerHTML = '<p class="fc-note">' + escapeHtml(t('loading')) + '</p>';
      try {
        await jiChart();
        const fd = new FormData();
        fd.append('file', file);
        fd.append('preset', document.getElementById('ji-preset').value);
        _ji = await jiSend('/migration/journals/preview', fd, true);
        jiRender(_ji);
      } catch (err) { box.innerHTML = '<p class="fc-risk-text">' + escapeHtml(err.message) + '</p>'; }
    }

    async function jiReview() {
      if (!_ji) return;
      try {
        const body = jiCollect();
        const r = await jiSend('/migration/journals/review', body);
        _ji = { ..._ji, ...r };
        jiRender(_ji);
      } catch (err) { document.getElementById('ji-msg').textContent = err.message; }
    }

    async function jiApply() {
      if (!_ji) return;
      const body = jiCollect();
      if (!(await uiConfirm({ message: tf('jiConfirm', { n: (_ji.counts && _ji.counts.ready) || 0 }), confirmLabel: t('jiApplyShort') }))) return;
      try {
        const out = await jiSend('/migration/journals/apply', body);
        document.getElementById('ji-result').innerHTML = '<p class="fc-note">' + escapeHtml(tf('jiDone', { n: formatNum(out.posted),
          skipped: formatNum(Object.values(out.skipped || {}).reduce((a, b) => a + b, 0)) })) + '</p>';
        _ji = null;
      } catch (err) { document.getElementById('ji-msg').textContent = err.message; }
    }

    (function wireJournalImport() {
      const btn = document.getElementById('ji-preview-btn');
      if (btn) btn.addEventListener('click', jiPreview);
    })();
