
    function addLineRow() {
      const tr = document.createElement('tr');
      tr.className = 'line-row';
      tr.innerHTML = `
        <td><input type="text" class="line-code" placeholder="${escapeHtml(t('tableAccountCode'))}"></td>
        <td><input type="number" class="line-debit" min="0" value="0" step="any"></td>
        <td><input type="number" class="line-credit" min="0" value="0" step="any"></td>
        <td><input type="text" class="line-desc"></td>
        <td><button type="button" class="btn btn-secondary remove-line">${escapeHtml(t('btnRemove'))}</button></td>
      `;
      linesTbody.appendChild(tr);
    }

    function renderAttachments() {
      attachmentGrid.innerHTML = '';
      if (!selectedAttachments.length) {
        attachmentGrid.innerHTML = '<div class="attachment-help" data-i18n="attachmentsNone">' + escapeHtml(t('attachmentsNone')) + '</div>';
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
          <div class="attachment-meta">${escapeHtml(joinParts([att.content_type, formatNum(att.size_bytes || 0) + ' ' + t('unitBytes')]))}</div>
          <div style="display:flex; gap:0.35rem;">
            <a class="btn btn-secondary btn-sm" href="${escapeHtml(att.url)}" target="_blank" rel="noreferrer" style="text-decoration:none;">${escapeHtml(t('btnOpen'))}</a>
            <button type="button" class="btn btn-danger btn-sm remove-attachment" data-id="${att.id}">${escapeHtml(t('btnRemove'))}</button>
          </div>
        `;
        attachmentGrid.appendChild(div);
      });
    }

    async function uploadAttachments() {
      const files = Array.from(attachmentInput.files || []);
      if (!files.length) {
        showAlert(t('msgChooseFile'), true);
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
            showAlert(data.detail || tf('msgUploadFileFailed', { name: f.name }), true);
            continue;
          }
          selectedAttachments.push(data);
        }
        attachmentInput.value = '';
        renderAttachments();
        showAlert(t('msgAttachmentsUploaded'));
      } catch (err) {
        showAlert(t('msgAttachmentUploadFailed') + err.message, true);
      } finally {
        attachmentUploadBtn.disabled = false;
      }
    }

    async function removeAttachment(id) {
      try {
        const res = await fetch(API + '/transactions/attachments/' + encodeURIComponent(id), { method: 'DELETE' });
        if (!res.ok && res.status !== 204) {
          const data = await res.json().catch(() => ({}));
          showAlert(data.detail || t('msgAttachmentRemoveFailed'), true);
          return;
        }
        selectedAttachments = selectedAttachments.filter(a => a.id !== id);
        renderAttachments();
      } catch (err) {
        showAlert(t('msgAttachmentRemoveError') + err.message, true);
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
      document.getElementById('date').value = localIsoDate(new Date());
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
      if (typeof syncVoucherRate === 'function') syncVoucherRate();
      linesTbody.innerHTML = '';
      addLineRow();
      addLineRow();
      selectedAttachments = [];
      attachmentInput.value = '';
      renderAttachments();
      // the totals bar follows the empty lines: it kept the saved voucher's figures (scenario S2)
      if (typeof updateVoucherBalanceBar === 'function') updateVoucherBalanceBar();
    }

    // Voucher balance bar live updater
    function updateVoucherBalanceBar() {
      const rows = document.querySelectorAll('#lines-tbody tr');
      let totalDebit = 0, totalCredit = 0;
      rows.forEach(r => {
        totalDebit = addMoney(totalDebit, parseMoney(r.querySelector('.line-debit')?.value));
        totalCredit = addMoney(totalCredit, parseMoney(r.querySelector('.line-credit')?.value));
      });
      const dEl = document.getElementById('bal-debit');
      const cEl = document.getElementById('bal-credit');
      const diffEl = document.getElementById('bal-diff');
      if (dEl) dEl.textContent = tf('voucherBarDebit', { amount: formatAmount(totalDebit) });
      if (cEl) cEl.textContent = tf('voucherBarCredit', { amount: formatAmount(totalCredit) });
      if (diffEl) {
        const diff = totalDebit - totalCredit;
        if (diff === 0) {
          diffEl.textContent = t('voucherBarBalanced');
          diffEl.className = 'balance-diff balanced';
        } else {
          diffEl.textContent = tf('voucherBarDiff', { amount: formatAmount(diff) });
          diffEl.className = 'balance-diff unbalanced';
        }
      }
    }
    document.getElementById('lines-tbody').addEventListener('input', updateVoucherBalanceBar);   // first drawn by applyLanguage

    // The names of the chart's accounts, for a confirmation that says more than codes.
    async function accountNamesByCode() {
      try {
        const r = await fetch(API + '/accounts');
        const list = r.ok ? await r.json() : [];
        return Object.fromEntries((Array.isArray(list) ? list : []).map(a => [a.code, a.name]));
      } catch (_) { return {}; }
    }

    function buildConfirmationSummary(date, description, lines, currency, names = {}) {
      // in the user's words and calendar, each account by its name too — it was
      // "Date: 2026-09-29 … Debit entries: 6112: 1,000 IRR" in a Persian, Jalali company
      const acct = (code) => code + (names[code] ? ' ' + names[code] : '');
      const money = (n) => currency ? formatMoney(n, currency) : formatAmount(n);
      const row = (l, amount) => '  ' + acct(l.account_code) + ': ' + money(amount) + (l.line_description ? ' (' + l.line_description + ')' : '');
      let summary = tf('voucherConfirmDate', { date: formatDisplayDate(date) }) + '\n';
      if (description) summary += tf('voucherConfirmDescription', { text: description }) + '\n';
      summary += '\n' + t('voucherConfirmDebits') + '\n' + lines.filter(l => l.debit > 0).map(l => row(l, l.debit)).join('\n') + '\n';
      summary += '\n' + t('voucherConfirmCredits') + '\n' + lines.filter(l => l.credit > 0).map(l => row(l, l.credit)).join('\n') + '\n';
      summary += '\n' + tf('voucherConfirmTotal', { amount: money(lines.reduce((s, l) => s + l.debit, 0)) });
      return summary;
    }

    // A voucher in another currency than the base one is converted at a rate
    // (roadmap §4.6): the one typed here, else the rate on file for its date.
    async function syncVoucherRate() {
      const wrap = document.getElementById('txn-rate-wrap');
      if (!wrap) return;
      const cur = (document.getElementById('txn-currency')?.value || '').toUpperCase();
      const base = String(baseCurrencyCode() || '').toUpperCase();
      wrap.hidden = !cur || !base || cur === base;
      if (wrap.hidden) { document.getElementById('txn-fx-rate').value = ''; return; }
      document.getElementById('txn-fx-rate-pair').textContent = '1 ' + cur + ' = ? ' + base;
      const hint = document.getElementById('txn-fx-rate-hint');
      const input = document.getElementById('txn-fx-rate');
      try {
        const res = await fetch(API + '/fx/convert', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount: 1, from_currency: cur, to_currency: base, strict: true,
                                 on_date: document.getElementById('date').value || null }),
        });
        const d = await res.json().catch(() => ({}));
        if (res.ok && d.rate) {
          input.placeholder = formatRate(d.rate);
          hint.textContent = tf('voucherRateOnFile', { rate: formatRate(d.rate) });
        } else {
          input.placeholder = '';
          hint.textContent = tf('voucherRateNone', { pair: cur + '→' + base });
        }
      } catch (_) { hint.textContent = ''; }
    }
    ['txn-currency', 'date'].forEach((id) => document.getElementById(id)?.addEventListener('change', syncVoucherRate));

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const date = document.getElementById('date').value;
      const reference = document.getElementById('reference').value.trim() || null;
      const description = document.getElementById('description').value.trim() || null;
      const currency = (document.getElementById('txn-currency')?.value || preferredFormCurrency());
      const rows = Array.from(linesTbody.querySelectorAll('.line-row'));
      const lines = rows.map(tr => ({
        account_code: asciiDigits(tr.querySelector('.line-code').value).trim(),   // «۶۱۱۲» is 6112
        debit: parseMoney(tr.querySelector('.line-debit').value),
        credit: parseMoney(tr.querySelector('.line-credit').value),
        line_description: tr.querySelector('.line-desc').value.trim() || null
      })).filter(l => l.account_code);
      if (lines.length < 2) {
        showAlert(t('msgTwoLinesNeeded'), true);
        return;
      }
      const totalDebit = lines.reduce((s, l) => addMoney(s, l.debit), 0);
      const totalCredit = lines.reduce((s, l) => addMoney(s, l.credit), 0);
      if (totalDebit !== totalCredit) {
        showAlert(tf('msgVoucherUnbalanced', { debit: formatAmount(totalDebit), credit: formatAmount(totalCredit) }), true);
        return;
      }
      // Warn if posting into a currency different from the reporting currency
      const meta = window.__FX_META;
      if (meta && meta.reporting_currency && currency && currency.toUpperCase() !== String(meta.reporting_currency).toUpperCase()) {
        const proceed = await uiConfirm({
          title: t('voucherCurrencyMismatchTitle'),
          message: tf('voucherCurrencyMismatchMsg', { currency, reporting: meta.reporting_currency }),
          confirmLabel: t('btnContinue'),
        });
        if (!proceed) return;
      }
      // Pre-save confirmation
      const confirmMsg = buildConfirmationSummary(date, description, lines, currency, await accountNamesByCode());
      if (!(await uiConfirm({ title: t('confirmSaveVoucherTitle'), message: confirmMsg, confirmLabel: t('saveVoucher') }))) return;

      submitBtn.disabled = true;
      document.getElementById('results-wrap').classList.add('loading');
      const entity_links = [];
      const ec = document.getElementById('entity-client').value;
      const eb = document.getElementById('entity-bank').value;
      const ep = document.getElementById('entity-payee').value;
      if (ec) entity_links.push({ role: 'client', entity_id: ec });
      if (eb) entity_links.push({ role: 'bank', entity_id: eb });
      if (ep) entity_links.push({ role: 'payee', entity_id: ep });
      const es = document.getElementById('entity-supplier').value;
      if (es) entity_links.push({ role: 'supplier', entity_id: es });
      try {
        const res = await fetch(API + '/transactions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            date,
            reference,
            description,
            currency,
            fx_rate: (Number(document.getElementById('txn-fx-rate')?.value) > 0
              ? Number(document.getElementById('txn-fx-rate').value) : null),
            lines,
            entity_links,
            attachment_ids: selectedAttachments.map(a => a.id),
          })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || t('msgVoucherSaveError') + res.status, true);
          return;
        }
        showAlert(t('msgVoucherSaved'));
        loadLedger();
        loadEntities();
        loadOwnerDashboard();
        resetVoucherForm();
      } catch (err) {
        showAlert(t('msgConnectionError') + err.message, true);
      } finally {
        submitBtn.disabled = false;
        document.getElementById('results-wrap').classList.remove('loading');
      }
    });

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
              // by what each account is: a company's own codes number as they please (docs/PLAN_OWN_CODES.md)
              const byClass = new Map((list || []).map((a) => [String(a.class_code || a.code), String(a.code)]));
              // Whichever primary cash/bank code the seeded chart contains
              // wins; the reporting locale only breaks the tie if both exist.
              const candidates = window.__REPORTING_LOCALE === 'uk' ? ['1200', '1110'] : ['1110', '1200'];
              let code = candidates.map((c) => byClass.get(c)).find(Boolean);
              if (!code) {
                const guess = (list || []).find((a) =>
                  /^1\d{2,3}$/.test(String(a.class_code || a.code)) && /(bank|cash|بانک|صندوق|نقد)/i.test(String(a.name || '')));
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

    // Not page-guarded: besides the voucher and invoice pickers, the journal
    // editor in the entity statement (any page) builds its party dropdowns
    // from entityOptions — empty options there dropped a journal's parties on
    // save. Never called at boot.
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
          none.textContent = t('optionNone');
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
          invSel.innerHTML = '<option value="">' + escapeHtml(t('optionNone')) + '</option>';
          (list || []).filter(e => e.type === 'client' || e.type === 'supplier').forEach(e => {
            const opt = document.createElement('option');
            opt.value = e.id;
            opt.textContent = e.name + ' — ' + enumLabel('entType_', e.type);
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
          none.textContent = '— ' + tf('optionDefaultBank', { code: localCode(defaultCash) }) + ' —';
          bankSel.appendChild(none);
          (entityOptions.bank || []).forEach(e => {
            const opt = document.createElement('option');
            opt.value = e.id;
            opt.dataset.accountCode = (e.code || '').trim() || defaultCash;
            opt.textContent = e.name + (e.code ? (' (' + localCode(e.code) + ')') : '');
            bankSel.appendChild(opt);
          });
          bankSel.value = '';
        }
      } catch (_) {}
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
            <thead><tr><th scope="col">${escapeHtml(t('labelDate'))}</th><th scope="col">${escapeHtml(t('labelDescription'))}</th><th scope="col">${escapeHtml(t('labelReference'))}</th><th scope="col">${escapeHtml(t('attachReceipt'))}</th><th scope="col"></th></tr></thead>
            <tbody>
              ${items.map(it => `
                <tr>
                  <td class="missing-ref-date">${escapeHtml(formatDateDual(it.date))}</td>
                  <td dir="auto" class="ent-tx-desc">${escapeHtml(it.description || '')}</td>
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
        if (!res.ok) throw new Error(data.detail || tf('msgUploadFileFailed', { name: f.name }));
        uploaded.push(data);
      }
      return uploaded;
    }

    async function getTransactionAttachmentIds(txId) {
      const res = await fetch(API + '/transactions/' + encodeURIComponent(txId));
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || t('msgTxnReadFailed'));
      return (data.attachments || []).map(a => a.id);
    }

    async function runAttachmentOCR(attachmentId) {
      const res = await fetch(API + '/transactions/attachments/' + encodeURIComponent(attachmentId) + '/ocr', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'OCR failed');
      return data;
    }
