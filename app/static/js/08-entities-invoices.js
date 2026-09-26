
    let _acctDetailLines = [];
    let _acctDetailMeta = {};
    async function openAccountDetail(accountCode) {
      const modal = document.getElementById('account-modal');
      const body = document.getElementById('account-modal-body');
      const title = document.getElementById('account-modal-title');
      title.textContent = accountCode + ' — ' + t('loading');
      body.innerHTML = '<p class="empty-state">' + t('loading') + '</p>';
      modal.style.display = 'flex';
      try {
        const res = await fetch(API + '/reports/accounts/' + encodeURIComponent(accountCode) + '/detail');
        if (!res.ok) throw new Error(res.statusText);
        const data = await res.json();
        _acctDetailLines = data.lines || [];
        _acctDetailMeta = { code: data.account_code, name: data.account_name, debit_turnover: data.debit_turnover, credit_turnover: data.credit_turnover, debit_balance: data.debit_balance, credit_balance: data.credit_balance };
        title.textContent = data.account_code + ' — ' + data.account_name;
        const kolLine = data.parent_code
          ? `<p style="margin:0 0 0.6rem;color:var(--text-muted);font-size:0.84rem;">${escapeHtml(t('ledgerKol'))}: <strong>${escapeHtml(data.parent_code)} — ${escapeHtml(data.parent_name || '')}</strong> · ${escapeHtml(t('ledgerMoin'))}: <strong>${escapeHtml(data.account_code)}</strong></p>`
          : '';
        body.innerHTML = `
          ${kolLine}
          <div class="detail-summary">
            <div><span>${t('fieldTotalDebit')}</span><strong>${formatNum(data.debit_turnover)}</strong></div>
            <div><span>${t('fieldTotalCredit')}</span><strong>${formatNum(data.credit_turnover)}</strong></div>
            <div><span>${t('fieldDebitBalance')}</span><strong>${formatNum(data.debit_balance)}</strong></div>
            <div><span>${t('fieldCreditBalance')}</span><strong>${formatNum(data.credit_balance)}</strong></div>
          </div>
          <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.75rem;align-items:center;">
            <input type="text" id="acct-detail-search" placeholder="${t('placeholderSearchCodeName')}" style="flex:1;min-width:180px;padding:0.4rem 0.6rem;font-size:0.85rem;">
            <button type="button" class="btn btn-secondary btn-sm" data-action="export-acct-detail" data-format="csv">CSV</button>
            <button type="button" class="btn btn-secondary btn-sm" data-action="export-acct-detail" data-format="pdf">PDF</button>
          </div>
          <div id="acct-detail-table-wrap" style="max-height:400px;overflow:auto;"></div>
        `;
        _renderAcctDetailTable(_acctDetailLines);
        document.getElementById('acct-detail-search').oninput = (e) => {
          const q = e.target.value.trim().toLowerCase();
          if (!q) { _renderAcctDetailTable(_acctDetailLines); return; }
          _renderAcctDetailTable(_acctDetailLines.filter(l =>
            (l.transaction_date || '').toLowerCase().includes(q) ||
            (l.reference || '').toLowerCase().includes(q) ||
            (l.description || '').toLowerCase().includes(q) ||
            (l.line_description || '').toLowerCase().includes(q)
          ));
        };
      } catch (err) {
        body.innerHTML = '<p class="empty-state">Error loading account details.</p>';
      }
    }

    function _renderAcctDetailTable(lines) {
      const wrap = document.getElementById('acct-detail-table-wrap');
      if (!lines.length) { wrap.innerHTML = '<p style="color:var(--text-muted);padding:0.5rem;">' + t('noDataYet') + '</p>'; return; }
      wrap.innerHTML = `<table class="detail-table"><thead><tr>
        <th>${t('labelDate')}</th><th>${t('labelReference')}</th><th>${t('labelDescription')}</th>
        <th class="num">${t('tableDebit')}</th><th class="num">${t('tableCredit')}</th><th>${t('tableLineDescription')}</th>
      </tr></thead><tbody>${lines.map(l => `<tr>
        <td>${escapeHtml(l.transaction_date)}</td>
        <td>${escapeHtml(l.reference || '—')}</td>
        <td>${escapeHtml(l.description || '—')}</td>
        <td class="num">${formatNum(l.debit)}</td>
        <td class="num">${formatNum(l.credit)}</td>
        <td>${escapeHtml(l.line_description || '—')}</td>
      </tr>`).join('')}</tbody></table>`;
    }

    function _exportAcctDetail(format) {
      const search = (document.getElementById('acct-detail-search')?.value || '').trim().toLowerCase();
      let lines = _acctDetailLines;
      if (search) lines = lines.filter(l => (l.transaction_date||'').toLowerCase().includes(search)||(l.reference||'').toLowerCase().includes(search)||(l.description||'').toLowerCase().includes(search)||(l.line_description||'').toLowerCase().includes(search));
      const m = _acctDetailMeta;
      if (format === 'csv') {
        let csv = `Account,${m.code} - ${m.name}\nDebit Turnover,${m.debit_turnover}\nCredit Turnover,${m.credit_turnover}\n\nDate,Reference,Description,Debit,Credit,Line Description\n`;
        lines.forEach(l => { csv += `${l.transaction_date},"${(l.reference||'').replace(/"/g,'""')}","${(l.description||'').replace(/"/g,'""')}",${l.debit},${l.credit},"${(l.line_description||'').replace(/"/g,'""')}"\n`; });
        downloadTextFile(`account_${m.code}.csv`, csv, 'text/csv');
      } else {
        const w = window.open('', '_blank');
        if (!w) { showAlert(t('allowPopupsPdf'), true); return; }
        w.document.write(`<html><head><title>Account ${escapeHtml(m.code)}</title><style>body{font-family:sans-serif;padding:20px}table{width:100%;border-collapse:collapse;font-size:12px}th,td{border:1px solid #ccc;padding:6px 8px;text-align:left}.num{text-align:right}h2{margin:0 0 4px}p{margin:2px 0;font-size:13px}</style></head><body>
          <h2>${escapeHtml(m.code)} — ${escapeHtml(m.name)}</h2>
          <p>Debit Turnover: ${formatNum(m.debit_turnover)} | Credit Turnover: ${formatNum(m.credit_turnover)}</p>
          <p>Debit Balance: ${formatNum(m.debit_balance)} | Credit Balance: ${formatNum(m.credit_balance)}</p>
          <table><thead><tr><th>Date</th><th>Reference</th><th>Description</th><th class="num">Debit</th><th class="num">Credit</th><th>Line Description</th></tr></thead><tbody>
          ${lines.map(l=>`<tr><td>${escapeHtml(l.transaction_date)}</td><td>${escapeHtml(l.reference||'')}</td><td>${escapeHtml(l.description||'')}</td><td class="num">${formatNum(l.debit)}</td><td class="num">${formatNum(l.credit)}</td><td>${escapeHtml(l.line_description||'')}</td></tr>`).join('')}
          </tbody></table></body></html>`);
        w.document.close();
        setTimeout(() => w.print(), 300);
      }
    }

    document.getElementById('account-modal-close').onclick = () => {
      document.getElementById('account-modal').style.display = 'none';
    };
    document.getElementById('account-modal').onclick = (e) => {
      if (e.target.id === 'account-modal') e.target.style.display = 'none';
    };

    async function loadEntities(highlightId) {
      // Also bound directly as an event listener — ignore Event arguments.
      if (typeof highlightId !== 'string' && typeof highlightId !== 'number') highlightId = null;
      const tbody = document.getElementById('entities-tbody');
      try {
        const filterType = document.getElementById('entity-filter-type').value;
        const searchTerm = document.getElementById('entity-search').value.trim();
        const params = new URLSearchParams();
        if (filterType) params.set('type', filterType);
        if (searchTerm) params.set('search', searchTerm);
        const qs = params.toString();
        const res = await fetch(API + '/entities' + (qs ? '?' + qs : ''));
        const list = await res.json();
        tbody.innerHTML = '';
        if (!list.length) {
          tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No entities yet. Add a client, bank, or employee above.</td></tr>';
          return;
        }
        list.forEach(e => {
          const tr = document.createElement('tr');
          tr.dataset.entityId = e.id;
          tr.innerHTML = `
            <td>${escapeHtml(e.type)}</td>
            <td>${escapeHtml(e.name)}</td>
            <td>${escapeHtml(e.code || '—')}</td>
            <td>
              <button type="button" class="btn btn-secondary btn-sm edit-entity" data-entity-id="${e.id}" data-entity-type="${escapeHtml(e.type)}" data-entity-name="${escapeHtml(e.name)}" data-entity-code="${escapeHtml(e.code || '')}">Edit</button>
              <button type="button" class="btn btn-danger btn-sm delete-entity" data-entity-id="${e.id}" data-entity-name="${escapeHtml(e.name)}" style="margin-left:0.35rem;">Delete</button>
            </td>
            <td><button type="button" class="btn btn-secondary btn-sm view-entity-txns" data-entity-id="${e.id}" data-entity-name="${escapeHtml(e.name)}" data-entity-type="${escapeHtml(e.type || '')}">${escapeHtml(t('entTxViewBtn'))}</button></td>
          `;
          tbody.appendChild(tr);
        });
        if (highlightId) flashRow(tbody.querySelector('tr[data-entity-id="' + CSS.escape(String(highlightId)) + '"]'));
      } catch (err) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-state">Error loading entities.</td></tr>';
      }
    }
    // Entity filter and search
    document.getElementById('entity-filter-type').addEventListener('change', loadEntities);
    let _entitySearchTimer;
    document.getElementById('entity-search').addEventListener('input', () => {
      clearTimeout(_entitySearchTimer);
      _entitySearchTimer = setTimeout(loadEntities, 300);
    });

    document.getElementById('entities-tbody').addEventListener('click', (e) => {
      const btn = e.target.closest('.view-entity-txns');
      if (btn) openEntityTransactions(btn.dataset.entityId, btn.dataset.entityName || '', btn.dataset.entityType || '');
      const editBtn = e.target.closest('.edit-entity');
      if (editBtn) editEntity(editBtn);
      const delBtn = e.target.closest('.delete-entity');
      if (delBtn) deleteEntity(delBtn);
    });
    invoicesTbody.addEventListener('click', async (e) => {
      const mailBtn = e.target.closest('.inv-email');
      if (mailBtn) {
        let to = '';
        if (mailBtn.dataset.entity) {
          try {
            const r = await fetch(API + '/entities/' + encodeURIComponent(mailBtn.dataset.entity));
            if (r.ok) to = (await r.json()).email || '';
          } catch (_) {}
        }
        const addr = await uiPrompt({
          title: t('invEmailTitle').replace('{n}', mailBtn.dataset.number),
          message: t('invEmailPrompt'), value: to, placeholder: 'billing@example.com', confirmLabel: t('invEmailSend'),
        });
        if (addr === null || addr === undefined) return;
        mailBtn.disabled = true;
        try {
          const res = await fetch(API + '/invoices/' + encodeURIComponent(mailBtn.dataset.id) + '/send', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ to: String(addr).trim() || null }),
          });
          const data = await readJsonSafe(res);
          if (!res.ok) { showAlert((data && typeof data.detail === 'string') ? data.detail : t('invEmailFailed'), true); return; }
          showAlert(t('invEmailSent').replace('{to}', data.to_address));
        } catch (_) { showAlert(t('invEmailFailed'), true); }
        finally { mailBtn.disabled = false; }
        return;
      }
      const tlBtn = e.target.closest('.inv-timeline');
      if (tlBtn) {
        try {
          const res = await fetch(API + '/invoices/' + encodeURIComponent(tlBtn.dataset.id) + '/timeline');
          const data = await res.json().catch(() => ([]));
          if (!res.ok) { showAlert('Cannot load timeline.', true); return; }
          const lines = (data || []).map(x => `${x.at} - ${x.event}${x.detail ? ': ' + x.detail : ''}`).join('\n');
          await uiConfirm({ title: t('invoiceTimelineTitle'), message: lines || t('noTimelineEvents'), confirmLabel: t('btnClose'), hideCancel: true });
        } catch (err) { showAlert('Connection error: ' + err.message, true); }
      }
      const editBtn = e.target.closest('.inv-edit');
      if (editBtn) {
        openInvoiceEditModal(editBtn.dataset.id);
      }
      const payBtn = e.target.closest('.inv-payment');
      if (payBtn && !payBtn.disabled) {
        const id = payBtn.dataset.id;
        const inv = _invoicesCache.find((x) => String(x.id) === String(id)) || {};
        const balance = (inv.balance_due != null) ? inv.balance_due : inv.amount;
        const raw = await uiPrompt({
          title: t('invAddPayment'),
          message: tf('invPaymentAmountPrompt', { balance: formatNum(balance || 0), currency: (inv.currency || '') }),
          type: 'number',
          value: String(balance || ''),
        });
        if (raw == null) return;
        const amount = parseInt(raw, 10);
        if (!amount || amount <= 0) { showAlert(t('invAmountInvalid'), true); return; }
        try {
          const res = await fetch(API + '/invoices/' + encodeURIComponent(id) + '/payments', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { showAlert(data.detail || t('invPaymentError'), true); return; }
          showAlert(t('invPaymentRecorded'));
          loadInvoices(id);
          loadLedger();
          loadOwnerDashboard();
        } catch (err) { showAlert('Connection error: ' + err.message, true); }
        return;
      }
      const cnBtn = e.target.closest('.inv-credit-note');
      if (cnBtn && !cnBtn.disabled) {
        const id = cnBtn.dataset.id;
        const inv = _invoicesCache.find((x) => String(x.id) === String(id)) || {};
        const raw = await uiPrompt({
          title: t('invCreditNote'),
          message: t('invCreditNoteAmountPrompt'),
          type: 'number',
          value: '',
        });
        if (raw == null) return;
        const amount = parseInt(raw, 10);
        if (!amount || amount <= 0) { showAlert(t('invAmountInvalid'), true); return; }
        const reason = await uiPrompt({ title: t('invCreditNote'), message: t('invCreditNoteReasonPrompt'), type: 'text', value: '' });
        if (reason == null) return;
        try {
          const res = await fetch(API + '/invoices/' + encodeURIComponent(id) + '/credit-notes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount, reason: reason || null }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { showAlert(data.detail || t('invCreditNoteError'), true); return; }
          showAlert(t('invCreditNoteRecorded'));
          loadInvoices(id);
          loadLedger();
          loadOwnerDashboard();
        } catch (err) { showAlert('Connection error: ' + err.message, true); }
        return;
      }
      const voidBtn = e.target.closest('.inv-void');
      if (voidBtn && !voidBtn.disabled) {
        const id = voidBtn.dataset.id;
        if (!(await uiConfirm({ message: tf('confirmVoidInvoice', { number: voidBtn.dataset.number || '' }), confirmLabel: t('invVoid'), danger: true }))) return;
        try {
          const res = await fetch(API + '/invoices/' + encodeURIComponent(id) + '/void', { method: 'POST' });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { showAlert(data.detail || t('invVoidError'), true); return; }
          showAlert(t('invVoidRecorded'));
          loadInvoices(id);
          loadLedger();
          loadOwnerDashboard();
        } catch (err) { showAlert('Connection error: ' + err.message, true); }
        return;
      }
      const delBtn = e.target.closest('.inv-del');
      if (delBtn) {
        if (!(await uiConfirm({ message: t('confirmDeleteInvoice'), confirmLabel: t('btnDelete'), danger: true }))) return;
        try {
          const res = await fetch(API + '/invoices/' + encodeURIComponent(delBtn.dataset.id), { method: 'DELETE' });
          if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            showAlert(data.detail || 'Error deleting invoice.', true);
            return;
          }
          showAlert('Invoice deleted.');
          loadInvoices();
          loadOwnerDashboard();
        } catch (err) { showAlert('Connection error: ' + err.message, true); }
      }
    });

    // Invoice edit: load the record into the modal form and PATCH on save.
    let _editingInvoiceId = null;
    function openInvoiceEditModal(id) {
      const inv = _invoicesCache.find((x) => String(x.id) === String(id));
      if (!inv) { showAlert('Error editing invoice.', true); return; }
      _editingInvoiceId = inv.id;
      document.getElementById('invoice-edit-number').value = inv.number || '';
      document.getElementById('invoice-edit-status').value = String(inv.status || 'issued').toLowerCase();
      document.getElementById('invoice-edit-amount').value = inv.amount != null ? String(inv.amount) : '0';
      const ccySel = document.getElementById('invoice-edit-currency');
      const ccy = String(inv.currency || 'IRR').toUpperCase();
      if ([...ccySel.options].some((o) => o.value === ccy)) ccySel.value = ccy;
      document.getElementById('invoice-edit-issue').value = inv.issue_date || '';
      document.getElementById('invoice-edit-due').value = inv.due_date || '';
      document.getElementById('invoice-edit-scheduled').value = inv.scheduled_payment_date || '';
      document.getElementById('invoice-edit-desc').value = inv.description || '';
      document.getElementById('invoice-edit-modal').style.display = 'flex';
    }

    async function saveInvoiceEdit() {
      if (!_editingInvoiceId) return;
      const number = document.getElementById('invoice-edit-number').value.trim();
      const issue_date = document.getElementById('invoice-edit-issue').value;
      const due_date = document.getElementById('invoice-edit-due').value;
      if (!number || !issue_date || !due_date) {
        showAlert('Invoice number, issue date, and due date are required.', true);
        return;
      }
      const payload = {
        number,
        status: document.getElementById('invoice-edit-status').value,
        amount: parseInt(document.getElementById('invoice-edit-amount').value, 10) || 0,
        currency: document.getElementById('invoice-edit-currency').value,
        issue_date,
        due_date,
        scheduled_payment_date: document.getElementById('invoice-edit-scheduled').value || null,
        description: document.getElementById('invoice-edit-desc').value.trim() || null,
      };
      const saveBtn = document.getElementById('invoice-edit-save');
      saveBtn.disabled = true;
      try {
        const res = await fetch(API + '/invoices/' + encodeURIComponent(_editingInvoiceId), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Error editing invoice.', true); return; }
        document.getElementById('invoice-edit-modal').style.display = 'none';
        showAlert('Invoice updated.');
        loadInvoices();
        loadOwnerDashboard();
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      } finally {
        saveBtn.disabled = false;
      }
    }
    document.getElementById('invoice-edit-save').addEventListener('click', saveInvoiceEdit);
    document.getElementById('invoice-edit-cancel').addEventListener('click', () => {
      document.getElementById('invoice-edit-modal').style.display = 'none';
    });
    document.getElementById('invoice-edit-close').addEventListener('click', () => {
      document.getElementById('invoice-edit-modal').style.display = 'none';
    });
    document.getElementById('invoice-edit-modal').addEventListener('click', (e) => {
      if (e.target.id === 'invoice-edit-modal') e.target.style.display = 'none';
    });

    recurringTbody.addEventListener('click', async (e) => {
      const delBtn = e.target.closest('.recurring-del');
      if (!delBtn) return;
      if (!(await uiConfirm({ message: t('confirmDeleteRecurring'), confirmLabel: t('btnDelete'), danger: true }))) return;
      try {
        const res = await fetch(API + '/recurring/' + encodeURIComponent(delBtn.dataset.id), { method: 'DELETE' });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          showAlert(data.detail || 'Error deleting recurring rule.', true);
          return;
        }
        showAlert('Recurring rule deleted.');
        loadRecurringRules();
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    });

    // Entity edit: load the record into the modal form (no chained prompts).
    // Billing/official-invoice identity fields editable alongside type/name/code.
    const _ENT_BILLING = ['legal_name','tax_id','economic_code','national_id','province','city',
                          'postal_code','email','phone','payment_terms','address',
                          'bank_name','account_holder','account_number','iban','sort_code'];
    let _editingEntityId = null;
    async function editEntity(btn) {
      _editingEntityId = btn.dataset.entityId;
      document.getElementById('entity-edit-type').value = (btn.dataset.entityType || 'client').trim();
      document.getElementById('entity-edit-name').value = (btn.dataset.entityName || '').trim();
      document.getElementById('entity-edit-code').value = (btn.dataset.entityCode || '').trim();
      _ENT_BILLING.forEach(f => { const el = document.getElementById('entity-edit-' + f); if (el) el.value = ''; });
      document.getElementById('entity-edit-modal').style.display = 'flex';
      document.getElementById('entity-edit-name').focus();
      // Fill billing fields from the full record (dataset only carries the basics).
      try {
        const res = await fetch(API + '/entities/' + encodeURIComponent(_editingEntityId));
        if (res.ok) {
          const d = await res.json();
          _ENT_BILLING.forEach(f => {
            const el = document.getElementById('entity-edit-' + f);
            if (el) el.value = d[f] || '';
          });
        }
      } catch (err) { /* billing prefill is best-effort */ }
    }

    async function saveEntityEdit() {
      if (!_editingEntityId) return;
      const name = document.getElementById('entity-edit-name').value.trim();
      const type = document.getElementById('entity-edit-type').value;
      const code = document.getElementById('entity-edit-code').value.trim();
      if (!name) { showAlert('Enter a name for the entity.', true); return; }
      const payload = { name, type, code };
      _ENT_BILLING.forEach(f => {
        const el = document.getElementById('entity-edit-' + f);
        if (el) payload[f] = el.value.trim();
      });
      const saveBtn = document.getElementById('entity-edit-save');
      saveBtn.disabled = true;
      try {
        const res = await fetch(API + '/entities/' + encodeURIComponent(_editingEntityId), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || 'Error updating entity.', true);
          return;
        }
        document.getElementById('entity-edit-modal').style.display = 'none';
        showAlert('Entity updated.');
        loadEntities(_editingEntityId);
        loadEntityOptions();
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      } finally {
        saveBtn.disabled = false;
      }
    }
    document.getElementById('entity-edit-save').addEventListener('click', saveEntityEdit);
    document.getElementById('entity-edit-cancel').addEventListener('click', () => {
      document.getElementById('entity-edit-modal').style.display = 'none';
    });
    document.getElementById('entity-edit-close').addEventListener('click', () => {
      document.getElementById('entity-edit-modal').style.display = 'none';
    });
    document.getElementById('entity-edit-modal').addEventListener('click', (e) => {
      if (e.target.id === 'entity-edit-modal') e.target.style.display = 'none';
    });

    async function deleteEntity(btn) {
      const id = btn.dataset.entityId;
      const name = btn.dataset.entityName || 'this entity';
      if (!(await uiConfirm({ message: tf('confirmDeleteEntity', { name }), confirmLabel: t('btnDelete'), danger: true }))) return;
      try {
        const res = await fetch(API + '/entities/' + encodeURIComponent(id), { method: 'DELETE' });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          showAlert(data.detail || 'Error deleting entity.', true);
          return;
        }
        showAlert('Entity deleted.');
        loadEntities();
        loadEntityOptions();
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      }
    }
    document.getElementById('reset-db-btn').addEventListener('click', async () => {
      if (!(await uiConfirm({ message: t('confirmResetDb'), confirmLabel: t('btnResetDb'), danger: true }))) return;
      try {
        const res = await fetch(API + '/admin/reset-db', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Reset failed.', true); return; }
        showAlert('Database reset. Chart of accounts re-seeded.');
        loadLedger();
        loadEntities();
        loadInvoices();
        loadRecurringRules();
        loadOwnerDashboard();
        loadBudgets();
        lastEntityMentions = null;
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      }
    });
    document.getElementById('entity-add').addEventListener('click', async () => {
      const type = document.getElementById('entity-type').value;
      const name = document.getElementById('entity-name').value.trim();
      const code = document.getElementById('entity-code').value.trim() || null;
      if (!name) { showAlert('Enter a name for the entity.', true); return; }
      const billing = {};
      ['legal_name','tax_id','economic_code','national_id','province','city','postal_code','email','phone','payment_terms','address','bank_name','account_holder','account_number','iban','sort_code'].forEach(f => {
        const el = document.getElementById('entity-' + f);
        if (el && el.value.trim()) billing[f] = el.value.trim();
      });
      try {
        const res = await fetch(API + '/entities', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ type, name, code, ...billing })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Error adding entity.', true); return; }
        showAlert('Entity added.');
        document.getElementById('entity-name').value = '';
        document.getElementById('entity-code').value = '';
        ['legal_name','tax_id','economic_code','national_id','province','city','postal_code','email','phone','payment_terms','address','bank_name','account_holder','account_number','iban','sort_code'].forEach(f => {
          const el = document.getElementById('entity-' + f); if (el) el.value = '';
        });
        loadEntities(data.id);
        loadEntityOptions();
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      }
    });
    // ═══════ Multi-line invoice builder ═══════
    let _invMode = 'itemized';
    const _invRateCache = {};          // "CODE|YYYY-MM-DD" -> rate
    const _invProductPrice = {};       // product_name(lower) -> last unit price

    function invCurrency() { return document.getElementById('inv-currency')?.value || preferredFormCurrency(); }
    function invFmt(n) { return (Number(n) || 0).toLocaleString() + ' ' + invCurrency(); }

    function invTaxCodeOptions(selected) {
      const codes = (typeof _taxRateCodes !== 'undefined' && _taxRateCodes) || [];
      return `<option value="">${t('taxCodeNone')}</option>` +
        codes.map(c => `<option value="${escapeHtml(c)}"${c === selected ? ' selected' : ''}>${escapeHtml(c)}</option>`).join('');
    }
    function invTreatmentOptions(sel) {
      const opts = [['standard', t('taxTreatStandard')], ['zero_rated', t('taxTreatZero')],
                    ['exempt', t('taxTreatExempt')], ['reverse_charge', t('taxTreatReverse')]];
      return opts.map(([v, l]) => `<option value="${v}"${v === sel ? ' selected' : ''}>${escapeHtml(l)}</option>`).join('');
    }

    function invAddLine(prefill) {
      const p = prefill || {};
      const body = document.getElementById('inv-items-body');
      if (!body) return;
      const tr = document.createElement('tr');
      tr.className = 'inv-line';
      tr.innerHTML =
        `<td><input type="text" class="il-desc" list="inv-products-datalist" value="${escapeHtml(p.description || '')}" placeholder="${escapeHtml(t('ibDescription'))}"></td>` +
        `<td><input type="text" class="il-sstid" maxlength="13" inputmode="numeric" dir="ltr" value="${escapeHtml(p.sstid || '')}" placeholder="${escapeHtml(t('ibGoodsIdPh'))}"></td>` +
        `<td><input type="number" class="il-qty" min="0" step="0.01" value="${p.quantity != null ? p.quantity : 1}" style="text-align:end;"></td>` +
        `<td><input type="number" class="il-price" min="0" step="1" value="${p.unit_price != null ? p.unit_price : 0}" style="text-align:end;"></td>` +
        `<td><select class="il-code">${invTaxCodeOptions(p.tax_code || '')}</select></td>` +
        `<td><input type="number" class="il-rate" min="0" max="100" step="0.5" value="${p.tax_rate != null ? p.tax_rate : 0}" style="text-align:end;"></td>` +
        `<td><select class="il-treat">${invTreatmentOptions(p.tax_treatment || 'standard')}</select></td>` +
        `<td class="il-total" style="text-align:end; white-space:nowrap;">—</td>` +
        `<td><button type="button" class="il-del" aria-label="${escapeHtml(t('ibRemoveLine'))}" style="border:none;background:transparent;cursor:pointer;color:var(--danger);font-size:1.1rem;">×</button></td>`;
      body.appendChild(tr);
      invRecompute();
    }

    // Re-localize the option labels + placeholder of already-rendered rows when
    // the UI language changes (the generic [data-i18n] loop can't reach them).
    function invRelocalizeRows() {
      document.querySelectorAll('#inv-items-body .inv-line').forEach(tr => {
        const desc = tr.querySelector('.il-desc');
        if (desc) desc.placeholder = t('ibDescription');
        const sid = tr.querySelector('.il-sstid');
        if (sid) sid.placeholder = t('ibGoodsIdPh');
        const code = tr.querySelector('.il-code');
        if (code) { const v = code.value; code.innerHTML = invTaxCodeOptions(v); code.value = v; }
        const treat = tr.querySelector('.il-treat');
        if (treat) { const v = treat.value; treat.innerHTML = invTreatmentOptions(v); treat.value = v; }
        const del = tr.querySelector('.il-del');
        if (del) del.setAttribute('aria-label', t('ibRemoveLine'));
      });
    }

    async function invEffectiveRate(code, on) {
      if (!code || !on) return 0;
      const key = code + '|' + on;
      if (_invRateCache[key] != null) return _invRateCache[key];
      try {
        const res = await fetch(API + '/reports/tax-rates/effective?code=' + encodeURIComponent(code) + '&on=' + encodeURIComponent(on));
        if (res.ok) { const d = await res.json(); _invRateCache[key] = (d.rate != null ? Number(d.rate) : 0); return _invRateCache[key]; }
      } catch (_) {}
      return 0;
    }

    function invRecompute() {
      let sub = 0, tax = 0;
      document.querySelectorAll('#inv-items-body .inv-line').forEach(tr => {
        const qty = parseFloat(tr.querySelector('.il-qty').value) || 0;
        const price = parseFloat(tr.querySelector('.il-price').value) || 0;
        const rate = parseFloat(tr.querySelector('.il-rate').value) || 0;
        const treat = tr.querySelector('.il-treat').value;
        const lt = Math.max(0, Math.round(qty * price));
        tr.querySelector('.il-total').textContent = invFmt(lt);
        sub += lt;
        if (treat === 'standard') tax += Math.round(lt * rate / 100);
      });
      const g = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = invFmt(v); };
      g('inv-sub', sub); g('inv-tax-total', tax); g('inv-grand', sub + tax);
      return { sub, tax, total: sub + tax };
    }

    function invCollectItems() {
      const items = [];
      document.querySelectorAll('#inv-items-body .inv-line').forEach(tr => {
        const desc = tr.querySelector('.il-desc').value.trim();
        const qty = parseFloat(tr.querySelector('.il-qty').value) || 0;
        const price = parseInt(tr.querySelector('.il-price').value, 10) || 0;
        if (!desc && qty <= 0) return;
        const rate = parseFloat(tr.querySelector('.il-rate').value) || 0;
        items.push({
          product_name: desc || 'Item',
          quantity: qty > 0 ? qty : 1,
          unit_price: price,
          line_total: Math.max(0, Math.round((qty > 0 ? qty : 1) * price)),
          tax_rate: rate, taxable: true,
          tax_code: tr.querySelector('.il-code').value || null,
          tax_treatment: tr.querySelector('.il-treat').value || 'standard',
          sstid: (tr.querySelector('.il-sstid').value || '').trim() || null,
        });
      });
      return items;
    }

    // Build the POST body for both create and preview. Returns null on a
    // validation error (and shows the alert).
    function invBuildBody(opts) {
      const allowBlankNumber = !!(opts && opts.allowBlankNumber);
      const number = document.getElementById('inv-number').value.trim();
      const kind = document.getElementById('inv-kind').value;
      const currency = invCurrency();
      const issue_date = document.getElementById('inv-issue').value;
      const due_date = document.getElementById('inv-due').value;
      const entity_id = document.getElementById('inv-entity').value || null;
      const description = document.getElementById('inv-desc').value.trim() || null;
      if ((!number && !allowBlankNumber) || !issue_date || !due_date) {
        showAlert(t(allowBlankNumber ? 'qtHeaderRequired' : 'invHeaderRequired'), true); return null;
      }
      const body = { number, kind, amount: 0, currency, issue_date, due_date, entity_id, description, status: 'issued' };
      if (_invMode === 'itemized') {
        const items = invCollectItems();
        if (!items.length) { showAlert(t('invNeedLine'), true); return null; }
        body.items = items;
      } else {
        const amount = parseInt(document.getElementById('inv-amount').value, 10) || 0;
        const taxRate = parseFloat(document.getElementById('inv-tax-rate').value) || 0;
        const taxCode = document.getElementById('inv-tax-code').value || null;
        const taxTreatment = document.getElementById('inv-tax-treatment').value || 'standard';
        body.amount = amount;
        if (amount > 0 && (taxRate > 0 || taxCode || taxTreatment !== 'standard')) {
          body.items = [{ product_name: description || number, quantity: 1, unit_price: amount,
            line_total: amount, tax_rate: taxRate, taxable: true, tax_code: taxCode, tax_treatment: taxTreatment }];
        }
      }
      return body;
    }

    function invSetMode(mode) {
      _invMode = mode;
      const itemized = mode === 'itemized';
      document.getElementById('inv-items-section').style.display = itemized ? '' : 'none';
      document.getElementById('inv-simple-fields').style.display = itemized ? 'none' : 'flex';
      document.getElementById('inv-mode-itemized').classList.toggle('active', itemized);
      document.getElementById('inv-mode-simple').classList.toggle('active', !itemized);
      if (itemized && !document.querySelector('#inv-items-body .inv-line')) invAddLine();
    }

    document.getElementById('inv-mode-itemized').addEventListener('click', () => invSetMode('itemized'));
    document.getElementById('inv-mode-simple').addEventListener('click', () => invSetMode('simple'));
    document.getElementById('inv-add-line').addEventListener('click', () => invAddLine());
    document.getElementById('inv-currency').addEventListener('change', invRecompute);

    // Row interactions (delegated).
    document.getElementById('inv-items-body').addEventListener('input', (e) => {
      if (e.target.classList.contains('il-desc')) {
        const price = _invProductPrice[e.target.value.trim().toLowerCase()];
        if (price != null) { const row = e.target.closest('tr'); const pe = row.querySelector('.il-price'); if (!parseFloat(pe.value)) pe.value = price; }
      }
      invRecompute();
    });
    document.getElementById('inv-items-body').addEventListener('change', async (e) => {
      if (e.target.classList.contains('il-code')) {
        const row = e.target.closest('tr');
        const rate = await invEffectiveRate(e.target.value, document.getElementById('inv-issue').value);
        if (rate) row.querySelector('.il-rate').value = rate;
        invRecompute();
      }
    });
    document.getElementById('inv-items-body').addEventListener('click', (e) => {
      if (e.target.classList.contains('il-del')) {
        e.target.closest('tr').remove();
        if (!document.querySelector('#inv-items-body .inv-line')) invAddLine();
        invRecompute();
      }
    });

    // Client autofill: currency + due date from the entity's saved terms.
    document.getElementById('inv-entity').addEventListener('change', async () => {
      const id = document.getElementById('inv-entity').value;
      if (!id) return;
      try {
        const res = await fetch(API + '/entities/' + id);
        if (!res.ok) return;
        const ent = await res.json();
        if (ent.currency) { const cs = document.getElementById('inv-currency'); if (cs) { cs.value = ent.currency; invRecompute(); } }
        const m = (ent.payment_terms || '').match(/(\d+)/);
        if (m) {
          const issue = document.getElementById('inv-issue').value;
          if (issue) { const d = new Date(issue + 'T00:00:00'); d.setDate(d.getDate() + parseInt(m[1], 10));
            document.getElementById('inv-due').value = d.toISOString().slice(0, 10); }
        }
      } catch (_) {}
    });

    async function invLoadProductDatalist() {
      try {
        const res = await fetch(API + '/products/catalog');
        if (!res.ok) return;
        const data = await res.json();
        const dl = document.getElementById('inv-products-datalist');
        if (dl) dl.innerHTML = (data.items || []).map(it => `<option value="${escapeHtml(it.product_name)}"></option>`).join('');
        (data.items || []).forEach(it => {
          const last = (it.last_unit_price != null) ? it.last_unit_price : null;
          if (last != null) _invProductPrice[it.product_name.toLowerCase()] = last;
        });
      } catch (_) {}
    }

    function invResetBuilder() {
      const body = document.getElementById('inv-items-body');
      if (body) body.innerHTML = '';
      const dl = document.getElementById('inv-download');
      if (dl) dl.style.display = 'none';
      if (_invMode === 'itemized') invAddLine();
      invRecompute();
    }

    let _invBuilderInit = false;
    function invInitBuilder() {
      if (_invBuilderInit) return;
      _invBuilderInit = true;
      // Default the invoice currency to the company/preferred currency BEFORE
      // computing totals, so the live Subtotal/Tax/Total show the right code
      // (not the dropdown's first option, IRR).
      applyDefaultFormCurrency();
      invSetMode('itemized');     // seeds the first empty line
      invRecompute();             // refresh totals with the initialized currency
      invLoadProductDatalist();
    }

    document.getElementById('inv-preview').addEventListener('click', async () => {
      const body = invBuildBody();
      if (!body) return;
      const btn = document.getElementById('inv-preview');
      btn.disabled = true;
      try {
        const res = await fetch(API + '/invoices/preview-pdf', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        if (!res.ok) { const e = await res.json().catch(() => ({})); showAlert(e.detail || t('invPreviewFailed'), true); return; }
        const blob = await res.blob();
        window.open(URL.createObjectURL(blob), '_blank', 'noopener');
      } catch (_) { showAlert(t('invPreviewFailed'), true); }
      finally { btn.disabled = false; }
    });

    document.getElementById('inv-add').addEventListener('click', async () => {
      const body = invBuildBody();
      if (!body) return;
      try {
        const res = await fetch(API + '/invoices', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Error creating invoice.', true); return; }
        showAlert('Invoice created.');
        // Offer the branded PDF for the just-created invoice.
        const dl = document.getElementById('inv-download');
        if (dl && data.id) { dl.href = API + '/invoices/' + data.id + '/pdf'; dl.style.display = ''; }
        // Reset for the next invoice.
        document.getElementById('inv-number').value = '';
        document.getElementById('inv-amount').value = '0';
        document.getElementById('inv-tax-rate').value = '0';
        document.getElementById('inv-desc').value = '';
        document.getElementById('inv-entity').value = '';
        document.getElementById('inv-bank-code').value = '';
        invResetBuilder();
        setInvoiceDateDefaults();
        applyDefaultFormCurrency();
        loadInvoices(data.id);
        loadOwnerDashboard();
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    });

    // ═══════ سامانه مودیان export ═══════
    let _moRows = [];
    async function loadMoadian() {
      const body = document.getElementById('mo-tbody');
      const state = document.getElementById('mo-state').value || 'pending';
      try {
        const res = await fetch(API + '/moadian/invoices?state=' + encodeURIComponent(state));
        const data = await readJsonSafe(res);
        if (!res.ok) { body.innerHTML = `<tr><td colspan="9" class="empty-state">${escapeHtml(qtError(data, 'moLoadFailed'))}</td></tr>`; return; }
        const st = data.settings || {};
        document.getElementById('mo-memory').value = st.memory_id || '';
        document.getElementById('mo-sstid').value = st.default_sstid || '';
        document.getElementById('mo-mu').value = st.default_mu || '';
        document.getElementById('mo-deadline').value = st.deadline_days || 12;
        _moRows = data.invoices || [];
        document.getElementById('mo-summary').textContent = data.overdue
          ? t('moOverdueCount').replace('{n}', data.overdue) : '';
        document.getElementById('mo-all').checked = false;
        if (!_moRows.length) { body.innerHTML = `<tr><td colspan="9" class="empty-state">${escapeHtml(t('moNone'))}</td></tr>`; return; }
        body.innerHTML = '';
        _moRows.forEach(r => {
          const late = r.moadian_status === null && r.days_left < 0;
          const deadline = r.moadian_status ? escapeHtml(r.deadline)
            : `<span style="color:${late ? 'var(--danger, #b91c1c)' : 'inherit'};">${escapeHtml(r.deadline)} · ${escapeHtml(t(late ? 'moDaysLate' : 'moDaysLeft').replace('{n}', Math.abs(r.days_left)))}</span>`;
          const status = t('moState_' + (r.moadian_status || 'pending'))
            + (r.reference ? `<div style="font-size:0.72rem;color:var(--text-muted);" dir="ltr">${escapeHtml(r.reference)}</div>` : '')
            + (r.error ? `<div style="font-size:0.72rem;color:var(--danger, #b91c1c);">${escapeHtml(r.error)}</div>` : '')
            + (r.taxid ? `<div style="font-size:0.7rem;color:var(--text-muted);font-family:monospace;" dir="ltr">${escapeHtml(r.taxid)}</div>` : '');
          const ready = r.ready
            ? `<span style="color:var(--success, #15803d);">✓</span>${(r.warnings || []).map(w => `<div style="font-size:0.72rem;color:var(--text-muted);">${escapeHtml(w)}</div>`).join('')}`
            : (r.problems || []).map(p => `<div style="font-size:0.74rem;color:var(--danger, #b91c1c);">${escapeHtml(p)}</div>`).join('');
          const canSelect = r.ready && r.moadian_status !== 'confirmed';
          const actions = [
            `<button type="button" class="btn btn-secondary btn-sm mo-preview" data-id="${r.id}">${escapeHtml(t('moPreview'))}</button>`,
            r.taxid && r.moadian_status !== 'confirmed' ? `<button type="button" class="btn btn-secondary btn-sm mo-result" data-id="${r.id}" data-status="confirmed" style="margin-inline-start:0.3rem;">${escapeHtml(t('moMarkConfirmed'))}</button>` : '',
            r.taxid && r.moadian_status !== 'confirmed' ? `<button type="button" class="btn btn-secondary btn-sm mo-result" data-id="${r.id}" data-status="rejected" style="margin-inline-start:0.3rem;">${escapeHtml(t('moMarkRejected'))}</button>` : '',
          ].join('');
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td><input type="checkbox" class="mo-pick" data-id="${r.id}" ${canSelect ? '' : 'disabled'}></td>
            <td>${escapeHtml(r.number)}</td><td>${escapeHtml(r.issue_date)}</td><td>${escapeHtml(r.customer || '—')}</td>
            <td>${formatMoney(r.amount, r.currency)}</td><td>${deadline}</td><td>${status}</td><td>${ready}</td><td>${actions}</td>`;
          body.appendChild(tr);
        });
      } catch (_) {
        body.innerHTML = `<tr><td colspan="9" class="empty-state">${escapeHtml(t('moLoadFailed'))}</td></tr>`;
      }
    }
    document.getElementById('moadian-panel').addEventListener('toggle', (e) => { if (e.target.open) loadMoadian(); });
    document.getElementById('mo-state').addEventListener('change', loadMoadian);
    document.getElementById('mo-all').addEventListener('change', (e) => {
      document.querySelectorAll('#mo-tbody .mo-pick:not(:disabled)').forEach(cb => { cb.checked = e.target.checked; });
    });
    document.getElementById('mo-save').addEventListener('click', async () => {
      try {
        const res = await fetch(API + '/moadian/settings', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            memory_id: document.getElementById('mo-memory').value.trim(),
            default_sstid: document.getElementById('mo-sstid').value.trim(),
            default_mu: document.getElementById('mo-mu').value.trim(),
            deadline_days: parseInt(document.getElementById('mo-deadline').value, 10) || 12,
          }),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert(qtError(data, 'moSaveFailed'), true); return; }
        showAlert(t('moSaved'));
        loadMoadian();
      } catch (_) { showAlert(t('moSaveFailed'), true); }
    });
    document.getElementById('mo-export').addEventListener('click', async () => {
      const ids = [...document.querySelectorAll('#mo-tbody .mo-pick:checked')].map(cb => cb.dataset.id);
      if (!ids.length) { showAlert(t('moPickSome'), true); return; }
      try {
        const res = await fetch(API + '/moadian/export', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ invoice_ids: ids }),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert(qtError(data, 'moExportFailed'), true); return; }
        if (data.count) {
          const file = { generated_at: data.generated_at, invoices: data.invoices, packets: data.packets };
          const url = URL.createObjectURL(new Blob([JSON.stringify(file, null, 2)], { type: 'application/json' }));
          const a = document.createElement('a');
          a.href = url; a.download = data.file_name; document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 2000);
        }
        let msg = t('moExported').replace('{n}', data.count);
        if (data.skipped && data.skipped.length) {
          msg += ' ' + t('moSkipped').replace('{n}', data.skipped.length) + ' '
            + data.skipped.map(x => `${x.number || x.id}: ${(x.problems || []).join('; ')}`).join(' | ');
        }
        showAlert(msg, !!(data.skipped && data.skipped.length));
        loadMoadian();
      } catch (_) { showAlert(t('moExportFailed'), true); }
    });
    document.getElementById('mo-tbody').addEventListener('click', async (e) => {
      const pv = e.target.closest('.mo-preview');
      const rs = e.target.closest('.mo-result');
      if (pv) {
        try {
          const res = await fetch(API + '/moadian/invoices/' + encodeURIComponent(pv.dataset.id) + '/preview');
          const data = await readJsonSafe(res);
          if (!res.ok) { showAlert(qtError(data, 'moLoadFailed'), true); return; }
          const head = data.provisional ? t('moProvisional') + '\n\n' : '';
          await uiConfirm({ title: t('moPreview'), message: head + JSON.stringify(data.packet, null, 2), confirmLabel: t('btnClose'), hideCancel: true });
        } catch (_) { showAlert(t('moLoadFailed'), true); }
        return;
      }
      if (rs) {
        const status = rs.dataset.status;
        const val = await uiPrompt({
          title: t(status === 'confirmed' ? 'moMarkConfirmed' : 'moMarkRejected'),
          message: t(status === 'confirmed' ? 'moRefPrompt' : 'moErrorPrompt'), value: '',
        });
        if (val === null || val === undefined) return;
        const body = status === 'confirmed' ? { status, reference: String(val).trim() } : { status, error: String(val).trim() };
        try {
          const res = await fetch(API + '/moadian/invoices/' + encodeURIComponent(rs.dataset.id), {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
          });
          const data = await readJsonSafe(res);
          if (!res.ok) { showAlert(qtError(data, 'moSaveFailed'), true); return; }
          loadMoadian();
        } catch (_) { showAlert(t('moSaveFailed'), true); }
      }
    });

    // ═══════ Recurring invoices ═══════
    document.getElementById('ri-create').addEventListener('click', async () => {
      if (document.getElementById('inv-kind').value !== 'sales') { showAlert(t('qtSalesOnly'), true); return; }
      const body = invBuildBody({ allowBlankNumber: true });
      if (!body) return;
      if (!body.entity_id) { showAlert(t('riNeedCustomer'), true); return; }
      const terms = Math.max(0, Math.round((new Date(body.due_date + 'T00:00:00') - new Date(body.issue_date + 'T00:00:00')) / 86400000));
      const payload = {
        entity_id: body.entity_id, currency: body.currency, description: body.description,
        amount: body.amount || 0, items: body.items || [], start_date: body.issue_date, terms_days: terms,
        frequency: document.getElementById('ri-frequency').value,
        calendar: document.getElementById('ri-calendar').value || null,
        end_date: document.getElementById('ri-end').value || null,
        auto_send: document.getElementById('ri-auto-send').checked, issue_status: 'issued',
      };
      try {
        const res = await fetch(API + '/recurring-invoices', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert(qtError(data, 'riSaveFailed'), true); return; }
        const n = (data.generated && data.generated.issued) || 0;
        showAlert(n ? t('riCreatedNow').replace('{n}', n) : t('riCreatedLater').replace('{d}', data.next_run_date));
        if (data.last_error) showAlert(data.last_error, true);
        loadRecurringInvoices(data.id);
        if (n) { loadInvoices(); if (typeof loadOwnerDashboard === 'function') loadOwnerDashboard(); }
      } catch (_) { showAlert(t('riSaveFailed'), true); }
    });

    async function loadRecurringInvoices(highlightId) {
      const body = document.getElementById('ri-tbody');
      if (!body) return;
      try {
        const res = await fetch(API + '/recurring-invoices');
        if (!res.ok) { body.innerHTML = `<tr><td colspan="7" class="empty-state">${escapeHtml(t('riLoadFailed'))}</td></tr>`; return; }
        const list = await res.json();
        if (!list.length) { body.innerHTML = `<tr><td colspan="7" class="empty-state">${escapeHtml(t('riNone'))}</td></tr>`; return; }
        body.innerHTML = '';
        list.forEach(r => {
          const ccy = (r.currency || 'IRR').toUpperCase();
          const toggle = r.status === 'ended' ? '' : `<button type="button" class="btn btn-secondary btn-sm ri-toggle" data-id="${r.id}" data-status="${r.status === 'active' ? 'paused' : 'active'}">${escapeHtml(t(r.status === 'active' ? 'riPause' : 'riResume'))}</button>`;
          const err = r.last_error ? `<div style="font-size:0.72rem;color:var(--danger, #b91c1c);">${escapeHtml(r.last_error)}</div>` : '';
          const mail = r.auto_send ? `<div style="font-size:0.72rem;color:var(--text-muted);">${escapeHtml(t('riEmailed'))}${r.entity_email ? ' → ' + escapeHtml(r.entity_email) : ''}</div>` : '';
          const tr = document.createElement('tr');
          tr.dataset.riId = r.id;
          tr.innerHTML = `
            <td>${escapeHtml(r.entity_name || '—')}${mail}</td>
            <td>${escapeHtml(t('riFreq_' + r.frequency))}${r.calendar === 'jalali' ? ' · ' + escapeHtml(t('riCalendarJalali')) : ''}</td>
            <td>${r.status === 'ended' ? '—' : escapeHtml(r.next_run_date)}</td>
            <td>${formatMoney(r.total, ccy)} <span class="ccy-badge ccy-${escapeHtml(ccy)}">${escapeHtml(ccy)}</span></td>
            <td>${escapeHtml(String(r.occurrences))}${r.last_invoice_number ? ' · ' + escapeHtml(r.last_invoice_number) : ''}</td>
            <td>${escapeHtml(t('riStatus_' + r.status))}${err}</td>
            <td>${toggle}<button type="button" class="btn btn-danger btn-sm ri-del" data-id="${r.id}" data-name="${escapeHtml(r.name)}" style="margin-inline-start:0.3rem;">${escapeHtml(t('btnDelete') || 'Delete')}</button></td>`;
          body.appendChild(tr);
        });
        if (highlightId) flashRow(body.querySelector('tr[data-ri-id="' + CSS.escape(String(highlightId)) + '"]'));
      } catch (_) {
        body.innerHTML = `<tr><td colspan="7" class="empty-state">${escapeHtml(t('riLoadFailed'))}</td></tr>`;
      }
    }

    document.getElementById('ri-tbody').addEventListener('click', async (e) => {
      const tog = e.target.closest('.ri-toggle');
      const del = e.target.closest('.ri-del');
      if (tog) {
        try {
          const res = await fetch(API + '/recurring-invoices/' + encodeURIComponent(tog.dataset.id), {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: tog.dataset.status }),
          });
          const data = await readJsonSafe(res);
          if (!res.ok) { showAlert(qtError(data, 'riSaveFailed'), true); return; }
          loadRecurringInvoices(tog.dataset.id);
        } catch (_) { showAlert(t('riSaveFailed'), true); }
        return;
      }
      if (del) {
        const ok = await uiConfirm({ title: t('btnDelete') || 'Delete', message: t('riDeleteConfirm').replace('{n}', del.dataset.name) });
        if (!ok) return;
        try {
          const res = await fetch(API + '/recurring-invoices/' + encodeURIComponent(del.dataset.id), { method: 'DELETE' });
          if (!res.ok) { const data = await readJsonSafe(res); showAlert(qtError(data, 'riSaveFailed'), true); return; }
          loadRecurringInvoices();
        } catch (_) { showAlert(t('riSaveFailed'), true); }
      }
    });

    // ═══════ Automatic reminders (owner switches them on) ═══════
    async function loadReminderSettings() {
      try {
        const res = await fetch(API + '/invoices/reminder-settings');
        if (!res.ok) return;
        const s = await res.json();
        document.getElementById('rem-enabled').checked = !!s.reminders_enabled;
        document.getElementById('rem-days').value = (s.reminder_days || []).join(', ');
        document.getElementById('rem-instructions').value = s.payment_instructions || '';
        document.getElementById('rem-link').value = s.payment_link || '';
        const st = document.getElementById('rem-mail-status');
        st.textContent = t(s.mail_configured ? 'remMailOn' : 'remMailOff');
        st.style.color = s.mail_configured ? 'var(--text-muted)' : 'var(--danger, #b91c1c)';
      } catch (_) {}
    }
    document.getElementById('inv-rem-panel').addEventListener('toggle', (e) => { if (e.target.open) loadReminderSettings(); });
    document.getElementById('rem-save').addEventListener('click', async () => {
      const days = document.getElementById('rem-days').value.split(/[,،;\s]+/).map(x => x.trim()).filter(Boolean)
        .map(x => parseInt(x.replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d)), 10));
      if (!days.length || days.some(Number.isNaN)) { showAlert(t('remDaysInvalid'), true); return; }
      const enabled = document.getElementById('rem-enabled').checked;
      if (enabled) {
        const ok = await uiConfirm({ title: t('remTitle'), message: t('remEnableConfirm') });
        if (!ok) return;
      }
      try {
        const res = await fetch(API + '/invoices/reminder-settings', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            reminders_enabled: enabled, reminder_days: days,
            payment_instructions: document.getElementById('rem-instructions').value,
            payment_link: document.getElementById('rem-link').value.trim(),
          }),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert((data && typeof data.detail === 'string') ? data.detail : t('remSaveFailed'), true); return; }
        showAlert(t('remSaved'));
        loadReminderSettings();
      } catch (_) { showAlert(t('remSaveFailed'), true); }
    });

    // ═══════ Quotes (پیش‌فاکتور) ═══════
    // Same form as the invoice; the due date becomes "valid until".
    document.getElementById('inv-save-quote').addEventListener('click', async () => {
      if (document.getElementById('inv-kind').value !== 'sales') { showAlert(t('qtSalesOnly'), true); return; }
      const body = invBuildBody({ allowBlankNumber: true });
      if (!body) return;
      const payload = {
        number: body.number || null, issue_date: body.issue_date, valid_until: body.due_date,
        amount: body.amount || 0, currency: body.currency, description: body.description,
        entity_id: body.entity_id, items: body.items || [], status: 'draft',
      };
      try {
        const res = await fetch(API + '/quotes', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const data = await readJsonSafe(res);
        if (!res.ok) { showAlert(qtError(data, 'qtSaveFailed'), true); return; }
        showAlert(t('qtSaved').replace('{n}', data.number));
        document.getElementById('inv-number').value = '';
        document.getElementById('inv-desc').value = '';
        invResetBuilder();
        loadQuotes(data.id);
      } catch (err) { showAlert(t('qtSaveFailed'), true); }
    });

    function qtError(data, fallbackKey) {
      if (data && typeof data.detail === 'string') return data.detail;
      if (data && Array.isArray(data.detail) && data.detail[0] && data.detail[0].msg) return data.detail[0].msg;
      return t(fallbackKey);
    }

    async function loadQuotes(highlightId) {
      const body = document.getElementById('quotes-tbody');
      if (!body) return;
      const filter = (document.getElementById('qt-filter') || {}).value || '';
      try {
        const res = await fetch(API + '/quotes' + (filter ? ('?status=' + encodeURIComponent(filter)) : ''));
        if (!res.ok) { body.innerHTML = `<tr><td colspan="6" class="empty-state">${escapeHtml(t('qtLoadFailed'))}</td></tr>`; return; }
        const list = await res.json();
        if (!list.length) { body.innerHTML = `<tr><td colspan="6" class="empty-state">${escapeHtml(t('qtNone'))}</td></tr>`; return; }
        body.innerHTML = '';
        list.forEach(q => {
          const ccy = (q.currency || 'IRR').toUpperCase();
          const st = q.effective_status || q.status;
          const open = q.status === 'draft' || q.status === 'sent';
          const canConvert = q.status !== 'converted' && q.status !== 'declined';
          const btn = (cls, key, extra) => `<button type="button" class="btn btn-secondary btn-sm ${cls}" data-id="${q.id}" data-number="${escapeHtml(q.number)}" style="margin-inline-start:0.3rem;" ${extra || ''}>${escapeHtml(t(key))}</button>`;
          const actions = [
            `<a class="btn btn-secondary btn-sm" href="${API}/quotes/${encodeURIComponent(q.id)}/pdf" target="_blank" rel="noopener" style="text-decoration:none;">PDF</a>`,
            q.status === 'draft' ? btn('qt-set', 'qtMarkSent', 'data-status="sent"') : '',
            open ? btn('qt-set', 'qtMarkAccepted', 'data-status="accepted"') : '',
            open ? btn('qt-set', 'qtMarkDeclined', 'data-status="declined"') : '',
            (q.status === 'declined' || q.status === 'accepted') ? btn('qt-set', 'qtReopen', 'data-status="draft"') : '',
            canConvert ? `<button type="button" class="btn btn-primary btn-sm qt-convert" data-id="${q.id}" data-number="${escapeHtml(q.number)}" style="margin-inline-start:0.3rem;">${escapeHtml(t('qtConvert'))}</button>` : '',
            q.status !== 'converted' ? `<button type="button" class="btn btn-danger btn-sm qt-del" data-id="${q.id}" data-number="${escapeHtml(q.number)}" style="margin-inline-start:0.3rem;">${escapeHtml(t('btnDelete') || 'Delete')}</button>` : '',
          ].join('');
          const invoiced = q.converted_invoice_number
            ? `<div style="font-size:0.72rem;color:var(--text-muted);">${escapeHtml(t('qtInvoicedAs'))} ${escapeHtml(q.converted_invoice_number)}</div>` : '';
          const tr = document.createElement('tr');
          tr.dataset.quoteId = q.id;
          tr.innerHTML = `
            <td>${escapeHtml(q.number)}</td>
            <td>${escapeHtml(q.entity_name || '—')}</td>
            <td><span class="qt-status qt-status-${escapeHtml(st)}">${escapeHtml(t('qtStatus_' + st))}</span>${invoiced}</td>
            <td>${formatMoney(q.amount, ccy)} <span class="ccy-badge ccy-${escapeHtml(ccy)}">${escapeHtml(ccy)}</span></td>
            <td>${escapeHtml(q.valid_until)}</td>
            <td>${actions}</td>`;
          body.appendChild(tr);
        });
        if (highlightId) flashRow(body.querySelector('tr[data-quote-id="' + CSS.escape(String(highlightId)) + '"]'));
      } catch (err) {
        body.innerHTML = `<tr><td colspan="6" class="empty-state">${escapeHtml(t('qtLoadFailed'))}</td></tr>`;
      }
    }

    document.getElementById('qt-filter').addEventListener('change', () => loadQuotes());

    document.getElementById('quotes-tbody').addEventListener('click', async (e) => {
      const set = e.target.closest('.qt-set');
      const conv = e.target.closest('.qt-convert');
      const del = e.target.closest('.qt-del');
      if (set) {
        try {
          const res = await fetch(API + '/quotes/' + encodeURIComponent(set.dataset.id), {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: set.dataset.status }),
          });
          const data = await readJsonSafe(res);
          if (!res.ok) { showAlert(qtError(data, 'qtSaveFailed'), true); return; }
          loadQuotes(set.dataset.id);
        } catch (_) { showAlert(t('qtSaveFailed'), true); }
        return;
      }
      if (conv) {
        let suggested = '';
        try {
          const r = await fetch(API + '/quotes/next-number');
          if (r.ok) suggested = (await r.json()).invoice_number || '';
        } catch (_) {}
        const number = await uiPrompt({
          title: t('qtConvertTitle').replace('{n}', conv.dataset.number),
          message: t('qtConvertPrompt'), value: suggested, confirmLabel: t('qtConvert'),
        });
        if (number === null || number === undefined) return;
        try {
          const res = await fetch(API + '/quotes/' + encodeURIComponent(conv.dataset.id) + '/convert', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ number: String(number).trim() || null, status: 'issued' }),
          });
          const data = await readJsonSafe(res);
          if (!res.ok) { showAlert(qtError(data, 'qtConvertFailed'), true); return; }
          showAlert(t('qtConverted').replace('{n}', data.invoice.number));
          loadQuotes(conv.dataset.id);
          loadInvoices(data.invoice.id);
          if (typeof loadOwnerDashboard === 'function') loadOwnerDashboard();
        } catch (_) { showAlert(t('qtConvertFailed'), true); }
        return;
      }
      if (del) {
        const ok = await uiConfirm({ title: t('btnDelete') || 'Delete', message: t('qtDeleteConfirm').replace('{n}', del.dataset.number) });
        if (!ok) return;
        try {
          const res = await fetch(API + '/quotes/' + encodeURIComponent(del.dataset.id), { method: 'DELETE' });
          if (!res.ok) { const data = await readJsonSafe(res); showAlert(qtError(data, 'qtSaveFailed'), true); return; }
          loadQuotes();
        } catch (_) { showAlert(t('qtSaveFailed'), true); }
      }
    });

    async function runInvoiceOCRImport(createDirect) {
      const fileInput = document.getElementById('inv-ocr-file');
      const statusEl = document.getElementById('inv-ocr-status');
      const btnScan = document.getElementById('inv-ocr-scan');
      const btnCreate = document.getElementById('inv-ocr-create');
      const file = (fileInput && fileInput.files && fileInput.files[0]) ? fileInput.files[0] : null;
      if (!file) { showAlert('Choose an invoice image/PDF first.', true); return; }
      const fd = new FormData();
      fd.append('file', file);
      fd.append('kind', document.getElementById('inv-kind').value || 'sales');
      fd.append('create', createDirect ? 'true' : 'false');
      const entityId = document.getElementById('inv-entity').value || '';
      if (entityId) fd.append('entity_id', entityId);
      const desc = (document.getElementById('inv-desc').value || '').trim();
      if (desc) fd.append('description', desc);
      if (statusEl) statusEl.textContent = 'Scanning invoice...';
      btnScan.disabled = true;
      btnCreate.disabled = true;
      try {
        const res = await fetch(API + '/invoices/ocr-import', { method: 'POST', body: fd });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || 'OCR import failed.', true);
          if (statusEl) statusEl.textContent = '';
          return;
        }
        const s = data.suggested || {};
        // A scanned invoice fills the single-amount fields — switch to that mode
        // so the imported amount is visible/editable.
        if (typeof invSetMode === 'function') invSetMode('simple');
        if (s.number) document.getElementById('inv-number').value = s.number;
        if (s.kind) document.getElementById('inv-kind').value = s.kind;
        if (s.amount != null) document.getElementById('inv-amount').value = String(s.amount);
        if (s.issue_date) document.getElementById('inv-issue').value = s.issue_date;
        if (s.due_date) {
          document.getElementById('inv-due').value = s.due_date;
          // Scanned due date counts as user-provided — don't recompute net-30.
          _invDueManuallySet = true;
        }
        if (s.description) document.getElementById('inv-desc').value = s.description;
        if (s.entity_id && document.getElementById('inv-entity')) {
          document.getElementById('inv-entity').value = s.entity_id;
        }
        const conf = (data.confidence != null) ? `confidence ${Math.round((Number(data.confidence) || 0) * 100)}%` : 'confidence N/A';
        const vendor = data.vendor_name ? `vendor: ${data.vendor_name}` : 'vendor: unknown';
        if (statusEl) statusEl.textContent = `${vendor}, ${conf}`;
        if (data.created_invoice && createDirect) {
          showAlert('Invoice scanned and created.');
          loadInvoices();
          loadOwnerDashboard();
        } else {
          showAlert('Invoice scanned and form filled.');
        }
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
        if (statusEl) statusEl.textContent = '';
      } finally {
        btnScan.disabled = false;
        btnCreate.disabled = false;
      }
    }

    document.getElementById('inv-ocr-scan').addEventListener('click', () => runInvoiceOCRImport(false));
    document.getElementById('inv-ocr-create').addEventListener('click', () => runInvoiceOCRImport(true));
    document.getElementById('recurring-create').addEventListener('click', async () => {
      const text = document.getElementById('recurring-text').value.trim();
      if (!text) { showAlert('Write a recurring instruction first.', true); return; }
      try {
        const res = await fetch(API + '/recurring/from-text', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Error creating recurring rule.', true); return; }
        showAlert('Recurring rule saved.');
        document.getElementById('recurring-text').value = '';
        loadRecurringRules(data.id);
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    });
    document.getElementById('budget-save').addEventListener('click', async () => {
      const month = document.getElementById('budget-month').value;
      const category = document.getElementById('budget-category').value.trim();
      const limit_amount = parseInt(document.getElementById('budget-limit').value, 10) || 0;
      if (!month || !category) { showAlert('Month and category are required.', true); return; }
      try {
        const res = await fetch(API + '/budgets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ month, category, limit_amount })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Failed to save budget.', true); return; }
        showAlert('Budget saved.');
        loadBudgets();
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    });
    document.getElementById('snapshot-btn').addEventListener('click', async () => {
      try {
        const res = await fetch(API + '/exports/monthly-snapshot', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Snapshot failed.', true); return; }
        showAlert('Snapshot created: ' + (data.snapshot_file || 'ok'));
        // Served only to this company's signed-in users (never from /uploads).
        if (data.snapshot_file) window.open(API + data.snapshot_file, '_blank');
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    });
    document.getElementById('notify-btn').addEventListener('click', async () => {
      try {
        const res = await fetch(API + '/notifications/check', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Notification check failed.', true); return; }
        showAlert('Alerts checked. Delivered: ' + ((data.delivered || []).join(', ') || 'none'));
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    });

    document.getElementById('missing-refs-wrap').addEventListener('click', async (e) => {
      const upBtn = e.target.closest('.missing-ref-upload');
      if (upBtn) {
        const id = upBtn.dataset.id;
        const fileInput = document.querySelector('.missing-ref-file[data-id="' + id + '"]');
        const files = Array.from((fileInput && fileInput.files) || []);
        if (!files.length) {
          showAlert('Choose image/PDF files first.', true);
          return;
        }
        try {
          upBtn.disabled = true;
          const uploaded = await uploadFilesToAttachments(files);
          const existingIds = await getTransactionAttachmentIds(id);
          const allIds = existingIds.concat(uploaded.map(a => a.id));
          const patchRes = await fetch(API + '/transactions/' + encodeURIComponent(id), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ attachment_ids: allIds })
          });
          const patchData = await patchRes.json().catch(() => ({}));
          if (!patchRes.ok) {
            showAlert(patchData.detail || 'Failed linking attachments to transaction.', true);
            return;
          }
          if (uploaded.length) {
            try {
              const ocr = await runAttachmentOCR(uploaded[0].id);
              const refInput = document.querySelector('.missing-ref-input[data-id="' + id + '"]');
              if (refInput && !refInput.value.trim() && ocr.invoice_or_receipt_no) {
                refInput.value = ocr.invoice_or_receipt_no;
              }
              showAlert('Attachments uploaded. OCR extraction completed.');
            } catch (_) {
              showAlert('Attachments uploaded and linked.');
            }
          } else {
            showAlert('Attachments uploaded and linked.');
          }
          if (fileInput) fileInput.value = '';
          loadOwnerDashboard();
        } catch (err) {
          showAlert('Upload failed: ' + err.message, true);
        } finally {
          upBtn.disabled = false;
        }
        return;
      }
      const btn = e.target.closest('.missing-ref-save');
      if (!btn) return;
      const id = btn.dataset.id;
      const input = document.querySelector('.missing-ref-input[data-id="' + id + '"]');
      const reference = (input && input.value || '').trim();
      if (!reference) { showAlert('Reference cannot be empty.', true); return; }
      try {
        const res = await fetch(API + '/transactions/' + encodeURIComponent(id), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reference })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Error saving reference.', true); return; }
        showAlert('Reference updated.');
        loadMissingReferences();
        loadOwnerDashboard();
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    });

    // Maps an entity type to the voucher link role it is saved under.
    function entityRoleForType(type) {
      const tp = String(type || '').toLowerCase();
      if (tp === 'employee') return 'payee';
      if (tp === 'client' || tp === 'bank' || tp === 'supplier' || tp === 'payee') return tp;
      return '';
    }

    async function openEntityTransactions(entityId, entityName, entityType) {
      const modal = document.getElementById('account-modal');
      const body = document.getElementById('account-modal-body');
      const title = document.getElementById('account-modal-title');
      const prevType = (currentEntityContext && currentEntityContext.entityId === entityId) ? currentEntityContext.entityType : '';
      currentEntityContext = { entityId, entityName, entityType: entityType || prevType || '', controlAccount: null };
      title.textContent = tf('entTxTitle', { name: entityName }) + ' — ' + t('loading');
      body.innerHTML = '<p class="empty-state">' + escapeHtml(t('loading')) + '</p>';
      modal.style.display = 'flex';
      try {
        const res = await fetch(API + '/reports/entities/' + encodeURIComponent(entityId) + '/transactions');
        if (!res.ok) throw new Error(res.statusText);
        const transactions = await res.json();
        entityTransactionsCache = transactions || [];
        await loadEntityOptions();
        title.textContent = tf('entTxTitle', { name: entityName });
        if (transactions.length) {
          const first = transactions[0];
          currentEntityContext.controlAccount = first.entity_control_account || null;
          if (!currentEntityContext.entityType) {
            const own = (first.entity_links || []).find(l => l.entity_id === entityId);
            if (own && own.entity_type) currentEntityContext.entityType = own.entity_type;
          }
        }
        // Manual entry from the statement itself (QA: "no feature for adding
        // transactions manually") — opens the editor in create mode with the
        // entity already linked.
        const toolbar = `
          <div style="display:flex; justify-content:flex-end; margin-bottom:0.5rem;">
            <button type="button" class="btn btn-primary btn-sm entity-tx-add">${escapeHtml(t('entTxAddBtn'))}</button>
          </div>`;
        if (!transactions.length) {
          body.innerHTML = toolbar + '<p class="empty-state">' + escapeHtml(t('entTxEmpty')) + '</p>';
          return;
        }
        // Statement-of-account columns: Debtor = the entity paid / gave,
        // Creditor = the entity received, Remaining = running balance.
        const money = (n, ccy) => (n ? formatMoney(n, ccy) : '');
        body.innerHTML = toolbar + `
          <table class="detail-table">
            <thead><tr>
              <th>${escapeHtml(t('labelDate'))}</th><th>${escapeHtml(t('labelReference'))}</th><th>${escapeHtml(t('entTxColCcy'))}</th>
              <th>${escapeHtml(t('labelDescription'))}</th><th>${escapeHtml(t('entTxColAttachments'))}</th>
              <th class="num" title="${escapeHtml(t('entTxDebtorHint'))}">${escapeHtml(t('entTxDebtor'))}</th>
              <th class="num" title="${escapeHtml(t('entTxCreditorHint'))}">${escapeHtml(t('entTxCreditor'))}</th>
              <th class="num" title="${escapeHtml(t('entTxRemainingHint'))}">${escapeHtml(t('entTxRemaining'))}</th>
              <th>${escapeHtml(t('entTxColActions'))}</th>
            </tr></thead>
            <tbody>
              ${transactions.map(t0 => {
                const ccy = (t0.currency || 'IRR').toUpperCase();
                const att = (t0.attachments || []);
                const attHtml = att.length
                  ? att.map(a => {
                      const href = encodeURI(String(a.url || ''));
                      return `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${escapeHtml(a.file_name || 'attachment')}</a>`;
                    }).join('<br>')
                  : '—';
                const placed = t0.entity_placed !== false;
                const paid = placed ? money(t0.entity_paid, ccy) : '—';
                const received = placed ? money(t0.entity_received, ccy) : '—';
                const remaining = placed ? formatMoney(t0.entity_balance || 0, ccy) : '—';
                return `<tr>
                  <td>${escapeHtml(formatDateDual(t0.date))}</td>
                  <td>${escapeHtml(t0.reference || '—')}</td>
                  <td><span class="ccy-badge ccy-${escapeHtml(ccy)}">${escapeHtml(ccy)}</span></td>
                  <td>${escapeHtml((t0.description || '—').slice(0, 50))}${(t0.description && t0.description.length > 50) ? '…' : ''}</td>
                  <td>${attHtml}</td>
                  <td class="num">${paid}</td>
                  <td class="num">${received}</td>
                  <td class="num" style="font-weight:600;">${remaining}</td>
                  <td>
                    <button type="button" class="btn btn-secondary btn-sm entity-tx-edit" data-tx-id="${t0.id}">${escapeHtml(t('btnEdit'))}</button>
                    <button type="button" class="btn btn-danger btn-sm entity-tx-del" data-tx-id="${t0.id}" style="margin-left:0.35rem;">${escapeHtml(t('btnDelete'))}</button>
                  </td>
                </tr>`;
              }).join('')}
            </tbody>
          </table>
        `;
      } catch (err) {
        body.innerHTML = '<p class="empty-state">' + escapeHtml(t('entTxLoadError')) + '</p>';
      }
    }

    function selectedEntityIdForRole(tx, role) {
      const links = tx && tx.entity_links ? tx.entity_links : [];
      const match = links.find(l => (l.role || '').toLowerCase() === role);
      return match && match.entity_id ? String(match.entity_id) : '';
    }

    function roleSelectHtml(role, currentId) {
      const options = entityOptions[role] || [];
      return `
        <option value="">— None —</option>
        ${options.map(o => `<option value="${escapeHtml(String(o.id))}" ${String(o.id) === String(currentId || '') ? 'selected' : ''}>${escapeHtml(o.name || '')}</option>`).join('')}
      `;
    }

    function txLineRowHtml(line) {
      const l = line || {};
      return `
        <tr class="edit-tx-line-row">
          <td><input type="text" class="edit-tx-line-code" value="${escapeHtml(l.account_code || '')}" required></td>
          <td><input type="number" class="edit-tx-line-debit" min="0" step="1" value="${Number(l.debit || 0)}"></td>
          <td><input type="number" class="edit-tx-line-credit" min="0" step="1" value="${Number(l.credit || 0)}"></td>
          <td><input type="text" class="edit-tx-line-desc" value="${escapeHtml(l.line_description || '')}"></td>
          <td><button type="button" class="btn btn-secondary btn-sm edit-tx-line-remove">Remove</button></td>
        </tr>
      `;
    }

    function openEntityTransactionEditor(txOrNull) {
      const body = document.getElementById('account-modal-body');
      const title = document.getElementById('account-modal-title');
      const creating = !txOrNull;
      let tx = txOrNull;
      if (creating) {
        // New journal with the current entity already linked under its role
        // and its control account on the first line, so a non-accountant
        // only has to fill the amount and the counter account.
        const ctx = currentEntityContext || {};
        const role = entityRoleForType(ctx.entityType);
        tx = {
          date: new Date().toISOString().slice(0, 10),
          reference: '', description: '',
          entity_links: role ? [{ role, entity_id: ctx.entityId }] : [],
          lines: [{ account_code: ctx.controlAccount || '' }, {}],
        };
      }
      title.textContent = creating
        ? tf('entTxAddTitle', { name: (currentEntityContext || {}).entityName || '' })
        : t('entTxEditTitle');
      body.innerHTML = `
        <form id="entity-tx-edit-form">
          <div class="form-grid">
            <div>
              <label>Date</label>
              <input type="date" id="edit-tx-date" value="${escapeHtml(tx.date || '')}" required>
              <span style="font-size: 0.75rem; color: var(--text-muted);">${toJalali(tx.date)}</span>
            </div>
            <div>
              <label>Reference</label>
              <input type="text" id="edit-tx-reference" value="${escapeHtml(tx.reference || '')}" placeholder="e.g. INV-001">
            </div>
            <div style="grid-column:1 / -1;">
              <label>Description</label>
              <textarea id="edit-tx-description" rows="2">${escapeHtml(tx.description || '')}</textarea>
            </div>
            <div>
              <label>Client</label>
              <select id="edit-tx-client">${roleSelectHtml('client', selectedEntityIdForRole(tx, 'client'))}</select>
            </div>
            <div>
              <label>Bank</label>
              <select id="edit-tx-bank">${roleSelectHtml('bank', selectedEntityIdForRole(tx, 'bank'))}</select>
            </div>
            <div>
              <label>Payee</label>
              <select id="edit-tx-payee">${roleSelectHtml('payee', selectedEntityIdForRole(tx, 'payee'))}</select>
            </div>
            <div>
              <label>Supplier</label>
              <select id="edit-tx-supplier">${roleSelectHtml('supplier', selectedEntityIdForRole(tx, 'supplier'))}</select>
            </div>
            <div style="grid-column:1 / -1;">
              <label>Lines (debit/credit)</label>
              <table class="detail-table" style="margin-top:0.35rem;">
                <thead><tr><th>Account code</th><th>Debit</th><th>Credit</th><th>Line description</th><th></th></tr></thead>
                <tbody id="edit-tx-lines-body">
                  ${(tx.lines || []).map(txLineRowHtml).join('')}
                </tbody>
              </table>
              <button type="button" class="btn btn-secondary btn-sm" id="edit-tx-add-line" style="margin-top:0.45rem;">+ Add line</button>
            </div>
          </div>
          <div style="display:flex; gap:0.5rem; margin-top:0.75rem;">
            <button type="submit" class="btn btn-primary">${escapeHtml(creating ? t('entTxSaveNew') : t('entTxSaveChanges'))}</button>
            <button type="button" class="btn btn-secondary" id="entity-tx-edit-cancel">${escapeHtml(t('entTxBack'))}</button>
          </div>
        </form>
      `;
      const cancelBtn = document.getElementById('entity-tx-edit-cancel');
      if (cancelBtn && currentEntityContext) {
        cancelBtn.onclick = () => openEntityTransactions(currentEntityContext.entityId, currentEntityContext.entityName);
      }
      const addLineBtn = document.getElementById('edit-tx-add-line');
      if (addLineBtn) {
        addLineBtn.onclick = () => {
          const bodyEl = document.getElementById('edit-tx-lines-body');
          if (bodyEl) bodyEl.insertAdjacentHTML('beforeend', txLineRowHtml({}));
        };
      }
      const linesBodyEl = document.getElementById('edit-tx-lines-body');
      if (linesBodyEl) {
        linesBodyEl.onclick = (e) => {
          const rm = e.target.closest('.edit-tx-line-remove');
          if (!rm) return;
          const rows = linesBodyEl.querySelectorAll('.edit-tx-line-row');
          if (rows.length <= 2) {
            showAlert('A transaction needs at least two lines.', true);
            return;
          }
          const tr = rm.closest('.edit-tx-line-row');
          if (tr) tr.remove();
        };
      }
      const form = document.getElementById('entity-tx-edit-form');
      if (form) {
        form.onsubmit = async (e) => {
          e.preventDefault();
          const lineRows = Array.from(document.querySelectorAll('#edit-tx-lines-body .edit-tx-line-row'));
          const lines = lineRows.map((tr) => ({
            account_code: (tr.querySelector('.edit-tx-line-code').value || '').trim(),
            debit: parseInt(tr.querySelector('.edit-tx-line-debit').value, 10) || 0,
            credit: parseInt(tr.querySelector('.edit-tx-line-credit').value, 10) || 0,
            line_description: (tr.querySelector('.edit-tx-line-desc').value || '').trim() || null,
          })).filter((l) => l.account_code);
          if (lines.length < 2) {
            showAlert('Please keep at least two lines with account code.', true);
            return;
          }
          const totalDebit = lines.reduce((s, l) => s + (l.debit || 0), 0);
          const totalCredit = lines.reduce((s, l) => s + (l.credit || 0), 0);
          if (totalDebit !== totalCredit) {
            showAlert('Debits and credits must be equal.', true);
            return;
          }
          const entity_links = [];
          const c = document.getElementById('edit-tx-client').value;
          const b = document.getElementById('edit-tx-bank').value;
          const p = document.getElementById('edit-tx-payee').value;
          const s = document.getElementById('edit-tx-supplier').value;
          if (c) entity_links.push({ role: 'client', entity_id: c });
          if (b) entity_links.push({ role: 'bank', entity_id: b });
          if (p) entity_links.push({ role: 'payee', entity_id: p });
          if (s) entity_links.push({ role: 'supplier', entity_id: s });
          const payload = {
            date: document.getElementById('edit-tx-date').value,
            reference: document.getElementById('edit-tx-reference').value.trim() || null,
            description: document.getElementById('edit-tx-description').value.trim() || null,
            lines,
            entity_links,
          };
          try {
            const res = await fetch(creating ? API + '/transactions' : API + '/transactions/' + encodeURIComponent(tx.id), {
              method: creating ? 'POST' : 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
              showAlert(data.detail || (creating ? t('entTxCreateError') : t('entTxUpdateError')), true);
              return;
            }
            showAlert(creating ? t('entTxCreated') : t('entTxUpdated'));
            if (currentEntityContext) openEntityTransactions(currentEntityContext.entityId, currentEntityContext.entityName);
            loadOwnerDashboard();
            loadLedger();
          } catch (err) {
            showAlert('Connection error: ' + err.message, true);
          }
        };
      }
    }

    document.getElementById('account-modal-body').addEventListener('click', async (e) => {
      if (e.target.closest('.entity-tx-add')) {
        openEntityTransactionEditor(null);
        return;
      }
      const editBtn = e.target.closest('.entity-tx-edit');
      if (editBtn) {
        const tx = entityTransactionsCache.find(x => String(x.id) === String(editBtn.dataset.txId));
        if (tx) openEntityTransactionEditor(tx);
        return;
      }
      const delBtn = e.target.closest('.entity-tx-del');
      if (!delBtn) return;
      if (!(await uiConfirm({ message: t('confirmDeleteTransaction'), confirmLabel: t('btnDelete'), danger: true }))) return;
      try {
        const res = await fetch(API + '/transactions/' + encodeURIComponent(delBtn.dataset.txId), { method: 'DELETE' });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          showAlert(data.detail || 'Error deleting transaction.', true);
          return;
        }
        showAlert('Transaction deleted.');
        if (currentEntityContext) openEntityTransactions(currentEntityContext.entityId, currentEntityContext.entityName);
        loadOwnerDashboard();
        loadLedger();
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      }
    });

    registerAction('export-acct-detail', (el) => _exportAcctDetail(el.dataset.format));

    // ═══════ Seasonal tax reports: TTMS + VAT return (app/services/tax_ir.py) ═══════
    let _ttmsSeasons = [];
    function _ttmsDays(n) {
      if (n < 0) return t('ttmsPassed');
      return n === 0 ? t('ttmsToday') : tf('ttmsDaysLeft', { n });
    }
    async function loadTtmsSeasons() {
      const sel = document.getElementById('ttms-season');
      if (!sel) return;
      try {
        const res = await fetch(API + '/tax/ir/seasons');
        const rows = res.ok ? await res.json() : [];
        _ttmsSeasons = Array.isArray(rows) ? rows : [];
        // Default to the season just ended: the one whose filings are due.
        const pick = _ttmsSeasons.find(s => !s.current) || _ttmsSeasons[0];
        sel.innerHTML = _ttmsSeasons.map(s => `<option value="${s.year}-${s.season}"${s === pick ? ' selected' : ''}>${escapeHtml(s.name)}${s.current ? ' · ' + escapeHtml(t('ttmsCurrent')) : ''} (${escapeHtml(String(s.sales_count + s.purchase_count))})</option>`).join('');
        updateTtmsDownload();
        loadTtms();
      } catch (_) { sel.innerHTML = ''; }
    }
    function _ttmsPick() {
      const v = (document.getElementById('ttms-season').value || '').split('-');
      return { year: v[0], season: v[1], mo: document.getElementById('ttms-include-mo').checked };
    }
    function updateTtmsDownload() {
      const p = _ttmsPick();
      const a = document.getElementById('ttms-download');
      if (a && p.year) a.href = `${API}/tax/ir/quarterly/export?year=${encodeURIComponent(p.year)}&season=${encodeURIComponent(p.season)}&include_moadian=${p.mo}`;
    }
    async function loadTtms() {
      const p = _ttmsPick();
      if (!p.year) return;
      const sumEl = document.getElementById('ttms-summary');
      const alerts = document.getElementById('ttms-alerts');
      const tables = document.getElementById('ttms-tables');
      try {
        const qs = `year=${encodeURIComponent(p.year)}&season=${encodeURIComponent(p.season)}`;
        const [rr, vr] = await Promise.all([
          fetch(`${API}/tax/ir/quarterly?${qs}&include_moadian=${p.mo}`), fetch(`${API}/tax/ir/vat-return?${qs}`),
        ]);
        const rep = await rr.json().catch(() => ({}));
        const ret = await vr.json().catch(() => ({}));
        if (!rr.ok || !vr.ok) { sumEl.textContent = rep.detail || ret.detail || t('ttmsLoadFailed'); return; }
        const sd = rep.season;
        const money = (n) => formatMoney(n, 'IRR');
        const s = rep.totals.sales, pu = rep.totals.purchases;
        const stat = (k, v, sub) => `<div class="ai-usage-stat"><div class="k">${escapeHtml(k)}</div><div class="v" dir="ltr">${v}</div>${sub ? `<div style="font-size:0.74rem;color:var(--text-muted);margin-top:0.2rem;">${sub}</div>` : ''}</div>`;
        const sdMeta = _ttmsSeasons.find(x => String(x.year) === String(sd.year) && String(x.season) === String(sd.season)) || {};
        sumEl.innerHTML = [
          stat(t('ttmsSales'), money(s.net_base), escapeHtml(t('ttmsVat')) + ': ' + money(s.net_vat)),
          stat(t('ttmsPurchases'), money(pu.net_base), escapeHtml(t('ttmsVat')) + ': ' + money(pu.net_vat)),
          stat(ret.payable ? t('ttmsReturnPayable') : t('ttmsReturnCredit'), money(ret.payable || ret.credit_carried_forward),
               escapeHtml(t('ttmsDeadlineVat')) + ': ' + escapeHtml(sd.vat_deadline_jalali) + ' · ' + escapeHtml(_ttmsDays(sdMeta.vat_days_left ?? 1))),
          stat(t('ttmsDeadlineTtms'), escapeHtml(sd.ttms_deadline_jalali), escapeHtml(_ttmsDays(sdMeta.ttms_days_left ?? 1))),
        ].join('');
        const notes = [];
        const rec = ret.reconciliation || {};
        notes.push(`<div class="tfa-note" style="${rec.matches ? 'background:color-mix(in srgb, var(--success, #16a34a) 10%, transparent);border-color:color-mix(in srgb, var(--success, #16a34a) 35%, transparent);' : ''}">${escapeHtml(rec.matches ? t('ttmsReconOk') : t('ttmsReconBad'))}</div>`);
        if (rep.excluded_moadian && rep.excluded_moadian.count) notes.push(`<div class="tfa-note">${escapeHtml(tf('ttmsExcludedMoadian', { n: rep.excluded_moadian.count }))}</div>`);
        if ((rep.other_currency || []).length) notes.push(`<div class="tfa-note">${escapeHtml(tf('ttmsOtherCurrency', { n: rep.other_currency.length }))}</div>`);
        if ((rep.incomplete || []).length) {
          notes.push(`<div class="tfa-note"><strong>${escapeHtml(tf('ttmsIncomplete', { n: rep.incomplete.length }))}</strong><ul style="margin:0.3rem 0 0;padding-inline-start:1.1rem;">${rep.incomplete.slice(0, 12).map(r => `<li>${escapeHtml(r.name)} — ${escapeHtml(r.missing_labels.join('، '))}</li>`).join('')}</ul></div>`);
        }
        alerts.innerHTML = notes.join('');
        const table = (title, rows) => `<div style="flex:1 1 22rem;"><h3 style="font-size:0.9rem;margin:0.4rem 0;">${escapeHtml(title)}</h3>
          <table class="results-table" style="font-size:0.82rem;"><thead><tr><th>${escapeHtml(t('ttmsParty'))}</th><th>${escapeHtml(t('ttmsInvoices'))}</th><th>${escapeHtml(t('ttmsNet'))}</th><th>${escapeHtml(t('ttmsVat'))}</th></tr></thead>
          <tbody>${rows.length ? rows.slice(0, 15).map(r => `<tr><td>${escapeHtml(r.name)}${r.missing.length ? ' <span title="' + escapeHtml(t('ttmsMissing')) + '">⚠</span>' : ''}</td><td dir="ltr">${escapeHtml(String(r.invoice_count))}</td><td dir="ltr">${money(r.net_base)}</td><td dir="ltr">${money(r.net_vat)}</td></tr>`).join('')
            : `<tr><td colspan="4" class="empty-state">${escapeHtml(t('ttmsNoData'))}</td></tr>`}</tbody></table></div>`;
        tables.innerHTML = table(t('ttmsSales'), rep.sales || []) + table(t('ttmsPurchases'), rep.purchases || []);
      } catch (_) { sumEl.textContent = t('ttmsLoadFailed'); }
    }
    (function wireTtms() {
      const panel = document.getElementById('ttms-panel');
      if (!panel) return;
      panel.addEventListener('toggle', (e) => { if (e.target.open && !_ttmsSeasons.length) loadTtmsSeasons(); });
      document.getElementById('ttms-load').addEventListener('click', () => { updateTtmsDownload(); loadTtms(); });
      document.getElementById('ttms-season').addEventListener('change', () => { updateTtmsDownload(); loadTtms(); });
      document.getElementById('ttms-include-mo').addEventListener('change', () => { updateTtmsDownload(); loadTtms(); });
    })();
