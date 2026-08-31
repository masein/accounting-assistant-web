    // Load the data a page needs. Called from BOTH the nav-button click
    // handler and hash navigation (hashchange / deep-link / back-forward), so
    // a page reached via the URL hash — not just a click — still fetches its
    // data (otherwise e.g. CFO/CEO cards render their empty "—" placeholders).
    function loadPageData(page) {
      if (page === 'dashboard') { loadOwnerDashboard(); }
      if (page === 'personal-dashboard') { loadPersonalDashboard(); }
      if (page === 'commitments') { loadCommitments(); }
      if (page === 'entities') { loadEntities(); }
      if (page === 'invoices') { loadInvoices(); invInitBuilder(); }
      if (page === 'recurring') { loadRecurringRules(); }
      if (page === 'bank-statements') { loadBankStatements(); }
      if (page === 'audit') { loadAuditLogs(); }
      if (page === 'cfo') { loadCFOReport(); }
      if (page === 'ceo') { loadCEOReport(); }
      if (page === 'inventory') { loadPriceMgmtItems(); }
      if (page === 'manager') { loadAccountDatalist(); loadProductEntityDatalist(); }
      if (page === 'products') { loadProductsCatalog(); }
      if (page === 'payroll') { loadPayroll(); }
      if (page === 'equity') { loadEquity(); }
      if (page === 'purchase-orders') { loadPurchaseOrders(); }
      if (page === 'expenses') { loadExpenses(); }
      if (page === 'time') { loadTimeTab(); }
      if (page === 'settings') { loadClosedPeriod(); loadAdjustments(); loadCompanyProfile(); }
      if (page === 'companies') { loadCompanies(); }
      if (page === 'migration') { migrationInitPage(); }
      if (page === 'petty-cash') { pettyInitPage(); }
      if (page === 'recurring') { recurringInitPage(); }
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
          + `${escapeHtml(t('migrationJournalPosted'))}: ${oj.opening_date || ''}`
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
            + `<button type="button" class="btn btn-secondary btn-sm" onclick="migrationAskAI('${r.id}')">${escapeHtml(t('migrationAiBtn'))}</button>`
            + `<button type="button" class="btn btn-secondary btn-sm" onclick="migrationResolve('${r.id}')">${escapeHtml(t('migrationResolveBtn'))}</button>`
            + `<button type="button" class="btn btn-secondary btn-sm" onclick="migrationDismiss('${r.id}')">${escapeHtml(t('migrationDismissBtn'))}</button>`
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
          alert((d && d.message ? d.message : t('migrationMissing')) + (missing ? ': ' + missing : ''));
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
      showPage('ai-accountant');
      const input = document.getElementById('ai-acct-input');
      if (input) {
        input.value = msg;
        const send = document.getElementById('ai-acct-send');
        if (send) send.click();
      }
    }


    // ═══════ Recurring: manual form + run-due ═══════
    async function _recLoadSelectors() {
      try {
        const [banksRes, acctsRes] = await Promise.all([
          fetch(API + '/entities?type=bank'), fetch(API + '/accounts'),
        ]);
        const banks = banksRes.ok ? await banksRes.json() : [];
        const accts = acctsRes.ok ? await acctsRes.json() : [];
        const bankSel = document.getElementById('rec-bank');
        if (bankSel) {
          bankSel.innerHTML = banks.filter(b => b.code)
            .map(b => `<option value="${escapeHtml(b.code)}">${escapeHtml(b.name)} (${escapeHtml(b.code)})</option>`).join('')
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
            <span style="flex:1; ${r.status === 'paused' ? 'opacity:0.5;' : ''}">${escapeHtml(r.title)} · ${escapeHtml(r.due_date)}${r.repeat !== 'none' ? ' ↻' : ''}</span>
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
        document.getElementById('petty-own-balance').textContent = formatMoney(acc.balance, 'IRR');
        const kinds = { deposit: t('pettyKindDeposit'), expense: t('pettyKindExpense'), adjustment: t('pettyKindAdjust') };
        const stats = { pending: t('pettyStPending'), approved: t('pettyStApproved'), rejected: t('pettyStRejected') };
        document.getElementById('petty-own-tbody').innerHTML = (acc.transactions || []).map(x => `
          <tr><td>${escapeHtml((x.created_at || '').slice(0, 10))}</td>
              <td>${escapeHtml(kinds[x.kind] || x.kind)}</td>
              <td class="num">${formatMoney(x.signed_amount, 'IRR')}</td>
              <td>${escapeHtml(x.description || '')}</td>
              <td>${escapeHtml(stats[x.status] || x.status)}</td></tr>`).join('')
          || `<tr><td colspan="5" class="empty-state">—</td></tr>`;
      } catch (_) {}
    }
    async function _pettyRenderAdmin(accounts) {
      const tbody = document.getElementById('petty-admin-tbody');
      tbody.innerHTML = accounts.map(a => `
        <tr><td>${escapeHtml(a.holder_name)}</td>
            <td class="num">${formatMoney(a.balance, 'IRR')}</td>
            <td class="num">${a.pending_expenses || 0}</td>
            <td>
              <button type="button" class="btn btn-secondary btn-sm petty-deposit" data-id="${a.id}" data-name="${escapeHtml(a.holder_name)}">${escapeHtml(t('pettyDepositBtn'))}</button>
              <button type="button" class="btn btn-secondary btn-sm petty-adjust" data-id="${a.id}" data-name="${escapeHtml(a.holder_name)}">${escapeHtml(t('pettyAdjustBtn'))}</button>
            </td></tr>`).join('')
        || `<tr><td colspan="4" class="empty-state">—</td></tr>`;
      tbody.querySelectorAll('.petty-deposit').forEach(b => b.addEventListener('click', async () => {
        const amount = parseInt(window.prompt(t('pettyDepositPrompt') + ' — ' + b.dataset.name) || '', 10);
        if (!amount || amount <= 0) return;
        const bank = window.prompt(t('pettyDepositBankPrompt'), document.getElementById('rec-bank')?.value || '1110');
        if (!bank) return;
        const res = await fetch(API + '/petty-cash/accounts/' + b.dataset.id + '/deposit', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount, bank_account_code: bank.trim() }),
        });
        if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(d.detail || 'failed', true); }
        pettyInitPage();
      }));
      tbody.querySelectorAll('.petty-adjust').forEach(b => b.addEventListener('click', async () => {
        const signed = parseInt(window.prompt(t('pettyAdjustPrompt') + ' — ' + b.dataset.name) || '', 10);
        if (!signed) return;
        const counter = window.prompt(t('pettyDepositBankPrompt'), '1110');
        if (!counter) return;
        const res = await fetch(API + '/petty-cash/accounts/' + b.dataset.id + '/adjust', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ signed_amount: signed, counter_account_code: counter.trim(),
                                 description: t('pettyAdjustBtn') }),
        });
        if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(d.detail || 'failed', true); }
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
            <span style="flex:1;">${escapeHtml(x.holder)} — ${formatMoney(x.amount, 'IRR')} · ${escapeHtml(x.description || '')}</span>
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
        st.textContent = res.ok ? t('pettyCreated') : (typeof d.detail === 'string' ? d.detail : 'failed');
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
          showAlert(typeof d.detail === 'string' ? d.detail : 'failed', true);
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
      const today = new Date().toISOString().slice(0, 10);
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
          <tr><td>${escapeHtml(r.date || '')}</td><td>${escapeHtml(r.description || r.reference || '')}</td>
          <td class="num">${r.debit_effect ? formatNum(r.debit_effect) : ''}</td>
          <td class="num">${r.credit_effect ? formatNum(r.credit_effect) : ''}</td>
          <td class="num">${formatNum(r.running_balance)}</td></tr>`).join('');
        body.innerHTML = `
          <p style="margin:0 0 0.6rem;color:var(--text-muted);font-size:0.84rem;">${escapeHtml(t('equityLedgerHint'))} · ${escapeHtml(t('equityColOutstandingClaim'))}: <strong>${formatNum(data.closing_balance)}</strong></p>
          <div style="max-height:400px;overflow:auto;"><table class="detail-table"><thead><tr>
            <th data-i18n="colDate">Date</th><th data-i18n="colDescription">Description</th>
            <th class="num" data-i18n="equityColDebit">Debit</th><th class="num" data-i18n="equityColCredit">Credit</th>
            <th class="num" data-i18n="equityColBalance">Balance</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="5" class="empty-state">' + escapeHtml(t('equityLedgerEmpty')) + '</td></tr>'}</tbody></table></div>`;
      } catch (err) {
        body.innerHTML = '<p class="empty-state">' + escapeHtml(t('equityLoadError')) + '</p>';
      }
    }

    // ═══════ Installments (اقساط) & cheques (چک) ═══════
    async function cmFillAccounts() {
      const sels = [document.getElementById('cm-p-acct'), document.getElementById('cm-c-acct')];
      if (!sels[0] || sels[0].options.length) return;
      try {
        const res = await fetch(API + '/manager-reports/accounts/list');
        if (!res.ok) return;
        const accs = await res.json();
        // Settling moves money against a liability or a receivable, not an
        // expense — offer only those, plus a blank for tracking-only items.
        const opts = `<option value="">${escapeHtml(t('cmNoPosting'))}</option>` + accs
          .filter(a => (a.code || '').startsWith('2') || (a.code || '').startsWith('1'))
          .map(a => `<option value="${escapeHtml(a.code)}">${escapeHtml(a.code)} — ${escapeHtml(a.name)}</option>`)
          .join('');
        sels.forEach(s => { if (s) s.innerHTML = opts; });
      } catch (_) { /* offline */ }
    }

    function cmStatusChip(row) {
      const today = new Date().toISOString().slice(0, 10);
      if (row.status === 'settled') return '<span class="alert-chip low">' + escapeHtml(t('cmSettled')) + '</span>';
      if (row.status === 'bounced') return '<span class="alert-chip high">' + escapeHtml(t('cmBounced')) + '</span>';
      if (row.due_date < today) return '<span class="alert-chip high">' + escapeHtml(t('cmOverdue')) + '</span>';
      return '<span class="alert-chip medium">' + escapeHtml(t('cmPending')) + '</span>';
    }

    async function loadCommitments() {
      const body = document.getElementById('cm-rows');
      if (!body) return;
      await cmFillAccounts();
      try {
        const [rows, sum] = await Promise.all([
          (await fetch(API + '/commitments')).json(),
          (await fetch(API + '/commitments/summary')).json(),
        ]);
        document.getElementById('cm-summary').innerHTML = `
          <div class="kpi-card"><div class="label">${escapeHtml(t('cmYouOwe'))}</div>
            <div class="value">${escapeHtml(formatNum(sum.payable))} ${escapeHtml(currencyUnit())}</div></div>
          <div class="kpi-card"><div class="label">${escapeHtml(t('cmOwedToYou'))}</div>
            <div class="value">${escapeHtml(formatNum(sum.receivable))} ${escapeHtml(currencyUnit())}</div></div>
          <div class="kpi-card"><div class="label">${escapeHtml(t('cmNextDue'))}</div>
            <div class="value">${sum.next_due_date ? escapeHtml(formatDisplayDate(sum.next_due_date)) : '—'}</div></div>`;

        if (!rows.length) {
          body.innerHTML = `<tr><td colspan="5" class="empty-state" style="padding:0.6rem;">${escapeHtml(t('cmNone'))}</td></tr>`;
          return;
        }
        body.innerHTML = rows.map(r => {
          const seq = (r.sequence && r.plan_total) ? ` (${r.sequence}/${r.plan_total})` : '';
          const who = r.direction === 'pay' ? t('cmDirPay') : t('cmDirReceive');
          const actions = r.status === 'settled' ? '✓' :
            `<button class="btn btn-secondary btn-sm cm-settle" data-id="${escapeHtml(r.id)}">${escapeHtml(t('cmSettle'))}</button>` +
            (r.kind === 'cheque' && r.status !== 'bounced'
              ? ` <button class="btn btn-secondary btn-sm cm-bounce" data-id="${escapeHtml(r.id)}">${escapeHtml(t('cmBounce'))}</button>` : '');
          return `<tr>
            <td>${escapeHtml(formatDisplayDate(r.due_date))}</td>
            <td dir="auto">${escapeHtml(r.title)}${escapeHtml(seq)} <span style="color:var(--text-muted); font-size:0.8rem;">${escapeHtml(who)}</span></td>
            <td>${escapeHtml(formatNum(r.amount))}</td>
            <td>${cmStatusChip(r)}</td>
            <td>${actions}</td>
          </tr>`;
        }).join('');
      } catch (_) {
        body.innerHTML = `<tr><td colspan="5" class="empty-state">${escapeHtml(t('cmNone'))}</td></tr>`;
      }
    }

    (function wireCommitments() {
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
          if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(d.detail || 'error', true); return; }
          showAlert(tf('cmPlanCreated', { n: body.count }));
          document.getElementById('cm-p-title').value = '';
          await loadCommitments();
        } catch (_) { showAlert('error', true); }
      });

      const chequeBtn = document.getElementById('cm-c-save');
      if (chequeBtn) chequeBtn.addEventListener('click', async () => {
        const body = {
          title: (document.getElementById('cm-c-title').value || '').trim(),
          amount: Number(document.getElementById('cm-c-amount').value || 0),
          due_date: document.getElementById('cm-c-due').value,
          direction: document.getElementById('cm-c-dir').value,
          reference: document.getElementById('cm-c-ref').value || null,
          bank_name: document.getElementById('cm-c-bank').value || null,
          counter_account_code: document.getElementById('cm-c-acct').value || null,
        };
        if (!body.title || !body.amount || !body.due_date) { showAlert(t('cmMissingFields'), true); return; }
        try {
          const res = await fetch(API + '/commitments/cheques', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(d.detail || 'error', true); return; }
          showAlert(t('cmChequeAdded'));
          document.getElementById('cm-c-title').value = '';
          document.getElementById('cm-c-amount').value = '';
          await loadCommitments();
        } catch (_) { showAlert('error', true); }
      });

      const rows = document.getElementById('cm-rows');
      if (rows) rows.addEventListener('click', async (e) => {
        const settle = e.target.closest('.cm-settle');
        const bounce = e.target.closest('.cm-bounce');
        if (!settle && !bounce) return;
        const id = (settle || bounce).dataset.id;
        if (settle && !await uiConfirm({ title: t('cmSettle'), message: t('cmSettleConfirm') })) return;
        try {
          await fetch(API + `/commitments/${id}/` + (settle ? 'settle' : 'bounce'), {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: settle ? JSON.stringify({ post: true }) : '{}' });
          await loadCommitments();
          if (typeof notifyRefresh === 'function') notifyRefresh();
        } catch (_) { showAlert('error', true); }
      });
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
          if (!res.ok) { const j = await res.json().catch(() => ({})); showAlert(j.detail || 'error', true); return; }
          showAlert(t('rdRuleCreated'));
          await loadRecurringRules();
          await loadDetectedRecurring();
        } catch (_) { showAlert('error', true); }
      });
    })();
