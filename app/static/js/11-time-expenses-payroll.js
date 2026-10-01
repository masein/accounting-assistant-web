
    // ═══════ Time & Billing Module ═══════
    let tmReadyPreview = null;
    let tmBooks = true;                  // set from /time/pickers: false for an employee
    function tmCur() { return (window.__REPORTING_CURRENCY || 'IRR'); }

    // --- Pending pushed entries (unmatched /api/v1 worklogs) ---
    async function loadPendingTime() {
      const section = document.getElementById('tm-pending-section');
      const wrap = document.getElementById('tm-pending-wrap');
      if (!section || !wrap) return;
      try {
        const res = await fetch(API + '/time/pending');
        if (!res.ok) { section.style.display = 'none'; return; }  // employees: 403 → hide
        const rows = await res.json().catch(() => []);
        if (!rows.length) { section.style.display = 'none'; return; }
        const er = await fetch(API + '/entities?type=employee');
        const emps = er.ok ? await er.json().catch(() => []) : [];
        const opts = emps.map(e => `<option value="${escapeHtml(e.id)}">${escapeHtml(e.name)}</option>`).join('');
        section.style.display = '';
        wrap.innerHTML = `
          <table class="results-table" style="font-size:0.85rem;">
            <thead><tr>
              <th>${escapeHtml(t('timePendingSource'))}</th><th>${escapeHtml(t('timePendingWorker'))}</th>
              <th>${escapeHtml(t('labelDate'))}</th><th>${escapeHtml(t('timeHours'))}</th>
              <th>${escapeHtml(t('timePendingAssign'))}</th><th></th>
            </tr></thead>
            <tbody>${rows.map(p => `
              <tr>
                <td>${escapeHtml(p.source)}<br><span style="color:var(--text-muted);font-size:0.76rem;">${escapeHtml(p.external_id)}</span></td>
                <td>${escapeHtml(p.worker)}</td>
                <td>${escapeHtml(formatDisplayDate(p.work_date))}</td>
                <td>${p.hours}</td>
                <td><select class="tm-pending-emp" data-id="${escapeHtml(p.id)}" style="font-size:0.8rem;padding:2px 4px;">${opts}</select></td>
                <td>
                  <button type="button" class="btn btn-primary btn-sm tm-pending-resolve" data-id="${escapeHtml(p.id)}">${escapeHtml(t('timePendingResolve'))}</button>
                  <button type="button" class="btn btn-secondary btn-sm tm-pending-reject" data-id="${escapeHtml(p.id)}">${escapeHtml(t('timePendingReject'))}</button>
                </td>
              </tr>`).join('')}
            </tbody>
          </table>`;
      } catch (_) { section.style.display = 'none'; }
    }

    document.addEventListener('click', async (ev) => {
      const rbtn = ev.target.closest && ev.target.closest('.tm-pending-resolve');
      const xbtn = ev.target.closest && ev.target.closest('.tm-pending-reject');
      if (!rbtn && !xbtn) return;
      const id = (rbtn || xbtn).dataset.id;
      try {
        let res;
        if (rbtn) {
          const sel = document.querySelector(`.tm-pending-emp[data-id="${id}"]`);
          const entityId = sel && sel.value;
          if (!entityId) { showAlert(t('timePendingNeedEmployee'), true); return; }
          res = await fetch(API + `/time/pending/${encodeURIComponent(id)}/resolve`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entity_id: entityId }),
          });
        } else {
          res = await fetch(API + `/time/pending/${encodeURIComponent(id)}/reject`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
          });
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || t('msgFailedDot'), true); return; }
        showAlert(rbtn ? t('timePendingResolved') : t('timePendingRejected'));
        loadPendingTime();
        if (typeof tmLoadEntries === 'function') tmLoadEntries();
      } catch (err) { showAlert(t('msgConnectionError') + err.message, true); }
    });

    async function loadMyPay() {
      const section = document.getElementById('my-pay-section');
      const body = document.getElementById('my-pay-body');
      if (!section || !body) return;
      try {
        const res = await fetch(API + '/payroll/my-payslips');
        if (!res.ok) { section.style.display = 'none'; return; }
        const data = await res.json();
        const slips = (data && data.payslips) || [];
        if (!slips.length) { section.style.display = 'none'; return; }
        section.style.display = '';
        body.innerHTML = slips.map(s => `
          <tr>
            <td>${escapeHtml(formatDisplayDate(s.period_start))} – ${escapeHtml(formatDisplayDate(s.period_end))}</td>
            <td class="num">${formatNum(s.gross)} ${escapeHtml(s.currency || '')}</td>
            <td class="num"><strong>${formatNum(s.net_pay)}</strong> ${escapeHtml(s.currency || '')}</td>
            <td>${escapeHtml(formatDisplayDate(s.pay_date))}</td>
            <td>${s.status === 'paid' ? '<span style="color:#15803d;font-weight:600;">' + escapeHtml(t('myPayStatusPaid')) + '</span>' : escapeHtml(s.status)}</td>
            <td>${escapeHtml(s.paid_to || '—')}</td>
            <td><a class="btn btn-secondary btn-sm" target="_blank"
                   href="${API}/payroll/runs/${encodeURIComponent(s.run_id)}/payslip/${encodeURIComponent(s.entity_id)}/pdf">PDF</a></td>
          </tr>`).join('');
      } catch (err) { section.style.display = 'none'; }
    }

    async function loadTimeTab() {
      loadMyPay();
      try {
        // Who the form can pick: everyone for books people; for an employee,
        // themselves and the clients by name (the entity list is books-only).
        const res = await fetch(API + '/time/pickers');
        const pk = res.ok ? await res.json() : { workers: [], clients: [], books: false };
        if (pk.books) loadPendingTime();                  // assigning pushed entries is a books job
        // projects and billable rates are set up by the books people, and they invoice
        tmBooks = !!pk.books;
        ['tm-new-project-btn', 'tm-set-rate-btn'].forEach((id) => {
          const b = document.getElementById(id);
          if (b) b.style.display = pk.books ? '' : 'none';
        });
        const workers = pk.workers || [];
        const wsel = document.getElementById('tm-worker');
        wsel.innerHTML = workers.length
          ? workers.map(w => `<option value="${w.id}">${escapeHtml(w.name)}</option>`).join('')
          : `<option value="">${t(pk.restricted ? 'timeNotLinked' : 'timeNoWorkers')}</option>`;
        const clients = pk.clients || [];
        const opts = clients.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
        document.getElementById('tm-client').innerHTML = clients.length ? opts : `<option value="">${t('timeNoClients')}</option>`;
        document.getElementById('tm-filter-client').innerHTML = `<option value="">${t('timeAllClients')}</option>` + opts;
      } catch (e) { /* ignore */ }
      if (!document.getElementById('tm-date').value) document.getElementById('tm-date').value = new Date().toISOString().slice(0, 10);
      await tmLoadProjects();
      await tmLoadEntries();
      await tmLoadReady();
      tmLoadBudgets();
    }

    // Projects and their budgets (roadmap §4.7) — books roles only: the API
    // refuses the others and the section stays hidden.
    async function tmLoadBudgets() {
      const sec = document.getElementById('tm-budgets-section');
      const body = document.getElementById('tm-budgets-body');
      if (!sec || !body) return;
      // employees log their own time here; budgets are for books roles (the API refuses them)
      if (currentRole === 'employee') { sec.style.display = 'none'; return; }
      try {
        const res = await fetch(API + '/time/project-budgets');
        if (!res.ok) { sec.style.display = 'none'; return; }
        const rows = await res.json();
        sec.style.display = '';
        if (!rows.length) {
          body.innerHTML = `<tr><td colspan="5" class="empty-state">${escapeHtml(t('timeBudgetsNone'))}</td></tr>`;
          return;
        }
        const meter = (used, budget, pct, unit) => {
          const u = unit === 'h' ? `${formatNum(used)} h` : `${formatNum(used)} ${escapeHtml(unit)}`;
          if (budget == null) return `${u} <span class="fx-hint" style="display:inline;">· ${escapeHtml(t('timeBudgetNoLimit'))}</span>`;
          const cls = pct >= 100 ? 'tm-bud-over' : (pct >= 85 ? 'tm-bud-warn' : 'tm-bud-ok');
          const b = unit === 'h' ? `${formatNum(budget)} h` : formatNum(budget);
          return `<span class="${cls}">${u} / ${b} (${pct}%)</span>`
            + `<div class="tm-bud-bar"><span class="${cls}" style="width:${Math.min(100, pct || 0)}%"></span></div>`;
        };
        body.innerHTML = rows.map(p => `<tr data-id="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name)}"
            data-hours="${p.budget_hours == null ? '' : escapeHtml(String(p.budget_hours))}" data-amount="${p.budget_amount == null ? '' : escapeHtml(String(p.budget_amount))}">
          <td>${escapeHtml(p.name)}</td><td>${escapeHtml(p.client_name || '')}</td>
          <td>${meter(p.hours_used, p.budget_hours, p.hours_pct, 'h')}</td>
          <td>${meter(p.amount_used, p.budget_amount, p.amount_pct, p.currency)}${p.unpriced_hours ? `<div class="fx-hint">${escapeHtml(tf('timeBudgetUnpriced', { h: p.unpriced_hours }))}</div>` : ''}</td>
          <td><button type="button" class="btn btn-secondary btn-sm tm-bud-set">${escapeHtml(t('timeBudgetSet'))}</button></td>
        </tr>`).join('');
      } catch (_) { sec.style.display = 'none'; }
    }
    document.getElementById('tm-budgets-body').addEventListener('click', async (e) => {
      const btn = e.target.closest('.tm-bud-set');
      if (!btn) return;
      const tr = btn.closest('tr[data-id]');
      const hours = await uiPrompt({ title: tr.dataset.name, message: t('timeBudgetHoursPrompt'), type: 'number', value: tr.dataset.hours });
      if (hours === null) return;
      const amount = await uiPrompt({ title: tr.dataset.name, message: t('timeBudgetFeesPrompt'), type: 'number', value: tr.dataset.amount });
      if (amount === null) return;
      const h = hours.trim() === '' ? null : parseFloat(hours);
      const a = amount.trim() === '' ? null : parseInt(amount, 10);
      if ((h !== null && !(h >= 0)) || (a !== null && !(a >= 0))) { showAlert(t('budgetEditBad'), true); return; }
      const res = await fetch(API + '/time/projects/' + encodeURIComponent(tr.dataset.id), {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ budget_hours: h, budget_amount: a }) });
      if (!res.ok) { showAlert(t('budgetSaveFailed'), true); return; }
      showAlert(t('timeBudgetSaved'));
      tmLoadBudgets();
    });

    document.getElementById('tm-client').addEventListener('change', tmLoadProjects);
    async function tmLoadProjects() {
      const cid = document.getElementById('tm-client').value;
      const sel = document.getElementById('tm-project');
      sel.innerHTML = `<option value="">${t('timeNoProject')}</option>`;
      if (!cid) return;
      try {
        const res = await fetch(API + '/time/projects?client_id=' + cid);
        if (!res.ok) return;
        (await res.json()).forEach(p => {
          const o = document.createElement('option'); o.value = p.id; o.textContent = p.name; sel.appendChild(o);
        });
      } catch (e) { /* ignore */ }
    }

    document.getElementById('tm-add').addEventListener('click', async () => {
      const body = {
        employee_id: document.getElementById('tm-worker').value || null,
        client_id: document.getElementById('tm-client').value || null,
        project_id: document.getElementById('tm-project').value || null,
        work_date: document.getElementById('tm-date').value,
        hours: parseFloat(document.getElementById('tm-hours').value || '0'),
        description: document.getElementById('tm-desc').value.trim() || null,
        billable: document.getElementById('tm-billable').checked,
      };
      if (!body.employee_id || !body.client_id || !body.work_date || body.hours <= 0) { showAlert(t('timeNeedFields'), true); return; }
      try {
        const res = await fetch(API + '/time/entries', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('timeLogFailed'), true); return; }
        showAlert(t('timeLogged'));
        document.getElementById('tm-hours').value = '';
        document.getElementById('tm-desc').value = '';
        await tmLoadEntries(); await tmLoadReady();
      } catch (e) { showAlert(t('timeLogFailed'), true); }
    });

    document.getElementById('tm-new-project-btn').addEventListener('click', async () => {
      const cid = document.getElementById('tm-client').value;
      if (!cid) { showAlert(t('timeNoClients'), true); return; }
      const name = await uiPrompt({ title: t('timeNewProject'), message: t('timeProjectName') });
      if (!name) return;
      try {
        const res = await fetch(API + '/time/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_id: cid, name }) });
        if (!res.ok) { showAlert(t('timeProjectFailed'), true); return; }
        await tmLoadProjects();
        showAlert(t('timeProjectCreated'));
      } catch (e) { showAlert(t('timeProjectFailed'), true); }
    });

    document.getElementById('tm-set-rate-btn').addEventListener('click', async () => {
      const wid = document.getElementById('tm-worker').value;
      if (!wid) { showAlert(t('timeNoWorkers'), true); return; }
      const rate = await uiPrompt({ title: t('timeSetRate'), message: t('timeRatePrompt'), type: 'number' });
      if (rate === null || rate === '') return;
      const cid = document.getElementById('tm-client').value || null;
      const pid = document.getElementById('tm-project').value || null;
      try {
        const res = await fetch(API + '/time/rates', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ employee_id: wid, rate: parseFloat(rate), client_id: pid ? null : cid, project_id: pid }) });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('timeRateFailed'), true); return; }
        showAlert(tf('timeRateSaved', { scope: data.scope }));
        await tmLoadEntries(); await tmLoadReady();
      } catch (e) { showAlert(t('timeRateFailed'), true); }
    });

    document.getElementById('tm-filter-client').addEventListener('change', tmLoadEntries);
    async function tmLoadEntries() {
      const cid = document.getElementById('tm-filter-client').value;
      try {
        const res = await fetch(API + '/time/entries' + (cid ? '?client_id=' + cid : ''));
        if (!res.ok) return;
        const rows = await res.json();
        const body = document.getElementById('tm-entries-body');
        body.innerHTML = rows.length ? '' : `<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:1rem;">${t('timeNoEntries')}</td></tr>`;
        rows.forEach(e => {
          const colors = { unbilled: '', invoiced: '#e8f5e9', written_off: '#f3f4f6' };
          const tr = document.createElement('tr');
          tr.style.background = colors[e.status] || '';
          const rateTxt = e.rate != null ? `${formatNum(Math.round(e.rate))} ${escapeHtml(e.currency || tmCur())}` : '—';
          const actions = e.locked ? `🔒` :
            `<button class="btn btn-secondary btn-sm tm-wo" data-id="${e.id}">${t('timeWriteOff')}</button> <button class="btn btn-secondary btn-sm tm-del" data-id="${e.id}" aria-label="${escapeHtml(t('btnDelete'))}" title="${escapeHtml(t('btnDelete'))}">✕</button>`;
          tr.innerHTML = `<td>${escapeHtml(formatDisplayDate(e.work_date))}</td><td>${escapeHtml(e.employee_name || '')}</td>
            <td>${escapeHtml(e.client_name || '')}</td><td>${escapeHtml(e.project_name || t('timeNoProject'))}</td>
            <td>${e.hours}</td><td>${rateTxt}</td>
            <td><span class="badge ${e.status === 'invoiced' ? 'badge-ok' : ''}">${t('timeStatus_' + e.status)}</span></td>
            <td>${actions}</td>`;
          body.appendChild(tr);
        });
      } catch (e) { /* ignore */ }
    }

    document.getElementById('tm-entries-body').addEventListener('click', async (e) => {
      const wo = e.target.closest('.tm-wo'); const del = e.target.closest('.tm-del');
      if (wo) {
        const ok = await uiConfirm({ title: t('timeWriteOff'), message: t('timeWriteOffConfirm') });
        if (!ok) return;
        const r = await fetch(API + '/time/entries/' + wo.dataset.id + '/write-off', { method: 'POST' });
        if (!r.ok) { const d = await readJsonSafe(r); showAlert((d && d.detail) || t('timeActionFailed'), true); }
        await tmLoadEntries(); await tmLoadReady();
      }
      if (del) {
        const ok = await uiConfirm({ title: t('btnDelete') || 'Delete', message: t('timeDeleteConfirm') });
        if (!ok) return;
        const r = await fetch(API + '/time/entries/' + del.dataset.id, { method: 'DELETE' });
        if (!r.ok) { const d = await readJsonSafe(r); showAlert((d && d.detail) || t('timeActionFailed'), true); }
        await tmLoadEntries(); await tmLoadReady();
      }
    });

    async function tmLoadReady() {
      // invoicing clients is a books job; an employee's page leaves it out
      const sec = document.getElementById('tm-ready-section');
      if (sec) sec.style.display = tmBooks ? '' : 'none';
      if (!tmBooks) return;
      try {
        const res = await fetch(API + '/time/unbilled');
        if (!res.ok) return;
        const data = await res.json();
        const body = document.getElementById('tm-ready-body');
        body.innerHTML = data.clients.length ? '' : `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:1rem;">${t('timeNothingReady')}</td></tr>`;
        data.clients.forEach(c => {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${escapeHtml(c.client_name)}</td><td>${c.hours}</td>
            <td>${formatNum(c.value)} ${escapeHtml(c.currency)}</td><td>${c.oldest}</td>
            <td><button class="btn btn-primary btn-sm tm-make-inv" data-id="${c.client_id}">${t('timeCreateInvoice')}</button></td>`;
          body.appendChild(tr);
        });
      } catch (e) { /* ignore */ }
    }

    document.getElementById('tm-ready-body').addEventListener('click', async (e) => {
      const btn = e.target.closest('.tm-make-inv');
      if (!btn) return;
      try {
        const res = await fetch(API + '/time/invoice-preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_id: btn.dataset.id }) });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('timePreviewFailed'), true); return; }
        tmRenderPreview(data, btn.dataset.id);
      } catch (e) { showAlert(t('timePreviewFailed'), true); }
    });

    function tmRenderPreview(pv, clientId) {
      tmReadyPreview = { client_id: clientId };
      const cur = pv.currency;
      let html = `<div><strong>${escapeHtml(pv.client_name)}</strong> · ${pv.period_from} → ${pv.period_to} · ${cur}</div>`;
      pv.groups.forEach(g => {
        html += `<div style="margin-top:0.4rem;font-weight:600;">${escapeHtml(g.project_name)}</div>`;
        g.lines.forEach(ln => {
          html += `<div style="margin-inline-start:1rem;">${escapeHtml(ln.employee_name)} — ${ln.hours} × ${ln.rate} = ${formatNum(ln.amount)} ${cur} <span style="color:var(--text-muted);font-size:0.78rem;">(${ln.rate_source})</span></div>`;
        });
        html += `<div style="margin-inline-start:1rem;color:var(--text-muted);">${t('timeSubtotal')}: ${formatNum(g.subtotal)} ${cur}</div>`;
      });
      html += `<div style="margin-top:0.5rem;">${t('timeSubtotal')}: ${formatNum(pv.subtotal)} ${cur} · ${t('timeVat')}: ${formatNum(pv.tax)} ${cur} · <strong>${t('poTotal')}: ${formatNum(pv.total)} ${cur}</strong></div>`;
      html += `<div style="color:var(--text-muted);font-size:0.8rem;margin-top:0.3rem;">${tf('timeIncludesN', { n: pv.entry_count, hours: pv.total_hours, value: formatNum(pv.total), currency: cur })}</div>`;
      document.getElementById('tm-preview-body').innerHTML = html;
      document.getElementById('tm-preview').style.display = 'block';
      document.getElementById('tm-preview').scrollIntoView({ behavior: 'smooth' });
    }

    document.getElementById('tm-preview-cancel').addEventListener('click', () => {
      document.getElementById('tm-preview').style.display = 'none'; tmReadyPreview = null;
    });
    document.getElementById('tm-preview-confirm').addEventListener('click', async () => {
      if (!tmReadyPreview) return;
      try {
        const res = await fetch(API + '/time/invoice', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_id: tmReadyPreview.client_id }) });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('timeInvoiceFailed'), true); return; }
        document.getElementById('tm-preview').style.display = 'none';
        const dl = window.location.origin + API + data.pdf_url;
        showAlert(tf('timeInvoiceCreated', { number: data.number }));
        window.open(API + data.pdf_url, '_blank');   // one-click download
        await tmLoadEntries(); await tmLoadReady();
        if (typeof loadInvoices === 'function') loadInvoices();
      } catch (e) { showAlert(t('timeInvoiceFailed'), true); }
    });

    // ═══════ Expenses / Mileage Module ═══════
    let expSettings = { mileage_rate: 0, mileage_unit: 'mile', approval_threshold: 0 };

    function expCur() { return (window.__REPORTING_CURRENCY || 'IRR'); }

    async function loadExpenses() {
      // What this caller can do here: claim (for themselves only, unless they
      // keep the books), change the settings, or just approve.
      let pk = null;
      try {
        const res = await fetch(API + '/expenses/pickers');
        pk = res.ok ? await res.json() : null;
      } catch (e) { pk = null; }
      document.getElementById('exp-settings-section').style.display = pk && pk.can_edit_settings ? '' : 'none';
      document.getElementById('exp-claim-section').style.display = pk && pk.can_claim ? '' : 'none';
      if (pk && pk.settings) {
        expSettings = pk.settings;
        document.getElementById('exp-rate').value = expSettings.mileage_rate;
        document.getElementById('exp-unit').value = expSettings.mileage_unit;
        document.getElementById('exp-threshold').value = expSettings.approval_threshold;
      }
      const emps = (pk && pk.employees) || [];
      const sel = document.getElementById('exp-emp');
      sel.innerHTML = emps.length
        ? emps.map(e => `<option value="${e.id}">${escapeHtml(e.name)}</option>`).join('')
        : `<option value="">${t(pk && pk.restricted ? 'timeNotLinked' : 'expNoEmployees')}</option>`;
      updateMileageCalc();
      await loadExpenseClaims();
    }

    function updateMileageCalc() {
      const dist = parseFloat(document.getElementById('exp-distance').value || '0');
      const rate = parseFloat(expSettings.mileage_rate || 0);
      const amount = Math.round(dist * rate);
      const el = document.getElementById('exp-calc');
      if (dist > 0 && rate > 0) {
        let msg = tf('expCalc', { distance: dist, unit: expSettings.mileage_unit, rate, amount: formatNum(amount), currency: expCur() });
        if (expSettings.approval_threshold > 0 && amount > expSettings.approval_threshold) {
          msg += ' — ' + t('expNeedsApproval');
        }
        el.textContent = msg;
      } else {
        el.textContent = '';
      }
    }
    document.getElementById('exp-distance').addEventListener('input', updateMileageCalc);

    document.getElementById('exp-save-settings').addEventListener('click', async () => {
      const payload = {
        mileage_rate: parseFloat(document.getElementById('exp-rate').value || '0'),
        mileage_unit: document.getElementById('exp-unit').value,
        approval_threshold: parseInt(document.getElementById('exp-threshold').value || '0', 10),
      };
      try {
        const res = await fetch(API + '/expenses/settings', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert(t('expSaveFailed'), true); return; }
        expSettings = data;
        showAlert(t('expSettingsSaved'));
        updateMileageCalc();
      } catch (e) { showAlert(t('expSaveFailed'), true); }
    });

    document.getElementById('exp-submit').addEventListener('click', async () => {
      const entity_id = document.getElementById('exp-emp').value || null;
      const claim_date = document.getElementById('exp-date').value;
      const distance = parseFloat(document.getElementById('exp-distance').value || '0');
      const purpose = document.getElementById('exp-purpose').value.trim() || null;
      if (!entity_id) { showAlert(t('expNoEmployees'), true); return; }
      if (!claim_date || distance <= 0) { showAlert(t('expNeedFields'), true); return; }
      try {
        const res = await fetch(API + '/expenses/mileage', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ entity_id, claim_date, distance, purpose }),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('expSubmitFailed'), true); return; }
        showAlert(data.needs_approval ? t('expSubmittedRouted') : t('expSubmittedPosted'));
        document.getElementById('exp-distance').value = '';
        document.getElementById('exp-purpose').value = '';
        updateMileageCalc();
        await loadExpenseClaims();
      } catch (e) { showAlert(t('expSubmitFailed'), true); }
    });

    async function loadExpenseClaims() {
      try {
        const res = await fetch(API + '/expenses');
        if (!res.ok) return;
        const claims = await res.json();
        const queue = document.getElementById('exp-queue-body');
        const all = document.getElementById('exp-claims-body');
        queue.innerHTML = '';
        all.innerHTML = '';
        const pending = claims.filter(c => c.status === 'pending_approval');
        if (!pending.length) {
          queue.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:0.75rem;">${t('expNoPending')}</td></tr>`;
        }
        pending.forEach(c => {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${escapeHtml(c.employee_name)}</td><td>${escapeHtml(formatDisplayDate(c.claim_date))}</td>
            <td>${c.distance} ${escapeHtml(c.unit)}</td><td>${formatNum(c.amount)} ${escapeHtml(c.currency)}</td>
            <td style="display:flex;gap:0.3rem;">
              <button class="btn btn-primary btn-sm exp-approve" data-id="${c.id}">${t('expApprove')}</button>
              <button class="btn btn-secondary btn-sm exp-reject" data-id="${c.id}">${t('expReject')}</button></td>`;
          queue.appendChild(tr);
        });
        if (!claims.length) {
          all.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:0.75rem;">${t('expNoClaims')}</td></tr>`;
        }
        claims.forEach(c => {
          const canPay = c.status === 'approved' && c.transaction_id && !c.reimbursement_transaction_id;
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${escapeHtml(c.employee_name)}</td><td>${escapeHtml(formatDisplayDate(c.claim_date))}</td>
            <td>${formatNum(c.amount)} ${escapeHtml(c.currency)}</td>
            <td><span class="badge ${c.status === 'reimbursed' ? 'badge-ok' : ''}">${t('expStatus_' + c.status)}</span></td>
            <td>${canPay ? `<button class="btn btn-secondary btn-sm exp-pay" data-id="${c.id}">${t('expReimburse')}</button>` : ''}</td>`;
          all.appendChild(tr);
        });
      } catch (e) { /* ignore */ }
    }

    document.getElementById('exp-queue-body').addEventListener('click', async (e) => {
      const ap = e.target.closest('.exp-approve');
      const rj = e.target.closest('.exp-reject');
      if (ap) await expDecide(ap.dataset.id, 'approve');
      if (rj) await expDecide(rj.dataset.id, 'reject');
    });

    async function expDecide(id, action) {
      const ok = await uiConfirm({
        title: action === 'approve' ? t('expApprove') : t('expReject'),
        message: action === 'approve' ? t('expApproveConfirm') : t('expRejectConfirm'),
      });
      if (!ok) return;
      try {
        const res = await fetch(API + '/expenses/' + id + '/' + action, { method: 'POST' });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('expDecideFailed'), true); return; }
        showAlert(action === 'approve' ? t('expApproved') : t('expRejected'));
        await loadExpenseClaims();
      } catch (e) { showAlert(t('expDecideFailed'), true); }
    }

    document.getElementById('exp-claims-body').addEventListener('click', async (e) => {
      const pay = e.target.closest('.exp-pay');
      if (!pay) return;
      const ok = await uiConfirm({ title: t('expReimburse'), message: t('expReimburseConfirm') });
      if (!ok) return;
      try {
        const res = await fetch(API + '/expenses/' + pay.dataset.id + '/reimburse', { method: 'POST' });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('expReimburseFailed'), true); return; }
        showAlert(t('expReimbursed'));
        await loadExpenseClaims();
      } catch (e) { showAlert(t('expReimburseFailed'), true); }
    });

    // ═══════ Purchase Orders Module ═══════
    let poCurrentId = null;
    let poCurrent = null;   // the order open in the detail panel

    function poCur() { return (window.__REPORTING_CURRENCY || 'IRR'); }

    function poAddLineRow(desc = '', qty = '', price = '') {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td><input type="text" class="po-l-desc" value="${escapeHtml(desc)}"><div class="po-l-hist fx-hint" style="margin:0.2rem 0 0;"></div></td>
        <td><input type="number" class="po-l-qty" min="0" step="0.01" value="${qty}" style="width:7rem;"></td>
        <td><input type="number" class="po-l-price" min="0" value="${price}" style="width:8rem;"></td>
        <td><button type="button" class="btn btn-secondary btn-sm po-l-del" aria-label="${escapeHtml(t('ibRemoveLine'))}" title="${escapeHtml(t('ibRemoveLine'))}">✕</button></td>`;
      document.getElementById('po-lines-body').appendChild(tr);
    }

    async function loadPurchaseOrders() {
      // Supplier dropdown.
      try {
        const res = await fetch(API + '/entities?type=supplier');
        const sups = res.ok ? await res.json() : [];
        const sel = document.getElementById('po-supplier');
        sel.innerHTML = sups.length
          ? sups.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('')
          : `<option value="">${t('poNoSuppliers')}</option>`;
      } catch (e) { /* ignore */ }
      // Seed one empty line if the editor is empty.
      if (!document.getElementById('po-lines-body').children.length) poAddLineRow();
      await loadPOList();
    }

    document.getElementById('po-add-line').addEventListener('click', () => poAddLineRow());
    // What this has cost before (roadmap §4.8): shown under the line as you type it.
    document.getElementById('po-lines-body').addEventListener('change', async (e) => {
      const inp = e.target.closest('.po-l-desc');
      if (!inp) return;
      const hint = inp.parentElement.querySelector('.po-l-hist');
      const q = inp.value.trim();
      hint.textContent = '';
      if (q.length < 2) return;
      try {
        const res = await fetch(API + '/purchase-orders/price-history?limit=20&q=' + encodeURIComponent(q));
        if (!res.ok) return;
        const d = await res.json();
        hint.textContent = Object.entries(d.by_currency || {}).map(([ccy, s]) => tf('poPriceHint', {
          last: formatNum(s.last) + ' ' + ccy, who: s.last_supplier || '—', date: formatDisplayDate(s.last_date),
          low: formatNum(s.lowest) + ' ' + ccy, lowWho: s.lowest_supplier || '—' })).join(' · ');
      } catch (_) { /* a hint only */ }
    });
    document.getElementById('po-lines-body').addEventListener('click', (e) => {
      if (e.target.closest('.po-l-del')) e.target.closest('tr').remove();
    });

    document.getElementById('po-create-btn').addEventListener('click', async () => {
      const entity_id = document.getElementById('po-supplier').value || null;
      const order_date = document.getElementById('po-order-date').value;
      const expected_date = document.getElementById('po-expected-date').value || null;
      if (!order_date) { showAlert(t('poNeedOrderDate'), true); return; }
      const lines = [];
      document.querySelectorAll('#po-lines-body tr').forEach(tr => {
        const desc = tr.querySelector('.po-l-desc').value.trim();
        const qty = parseFloat(tr.querySelector('.po-l-qty').value || '0');
        const price = parseInt(tr.querySelector('.po-l-price').value || '0', 10);
        if (desc && qty > 0) lines.push({ description: desc, ordered_qty: qty, unit_price: price });
      });
      if (!lines.length) { showAlert(t('poNeedLine'), true); return; }
      try {
        const res = await fetch(API + '/purchase-orders', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ entity_id, order_date, expected_date, lines }),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('poCreateFailed'), true); return; }
        showAlert(t('poCreated'));
        document.getElementById('po-lines-body').innerHTML = '';
        poAddLineRow();
        await loadPOList();
      } catch (e) { showAlert(t('poCreateFailed'), true); }
    });

    async function loadPOList() {
      try {
        const res = await fetch(API + '/purchase-orders');
        if (!res.ok) return;
        const pos = await res.json();
        const body = document.getElementById('po-list-body');
        body.innerHTML = '';
        if (!pos.length) {
          body.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:1rem;">${t('poNoneYet')}</td></tr>`;
          return;
        }
        pos.forEach(p => {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${escapeHtml(p.number)}</td><td>${escapeHtml(p.supplier_name || '—')}</td>
            <td>${escapeHtml(formatDisplayDate(p.order_date))}</td><td>${formatNum(p.total)} ${escapeHtml(p.currency)}</td>
            <td><span class="badge ${p.status === 'received' ? 'badge-ok' : ''}">${t('poStatus_' + p.status)}</span></td>
            <td><button class="btn btn-secondary btn-sm po-view" data-id="${p.id}">${t('payrollViewBtn')}</button></td>`;
          body.appendChild(tr);
        });
      } catch (e) { /* ignore */ }
    }

    document.getElementById('po-list-body').addEventListener('click', async (e) => {
      const btn = e.target.closest('.po-view');
      if (!btn) return;
      await openPODetail(btn.dataset.id);
    });

    async function openPODetail(id) {
      try {
        const res = await fetch(API + '/purchase-orders/' + id);
        if (!res.ok) return;
        const po = await res.json();
        poCurrentId = po.id;
        document.getElementById('po-detail').style.display = 'block';
        document.getElementById('po-match-result').style.display = 'none';
        document.getElementById('po-detail-title').textContent =
          `${t('poDetail')} ${po.number} (${t('poStatus_' + po.status)})`;
        const body = document.getElementById('po-detail-lines');
        body.innerHTML = '';
        poCurrent = po;
        po.lines.forEach(ln => {
          const outstanding = Math.max(0, ln.ordered_qty - ln.received_qty);
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${escapeHtml(ln.description)}</td><td>${ln.ordered_qty}</td>
            <td>${ln.received_qty}</td><td>${ln.billed_qty}</td><td>${formatNum(ln.unit_price)} ${escapeHtml(po.currency)}</td>
            <td><input type="number" class="po-recv-qty" data-line="${ln.id}" min="0" max="${outstanding}" step="0.01" value="${outstanding}" style="width:7rem;"></td>`;
          body.appendChild(tr);
        });
        // What can be done with it now: the lifecycle (§4.8).
        const st = po.status;
        const show = (id, on) => { const b = document.getElementById(id); if (b) b.style.display = on ? '' : 'none'; };
        show('po-issue-btn', st === 'draft');
        show('po-delete-btn', st === 'draft');
        show('po-cancel-btn', st === 'draft' || (st === 'issued' && po.lines.every(l => !l.received_qty)));
        show('po-close-btn', ['issued', 'partially_received', 'received'].includes(st));
        show('po-bill-btn', po.lines.some(l => l.billable_qty > 0) && !['draft', 'cancelled'].includes(st));
        show('po-receive-btn', !['draft', 'cancelled', 'closed', 'received'].includes(st));
        const billsEl = document.getElementById('po-bills');
        billsEl.innerHTML = (po.bills || []).length
          ? escapeHtml(t('poBillsLabel')) + ' ' + po.bills.map(b => `<strong>${escapeHtml(b.number)}</strong> ${formatNum(b.amount)} ${escapeHtml(b.currency)}`
              + (b.status === 'voided' ? ` (${escapeHtml(t('poBillVoided'))})` : '')).join(' · ')
          : '';
        // Bills (purchase invoices) for the match dropdown.
        const billRes = await fetch(API + '/invoices?kind=purchase');
        const bills = billRes.ok ? await billRes.json() : [];
        const sel = document.getElementById('po-match-bill');
        sel.innerHTML = bills.length
          ? bills.map(b => `<option value="${b.id}">${escapeHtml(b.number)} — ${formatNum(b.amount)} ${escapeHtml(b.currency)}</option>`).join('')
          : `<option value="">${t('poNoBills')}</option>`;
      } catch (e) { showAlert(t('poLoadFailed'), true); }
    }

    document.getElementById('po-receive-btn').addEventListener('click', async () => {
      if (!poCurrentId) return;
      const receipt_date = document.getElementById('po-receive-date').value || new Date().toISOString().slice(0, 10);
      const lines = [];
      document.querySelectorAll('#po-detail-lines .po-recv-qty').forEach(inp => {
        const q = parseFloat(inp.value || '0');
        if (q > 0) lines.push({ po_line_id: inp.dataset.line, quantity: q });
      });
      if (!lines.length) { showAlert(t('poNeedReceiveQty'), true); return; }
      try {
        const res = await fetch(API + '/purchase-orders/' + poCurrentId + '/receipts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ receipt_date, lines }),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('poReceiveFailed'), true); return; }
        showAlert(t('poReceived'));
        await loadPOList();
        await openPODetail(poCurrentId);
      } catch (e) { showAlert(t('poReceiveFailed'), true); }
    });

    async function poSetStatus(status, confirmKey) {
      if (!poCurrentId) return;
      if (confirmKey && !(await uiConfirm({ message: t(confirmKey), confirmLabel: t('btnConfirm'), danger: status === 'cancelled' }))) return;
      const res = await fetch(API + '/purchase-orders/' + poCurrentId, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
      const d = await readJsonSafe(res);
      if (!res.ok) { showAlert((d && d.detail) ? d.detail : t('poUpdateFailed'), true); return; }
      await loadPOList();
      await openPODetail(poCurrentId);
    }
    document.getElementById('po-issue-btn').addEventListener('click', () => poSetStatus('issued'));
    document.getElementById('po-close-btn').addEventListener('click', () => poSetStatus('closed', 'poCloseConfirm'));
    document.getElementById('po-cancel-btn').addEventListener('click', () => poSetStatus('cancelled', 'poCancelConfirm'));
    document.getElementById('po-delete-btn').addEventListener('click', async () => {
      if (!poCurrentId || !(await uiConfirm({ message: t('poDeleteConfirm'), confirmLabel: t('btnDelete'), danger: true }))) return;
      const res = await fetch(API + '/purchase-orders/' + poCurrentId, { method: 'DELETE' });
      if (!res.ok) { const d = await readJsonSafe(res); showAlert((d && d.detail) ? d.detail : t('poUpdateFailed'), true); return; }
      poCurrentId = null;
      document.getElementById('po-detail').style.display = 'none';
      await loadPOList();
    });
    document.getElementById('po-bill-btn').addEventListener('click', async () => {
      if (!poCurrentId || !poCurrent) return;
      const total = poCurrent.lines.reduce((a, l) => a + Math.round(l.billable_qty * l.unit_price), 0);
      const number = await uiPrompt({ title: t('poBillBtn'), message: tf('poBillPrompt', { amount: formatNum(total) + ' ' + poCurrent.currency }) });
      if (number === null) return;
      const res = await fetch(API + '/purchase-orders/' + poCurrentId + '/bill', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ number: number.trim() || null }) });
      const d = await readJsonSafe(res);
      if (!res.ok) { showAlert((d && d.detail) ? d.detail : t('poBillFailed'), true); return; }
      showAlert(tf('poBilled', { number: d.invoice_number, amount: formatNum(d.amount) + ' ' + d.currency }));
      await loadPOList();
      await openPODetail(poCurrentId);
    });

    document.getElementById('po-match-btn').addEventListener('click', async () => {
      if (!poCurrentId) return;
      const invoice_id = document.getElementById('po-match-bill').value;
      if (!invoice_id) { showAlert(t('poNoBills'), true); return; }
      try {
        const res = await fetch(API + '/purchase-orders/' + poCurrentId + '/match', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ invoice_id }),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('poMatchFailed'), true); return; }
        renderMatchResult(data);
        await loadPOList();
      } catch (e) { showAlert(t('poMatchFailed'), true); }
    });

    function renderMatchResult(data) {
      const el = document.getElementById('po-match-result');
      el.style.display = 'block';
      if (data.matched) {
        el.style.background = '#e8f5e9';
        el.innerHTML = `<strong style="color:#2e7d32;">✓ ${t('poMatchOk')}</strong> — ${t('poMatchApprovable')}`;
        return;
      }
      el.style.background = '#fdecea';
      const labels = {
        over_quantity: t('poDiscOverQty'), over_price: t('poDiscOverPrice'),
        short_receipt: t('poDiscShortReceipt'), no_po_line: t('poDiscNoLine'),
      };
      const items = (data.discrepancies || []).map(d =>
        `<li>${escapeHtml(d.description || '')}: <strong>${labels[d.type] || d.type}</strong></li>`).join('');
      el.innerHTML = `<strong style="color:#c62828;">⚠ ${t('poMatchDiscrepancies')}</strong>`
        + `<ul style="margin:0.4rem 0 0;padding-inline-start:1.2rem;">${items}</ul>`
        + `<p style="margin:0.4rem 0 0;color:var(--text-muted);">${t('poMatchNotApproved')}</p>`;
    }

    // ═══════ Payroll Module ═══════
    let prCurrentRunId = null;

    function prCur() { return (window.__REPORTING_CURRENCY || 'IRR'); }

    async function loadPayroll() {
      // Populate the employee dropdown from employee entities.
      try {
        const res = await fetch(API + '/entities?type=employee');
        const emps = res.ok ? await res.json() : [];
        const sel = document.getElementById('pr-emp');
        sel.innerHTML = emps.length
          ? emps.map(e => `<option value="${e.id}">${escapeHtml(e.name)}</option>`).join('')
          : `<option value="">${t('payrollNoEmployees')}</option>`;
      } catch (e) { /* ignore */ }
      await loadPayProfiles();
      await loadPayRuns();
      await loadPayrollRules();
    }

    // ── Statutory rule sets (read for everyone on this page; edit = super-admin) ──
    let prRuleSets = [];

    function prRuleSummary(rs) {
      if (!rs) return `<p style="color:var(--text-muted);">${t('payrollRulesNone')}</p>`;
      const p = rs.params || {};
      const cur = p.currency || prCur();
      const brackets = (p.tax_brackets || []).map(b =>
        b.upto == null ? `${t('payrollRulesAbove')} ${Math.round(b.rate * 100)}%` : `${formatNum(b.upto)}: ${Math.round(b.rate * 100)}%`
      ).join(' · ');
      const item = (label, val) => `<div><span style="color:var(--text-muted);">${t(label)}:</span> ${val}</div>`;
      return `<div style="font-weight:600;margin-bottom:0.3rem;"><bdi>${escapeHtml(rs.name)}</bdi> (${escapeHtml(rs.year)}) — ${escapeHtml(formatDisplayDate(rs.effective_from))} → ${escapeHtml(formatDisplayDate(rs.effective_to) || '…')}</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0.25rem 1rem;">
          ${item('payrollRulesMinWage', `${formatNum(p.min_wage_daily || 0)} ${cur}`)}
          ${item('payrollRulesHousing', `${formatNum(p.housing_allowance || 0)} ${cur}`)}
          ${item('payrollRulesGrocery', `${formatNum(p.grocery_allowance || 0)} ${cur}`)}
          ${item('payrollRulesChild', `${formatNum(p.child_allowance_per_child || 0)} ${cur}`)}
          ${item('payrollRulesSeniority', `${formatNum(p.seniority_daily || 0)} ${cur}`)}
          ${item('payrollRulesInsurance', `${Math.round((p.insurance_employee_rate || 0) * 100)}% / ${Math.round((p.insurance_employer_rate || 0) * 100)}%`)}
          ${item('payrollRulesCeiling', p.insurance_ceiling == null ? '—' : `${formatNum(p.insurance_ceiling)} ${cur}`)}
          ${item('payrollRulesOvertime', `× ${p.overtime_multiplier || 1}`)}
        </div>
        <div style="margin-top:0.3rem;"><span style="color:var(--text-muted);">${t('payrollRulesBrackets')}:</span> ${brackets || '—'}</div>`;
    }

    function prFillRuleEditor(rs) {
      if (!rs) return;
      document.getElementById('pr-rules-name').value = rs.name || '';
      document.getElementById('pr-rules-from').value = rs.effective_from || '';
      document.getElementById('pr-rules-to').value = rs.effective_to || '';
      document.getElementById('pr-rules-json').value = JSON.stringify(rs.params || {}, null, 2);
    }

    async function loadPayrollRules() {
      const summary = document.getElementById('pr-rules-summary');
      if (!summary) return;
      try {
        const res = await fetch(API + '/payroll/rules/active');
        if (!res.ok) { summary.innerHTML = ''; return; }
        const data = await res.json();
        summary.innerHTML = prRuleSummary(data.rule_set);
      } catch (e) { summary.innerHTML = ''; }
      const admin = document.getElementById('pr-rules-admin');
      if (!isSuperadmin) { admin.style.display = 'none'; return; }
      admin.style.display = '';
      try {
        const res = await fetch(API + '/payroll/rules');
        prRuleSets = res.ok ? await res.json() : [];
      } catch (e) { prRuleSets = []; }
      const sel = document.getElementById('pr-rules-select');
      sel.innerHTML = prRuleSets.map(rs =>
        `<option value="${rs.id}">${escapeHtml(rs.locale.toUpperCase())} ${escapeHtml(rs.year)} — ${escapeHtml(rs.name)}</option>`
      ).join('');
      prFillRuleEditor(prRuleSets[0]);
    }

    document.getElementById('pr-rules-select').addEventListener('change', (e) => {
      prFillRuleEditor(prRuleSets.find(rs => rs.id === e.target.value));
    });

    function prReadRuleJson() {
      try { return JSON.parse(document.getElementById('pr-rules-json').value || '{}'); }
      catch (e) { showAlert(t('payrollRulesBadJson'), true); return null; }
    }

    document.getElementById('pr-rules-save').addEventListener('click', async () => {
      const id = document.getElementById('pr-rules-select').value;
      const params = prReadRuleJson();
      if (!id || !params) return;
      const payload = {
        name: document.getElementById('pr-rules-name').value.trim() || null,
        effective_from: document.getElementById('pr-rules-from').value || null,
        effective_to: document.getElementById('pr-rules-to').value || null,
        params,
      };
      try {
        const res = await fetch(API + '/payroll/rules/' + encodeURIComponent(id), {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : t('payrollRulesSaveFailed'), true); return; }
        showAlert(t('payrollRulesSaved'));
        await loadPayrollRules();
      } catch (e) { showAlert(t('payrollRulesSaveFailed'), true); }
    });

    document.getElementById('pr-rules-create').addEventListener('click', async () => {
      const year = document.getElementById('pr-rules-new-year').value.trim();
      const params = prReadRuleJson();
      if (!year) { showAlert(t('payrollRulesNeedYear'), true); return; }
      if (!params) return;
      const payload = {
        locale: document.getElementById('pr-rules-new-locale').value,
        year,
        name: document.getElementById('pr-rules-name').value.trim() || year,
        effective_from: document.getElementById('pr-rules-from').value,
        effective_to: document.getElementById('pr-rules-to').value || null,
        params,
      };
      try {
        const res = await fetch(API + '/payroll/rules', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : t('payrollRulesSaveFailed'), true); return; }
        showAlert(t('payrollRulesSaved'));
        document.getElementById('pr-rules-new-year').value = '';
        await loadPayrollRules();
      } catch (e) { showAlert(t('payrollRulesSaveFailed'), true); }
    });

    function prToggleTaxMode() {
      const statutory = document.getElementById('pr-taxmode').value === 'statutory';
      document.getElementById('pr-children-wrap').style.display = statutory ? '' : 'none';
      document.getElementById('pr-seniority-wrap').style.display = statutory ? '' : 'none';
      document.getElementById('pr-taxmode-hint').style.display = statutory ? '' : 'none';
      document.getElementById('pr-tax').disabled = statutory;
      document.getElementById('pr-ss').disabled = statutory;
    }
    document.getElementById('pr-taxmode').addEventListener('change', prToggleTaxMode);

    async function loadPayProfiles() {
      try {
        const res = await fetch(API + '/payroll/profiles');
        if (!res.ok) return;
        const rows = await res.json();
        const body = document.getElementById('pr-profiles-body');
        body.innerHTML = '';
        if (!rows.length) {
          body.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:1rem;">${t('payrollNoProfiles')}</td></tr>`;
          return;
        }
        rows.forEach(p => {
          const pay = p.pay_type === 'hourly'
            ? `${formatNum(p.hourly_rate)} ${prCur()}/h · ${p.standard_hours}h`
            : `${formatNum(p.base_salary)} ${prCur()}`;
          const tr = document.createElement('tr');
          const statutory = p.tax_mode === 'statutory';
          const statutoryCell = `<span title="${t('payrollTaxModeStatutory')}">${t('payrollStatutoryShort')}${p.children ? ` · ${p.children} ${t('payrollChildrenShort')}` : ''}</span>`;
          const hired = p.hired_on ? `<div style="color:var(--text-muted);font-size:0.78rem;">${escapeHtml(tf('payrollHiredSince', { date: p.hired_on }))}</div>` : '';
          tr.innerHTML = `<td>${escapeHtml(p.employee_name || '')}${hired}</td><td>${t(p.pay_type === 'hourly' ? 'payrollHourly' : 'payrollSalaried')}</td>
            <td>${pay}</td><td>${statutory ? statutoryCell : (p.income_tax_rate * 100).toFixed(1) + '%'}</td>
            <td>${statutory ? t('payrollStatutoryShort') : (p.social_security_rate * 100).toFixed(1) + '%'}</td><td>${(p.pension_rate * 100).toFixed(1)}%</td>`;
          body.appendChild(tr);
        });
      } catch (e) { /* ignore */ }
    }

    document.getElementById('pr-save-profile').addEventListener('click', async () => {
      const entity_id = document.getElementById('pr-emp').value;
      if (!entity_id) { showAlert(t('payrollNoEmployees'), true); return; }
      const payload = {
        entity_id,
        pay_type: document.getElementById('pr-type').value,
        base_salary: parseInt(document.getElementById('pr-base').value || '0', 10),
        hourly_rate: parseInt(document.getElementById('pr-rate').value || '0', 10),
        standard_hours: parseFloat(document.getElementById('pr-std').value || '0'),
        overtime_multiplier: parseFloat(document.getElementById('pr-otm').value || '1.5'),
        income_tax_rate: parseFloat(document.getElementById('pr-tax').value || '0') / 100,
        social_security_rate: parseFloat(document.getElementById('pr-ss').value || '0') / 100,
        pension_rate: parseFloat(document.getElementById('pr-pension').value || '0') / 100,
        tax_mode: document.getElementById('pr-taxmode').value,
        children: parseInt(document.getElementById('pr-children').value || '0', 10),
        seniority_eligible: document.getElementById('pr-seniority').checked,
        hired_on: document.getElementById('pr-hired').value || null,
      };
      try {
        const res = await fetch(API + '/payroll/profiles', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('payrollSaveFailed'), true); return; }
        showAlert(t('payrollProfileSaved'));
        await loadPayProfiles();
      } catch (e) { showAlert(t('payrollSaveFailed'), true); }
    });

    async function loadPayRuns() {
      try {
        const res = await fetch(API + '/payroll/runs');
        if (!res.ok) return;
        const runs = await res.json();
        const body = document.getElementById('pr-runs-body');
        body.innerHTML = '';
        if (!runs.length) {
          body.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:1rem;">${t('payrollNoRuns')}</td></tr>`;
          return;
        }
        runs.forEach(r => {
          const tr = document.createElement('tr');
          const kind = r.kind === 'year_end'
            ? `<div><span class="badge">${escapeHtml(tf('payrollYearEndRun', { year: r.year_key || '' }))}</span></div>` : '';
          tr.innerHTML = `<td>${escapeHtml(formatDisplayDate(r.period_start))} – ${escapeHtml(formatDisplayDate(r.period_end))}${kind}</td><td>${escapeHtml(formatDisplayDate(r.pay_date))}</td>
            <td>${formatNum(r.total_gross)} ${escapeHtml(r.currency)}</td><td>${formatNum(r.total_net)} ${escapeHtml(r.currency)}</td>
            <td><span class="badge ${r.status === 'paid' ? 'badge-ok' : ''}">${t('payrollStatus_' + r.status)}</span></td>
            <td><button class="btn btn-secondary btn-sm pr-view-run" data-id="${r.id}">${t('payrollViewBtn')}</button></td>`;
          body.appendChild(tr);
        });
      } catch (e) { /* ignore */ }
    }

    document.getElementById('pr-run-btn').addEventListener('click', async () => {
      const period_start = document.getElementById('pr-start').value;
      const period_end = document.getElementById('pr-end').value;
      const pay_date = document.getElementById('pr-paydate').value || period_end;
      if (!period_start || !period_end) { showAlert(t('payrollNeedDates'), true); return; }
      try {
        const res = await fetch(API + '/payroll/runs', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ period_start, period_end, pay_date }),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('payrollRunFailed'), true); return; }
        await loadPayRuns();
        renderPayRunDetail(data);
      } catch (e) { showAlert(t('payrollRunFailed'), true); }
    });

    // Year end: عیدی و پاداش + حق سنوات for a Jalali year (roadmap §3.3).
    document.getElementById('pr-ye-btn').addEventListener('click', async () => {
      const pay_date = document.getElementById('pr-ye-paydate').value;
      if (!pay_date) { showAlert(t('payrollNeedDates'), true); return; }
      const year = (document.getElementById('pr-ye-year').value || '').trim() || null;
      try {
        const res = await fetch(API + '/payroll/runs/year-end', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ year, pay_date }),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('payrollYearEndFailed'), true); return; }
        await loadPayRuns();
        renderPayRunDetail(data);
      } catch (e) { showAlert(t('payrollYearEndFailed'), true); }
    });

    document.getElementById('pr-runs-body').addEventListener('click', async (e) => {
      const btn = e.target.closest('.pr-view-run');
      if (!btn) return;
      try {
        const res = await fetch(API + '/payroll/runs/' + btn.dataset.id);
        if (!res.ok) return;
        renderPayRunDetail(await res.json());
      } catch (err) { /* ignore */ }
    });

    function renderPayRunDetail(run) {
      prCurrentRunId = run.id;
      document.getElementById('pr-run-detail').style.display = 'block';
      const yearEnd = run.kind === 'year_end';
      document.getElementById('pr-run-detail-title').textContent =
        `${yearEnd ? tf('payrollYearEndRun', { year: run.year_key || '' }) : t('payrollRunDetail')} — ${escapeHtml(formatDisplayDate(run.period_start))} – ${escapeHtml(formatDisplayDate(run.period_end))} (${t('payrollStatus_' + run.status)})`;
      const body = document.getElementById('pr-run-lines-body');
      body.innerHTML = '';
      (run.lines || []).forEach(ln => {
        const tr = document.createElement('tr');
        const ye = yearEnd ? `<div style="color:var(--text-muted);font-size:0.78rem;">${escapeHtml(tf('payrollYearEndLine', {
          eidi: formatNum(ln.eidi || 0), sanavat: formatNum(ln.sanavat || 0), days: ln.days_worked || 0 }))}</div>` : '';
        tr.innerHTML = `<td>${escapeHtml(ln.employee_name)}${ye}</td><td>${formatNum(ln.gross)}</td>
          <td>${formatNum(ln.allowances || 0)}</td>
          <td>${formatNum(ln.income_tax)}</td><td>${formatNum(ln.social_security)}</td>
          <td>${formatNum(ln.employer_social || 0)}</td>
          <td>${formatNum(ln.pre_tax_deductions)}</td><td>${formatNum(ln.net_pay)}</td>
          <td><button class="btn btn-secondary btn-sm pr-payslip" data-rid="${run.id}" data-eid="${ln.entity_id}">${t('payrollPayslip')}</button></td>`;
        body.appendChild(tr);
      });
      document.getElementById('pr-post-btn').style.display = run.status === 'draft' ? '' : 'none';
      document.getElementById('pr-pay-btn').style.display = run.status === 'posted' ? '' : 'none';
      document.getElementById('pr-email-btn').style.display = (run.status === 'posted' || run.status === 'paid') ? '' : 'none';
      const rid = encodeURIComponent(run.id);
      document.getElementById('pr-ins-csv').href = `${API}/payroll/runs/${rid}/insurance-list.csv`;
      document.getElementById('pr-ins-csv').style.display = yearEnd ? 'none' : '';      // عیدی/سنوات carry no insurance
      document.getElementById('pr-tax-csv').href = `${API}/payroll/runs/${rid}/tax-list.csv`;
    }

    // Each employee gets their own payslip at the address on their record (roadmap §4.9).
    document.getElementById('pr-email-btn').addEventListener('click', async () => {
      if (!prCurrentRunId) return;
      if (!(await uiConfirm({ message: t('payrollEmailConfirm'), confirmLabel: t('payrollEmailPayslips') }))) return;
      try {
        const res = await fetch(API + '/payroll/runs/' + prCurrentRunId + '/payslips/email', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
        const d = await readJsonSafe(res);
        if (!res.ok) { showAlert((d && d.detail) ? d.detail : t('payrollEmailFailed'), true); return; }
        let msg = tf('payrollEmailResult', { sent: d.sent.length, failed: d.failed.length, skipped: d.skipped.length });
        if (d.skipped.length) msg += ' ' + tf('payrollEmailNoAddress', { names: d.skipped.map(x => x.name).join(', ') });
        showAlert(msg, d.failed.length > 0 && !d.sent.length);
      } catch (e) { showAlert(t('payrollEmailFailed'), true); }
    });

    document.getElementById('pr-post-btn').addEventListener('click', async () => {
      if (!prCurrentRunId) return;
      const ok = await uiConfirm({ title: t('payrollPost'), message: t('payrollPostConfirm') });
      if (!ok) return;
      try {
        const res = await fetch(API + '/payroll/runs/' + prCurrentRunId + '/post', { method: 'POST' });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('payrollPostFailed'), true); return; }
        showAlert(t('payrollPosted'));
        await loadPayRuns();
        renderPayRunDetail(data);
      } catch (e) { showAlert(t('payrollPostFailed'), true); }
    });

    document.getElementById('pr-pay-btn').addEventListener('click', async () => {
      if (!prCurrentRunId) return;
      const ok = await uiConfirm({ title: t('payrollPay'), message: t('payrollPayConfirm') });
      if (!ok) return;
      try {
        const res = await fetch(API + '/payroll/runs/' + prCurrentRunId + '/pay', { method: 'POST' });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && data.detail) ? data.detail : t('payrollPayFailed'), true); return; }
        if (data && Array.isArray(data.warnings) && data.warnings.length) {
          // Paid fine, but some employees have no bank on file — surface it.
          showAlert(t('payrollPaidNoBankWarn') + ' ' + data.warnings.join(' | '), true);
        } else {
          showAlert(t('payrollPaid'));
        }
        await loadPayRuns();
        renderPayRunDetail(data);
      } catch (e) { showAlert(t('payrollPayFailed'), true); }
    });

    document.getElementById('pr-run-lines-body').addEventListener('click', async (e) => {
      const btn = e.target.closest('.pr-payslip');
      if (!btn) return;
      try {
        const res = await fetch(API + '/payroll/runs/' + btn.dataset.rid + '/payslip/' + btn.dataset.eid);
        if (!res.ok) return;
        const s = await res.json();
        const cur = s.currency || prCur();
        const msg = `${s.employee_name} · ${escapeHtml(formatDisplayDate(s.period_start))} – ${escapeHtml(formatDisplayDate(s.period_end))}\n`
          + `${t('payrollGross')}: ${formatNum(s.gross)} ${cur}\n`
          + `${t('payrollIncomeTax')}: ${formatNum(s.income_tax)} ${cur}\n`
          + `${t('payrollSocial')}: ${formatNum(s.social_security)} ${cur}\n`
          + `${t('payrollDeductions')}: ${formatNum(s.pre_tax_deductions)} ${cur}\n`
          + (s.allowances ? `${t('payrollAllowances')}: ${formatNum(s.allowances)} ${cur}\n` : '')
          + (s.employer_social ? `${t('payrollEmployerShare')}: ${formatNum(s.employer_social)} ${cur}\n` : '')
          + `${t('payrollNet')}: ${formatNum(s.net_pay)} ${cur}`;
        await uiConfirm({ title: t('payrollPayslip') + ' — ' + s.employee_name, message: msg, hideCancel: true });
      } catch (err) { /* ignore */ }
    });

    // ═══════ Audit Module ═══════   (the Run button's handler is in 13-companies-products.js)

    // "create" / "transaction" in the reader's language (auditAct_* / auditEnt_*
    // keys; tests/test_audit_labels.py keeps one for every value the server
    // writes), and a time in the user's calendar rather than the browser's.
    function auditLabel(prefix, value) {
      return enumLabel(prefix, value || '');
    }
    function auditWhen(ts) {
      const d = new Date(ts);
      if (!ts || isNaN(d.getTime())) return '';
      const p = (n) => String(n).padStart(2, '0');
      return formatDisplayDate(`${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }

    async function loadAuditLogs() {
      try {
        const res = await fetch(bsAPI + '/audit/logs?limit=30');
        if (!res.ok) return;
        const logs = await res.json();
        const body = document.getElementById('audit-log-body');
        body.innerHTML = '';
        logs.forEach(l => {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td style="font-size:0.8rem;white-space:nowrap;"><bdi>${escapeHtml(auditWhen(l.timestamp))}</bdi></td>
            <td>${escapeHtml(auditLabel('auditAct_', l.action))}</td><td>${escapeHtml(auditLabel('auditEnt_', l.entity_type))}</td>
            <td style="font-size:0.8rem;">${escapeHtml((l.entity_id || '').substring(0, 8))}</td>
            <td>${escapeHtml(l.username || '—')}</td>
            <td style="font-size:0.8rem;max-width:300px;overflow:hidden;text-overflow:ellipsis;"><bdi dir="ltr">${escapeHtml((l.detail || '').substring(0, 120))}</bdi></td>`;
          body.appendChild(tr);
        });
      } catch (e) { /* ignore */ }
    }

    // ═══════ CFO Module ═══════
    async function loadCFOReport() {
      try {
        const res = await fetch(bsAPI + '/cfo/report');
        if (!res.ok) return;
        const data = await res.json();
        document.getElementById('cfo-grade').textContent = data.health_grade;
        document.getElementById('cfo-grade').style.color = data.health_grade <= 'B' ? '#2e7d32' : data.health_grade <= 'C' ? '#f57f17' : '#c62828';
        document.getElementById('cfo-risk').textContent = data.risk_score + '/100';
        document.getElementById('cfo-risk').style.color = data.risk_score <= 30 ? '#2e7d32' : data.risk_score <= 60 ? '#f57f17' : '#c62828';
        document.getElementById('cfo-runway').textContent = data.runway_months + ' ' + t('monthsShort');
        // Sync the global from the server's response so every other
        // widget on the page picks up the right currency too.
        if (data.currency) window.__REPORTING_CURRENCY = data.currency;
        document.getElementById('cfo-burn').textContent = data.burn_rate.toLocaleString() + ' ' + currencyUnit() + '/' + t('monthsShort');

        const kpiGrid = document.getElementById('cfo-kpis');
        kpiGrid.innerHTML = '';
        data.kpis.forEach(k => {
          const riskColor = k.risk_level === 'danger' ? '#c62828' : k.risk_level === 'caution' ? '#f57f17' : 'var(--text)';
          const trendIcon = k.trend === 'up' ? '↑' : k.trend === 'down' ? '↓' : '';
          const div = document.createElement('div');
          div.className = 'panel';
          div.style.cssText = 'padding:0.6rem;';
          // Any non-% non-months unit is a currency code → format with thousands.
          const isCurrencyUnit = k.unit && k.unit !== '%' && k.unit !== 'months';
          const displayVal = isCurrencyUnit ? Number(k.value).toLocaleString() : k.value;
          // "32.5%" not "32.5 %" (a space lets the sign drift to the far side in
          // Persian), months in the reader's language, and <bdi> keeps a minus
          // sign in front of its number in a right-to-left page.
          const num = k.unit === '%' ? displayVal + '%' : String(displayVal);
          const unit = k.unit === '%' ? '' : k.unit === 'months' ? t('monthsShort') : (k.unit || '');
          div.innerHTML = `<div style="font-size:0.72rem;color:var(--text-muted);">${escapeHtml(localizeDynamicText(k.label))}</div>
            <div style="font-size:1.1rem;font-weight:700;color:${riskColor};"><bdi>${escapeHtml(num)}</bdi>${unit ? ' ' + escapeHtml(unit) : ''}</div>
            ${trendIcon ? `<div style="font-size:0.75rem;color:${
              (k.key === 'expense_trend' || k.key === 'burn_rate')
                ? (k.trend === 'up' ? '#c62828' : '#2e7d32')
                : (k.trend === 'up' ? '#2e7d32' : '#c62828')
            };">${trendIcon} <bdi>${escapeHtml(String(k.trend_pct))}%</bdi></div>` : ''}`;
          kpiGrid.appendChild(div);
        });

        const narrativeEl = document.getElementById('cfo-narrative');
        if (data.narrative) {
          narrativeEl.style.display = 'block';
          narrativeEl.textContent = data.narrative;
        }

        const insightsEl = document.getElementById('cfo-insights');
        insightsEl.innerHTML = '';
        data.insights.forEach(i => {
          const color = i.severity === 'critical' ? '#c62828' : i.severity === 'warning' ? '#f57f17' : '#1565c0';
          const div = document.createElement('div');
          div.style.cssText = `padding:0.5rem 0.75rem;margin-bottom:0.4rem;border-left:4px solid ${color};background:#fafafa;border-radius:4px;`;
          div.innerHTML = `<strong style="color:${color}">${escapeHtml(i.title)}</strong><br><span style="font-size:0.85rem;">${escapeHtml(i.body)}</span>`;
          insightsEl.appendChild(div);
        });
      } catch (e) { console.warn('CFO report load failed:', e); }
    }

    document.getElementById('cfo-ask-btn').addEventListener('click', async () => {
      const q = document.getElementById('cfo-question-input').value.trim();
      if (!q) return;
      const answerEl = document.getElementById('cfo-answer');
      answerEl.style.display = 'block';
      answerEl.textContent = t('msgThinking');
      try {
        const res = await fetch(bsAPI + '/cfo/ask', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: q })
        });
        const data = await res.json();
        answerEl.innerHTML = `<strong>${escapeHtml(t('cfoAskQ'))}</strong> ${escapeHtml(data.question)}<br><br><strong>${escapeHtml(t('cfoAskA'))}</strong> ${escapeHtml(data.answer)}<br><br><span style="font-size:0.8rem;color:var(--text-muted);">${escapeHtml(tf('cfoAskHealth', { grade: data.health_grade, score: data.risk_score }))}</span>`;
      } catch (e) { answerEl.textContent = tf('errorWithMessage', { message: e.message }); }
    });
    document.getElementById('cfo-question-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') document.getElementById('cfo-ask-btn').click(); });

    // Old generic "Load demo data" button replaced by the per-locale
    // "Reset & load Iran/UK demo" buttons in the Settings page. The
    // legacy /cfo/seed-sample-data endpoint stays available for callers
    // that want the broad multi-section seed.

    document.querySelectorAll('.cfo-quick').forEach(btn => {
      btn.addEventListener('click', () => {
        document.getElementById('cfo-question-input').value = btn.dataset.q;
        document.getElementById('cfo-ask-btn').click();
      });
    });

    // Auto-load data when switching to new pages
    const origShowPage = showPage;
    if (typeof showPage === 'function') {
      const _origShowPage = showPage;
    }
