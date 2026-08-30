
    /* ===== Excel Journal Import ===== */

    let _excelImportToken = null;
    let _excelImportAccounts = [];
    let _excelImportVouchers = [];

    function excelImportCurrencyChanged() {
      const currency = document.getElementById('excel-import-currency').value;
      const multiplierSel = document.getElementById('excel-import-multiplier');
      // Auto-set sensible defaults based on currency
      if (currency === 'IRT') {
        multiplierSel.value = '10';  // Toman to Rial: x10
      } else if (currency === 'IRR') {
        multiplierSel.value = '1';   // Already Rial
      } else {
        multiplierSel.value = '1';   // Foreign currency: keep as-is
      }
    }

    async function excelImportUpload() {
      const fileInput = document.getElementById('excel-import-file');
      const yearInput = document.getElementById('excel-import-year');
      const statusEl = document.getElementById('excel-import-status');
      const btn = document.getElementById('excel-import-upload-btn');

      if (!fileInput.files || !fileInput.files.length) {
        showAlert('Please select an Excel file', true);
        return;
      }

      const formData = new FormData();
      formData.append('file', fileInput.files[0]);

      let url = API + '/transactions/excel-import/preview';
      const year = yearInput.value.trim();
      if (year) url += '?jalali_year=' + encodeURIComponent(year);

      btn.disabled = true;
      statusEl.innerHTML = '<span style="color:var(--primary);">Uploading and parsing...</span>';

      try {
        const res = await fetch(url, { method: 'POST', body: formData });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || res.statusText);
        }
        const data = await res.json();

        _excelImportToken = null;
        // Extract token from the response or from file name
        // The token is embedded in the preview - we need to get it from the upload
        // Actually, let's re-read: the preview response doesn't return the token directly
        // We need to extract it. Let me check... The backend stores it keyed by hash.
        // We need to pass it back. Let me fix the backend to return the token.
        // For now, let's use the file name hash approach.

        // Actually the token IS returned indirectly — but we need to fix the backend.
        // Let me store the file info and use it.
        // WORKAROUND: re-upload on confirm. Better: fix backend to return token.

        _excelImportToken = data.file_token;
        _excelImportAccounts = data.unique_accounts || [];
        _excelImportVouchers = data.vouchers || [];

        // Update year field
        if (data.jalali_year) yearInput.value = data.jalali_year;

        // Show preview info
        const infoEl = document.getElementById('excel-import-preview-info');
        infoEl.innerHTML = `
          <div style="display:flex; gap:1.5rem; flex-wrap:wrap; font-size:0.85rem; padding:0.5rem; background:var(--bg-surface); border-radius:6px;">
            <span><strong>Rows:</strong> ${data.total_rows}</span>
            <span><strong>Vouchers:</strong> ${data.total_vouchers}</span>
            <span><strong>Jalali Year:</strong> ${data.jalali_year}</span>
            <span><strong>Unique Accounts:</strong> ${_excelImportAccounts.length}</span>
            ${data.errors.length ? '<span style="color:var(--danger);"><strong>Warnings:</strong> ' + data.errors.length + '</span>' : '<span style="color:var(--success);">All vouchers balanced</span>'}
          </div>
        `;

        // Render account mapping table
        _renderExcelAccountMappings(data.unique_accounts);

        // Render vouchers preview
        _renderExcelVouchersPreview(data.vouchers);

        // Show errors if any
        const errEl = document.getElementById('excel-import-errors');
        if (data.errors.length) {
          errEl.style.display = 'block';
          errEl.innerHTML = '<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:0.5rem;font-size:0.8rem;">' +
            data.errors.map(e => '<div>' + escapeHtml(e) + '</div>').join('') + '</div>';
        } else {
          errEl.style.display = 'none';
        }

        // Switch to step 2
        document.getElementById('excel-import-step1').style.display = 'none';
        document.getElementById('excel-import-step2').style.display = 'block';
        statusEl.innerHTML = '';

      } catch (e) {
        statusEl.innerHTML = '<span style="color:var(--danger);">' + escapeHtml(e.message) + '</span>';
      } finally {
        btn.disabled = false;
      }
    }

    function _renderExcelAccountMappings(accounts) {
      const tbody = document.querySelector('#excel-import-mapping-table tbody');
      tbody.innerHTML = '';
      accounts.forEach((acct, idx) => {
        const tr = document.createElement('tr');
        const statusIcon = acct.exists_in_chart
          ? '<span style="color:var(--success);" title="Account exists">&#10003;</span>'
          : (acct.suggested_code
            ? '<span style="color:#f0ad4e;" title="Account exists but mapping is suggested">~</span>'
            : '<span style="color:var(--danger);" title="No match found">&#10007;</span>');
        tr.innerHTML = `
          <td style="font-size:0.8rem;">${escapeHtml(acct.title1)}</td>
          <td style="font-size:0.8rem;">${escapeHtml(acct.title2)}</td>
          <td style="font-size:0.8rem;">${escapeHtml(acct.title3)}</td>
          <td><input type="text" class="excel-acct-code" data-idx="${idx}" value="${acct.suggested_code || ''}" style="width:80px;height:32px;font-size:0.8rem;" list="excel-acct-datalist"></td>
          <td style="text-align:center;">${statusIcon}</td>
        `;
        tbody.appendChild(tr);
      });

      // Build datalist of existing accounts
      _buildExcelAccountDatalist();
    }

    async function _buildExcelAccountDatalist() {
      let dl = document.getElementById('excel-acct-datalist');
      if (!dl) {
        dl = document.createElement('datalist');
        dl.id = 'excel-acct-datalist';
        document.body.appendChild(dl);
      }
      try {
        const res = await fetch(API + '/accounts');
        const data = await res.json();
        const accounts = Array.isArray(data) ? data : (data.items || []);
        dl.innerHTML = accounts.map(a =>
          `<option value="${escapeHtml(a.code)}">${escapeHtml(a.code)} - ${escapeHtml(a.name)}</option>`
        ).join('');
      } catch (e) {}
    }

    function _renderExcelVouchersPreview(vouchers) {
      const container = document.getElementById('excel-import-vouchers-preview');
      let html = '';
      vouchers.forEach(v => {
        const dateStr = v.gregorian_date || ('Day: ' + (v.date_code || '?'));
        const balClass = v.is_balanced ? 'color:var(--success)' : 'color:var(--danger)';
        html += `<div style="border:1px solid var(--border);border-radius:6px;padding:0.5rem;margin-bottom:0.5rem;">
          <div style="display:flex;justify-content:space-between;font-size:0.85rem;margin-bottom:0.3rem;">
            <strong>Voucher #${escapeHtml(String(v.voucher_number))}</strong>
            <span>${escapeHtml(String(dateStr))}</span>
            <span style="${balClass}">D: ${v.total_debit.toLocaleString()} | C: ${v.total_credit.toLocaleString()}</span>
          </div>
          <table style="width:100%;font-size:0.75rem;border-collapse:collapse;">
            <tr style="background:var(--bg-surface);"><th style="text-align:start;padding:2px 4px;">Account</th><th style="text-align:start;padding:2px 4px;">Description</th><th style="text-align:end;padding:2px 4px;">Debit</th><th style="text-align:end;padding:2px 4px;">Credit</th></tr>`;
        v.lines.forEach(l => {
          const acctPath = [l.title1, l.title2, l.title3].filter(Boolean).join(' > ');
          html += `<tr>
            <td style="padding:2px 4px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(acctPath)}">${escapeHtml(acctPath)}</td>
            <td style="padding:2px 4px;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(l.description)}">${escapeHtml(l.description)}</td>
            <td style="padding:2px 4px;text-align:end;">${l.debit ? l.debit.toLocaleString() : ''}</td>
            <td style="padding:2px 4px;text-align:end;">${l.credit ? l.credit.toLocaleString() : ''}</td>
          </tr>`;
        });
        html += '</table></div>';
      });
      container.innerHTML = html;
    }

    async function excelImportConfirm() {
      const btn = document.getElementById('excel-import-confirm-btn');
      const statusEl = document.getElementById('excel-import-confirm-status');
      const yearInput = document.getElementById('excel-import-year');
      const multiplierSel = document.getElementById('excel-import-multiplier');

      // Collect account mappings from the table
      const codeInputs = document.querySelectorAll('.excel-acct-code');
      const accountMappings = [];
      let hasEmpty = false;

      codeInputs.forEach((input, idx) => {
        const code = input.value.trim();
        if (!code) {
          hasEmpty = true;
          input.style.borderColor = 'var(--danger)';
        } else {
          input.style.borderColor = '';
        }
        if (_excelImportAccounts[idx]) {
          accountMappings.push({
            title1: _excelImportAccounts[idx].title1 || '',
            title2: _excelImportAccounts[idx].title2 || '',
            title3: _excelImportAccounts[idx].title3 || '',
            account_code: code,
          });
        }
      });

      if (hasEmpty) {
        showAlert('Please fill in all account codes before confirming', true);
        return;
      }

      btn.disabled = true;
      statusEl.innerHTML = '<span style="color:var(--primary);">Importing...</span>';

      try {
        if (!_excelImportToken) {
          throw new Error('No file token. Please re-upload the file.');
        }

        const year = yearInput.value.trim();
        const multiplier = parseFloat(multiplierSel.value) || 1;
        const importCurrency = document.getElementById('excel-import-currency').value || 'IRR';
        // Warn on suspicious combinations before firing the request
        if (multiplier >= 100000) {
          if (!(await uiConfirm({ message: tf('confirmLargeMultiplier', { multiplier }), confirmLabel: t('btnContinue') }))) {
            btn.disabled = false;
            statusEl.innerHTML = '';
            return;
          }
        }
        if (importCurrency !== 'IRR' && multiplier >= 1000) {
          if (!(await uiConfirm({ message: tf('confirmCurrencyMultiplier', { currency: importCurrency, multiplier }), confirmLabel: t('btnContinue') }))) {
            btn.disabled = false;
            statusEl.innerHTML = '';
            return;
          }
        }

        const confirmRes = await fetch(API + '/transactions/excel-import/confirm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            file_token: _excelImportToken,
            jalali_year: parseInt(year) || 1404,
            account_mappings: accountMappings,
            amount_multiplier: multiplier,
            currency: importCurrency,
          }),
        });

        if (!confirmRes.ok) {
          const err = await confirmRes.json().catch(() => ({}));
          throw new Error(err.detail || confirmRes.statusText);
        }

        const result = await confirmRes.json();

        // Show result
        document.getElementById('excel-import-step2').style.display = 'none';
        const step3 = document.getElementById('excel-import-step3');
        step3.style.display = 'block';
        step3.innerHTML = `
          <div style="background:var(--bg-success,#d4edda);border:1px solid var(--success,#28a745);border-radius:8px;padding:1rem;">
            <h4 style="margin:0 0 0.5rem 0;color:var(--success,#28a745);">Import Successful!</h4>
            <p style="margin:0;font-size:0.9rem;">
              <strong>${result.imported}</strong> vouchers imported as transactions.
              ${result.accounts_created ? '<br>' + result.accounts_created + ' new accounts created.' : ''}
              ${result.errors.length ? '<br><span style="color:var(--danger);">Warnings: ' + result.errors.length + '</span>' : ''}
            </p>
            ${result.errors.length ? '<div style="margin-top:0.5rem;font-size:0.8rem;color:var(--danger);">' + result.errors.map(e => '<div>' + escapeHtml(e) + '</div>').join('') + '</div>' : ''}
            <button type="button" class="btn btn-secondary btn-sm" style="margin-top:0.75rem;" onclick="
              document.getElementById('excel-import-step3').style.display='none';
              document.getElementById('excel-import-step1').style.display='block';
              document.getElementById('excel-import-file').value='';
              loadTransactions();
            ">Done</button>
          </div>
        `;

        // Refresh transaction list
        if (typeof loadTransactions === 'function') loadTransactions();

      } catch (e) {
        statusEl.innerHTML = '<span style="color:var(--danger);">' + escapeHtml(e.message) + '</span>';
      } finally {
        btn.disabled = false;
      }
    }
