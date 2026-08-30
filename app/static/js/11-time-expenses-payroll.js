
    // ═══════ Time & Billing Module ═══════
    let tmReadyPreview = null;
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
                <td>${escapeHtml(p.work_date)}</td>
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
        if (!res.ok) { showAlert(data.detail || 'Failed.', true); return; }
        showAlert(rbtn ? t('timePendingResolved') : t('timePendingRejected'));
        loadPendingTime();
        if (typeof tmLoadEntries === 'function') tmLoadEntries();
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
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
            <td>${escapeHtml(s.period_start)} – ${escapeHtml(s.period_end)}</td>
            <td class="num">${formatNum(s.gross)} ${escapeHtml(s.currency || '')}</td>
            <td class="num"><strong>${formatNum(s.net_pay)}</strong> ${escapeHtml(s.currency || '')}</td>
            <td>${escapeHtml(s.pay_date)}</td>
            <td>${s.status === 'paid' ? '<span style="color:#15803d;font-weight:600;">' + escapeHtml(t('myPayStatusPaid')) + '</span>' : escapeHtml(s.status)}</td>
            <td>${escapeHtml(s.paid_to || '—')}</td>
            <td><a class="btn btn-secondary btn-sm" target="_blank"
                   href="${API}/payroll/runs/${encodeURIComponent(s.run_id)}/payslip/${encodeURIComponent(s.entity_id)}/pdf">PDF</a></td>
          </tr>`).join('');
      } catch (err) { section.style.display = 'none'; }
    }

    async function loadTimeTab() {
      loadPendingTime();
      loadMyPay();
      try {
        const [wr, cr] = await Promise.all([
          fetch(API + '/entities?type=employee'), fetch(API + '/entities?type=supplier'),
        ]);
        const emps = wr.ok ? await wr.json() : [];
        const sups = cr.ok ? await cr.json() : [];
        const workers = [...emps, ...sups];
        const wsel = document.getElementById('tm-worker');
        wsel.innerHTML = workers.length
          ? workers.map(w => `<option value="${w.id}">${escapeHtml(w.name)}</option>`).join('')
          : `<option value="">${t('timeNoWorkers')}</option>`;
        const clRes = await fetch(API + '/entities?type=client');
        const clients = clRes.ok ? await clRes.json() : [];
        const opts = clients.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
        document.getElementById('tm-client').innerHTML = clients.length ? opts : `<option value="">${t('timeNoClients')}</option>`;
        document.getElementById('tm-filter-client').innerHTML = `<option value="">${t('timeAllClients')}</option>` + opts;
      } catch (e) { /* ignore */ }
      if (!document.getElementById('tm-date').value) document.getElementById('tm-date').value = new Date().toISOString().slice(0, 10);
      await tmLoadProjects();
      await tmLoadEntries();
      await tmLoadReady();
    }

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
            `<button class="btn btn-secondary btn-sm tm-wo" data-id="${e.id}">${t('timeWriteOff')}</button> <button class="btn btn-secondary btn-sm tm-del" data-id="${e.id}">✕</button>`;
          tr.innerHTML = `<td>${e.work_date}</td><td>${escapeHtml(e.employee_name || '')}</td>
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
      try {
        const res = await fetch(API + '/expenses/settings');
        if (res.ok) {
          expSettings = await res.json();
          document.getElementById('exp-rate').value = expSettings.mileage_rate;
          document.getElementById('exp-unit').value = expSettings.mileage_unit;
          document.getElementById('exp-threshold').value = expSettings.approval_threshold;
        }
      } catch (e) { /* ignore */ }
      try {
        const res = await fetch(API + '/entities?type=employee');
        const emps = res.ok ? await res.json() : [];
        const sel = document.getElementById('exp-emp');
        sel.innerHTML = emps.length
          ? emps.map(e => `<option value="${e.id}">${escapeHtml(e.name)}</option>`).join('')
          : `<option value="">${t('expNoEmployees')}</option>`;
      } catch (e) { /* ignore */ }
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
          tr.innerHTML = `<td>${escapeHtml(c.employee_name)}</td><td>${c.claim_date}</td>
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
          tr.innerHTML = `<td>${escapeHtml(c.employee_name)}</td><td>${c.claim_date}</td>
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

    function poCur() { return (window.__REPORTING_CURRENCY || 'IRR'); }

    function poAddLineRow(desc = '', qty = '', price = '') {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td><input type="text" class="po-l-desc" value="${escapeHtml(desc)}"></td>
        <td><input type="number" class="po-l-qty" min="0" step="0.01" value="${qty}" style="width:7rem;"></td>
        <td><input type="number" class="po-l-price" min="0" value="${price}" style="width:8rem;"></td>
        <td><button type="button" class="btn btn-secondary btn-sm po-l-del">✕</button></td>`;
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
            <td>${p.order_date}</td><td>${formatNum(p.total)} ${escapeHtml(p.currency)}</td>
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
        po.lines.forEach(ln => {
          const outstanding = Math.max(0, ln.ordered_qty - ln.received_qty);
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${escapeHtml(ln.description)}</td><td>${ln.ordered_qty}</td>
            <td>${ln.received_qty}</td><td>${formatNum(ln.unit_price)} ${escapeHtml(po.currency)}</td>
            <td><input type="number" class="po-recv-qty" data-line="${ln.id}" min="0" max="${outstanding}" step="0.01" value="${outstanding}" style="width:7rem;"></td>`;
          body.appendChild(tr);
        });
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
    }

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
          tr.innerHTML = `<td>${escapeHtml(p.employee_name || '')}</td><td>${t(p.pay_type === 'hourly' ? 'payrollHourly' : 'payrollSalaried')}</td>
            <td>${pay}</td><td>${(p.income_tax_rate * 100).toFixed(1)}%</td>
            <td>${(p.social_security_rate * 100).toFixed(1)}%</td><td>${(p.pension_rate * 100).toFixed(1)}%</td>`;
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
          tr.innerHTML = `<td>${r.period_start} – ${r.period_end}</td><td>${r.pay_date}</td>
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
      document.getElementById('pr-run-detail-title').textContent =
        `${t('payrollRunDetail')} — ${run.period_start} – ${run.period_end} (${t('payrollStatus_' + run.status)})`;
      const body = document.getElementById('pr-run-lines-body');
      body.innerHTML = '';
      (run.lines || []).forEach(ln => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${escapeHtml(ln.employee_name)}</td><td>${formatNum(ln.gross)}</td>
          <td>${formatNum(ln.income_tax)}</td><td>${formatNum(ln.social_security)}</td>
          <td>${formatNum(ln.pre_tax_deductions)}</td><td>${formatNum(ln.net_pay)}</td>
          <td><button class="btn btn-secondary btn-sm pr-payslip" data-rid="${run.id}" data-eid="${ln.entity_id}">${t('payrollPayslip')}</button></td>`;
        body.appendChild(tr);
      });
      document.getElementById('pr-post-btn').style.display = run.status === 'draft' ? '' : 'none';
      document.getElementById('pr-pay-btn').style.display = run.status === 'posted' ? '' : 'none';
    }

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
        const msg = `${s.employee_name} · ${s.period_start} – ${s.period_end}\n`
          + `${t('payrollGross')}: ${formatNum(s.gross)} ${cur}\n`
          + `${t('payrollIncomeTax')}: ${formatNum(s.income_tax)} ${cur}\n`
          + `${t('payrollSocial')}: ${formatNum(s.social_security)} ${cur}\n`
          + `${t('payrollDeductions')}: ${formatNum(s.pre_tax_deductions)} ${cur}\n`
          + `${t('payrollNet')}: ${formatNum(s.net_pay)} ${cur}`;
        await uiConfirm({ title: t('payrollPayslip') + ' — ' + s.employee_name, message: msg, hideCancel: true });
      } catch (err) { /* ignore */ }
    });

    // ═══════ Audit Module ═══════
    document.getElementById('audit-run-btn').addEventListener('click', async () => {
      try {
        document.getElementById('audit-run-btn').disabled = true;
        const res = await fetch(bsAPI + '/audit/report');
        const data = await res.json();
        document.getElementById('audit-scores').style.display = 'flex';
        document.getElementById('audit-integrity-score').textContent = data.integrity_score;
        document.getElementById('audit-integrity-score').style.color = data.integrity_score >= 80 ? '#2e7d32' : data.integrity_score >= 50 ? '#f57f17' : '#c62828';
        document.getElementById('audit-health-score').textContent = data.health_score;
        document.getElementById('audit-health-score').style.color = data.health_score >= 80 ? '#2e7d32' : data.health_score >= 50 ? '#f57f17' : '#c62828';
        document.getElementById('audit-checks-summary').textContent = `${data.checks_passed} passed / ${data.checks_failed} failed`;
        const findingsWrap = document.getElementById('audit-findings-wrap');
        const findingsList = document.getElementById('audit-findings-list');
        findingsList.innerHTML = '';
        if (data.findings.length) {
          findingsWrap.style.display = 'block';
          data.findings.forEach(f => {
            const color = f.severity === 'critical' ? '#c62828' : f.severity === 'warning' ? '#f57f17' : '#1565c0';
            const div = document.createElement('div');
            div.style.cssText = `padding:0.5rem 0.75rem;margin-bottom:0.4rem;border-left:4px solid ${color};background:#fafafa;border-radius:4px;`;
            div.innerHTML = `<strong style="color:${color}">${escapeHtml(f.severity.toUpperCase())}</strong> — <strong>${escapeHtml(f.title)}</strong><br><span style="font-size:0.85rem;color:var(--text-muted);">${escapeHtml(f.detail)}</span>`;
            findingsList.appendChild(div);
          });
        } else {
          findingsWrap.style.display = 'block';
          findingsList.innerHTML = '<p style="color:#2e7d32;">All checks passed. No issues found.</p>';
        }
      } catch (e) { showAlert('Audit failed: ' + e.message, true); }
      finally { document.getElementById('audit-run-btn').disabled = false; }
    });

    async function loadAuditLogs() {
      try {
        const res = await fetch(bsAPI + '/audit/logs?limit=30');
        if (!res.ok) return;
        const logs = await res.json();
        const body = document.getElementById('audit-log-body');
        body.innerHTML = '';
        logs.forEach(l => {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td style="font-size:0.8rem;">${l.timestamp ? new Date(l.timestamp).toLocaleString() : ''}</td>
            <td>${escapeHtml(l.action)}</td><td>${escapeHtml(l.entity_type)}</td>
            <td style="font-size:0.8rem;">${escapeHtml((l.entity_id || '').substring(0, 8))}</td>
            <td>${escapeHtml(l.username || '—')}</td>
            <td style="font-size:0.8rem;max-width:300px;overflow:hidden;text-overflow:ellipsis;">${escapeHtml((l.detail || '').substring(0, 120))}</td>`;
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
        document.getElementById('cfo-runway').textContent = data.runway_months + ' mo';
        // Sync the global from the server's response so every other
        // widget on the page picks up the right currency too.
        if (data.currency) window.__REPORTING_CURRENCY = data.currency;
        document.getElementById('cfo-burn').textContent = data.burn_rate.toLocaleString() + ' ' + currencyUnit() + '/mo';

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
          div.innerHTML = `<div style="font-size:0.72rem;color:var(--text-muted);">${escapeHtml(localizeDynamicText(k.label))}</div>
            <div style="font-size:1.1rem;font-weight:700;color:${riskColor};">${displayVal} ${k.unit || ''}</div>
            ${trendIcon ? `<div style="font-size:0.75rem;color:${
              (k.key === 'expense_trend' || k.key === 'burn_rate')
                ? (k.trend === 'up' ? '#c62828' : '#2e7d32')
                : (k.trend === 'up' ? '#2e7d32' : '#c62828')
            };">${trendIcon} ${k.trend_pct}%</div>` : ''}`;
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
      answerEl.textContent = 'Thinking...';
      try {
        const res = await fetch(bsAPI + '/cfo/ask', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: q })
        });
        const data = await res.json();
        answerEl.innerHTML = `<strong>Q:</strong> ${escapeHtml(data.question)}<br><br><strong>A:</strong> ${escapeHtml(data.answer)}<br><br><span style="font-size:0.8rem;color:var(--text-muted);">Health: ${data.health_grade} | Risk: ${data.risk_score}/100</span>`;
      } catch (e) { answerEl.textContent = 'Error: ' + e.message; }
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
