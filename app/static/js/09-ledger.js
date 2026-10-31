
    let ledgerLevels = {};   // {code: tier} for the group rows (accountLevels)

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
      // isolated: a code and a Latin name keep their order in RTL text
      const acctLabel = (r) => r ? '<bdi>' + escapeHtml(localCode(r.account_code) + (r.account_name ? ' — ' + r.account_name : '')) + '</bdi>' : '—';
      ledgerKpisEl.innerHTML = `
        <div class="ledger-kpi"><div class="k">${escapeHtml(t('ledgerKpiShown'))}</div><div class="v">${formatNum(rows.length)}</div></div>
        <div class="ledger-kpi"><div class="k">${escapeHtml(t('ledgerKpiActive'))}</div><div class="v">${formatNum(active)}</div></div>
        <div class="ledger-kpi"><div class="k">${escapeHtml(t('ledgerKpiMovement'))}</div><div class="v">${formatAmount(totalMovement)} ${escapeHtml(currencyUnit())}</div></div>
        <div class="ledger-kpi"><div class="k">${escapeHtml(t('ledgerKpiNet'))}</div><div class="v ${net >= 0 ? 'ledger-positive' : 'ledger-negative'}">${formatAmount(Math.abs(net))} ${escapeHtml(currencyUnit())} ${escapeHtml(t(net >= 0 ? 'balanceDr' : 'balanceCr'))}</div></div>
        <div class="ledger-kpi"><div class="k">${escapeHtml(t('ledgerKpiTopDebit'))}</div><div class="v" style="font-size:0.95rem;">${acctLabel(topDebit)}</div></div>
        <div class="ledger-kpi"><div class="k">${escapeHtml(t('ledgerKpiTopCredit'))}</div><div class="v" style="font-size:0.95rem;">${acctLabel(topCredit)}</div></div>
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
      const q = foldFa((ledgerSearchEl.value || '').trim());
      const onlyNonZero = !!ledgerNonZeroEl.checked;
      let rows = ledgerData.rows.filter(r => {
        if (onlyNonZero && accountTurnover(r) === 0) return false;
        if (!q) return true;
        return String(r.account_code || '').toLowerCase().includes(q) || foldFa(r.account_name).includes(q);
      });
      rows = sortLedgerRows(rows);
      return rows;
    }

    // A bar's account: its code, then its name — cut names end in «…» («موجودی نقد و بان ۱۱۱۰» was cut
    // mid-word with the code tacked on, Persian copy review 2026-10-31).
    function ledgerChartLabel(r) {
      const name = String(r.account_name || '');
      return localCode(r.account_code) + ' ' + (name.length > 16 ? name.slice(0, 15).trimEnd() + '…' : name);
    }

    function renderCharts(rows) {
      const row = document.getElementById('charts-row');
      if (!rows || rows.length === 0) { row.style.display = 'none'; return; }
      if (typeof Chart === 'undefined') { row.style.display = 'none'; return; }  // chart lib missing → table still works
      row.style.display = 'grid';
      const maxN = Number(ledgerTopNEl.value || 12);
      const chartRows = rows.slice(0, maxN);
      const labels = chartRows.map(ledgerChartLabel);
      if (chartTurnover) chartTurnover.destroy();
      if (chartBalance) chartBalance.destroy();
      const colors = ['#0f766e', '#14b8a6', '#0ea5e9', '#f59e0b'];
      chartTurnover = new Chart(document.getElementById('chart-turnover'), {
        type: 'bar',
        data: {
          labels,
          datasets: [
            { label: t('tableDebitTurnover'), data: chartRows.map(r => r.debit_turnover), backgroundColor: colors[0], borderRadius: 5, stack: 'turnover' },
            { label: t('tableCreditTurnover'), data: chartRows.map(r => r.credit_turnover), backgroundColor: colors[1], borderRadius: 5, stack: 'turnover' }
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
      // each bar says which account, as the turnover chart's do (a bare code said little)
      const balanceLabels = chartRows.map(ledgerChartLabel);
      const balanceData = chartRows.map(r => accountNet(r));
      chartBalance = new Chart(document.getElementById('chart-balance'), {
        type: 'line',
        data: {
          labels: balanceLabels,
          datasets: [{
            label: t('legendNetBalance'),
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
        resultsTbody.innerHTML = '<tr><td colspan="6" class="empty-state">' + escapeHtml(t('ledgerNoMatch')) + '</td></tr>';
        resultsFoot.style.display = 'none';
        document.getElementById('charts-row').style.display = 'none';
        ledgerKpisEl.innerHTML = '';
        return;
      }
      // Each account under its parent, with a header + subtotal per parent. The header names the
      // parent's own tier on the chart (گروه / کل / معین / تفصیلی): every parent was tagged «دفتر کل»
      // though 11 is a «گروه» on the chart page (Persian copy review, 2026-10-31). Rows without a
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
          const tier = accountLevelLabel(ledgerLevels, g.code);
          const gh = document.createElement('tr');
          gh.className = 'ledger-group-row';
          gh.innerHTML = `
            <td style="font-weight:700;background:var(--bg-subtle,#f4f6f8);">${escapeHtml(localCode(g.code))}</td>
            <td colspan="5" style="font-weight:700;background:var(--bg-subtle,#f4f6f8);">${escapeHtml(g.name || '')}${tier ? ` <span style="color:var(--text-muted);font-weight:400;font-size:0.78rem;">(${escapeHtml(tier)})</span>` : ''}</td>
          `;
          resultsTbody.appendChild(gh);
        }
        g.rows.forEach(r => {
          const net = accountNet(r);
          const tr = document.createElement('tr');
          tr.className = 'ledger-row';
          tr.dataset.accountCode = r.account_code;
          tr.tabIndex = 0;   // opened from the keyboard too (Enter / Space), not only by a click
          tr.innerHTML = `
            <td style="${grouped && g.code ? 'padding-inline-start:1.4rem;' : ''}">${escapeHtml(localCode(r.account_code))}</td>
            <td>${r.account_name}</td>
            <td class="num">${formatAmount(r.debit_turnover)}</td>
            <td class="num">${formatAmount(r.credit_turnover)}</td>
            <td class="num">${formatAmount(r.debit_balance)}</td>
            <td class="num ${net < 0 ? 'ledger-negative' : ''}">${formatAmount(r.credit_balance)}</td>
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
            <td style="color:var(--text-muted);font-size:0.8rem;">${escapeHtml(tf('ledgerKolTotal', { name: g.name || localCode(g.code) }))}</td>
            <td class="num" style="font-weight:600;">${formatAmount(st.dt)}</td>
            <td class="num" style="font-weight:600;">${formatAmount(st.ct)}</td>
            <td class="num" style="font-weight:600;">${formatAmount(st.db)}</td>
            <td class="num" style="font-weight:600;">${formatAmount(st.cb)}</td>
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
      document.getElementById('foot-debit-turnover').textContent = formatAmount(totals.debitTurnover);
      document.getElementById('foot-credit-turnover').textContent = formatAmount(totals.creditTurnover);
      document.getElementById('foot-debit-balance').textContent = formatAmount(totals.debitBalance);
      document.getElementById('foot-credit-balance').textContent = formatAmount(totals.creditBalance);
      renderLedgerKpis(rows);
      renderCharts(rows);
    }

    function renderLedgerView() {
      const rows = filteredLedgerRows();
      renderLedgerTable(rows);
    }

    // Keep the currency <select> in step with the server's view: it lists the
    // currency shown first, then every other currency the books contain.
    function syncLedgerCurrencyControls(data) {
      const sel = document.getElementById('ledger-currency');
      if (sel && data && data.currency) {
        const opts = [data.currency].concat((data.other_currencies || []).filter(c => c !== data.currency));
        // more than one currency: offer them all together, at base value
        if (opts.length > 1 && !opts.includes('ALL')) opts.push('ALL');
        const label = (c) => c === 'ALL' ? tf('currencyAllInBase', { base: baseCurrencyCode() }) : c;
        sel.innerHTML = opts.map(c => '<option value="' + escapeHtml(c) + '"' + (c === data.currency ? ' selected' : '') + '>' + escapeHtml(label(c)) + '</option>').join('');
      }
      renderCurrencyViewNote(document.getElementById('ledger-currency-note'), data, (c) => loadLedger(c));
    }

    async function loadLedger(currency) {
      if (!onPage('ledger')) return;      // reloads when it opens (loadPageData)
      try {
        const sel = document.getElementById('ledger-currency');
        const ccy = (typeof currency === 'string' && currency) || (sel && sel.value) || '';
        const levels = accountLevels();      // the parents' tiers, fetched alongside
        const res = await fetch(API + '/reports/ledger-summary' + (ccy ? ('?currency=' + encodeURIComponent(ccy)) : ''));
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error('HTTP ' + res.status + (typeof err.detail === 'string' ? ' — ' + err.detail : ''));
        }
        const data = await res.json();
        ledgerLevels = await levels;
        ledgerData = data;
        syncLedgerCurrencyControls(data);
        if (!data.rows || data.rows.length === 0) {
          resultsTbody.innerHTML = '<tr><td colspan="6" class="empty-state">' + escapeHtml(t('ledgerEmpty')) + '</td></tr>';
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

    { const sel = document.getElementById('ledger-currency'); if (sel) sel.addEventListener('change', () => loadLedger(sel.value)); }
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
    // Looked up when clicked: 13-companies-products.js wraps runManagerReport
    // (accounts payable, the product filter) after this file has run — a
    // reference taken here kept the unwrapped one, so "Accounts payable" ran
    // the balance sheet and the product filter was ignored.
    if (mgrRunBtn) mgrRunBtn.addEventListener('click', () => runManagerReport());
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
        if (!res.ok) { showAlert(data.detail || t('msgRoleUpdateFailed'), true); loadUsers(); return; }
        showAlert(t('usersRoleUpdated'));
        loadUsers();
      } catch (err) { showAlert(t('msgConnectionError') + err.message, true); }
    });
    if (openAiChatInlineBtn) {
      openAiChatInlineBtn.addEventListener('click', () => {
        toggleInlineChat();
        if (voucherChatInlineEl && voucherChatInlineEl.classList.contains('is-open') && chatInput) chatInput.focus();
      });
    }

    document.getElementById('date').value = localIsoDate(new Date());
