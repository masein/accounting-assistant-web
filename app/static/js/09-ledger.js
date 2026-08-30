
    function parseCode(code) {
      const n = Number(code);
      return Number.isFinite(n) ? n : Number.MAX_SAFE_INTEGER;
    }

    function accountTurnover(r) {
      return (r.debit_turnover || 0) + (r.credit_turnover || 0);
    }

    function accountNet(r) {
      return (r.debit_balance || 0) - (r.credit_balance || 0);
    }

    function renderLedgerKpis(rows) {
      if (!rows || !rows.length) {
        ledgerKpisEl.innerHTML = '';
        return;
      }
      const totalMovement = rows.reduce((s, r) => s + accountTurnover(r), 0);
      const net = rows.reduce((s, r) => s + accountNet(r), 0);
      const active = rows.filter(r => accountTurnover(r) !== 0).length;
      const topDebit = [...rows].sort((a, b) => (b.debit_turnover || 0) - (a.debit_turnover || 0))[0];
      const topCredit = [...rows].sort((a, b) => (b.credit_turnover || 0) - (a.credit_turnover || 0))[0];
      // "1200 — Bank current account" instead of a bare account code.
      const acctLabel = (r) => r ? escapeHtml(r.account_code + (r.account_name ? ' — ' + r.account_name : '')) : '—';
      ledgerKpisEl.innerHTML = `
        <div class="ledger-kpi"><div class="k">Accounts shown</div><div class="v">${formatNum(rows.length)}</div></div>
        <div class="ledger-kpi"><div class="k">Active accounts</div><div class="v">${formatNum(active)}</div></div>
        <div class="ledger-kpi"><div class="k">Total movement</div><div class="v">${formatNum(totalMovement)} ${escapeHtml(currencyUnit())}</div></div>
        <div class="ledger-kpi"><div class="k">Net position</div><div class="v ${net >= 0 ? 'ledger-positive' : 'ledger-negative'}">${formatNum(Math.abs(net))} ${escapeHtml(currencyUnit())} ${net >= 0 ? 'DR' : 'CR'}</div></div>
        <div class="ledger-kpi"><div class="k">Top debit</div><div class="v" style="font-size:0.95rem;">${acctLabel(topDebit)}</div></div>
        <div class="ledger-kpi"><div class="k">Top credit</div><div class="v" style="font-size:0.95rem;">${acctLabel(topCredit)}</div></div>
      `;
    }

    function sortLedgerRows(rows) {
      const sortBy = ledgerSortEl.value || 'turnover_desc';
      const out = [...rows];
      out.sort((a, b) => {
        if (sortBy === 'code_asc') return parseCode(a.account_code) - parseCode(b.account_code);
        if (sortBy === 'name_asc') return String(a.account_name || '').localeCompare(String(b.account_name || ''));
        if (sortBy === 'turnover_asc') return accountTurnover(a) - accountTurnover(b);
        if (sortBy === 'balance_desc') return accountNet(b) - accountNet(a);
        if (sortBy === 'balance_asc') return accountNet(a) - accountNet(b);
        return accountTurnover(b) - accountTurnover(a);
      });
      return out;
    }

    function filteredLedgerRows() {
      if (!ledgerData || !Array.isArray(ledgerData.rows)) return [];
      const q = (ledgerSearchEl.value || '').trim().toLowerCase();
      const onlyNonZero = !!ledgerNonZeroEl.checked;
      let rows = ledgerData.rows.filter(r => {
        if (onlyNonZero && accountTurnover(r) === 0) return false;
        if (!q) return true;
        return String(r.account_code || '').toLowerCase().includes(q) || String(r.account_name || '').toLowerCase().includes(q);
      });
      rows = sortLedgerRows(rows);
      return rows;
    }

    function renderCharts(rows) {
      const row = document.getElementById('charts-row');
      if (!rows || rows.length === 0) { row.style.display = 'none'; return; }
      if (typeof Chart === 'undefined') { row.style.display = 'none'; return; }  // chart lib missing → table still works
      row.style.display = 'grid';
      const maxN = Number(ledgerTopNEl.value || 12);
      const chartRows = rows.slice(0, maxN);
      const labels = chartRows.map(r => r.account_code + ' ' + (r.account_name || '').slice(0, 16));
      if (chartTurnover) chartTurnover.destroy();
      if (chartBalance) chartBalance.destroy();
      const colors = ['#0f766e', '#14b8a6', '#0ea5e9', '#f59e0b'];
      chartTurnover = new Chart(document.getElementById('chart-turnover'), {
        type: 'bar',
        data: {
          labels,
          datasets: [
            { label: 'Debit turnover', data: chartRows.map(r => r.debit_turnover), backgroundColor: colors[0], borderRadius: 5, stack: 'turnover' },
            { label: 'Credit turnover', data: chartRows.map(r => r.credit_turnover), backgroundColor: colors[1], borderRadius: 5, stack: 'turnover' }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'top' },
            tooltip: { callbacks: { label: (ctx) => ctx.dataset.label + ': ' + formatNum(ctx.raw || 0) } }
          },
          scales: {
            x: { stacked: true, ticks: { maxRotation: 0, autoSkip: true } },
            y: { beginAtZero: true, ticks: { callback: (v) => formatNum(v) } }
          }
        }
      });
      const balanceLabels = chartRows.map(r => r.account_code);
      const balanceData = chartRows.map(r => accountNet(r));
      chartBalance = new Chart(document.getElementById('chart-balance'), {
        type: 'line',
        data: {
          labels: balanceLabels,
          datasets: [{
            label: 'Net balance (debit − credit)',
            data: balanceData,
            borderColor: colors[2],
            backgroundColor: balanceData.map(v => v >= 0 ? 'rgba(5, 150, 105, 0.22)' : 'rgba(185, 28, 28, 0.22)'),
            fill: true,
            tension: 0.25,
            pointRadius: 3,
            pointBackgroundColor: balanceData.map(v => v >= 0 ? '#059669' : '#b91c1c')
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'top' },
            tooltip: { callbacks: { label: (ctx) => ctx.dataset.label + ': ' + formatNum(ctx.raw || 0) } }
          },
          scales: {
            x: { ticks: { maxRotation: 0, autoSkip: true } },
            y: { beginAtZero: true, ticks: { callback: (v) => formatNum(v) } }
          }
        }
      });
    }

    function renderLedgerTable(rows) {
      resultsTbody.innerHTML = '';
      if (!rows.length) {
        resultsTbody.innerHTML = '<tr><td colspan="6" class="empty-state">No ledger rows match your filters.</td></tr>';
        resultsFoot.style.display = 'none';
        document.getElementById('charts-row').style.display = 'none';
        ledgerKpisEl.innerHTML = '';
        return;
      }
      // کل / معین structure: group معین (posting) accounts under their کل
      // (parent GROUP) with a header + subtotal per group. Rows without a
      // parent fall into a trailing ungrouped block.
      const groups = new Map();
      rows.forEach(r => {
        const key = r.parent_code || '~';
        if (!groups.has(key)) groups.set(key, { code: r.parent_code, name: r.parent_name, rows: [] });
        groups.get(key).rows.push(r);
      });
      const sortedGroups = [...groups.values()].sort((a, b) => (a.code || '￿').localeCompare(b.code || '￿'));
      const grouped = sortedGroups.length > 1 || (sortedGroups[0] && sortedGroups[0].code);
      sortedGroups.forEach(g => {
        if (grouped && g.code) {
          const gh = document.createElement('tr');
          gh.className = 'ledger-group-row';
          gh.innerHTML = `
            <td style="font-weight:700;background:var(--bg-subtle,#f4f6f8);">${escapeHtml(g.code)}</td>
            <td colspan="5" style="font-weight:700;background:var(--bg-subtle,#f4f6f8);">${escapeHtml(g.name || '')} <span style="color:var(--text-muted);font-weight:400;font-size:0.78rem;">(${escapeHtml(t('ledgerKol'))})</span></td>
          `;
          resultsTbody.appendChild(gh);
        }
        g.rows.forEach(r => {
          const net = accountNet(r);
          const tr = document.createElement('tr');
          tr.className = 'ledger-row';
          tr.dataset.accountCode = r.account_code;
          tr.innerHTML = `
            <td style="${grouped && g.code ? 'padding-inline-start:1.4rem;' : ''}">${r.account_code}</td>
            <td>${r.account_name}</td>
            <td class="num">${formatNum(r.debit_turnover)}</td>
            <td class="num">${formatNum(r.credit_turnover)}</td>
            <td class="num">${formatNum(r.debit_balance)}</td>
            <td class="num ${net < 0 ? 'ledger-negative' : ''}">${formatNum(r.credit_balance)}</td>
          `;
          resultsTbody.appendChild(tr);
        });
        if (grouped && g.code && g.rows.length > 1) {
          const st = g.rows.reduce((a, r) => {
            a.dt += r.debit_turnover || 0; a.ct += r.credit_turnover || 0;
            a.db += r.debit_balance || 0; a.cb += r.credit_balance || 0; return a;
          }, { dt: 0, ct: 0, db: 0, cb: 0 });
          const sr = document.createElement('tr');
          sr.innerHTML = `
            <td></td>
            <td style="color:var(--text-muted);font-size:0.8rem;">${escapeHtml(tf('ledgerKolTotal', { name: g.name || g.code }))}</td>
            <td class="num" style="font-weight:600;">${formatNum(st.dt)}</td>
            <td class="num" style="font-weight:600;">${formatNum(st.ct)}</td>
            <td class="num" style="font-weight:600;">${formatNum(st.db)}</td>
            <td class="num" style="font-weight:600;">${formatNum(st.cb)}</td>
          `;
          resultsTbody.appendChild(sr);
        }
      });
      const totals = rows.reduce((acc, r) => {
        acc.debitTurnover += r.debit_turnover || 0;
        acc.creditTurnover += r.credit_turnover || 0;
        acc.debitBalance += r.debit_balance || 0;
        acc.creditBalance += r.credit_balance || 0;
        return acc;
      }, { debitTurnover: 0, creditTurnover: 0, debitBalance: 0, creditBalance: 0 });
      resultsFoot.style.display = 'table-row-group';
      document.getElementById('foot-debit-turnover').textContent = formatNum(totals.debitTurnover);
      document.getElementById('foot-credit-turnover').textContent = formatNum(totals.creditTurnover);
      document.getElementById('foot-debit-balance').textContent = formatNum(totals.debitBalance);
      document.getElementById('foot-credit-balance').textContent = formatNum(totals.creditBalance);
      renderLedgerKpis(rows);
      renderCharts(rows);
    }

    function renderLedgerView() {
      const rows = filteredLedgerRows();
      renderLedgerTable(rows);
    }

    async function loadLedger() {
      try {
        const res = await fetch(API + '/reports/ledger-summary');
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error('HTTP ' + res.status + (typeof err.detail === 'string' ? ' — ' + err.detail : ''));
        }
        const data = await res.json();
        ledgerData = data;
        if (!data.rows || data.rows.length === 0) {
          resultsTbody.innerHTML = '<tr><td colspan="6" class="empty-state">No transactions yet. Use the form above to add a voucher.</td></tr>';
          resultsFoot.style.display = 'none';
          document.getElementById('charts-row').style.display = 'none';
          ledgerKpisEl.innerHTML = '';
          return;
        }
        renderLedgerView();
      } catch (err) {
        // show the real reason so a server failure is diagnosable from the UI
        resultsTbody.innerHTML = '<tr><td colspan="6" class="empty-state">Error loading ledger. ' + escapeHtml(err.message || '') + '</td></tr>';
        resultsFoot.style.display = 'none';
        document.getElementById('charts-row').style.display = 'none';
        ledgerKpisEl.innerHTML = '';
      }
    }

    [ledgerSearchEl, ledgerSortEl, ledgerTopNEl, ledgerNonZeroEl].forEach(el => {
      if (!el) return;
      el.addEventListener('input', renderLedgerView);
      el.addEventListener('change', renderLedgerView);
    });
    if (aiProviderSelect) aiProviderSelect.addEventListener('change', syncAIFieldsByProvider);
    if (aiSaveBtn) aiSaveBtn.addEventListener('click', saveAIConfig);
    const anthropicSaveBtn = document.getElementById('anthropic-save-btn');
    if (anthropicSaveBtn) anthropicSaveBtn.addEventListener('click', saveAnthropicConfig);
    const chatShapeSaveBtn = document.getElementById('chat-shape-save-btn');
    if (chatShapeSaveBtn) chatShapeSaveBtn.addEventListener('click', saveChatProviderShape);
    const chatShapeSelect = document.getElementById('chat-shape-select');
    if (chatShapeSelect) {
      // Live-preview the visibility toggle without requiring Save (the
      // Apply button still persists the choice).
      chatShapeSelect.addEventListener('change', () => {
        const v = chatShapeSelect.value;
        if (v === 'openai' || v === 'anthropic') _applyChatShapeVisibility(v);
        // For "" (auto) we leave the current visibility alone — re-evaluating
        // would require a server call.
      });
    }
    if (mgrRunBtn) mgrRunBtn.addEventListener('click', runManagerReport);
    if (mgrReportTypeEl) mgrReportTypeEl.addEventListener('change', syncManagerFilterLabels);
    if (mgrExportJsonBtn) mgrExportJsonBtn.addEventListener('click', exportManagerReportJson);
    if (mgrExportCsvBtn) mgrExportCsvBtn.addEventListener('click', exportManagerReportCsv);
    if (mgrExportPdfBtn) mgrExportPdfBtn.addEventListener('click', exportManagerReportPdf);
    if (mgrAddItemBtn) mgrAddItemBtn.addEventListener('click', addManagerInventoryItem);
    if (mgrAddMvBtn) mgrAddMvBtn.addEventListener('click', addManagerInventoryMovement);
    if (invRunBalanceBtn) invRunBalanceBtn.addEventListener('click', () => runInventoryReport('balance'));
    if (invRunMovementBtn) invRunMovementBtn.addEventListener('click', () => runInventoryReport('movement'));
    if (invExportJsonBtn) invExportJsonBtn.addEventListener('click', exportInventoryReportJson);
    if (invExportCsvBtn) invExportCsvBtn.addEventListener('click', exportInventoryReportCsv);
    if (invExportPdfBtn) invExportPdfBtn.addEventListener('click', exportInventoryReportPdf);
    if (saveLanguageBtn) saveLanguageBtn.addEventListener('click', saveLanguagePreference);
    if (logoutBtn) {
      logoutBtn.addEventListener('click', async () => {
        try {
          await fetch(API + '/auth/logout', { method: 'POST' });
        } catch (_) {}
        window.location.href = '/login';
      });
    }
    if (createUserBtn) createUserBtn.addEventListener('click', createUser);
    { const b = document.getElementById('digest-save-btn'); if (b) b.addEventListener('click', saveDigestSettings); }
    { const b = document.getElementById('digest-preview-btn'); if (b) b.addEventListener('click', previewDigest); }
    { const b = document.getElementById('apikey-create-btn'); if (b) b.addEventListener('click', createApiKey); }
    if (usersWrapEl) usersWrapEl.addEventListener('click', handleUserTableAction);
    if (usersWrapEl) usersWrapEl.addEventListener('change', async (e) => {
      const sel = e.target.closest('.user-role-select');
      if (!sel) return;
      const id = sel.dataset.id;
      try {
        const res = await fetch(API + '/admin/users/' + encodeURIComponent(id), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role: sel.value }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Failed to update role.', true); loadUsers(); return; }
        showAlert(t('usersRoleUpdated'));
        loadUsers();
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    });
    if (openAiChatInlineBtn) {
      openAiChatInlineBtn.addEventListener('click', () => {
        toggleInlineChat();
        if (voucherChatInlineEl && voucherChatInlineEl.classList.contains('is-open') && chatInput) chatInput.focus();
      });
    }

    document.getElementById('date').value = new Date().toISOString().slice(0, 10);
