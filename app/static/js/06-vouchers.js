
    function addLineRow() {
      const tr = document.createElement('tr');
      tr.className = 'line-row';
      tr.innerHTML = `
        <td><input type="text" class="line-code" placeholder="Account code"></td>
        <td><input type="number" class="line-debit" min="0" value="0" step="1"></td>
        <td><input type="number" class="line-credit" min="0" value="0" step="1"></td>
        <td><input type="text" class="line-desc"></td>
        <td><button type="button" class="btn btn-secondary remove-line">Remove</button></td>
      `;
      linesTbody.appendChild(tr);
    }

    function renderAttachments() {
      attachmentGrid.innerHTML = '';
      if (!selectedAttachments.length) {
        attachmentGrid.innerHTML = '<div class="attachment-help">No attachments uploaded yet.</div>';
        return;
      }
      selectedAttachments.forEach(att => {
        const div = document.createElement('div');
        div.className = 'attachment-item';
        const isImage = (att.content_type || '').startsWith('image/');
        const thumb = isImage ? `<img src="${escapeHtml(att.url)}" alt="${escapeHtml(att.file_name)}">` : '<img alt="PDF" src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22240%22 height=%22140%22%3E%3Crect width=%22240%22 height=%22140%22 fill=%22%23e2e8f0%22/%3E%3Ctext x=%2250%25%22 y=%2250%25%22 text-anchor=%22middle%22 fill=%22%23475569%22 font-size=%2222%22 dy=%228%22%3EPDF%3C/text%3E%3C/svg%3E">';
        div.innerHTML = `
          ${thumb}
          <div class="attachment-name">${escapeHtml(att.file_name)}</div>
          <div class="attachment-meta">${escapeHtml(att.content_type)} · ${formatNum(att.size_bytes || 0)} bytes</div>
          <div style="display:flex; gap:0.35rem;">
            <a class="btn btn-secondary btn-sm" href="${escapeHtml(att.url)}" target="_blank" rel="noreferrer" style="text-decoration:none;">Open</a>
            <button type="button" class="btn btn-danger btn-sm remove-attachment" data-id="${att.id}">Remove</button>
          </div>
        `;
        attachmentGrid.appendChild(div);
      });
    }

    async function uploadAttachments() {
      const files = Array.from(attachmentInput.files || []);
      if (!files.length) {
        showAlert('Choose at least one file.', true);
        return;
      }
      attachmentUploadBtn.disabled = true;
      try {
        for (const f of files) {
          const fd = new FormData();
          fd.append('file', f);
          const res = await fetch(API + '/transactions/attachments', { method: 'POST', body: fd });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            showAlert(data.detail || ('Failed to upload ' + f.name), true);
            continue;
          }
          selectedAttachments.push(data);
        }
        attachmentInput.value = '';
        renderAttachments();
        showAlert('Attachments uploaded.');
      } catch (err) {
        showAlert('Attachment upload failed: ' + err.message, true);
      } finally {
        attachmentUploadBtn.disabled = false;
      }
    }

    async function removeAttachment(id) {
      try {
        const res = await fetch(API + '/transactions/attachments/' + encodeURIComponent(id), { method: 'DELETE' });
        if (!res.ok && res.status !== 204) {
          const data = await res.json().catch(() => ({}));
          showAlert(data.detail || 'Could not remove attachment.', true);
          return;
        }
        selectedAttachments = selectedAttachments.filter(a => a.id !== id);
        renderAttachments();
      } catch (err) {
        showAlert('Could not remove attachment: ' + err.message, true);
      }
    }

    attachmentUploadBtn.addEventListener('click', uploadAttachments);
    attachmentGrid.addEventListener('click', (e) => {
      const btn = e.target.closest('.remove-attachment');
      if (btn) removeAttachment(btn.dataset.id);
    });
    addLineBtn.addEventListener('click', addLineRow);
    linesTbody.addEventListener('click', (e) => {
      if (e.target.classList.contains('remove-line') && linesTbody.querySelectorAll('.line-row').length > 2)
        e.target.closest('.line-row').remove();
    });

    function resetVoucherForm() {
      document.getElementById('date').value = new Date().toISOString().slice(0, 10);
      document.getElementById('reference').value = '';
      document.getElementById('description').value = '';
      document.getElementById('entity-client').value = '';
      document.getElementById('entity-bank').value = '';
      document.getElementById('entity-payee').value = '';
      document.getElementById('entity-supplier').value = '';
      // Smart-default the voucher currency from FX metadata (reporting currency,
      // falling back to most-common currency, then IRR).
      const curSel = document.getElementById('txn-currency');
      if (curSel) {
        const pref = preferredFormCurrency();
        if ([...curSel.options].some(o => o.value === pref)) curSel.value = pref;
      }
      linesTbody.innerHTML = '';
      addLineRow();
      addLineRow();
      selectedAttachments = [];
      attachmentInput.value = '';
      renderAttachments();
    }

    // Primary cash/bank account of the seeded chart, used as the default
    // posting account for invoice payments. Derived from the chart itself
    // (UK chart → 1200 "Bank current account", Iranian chart → 1110) instead
    // of hardcoding the Iranian cash code.
    window.__DEFAULT_CASH_CODE = null;
    let _defaultCashCodePromise = null;
    function getDefaultCashCode(force) {
      if (!_defaultCashCodePromise || force) {
        _defaultCashCodePromise = (async () => {
          try {
            const r = await fetch(API + '/accounts?limit=500');
            if (r.ok) {
              const list = await r.json();
              const codes = new Set((list || []).map((a) => String(a.code)));
              // Whichever primary cash/bank code the seeded chart contains
              // wins; the reporting locale only breaks the tie if both exist.
              const candidates = window.__REPORTING_LOCALE === 'uk' ? ['1200', '1110'] : ['1110', '1200'];
              let code = candidates.find((c) => codes.has(c));
              if (!code) {
                const guess = (list || []).find((a) =>
                  /^1\d{2,3}$/.test(String(a.code)) && /(bank|cash|بانک|صندوق|نقد)/i.test(String(a.name || '')));
                code = guess ? String(guess.code) : null;
              }
              if (code) window.__DEFAULT_CASH_CODE = code;
            }
          } catch (_) { /* keep fallback below */ }
          return window.__DEFAULT_CASH_CODE || '1110';
        })();
      }
      return _defaultCashCodePromise;
    }

    async function loadEntityOptions() {
      try {
        const res = await fetch(API + '/entities');
        const list = await res.json();
        entityOptions.client = (list || []).filter(e => e.type === 'client');
        entityOptions.bank = (list || []).filter(e => e.type === 'bank');
        entityOptions.payee = (list || []).filter(e => e.type === 'employee' || e.type === 'payee');
        entityOptions.supplier = (list || []).filter(e => e.type === 'supplier');
        const sel = (id, arr) => {
          const el = document.getElementById(id);
          el.innerHTML = '';
          const none = document.createElement('option');
          none.value = '';
          none.textContent = '— None —';
          el.appendChild(none);
          (arr || []).forEach(e => {
            const opt = document.createElement('option');
            opt.value = e.id;
            opt.textContent = e.name;
            el.appendChild(opt);
          });
        };
        sel('entity-client', entityOptions.client);
        sel('entity-bank', entityOptions.bank);
        sel('entity-payee', entityOptions.payee);
        sel('entity-supplier', entityOptions.supplier);
        const invSel = document.getElementById('inv-entity');
        if (invSel) {
          invSel.innerHTML = '<option value="">— None —</option>';
          (list || []).filter(e => e.type === 'client' || e.type === 'supplier').forEach(e => {
            const opt = document.createElement('option');
            opt.value = e.id;
            opt.textContent = e.type + ': ' + e.name;
            invSel.appendChild(opt);
          });
        }
        const bankSel = document.getElementById('inv-bank-code');
        if (bankSel) {
          const defaultCash = await getDefaultCashCode();
          bankSel.innerHTML = '';
          const none = document.createElement('option');
          none.value = '';
          none.dataset.accountCode = defaultCash;
          none.textContent = '— ' + tf('optionDefaultBank', { code: defaultCash }) + ' —';
          bankSel.appendChild(none);
          (entityOptions.bank || []).forEach(e => {
            const opt = document.createElement('option');
            opt.value = e.id;
            opt.dataset.accountCode = (e.code || '').trim() || defaultCash;
            opt.textContent = e.name + (e.code ? (' (' + e.code + ')') : '');
            bankSel.appendChild(opt);
          });
          bankSel.value = '';
        }
      } catch (_) {}
    }

    let _invoicesCache = [];
    async function loadInvoices(highlightId) {
      if (typeof highlightId !== 'string' && typeof highlightId !== 'number') highlightId = null;
      loadTaxRates();
      try {
        const res = await fetch(API + '/invoices');
        const list = await res.json();
        _invoicesCache = list || [];
        invoicesTbody.innerHTML = '';
        if (!list.length) {
          invoicesTbody.innerHTML = '<tr><td colspan="6" class="empty-state">' + t('noInvoicesYet') + '</td></tr>';
          return;
        }
        list.forEach(i => {
          const status = String(i.status || '').toLowerCase();
          const isPaid = status === 'paid';
          const settled = isPaid || status === 'canceled';
          const ccy = (i.currency || 'IRR').toUpperCase();
          const paid = Number(i.amount_paid || 0);
          const balance = (i.balance_due != null) ? Number(i.balance_due) : Number(i.amount || 0);
          const taxTotal = Number(i.tax_total || 0);
          const subtotal = Number(i.subtotal || i.amount || 0);
          const taxLine = taxTotal > 0
            ? `<div style="font-size:0.72rem;color:var(--text-muted);">${t('invSubtotal')}: ${formatMoney(subtotal, ccy)} + ${t('invTax')}: ${formatMoney(taxTotal, ccy)}</div>`
            : '';
          const tr = document.createElement('tr');
          tr.dataset.invoiceId = i.id;
          tr.innerHTML = `
            <td>${escapeHtml(i.number)}</td>
            <td>${escapeHtml(i.kind)}</td>
            <td>${escapeHtml(localizeDynamicText(i.status))}</td>
            <td>${formatMoney(i.amount, ccy)} <span class="ccy-badge ccy-${escapeHtml(ccy)}">${escapeHtml(ccy)}</span>${taxLine}</td>
            <td>${formatMoney(paid, ccy)}</td>
            <td><strong>${formatMoney(balance, ccy)}</strong></td>
            <td>${escapeHtml(i.due_date)}</td>
            <td>
              <button type="button" class="btn btn-secondary btn-sm inv-edit" data-id="${i.id}" data-status="${escapeHtml(i.status)}">${escapeHtml(t('btnEdit') || 'Edit')}</button>
              <button type="button" class="btn btn-primary btn-sm inv-payment" data-id="${i.id}" style="margin-left:0.3rem;" ${settled ? 'disabled' : ''}>${escapeHtml(t('invAddPayment'))}</button>
              <button type="button" class="btn btn-secondary btn-sm inv-credit-note" data-id="${i.id}" style="margin-left:0.3rem;" ${settled ? 'disabled' : ''}>${escapeHtml(t('invCreditNote'))}</button>
              <a class="btn btn-secondary btn-sm" href="${escapeHtml(i.pdf_url || ('/invoices/' + i.id + '/pdf'))}" target="_blank" style="margin-left:0.3rem; text-decoration:none;">PDF</a>
              <button type="button" class="btn btn-secondary btn-sm inv-timeline" data-id="${i.id}" style="margin-left:0.3rem;">${escapeHtml(t('invHistory'))}</button>
              <button type="button" class="btn btn-danger btn-sm inv-void" data-id="${i.id}" data-number="${escapeHtml(i.number)}" style="margin-left:0.3rem;" ${(i.status === 'voided' || i.status === 'canceled') ? 'disabled' : ''}>${escapeHtml(t('invVoid'))}</button>
              <button type="button" class="btn btn-danger btn-sm inv-del" data-id="${i.id}" style="margin-left:0.3rem;">${escapeHtml(t('btnDelete') || 'Delete')}</button>
            </td>
          `;
          invoicesTbody.appendChild(tr);
        });
        if (highlightId) flashRow(invoicesTbody.querySelector('tr[data-invoice-id="' + CSS.escape(String(highlightId)) + '"]'));
      } catch (err) {
        invoicesTbody.innerHTML = '<tr><td colspan="8" class="empty-state">Error loading invoices.</td></tr>';
      }
    }

    async function loadRecurringRules(highlightId) {
      if (typeof highlightId !== 'string' && typeof highlightId !== 'number') highlightId = null;
      try {
        const res = await fetch(API + '/recurring');
        const list = await res.json();
        recurringTbody.innerHTML = '';
        if (!list.length) {
          recurringTbody.innerHTML = '<tr><td colspan="10" class="empty-state">No recurring rules yet.</td></tr>';
          return;
        }
        list.forEach(r => {
          const tr = document.createElement('tr');
          tr.dataset.ruleId = r.id;
          const paused = r.status === 'paused';
          tr.innerHTML = `
            <td>${escapeHtml(r.name)}</td>
            <td>${escapeHtml(r.direction)}</td>
            <td>${escapeHtml(r.frequency)}</td>
            <td>${r.amount == null ? '—' : formatNum(r.amount)}</td>
            <td>${escapeHtml(r.bank_account_code || r.bank_name || '—')}</td>
            <td>${escapeHtml(r.counter_account_code || '—')}</td>
            <td>${escapeHtml(r.next_run_date)}</td>
            <td>${r.auto_post ? '✓' : '—'}</td>
            <td>${escapeHtml(r.status)}</td>
            <td>
              <button type="button" class="btn btn-secondary btn-sm recurring-pause" data-id="${r.id}" data-paused="${paused ? '1' : ''}">${paused ? '▶' : '⏸'}</button>
              <button type="button" class="btn btn-danger btn-sm recurring-del" data-id="${r.id}">Delete</button>
            </td>
          `;
          recurringTbody.appendChild(tr);
        });
        recurringTbody.querySelectorAll('.recurring-pause').forEach(b => b.addEventListener('click', async () => {
          await fetch(API + '/recurring/' + b.dataset.id, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: b.dataset.paused ? 'active' : 'paused' }),
          }).catch(() => {});
          loadRecurringRules();
        }));
        if (highlightId) flashRow(recurringTbody.querySelector('tr[data-rule-id="' + CSS.escape(String(highlightId)) + '"]'));
      } catch (err) {
        recurringTbody.innerHTML = '<tr><td colspan="10" class="empty-state">Error loading recurring rules.</td></tr>';
      }
    }

    async function loadMissingReferences() {
      const wrap = document.getElementById('missing-refs-wrap');
      try {
        const res = await fetch(API + '/reports/missing-references');
        const data = await res.json();
        const items = data.items || [];
        if (!items.length) {
          wrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('noMissingReferences')) + '</p>';
          return;
        }
        wrap.innerHTML = `
          <table class="mini-table missing-ref-table">
            <thead><tr><th>${escapeHtml(t('labelDate'))}</th><th>${escapeHtml(t('labelDescription'))}</th><th>${escapeHtml(t('labelReference'))}</th><th>${escapeHtml(t('attachReceipt'))}</th><th></th></tr></thead>
            <tbody>
              ${items.map(it => `
                <tr>
                  <td class="missing-ref-date">${escapeHtml(formatDateDual(it.date))}</td>
                  <td>${escapeHtml((it.description || '').slice(0, 60))}</td>
                  <td><input type="text" class="missing-ref-input" data-id="${it.transaction_id}" value="${escapeHtml(it.suggested_reference || '')}"></td>
                  <td>
                    <div class="missing-ref-actions">
                      <input type="file" class="missing-ref-file" data-id="${it.transaction_id}" accept="image/jpeg,image/png,image/webp,application/pdf" multiple>
                      <button type="button" class="btn btn-secondary btn-sm missing-ref-upload" data-id="${it.transaction_id}">${escapeHtml(t('uploadReceipts'))}</button>
                    </div>
                  </td>
                  <td><button type="button" class="btn btn-primary btn-sm missing-ref-save" data-id="${it.transaction_id}">${escapeHtml(t('saveReference'))}</button></td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        `;
      } catch (err) {
        wrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('errorLoadingMissingReferences')) + '</p>';
      }
    }

    async function uploadFilesToAttachments(files) {
      const uploaded = [];
      for (const f of files) {
        const fd = new FormData();
        fd.append('file', f);
        const res = await fetch(API + '/transactions/attachments', { method: 'POST', body: fd });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || ('Failed upload: ' + f.name));
        uploaded.push(data);
      }
      return uploaded;
    }

    async function getTransactionAttachmentIds(txId) {
      const res = await fetch(API + '/transactions/' + encodeURIComponent(txId));
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Cannot read transaction');
      return (data.attachments || []).map(a => a.id);
    }

    async function runAttachmentOCR(attachmentId) {
      const res = await fetch(API + '/transactions/attachments/' + encodeURIComponent(attachmentId) + '/ocr', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'OCR failed');
      return data;
    }

    async function loadBudgets() {
      const monthVal = document.getElementById('budget-month').value || new Date().toISOString().slice(0, 7);
      try {
        const res = await fetch(API + '/budgets/actual-vs-budget?month=' + encodeURIComponent(monthVal));
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'budget error');
        const rows = data.rows || [];
        if (!rows.length) {
          budgetWrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">No budget rows for this month.</p>';
          return;
        }
        budgetWrap.innerHTML = `
          <table class="mini-table">
            <thead><tr><th>Category</th><th>Limit</th><th>Actual</th><th>Variance</th><th>Utilization</th></tr></thead>
            <tbody>
              ${rows.map(r => `<tr>
                <td>${escapeHtml(r.category)}</td>
                <td>${formatNum(r.limit_amount)}</td>
                <td>${formatNum(r.actual_amount)}</td>
                <td>${formatNum(r.variance)}</td>
                <td>${escapeHtml(r.utilization_pct)}%</td>
              </tr>`).join('')}
            </tbody>
          </table>
        `;
      } catch (err) {
        budgetWrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">Error loading budgets.</p>';
      }
    }

    function setEntityDropdownsFromResponse(resolvedEntities, mentions) {
      const byRole = { client: 'entity-client', bank: 'entity-bank', payee: 'entity-payee', supplier: 'entity-supplier' };
      const assigned = new Set();
      Object.keys(byRole).forEach(role => {
        const el = document.getElementById(byRole[role]);
        if (el) el.value = '';
      });
      if (resolvedEntities && resolvedEntities.length) {
        resolvedEntities.forEach(r => {
          const role = (r.role || '').toLowerCase();
          const selId = byRole[role];
          if (r.entity_id && selId) {
            document.getElementById(selId).value = r.entity_id;
            assigned.add(role);
          }
        });
      }
      if (!mentions || !mentions.length) return;
      mentions.forEach(m => {
        const role = (m.role || '').toLowerCase();
        if (assigned.has(role)) return;
        const name = (m.name || '').trim();
        if (!name || !byRole[role]) return;
        const arr = entityOptions[role] || [];
        const found = arr.find(e => (e.name || '').toLowerCase() === name.toLowerCase());
        const selId = byRole[role];
        if (found && selId) document.getElementById(selId).value = found.id;
      });
    }
