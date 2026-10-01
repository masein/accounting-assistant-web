
    function renderMiniTable(elId, headers, rows) {
      const el = document.getElementById(elId);
      if (!rows || !rows.length) {
        el.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('noDataYet')) + '</p>';
        return;
      }
      const th = headers.map(h => `<th>${escapeHtml(localizeReportFieldName(h))}</th>`).join('');
      const tr = rows.map(r => `<tr>${r.map(c => `<td>${c && c.__html ? c.__html : (typeof c === 'string' ? escapeHtml(localizeDynamicText(c)) : c)}</td>`).join('')}</tr>`).join('');
      el.innerHTML = `<table class="mini-table"><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`;
    }

    async function loadOwnerDashboard(pickedCurrency) {
      if (!onPage('dashboard')) return;   // reloads when it opens (loadPageData)
      try {
        // Make sure the reporting currency is resolved before the first paint,
        // so figures are labelled in the company's currency (GBP for UK, etc.)
        // rather than the default IRR.
        if (!window.__FX_META) { try { await loadFxMetadata(); } catch (_) { /* offline */ } }
        await loadReportingCurrency();
        // Honour a global currency selector if one is present; falls back to no filter.
        const dashCcy = (typeof pickedCurrency === 'string' && pickedCurrency)
          || document.getElementById('mgr-currency')?.value
          || window.__FX_META?.reporting_currency
          || '';
        const url = API + '/reports/owner-dashboard' + (dashCcy ? ('?currency=' + encodeURIComponent(dashCcy)) : '');
        const res = await fetch(url);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'owner dashboard error');
        // Other currencies in the books are offered as separate views, never
        // folded into these figures.
        renderCurrencyViewNote(document.getElementById('dash-currency-note'), data, (c) => loadOwnerDashboard(c));

        const kpiGrid = document.getElementById('kpi-grid');
        const kpiLabelMap = {
          cash_on_hand: 'kpiCashOnHand',
          monthly_net_profit: 'kpiMonthlyNetProfit',
          burn_rate: 'kpiMonthlyBurnRate',
          runway_months: 'kpiRunway',
          ar_due_week: 'kpiArDueWeek',
          ap_due_week: 'kpiApDueWeek',
          tax_and_liability_payable: 'kpiLiabilitiesPayable',
        };
        kpiGrid.innerHTML = (data.kpis || []).map(k => `
          <div class="kpi-card">
            <div class="label">${escapeHtml(kpiLabelMap[k.key] ? t(kpiLabelMap[k.key]) : localizeDynamicText(k.label))}</div>
            <div class="value">${escapeHtml(formatKpiValue(k.value, k.unit))}</div>
          </div>
        `).join('');

        renderMiniTable(
          'forecast-wrap',
          ['week_start', 'projected_inflow', 'projected_outflow', 'projected_net', 'projected_cash', 'risk'],
          (data.forecast_13_weeks || []).map(r => [
            escapeHtml(r.week_start),
            formatNum(r.projected_inflow),
            formatNum(r.projected_outflow),
            formatNum(r.projected_net),
            formatNum(r.projected_cash),
            {__html: r.risk ? '<span class="alert-chip high">' + escapeHtml(t('risk')) + '</span>' : '<span class="alert-chip low">' + escapeHtml(t('ok')) + '</span>'}
          ])
        );
        renderForecastSummary(data.forecast_13_weeks || []);
        window.__DASH_CCY = data.currency || '';
        const explorer = document.getElementById('forecast-explorer');
        if (explorer && !explorer.dataset.bound) {
          explorer.dataset.bound = '1';
          explorer.addEventListener('toggle', () => { if (explorer.open) loadForecastExplorer(); });
        }
        if (explorer && explorer.open) loadForecastExplorer();

        loadInsightsPanel('insights-wrap');
        const alertsWrap = document.getElementById('alerts-wrap');
        const alerts = data.alerts || [];
        if (!alerts.length) {
          alertsWrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('noActiveAlerts')) + '</p>';
        } else {
          alertsWrap.innerHTML = alerts.map(a => `
            <div style="border:1px solid var(--border); border-radius:10px; padding:0.55rem; margin-bottom:0.45rem;">
              <span class="alert-chip ${escapeHtml(a.level)}">${escapeHtml((a.level || '').toUpperCase())}</span>
              <strong style="display:block; margin-top:0.25rem;">${escapeHtml(localizeDynamicText(a.title))}</strong>
              <div style="font-size:0.82rem; color:var(--text-muted);">${escapeHtml(localizeDynamicText(a.message))}</div>
            </div>
          `).join('');
        }

        renderMiniTable(
          'ar-aging-wrap',
          ['client', 'current', 'days_31_60', 'days_60_plus', 'total'],
          (data.ar_aging || []).map(r => [escapeHtml(r.name), formatNum(r.current), formatNum(r.days_31_60), formatNum(r.days_60_plus), formatNum(r.total)])
        );
        renderMiniTable(
          'ap-aging-wrap',
          ['vendor', 'current', 'days_31_60', 'days_60_plus', 'total'],
          (data.ap_aging || []).map(r => [escapeHtml(r.name), formatNum(r.current), formatNum(r.days_31_60), formatNum(r.days_60_plus), formatNum(r.total)])
        );
        renderMiniTable(
          'expense-category-wrap',
          ['category', 'amount'],
          (data.expense_by_category || []).map(r => [escapeHtml(r.category), formatNum(r.amount)])
        );
        renderMiniTable(
          'vendor-spend-wrap',
          ['vendor', 'amount'],
          (data.spend_by_vendor || []).map(r => [escapeHtml(r.vendor), formatNum(r.amount)])
        );
        renderMiniTable(
          'profitability-wrap',
          ['client', 'revenue', 'cost', 'profit', 'margin_pct'],
          (data.profitability_by_client || []).map(r => [escapeHtml(localizeDynamicText(r.client)), formatNum(r.revenue), formatNum(r.cost), formatNum(r.profit), (r.margin_pct == null ? '—' : String(r.margin_pct))])
        );

        const health = document.getElementById('health-wrap');
        health.innerHTML = `
          <div style="font-size:1.15rem; font-weight:700; margin-bottom:0.45rem;">${escapeHtml(t('scoreLabel'))}: ${escapeHtml(data.health_score)}/100</div>
          ${(data.health_issues || []).map(i => `<div style="font-size:0.82rem; color:var(--text-muted);">${escapeHtml(localizeDynamicText(i.label))}: ${escapeHtml(i.count)} (${Math.round((i.ratio || 0) * 100)}%)</div>`).join('')}
        `;

        const checklist = document.getElementById('checklist-wrap');
        checklist.innerHTML = (data.close_checklist || []).map(c => `
          <div style="border:1px solid var(--border); border-radius:9px; padding:0.4rem; margin-bottom:0.35rem;">
            <strong>${c.ok ? '✓' : '•'} ${escapeHtml(localizeDynamicText(c.item))}</strong>
            <div style="font-size:0.78rem; color:var(--text-muted);">${escapeHtml(localizeDynamicText(c.detail))}</div>
          </div>
        `).join('');
        const topProfit = (data.profitability_by_client || [])[0];
        const topProfitText = topProfit
          ? `${localizeDynamicText(topProfit.client)} (${formatNum(topProfit.profit || 0)} ${currencyUnit()})`
          : t('na');
        document.getElementById('owner-pack').textContent =
          `${t('ownerPackTitle')} (${new Date().toISOString().slice(0, 10)})\n\n` +
          `- ${t('kpiCashOnHand')}: ${formatNum((data.kpis || []).find((k) => k.key === 'cash_on_hand')?.value || 0)} ${currencyUnit()}\n` +
          `- ${t('ownerNetProfitMonth')}: ${formatNum((data.kpis || []).find((k) => k.key === 'monthly_net_profit')?.value || 0)} ${currencyUnit()}\n` +
          `- ${t('kpiMonthlyBurnRate')}: ${formatNum((data.kpis || []).find((k) => k.key === 'burn_rate')?.value || 0)} ${currencyUnit()}/${t('monthWord')}\n` +
          `- ${t('kpiRunway')}: ${formatKpiValue((data.kpis || []).find((k) => k.key === 'runway_months')?.value, 'months')}\n` +
          `- ${t('ownerOverdueAR')}: ${formatNum((data.ar_aging || []).reduce((a, r) => a + (r.days_31_60 || 0) + (r.days_60_plus || 0), 0))} ${currencyUnit()}\n` +
          `- ${t('ownerOverdueAP')}: ${formatNum((data.ap_aging || []).reduce((a, r) => a + (r.days_31_60 || 0) + (r.days_60_plus || 0), 0))} ${currencyUnit()}\n` +
          `- ${t('ownerDataHealth')}: ${data.health_score || 0}/100\n` +
          `- ${t('ownerMostProfitableClient')}: ${topProfitText}\n\n` +
          `${t('ownerPriorityActions')}\n` +
          `1. ${t('ownerAction1')}\n` +
          `2. ${t('ownerAction2')}\n` +
          `3. ${t('ownerAction3')}\n`;
        loadMissingReferences();
      } catch (err) {
        document.getElementById('kpi-grid').innerHTML = '<p class="empty-state">' + escapeHtml(t('errorLoadingOwnerDashboard')) + '</p>';
        document.getElementById('missing-refs-wrap').innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('errorLoadingMissingReferences')) + '</p>';
      }
    }

    function managerEndpointFor(type) {
      const locale = window.__REPORTING_LOCALE || 'default';
      const ir = locale === 'ir';
      const uk = locale === 'uk';
      switch (type) {
        case 'balance_sheet':
          if (uk) return '/manager-reports/financial/uk/balance-sheet';
          if (ir) return '/manager-reports/financial/iran/balance-sheet';
          return '/manager-reports/financial/balance-sheet';
        case 'income_statement':
          if (uk) return '/manager-reports/financial/uk/profit-and-loss';
          if (ir) return '/manager-reports/financial/iran/income-statement';
          return '/manager-reports/financial/income-statement';
        case 'changes_in_equity':
          if (uk) return '/manager-reports/financial/uk/changes-in-equity';
          return '/manager-reports/financial/iran/changes-in-equity';
        case 'comprehensive_income':
          if (uk) return '/manager-reports/financial/uk/comprehensive-income';
          return '/manager-reports/financial/iran/comprehensive-income';
        case 'cash_flow':
          if (uk) return '/manager-reports/financial/uk/cash-flow';
          if (ir) return '/manager-reports/financial/iran/cash-flow';
          return '/manager-reports/financial/cash-flow';
        case 'general_journal': return '/manager-reports/books/general-journal';
        case 'general_ledger': return '/manager-reports/books/general-ledger';
        case 'trial_balance': return '/manager-reports/books/trial-balance';
        case 'account_ledger': return '/manager-reports/books/account-ledger/' + encodeURIComponent((mgrAccountCodeEl.value || '1110').trim() || '1110');
        case 'debtor_creditor': return '/manager-reports/operational/debtor-creditor';
        case 'inventory_balance': return '/manager-reports/inventory/balance';
        case 'inventory_movement': return '/manager-reports/inventory/movements';
        case 'sales_by_product': return '/manager-reports/sales/by-product';
        case 'sales_by_invoice': return '/manager-reports/sales/by-invoice';
        case 'purchase_by_product': return '/manager-reports/purchases/by-product';
        case 'purchase_by_invoice': return '/manager-reports/purchases/by-invoice';
        default: return '/manager-reports/financial/balance-sheet';
      }
    }

    function syncManagerFilterLabels() {
      const type = (mgrReportTypeEl.value || '').trim();
      if (type === 'balance_sheet') {
        if (mgrFromLabelEl) mgrFromLabelEl.textContent = t('comparativeAsOf');
        if (mgrToLabelEl) mgrToLabelEl.textContent = t('asOfDate');
      } else {
        if (mgrFromLabelEl) mgrFromLabelEl.textContent = t('labelFrom');
        if (mgrToLabelEl) mgrToLabelEl.textContent = t('labelTo');
      }
    }

    function downloadTextFile(fileName, content, mime = 'text/plain') {
      const blob = new Blob([content], { type: mime });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = fileName;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    function reportToCsv(report) {
      const data = reportToTableData(report);
      if (!data.headers.length) return '';
      const esc = (v) => {
        const s = String(v ?? '');
        if (s.includes(',') || s.includes('"') || s.includes('\n')) return '"' + s.replace(/"/g, '""') + '"';
        return s;
      };
      const lines = [data.headers.map(esc).join(',')];
      data.rows.forEach(r => lines.push(r.map(esc).join(',')));
      return lines.join('\n');
    }

    function reportFileBaseName(report) {
      const type = ((report && report.report_type) || 'report').replace(/[^a-z0-9_\-]+/ig, '-').toLowerCase();
      const p = report && report.period ? report.period : {};
      const to = (p.to || p.to_date || '').replace(/[^0-9\-]/g, '');
      return to ? `${type}-${to}` : type;
    }

    function openReportPrintWindow(report, chartImg = '') {
      const period = reportPeriodText(report);
      const preview = renderReportPreviewHtml(report);
      const w = window.open('', '_blank', 'width=1100,height=900');
      if (!w) {
        showAlert(t('allowPopupsPdf'), true);
        return;
      }
      w.document.write(`
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8">
            <title>${escapeHtml(t('reportExportTitle'))}</title>
            <style>
              body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #0f172a; }
              h1 { margin: 0 0 8px 0; font-size: 22px; }
              .meta { color: #475569; margin-bottom: 12px; font-size: 13px; }
              .mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
              .mini-table th, .mini-table td { border: 1px solid #cbd5e1; padding: 6px; text-align: left; vertical-align: top; word-break: break-word; }
              .mini-table th { background: #f8fafc; }
              .panel { border: 1px solid #cbd5e1; border-radius: 10px; padding: 10px; margin-bottom: 10px; }
              .report-preview-wrap { overflow-x: auto; }
              img { width: 100%; max-width: 880px; margin-top: 12px; border: 1px solid #cbd5e1; border-radius: 8px; }
            </style>
          </head>
          <body>
            <h1>${escapeHtml(localizeDynamicText(report.report_type || t('reportWord')))}</h1>
            <div class="meta">${period ? (escapeHtml(t('periodLabel')) + ': ' + escapeHtml(period)) : ''}</div>
            ${preview}
            ${chartImg ? `<img src="${chartImg}" alt="report chart">` : ''}
          </body>
        </html>
      `);
      w.document.close();
      w.focus();
      setTimeout(() => { w.print(); }, 350);
    }

    // The financial statements from the server (roadmap §4.9): the page's
    // period and currency, every statement the company's template has.
    document.querySelectorAll('.mgr-statements-export').forEach((btn) => btn.addEventListener('click', () => {
      const q = new URLSearchParams({ format: btn.dataset.format });
      const from = document.getElementById('mgr-from-date').value;
      const to = document.getElementById('mgr-to-date').value;
      const ccy = document.getElementById('mgr-currency')?.value;
      if (from) q.set('from_date', from);
      if (to) q.set('to_date', to);
      if (ccy) q.set('currency', ccy);
      window.location.href = API + '/manager-reports/financial/export?' + q.toString();
    }));

    // ═══════ The monthly close pack (roadmap §4.9) ═══════
    // The checklist is read in the UI's language (Persian or English); the
    // pack itself is written in the company's document language.
    async function loadClosePack() {
      if (!onPage('manager')) return;
      const sel = document.getElementById('close-pack-month');
      const list = document.getElementById('close-pack-checklist');
      const sum = document.getElementById('close-pack-summary');
      if (!sel || !list || !sum) return;
      const lang = currentLanguage === 'fa' ? 'fa' : 'en';
      const q = new URLSearchParams({ lang });
      if (sel.value) q.set('month', sel.value);
      let data;
      try {
        const res = await fetch(API + '/manager-reports/close-pack/checklist?' + q.toString());
        if (!res.ok) throw new Error(String(res.status));
        data = await res.json();
      } catch (e) {
        sum.textContent = t('closePackFailed');
        sum.className = 'close-pack-summary warn';
        list.innerHTML = '';
        return;
      }
      if (sel.dataset.lang !== data.lang || !sel.options.length) {
        const picked = sel.value || data.month;
        sel.innerHTML = data.months.map(m => `<option value="${escapeHtml(m.key)}">${escapeHtml(m.label)}</option>`).join('');
        sel.value = [...sel.options].some(o => o.value === picked) ? picked : data.month;
        sel.dataset.lang = data.lang;
      }
      sum.textContent = data.summary;
      sum.className = 'close-pack-summary ' + (data.open ? 'warn' : 'ok');
      const marks = { ok: '✓', warn: '!', info: '•' };
      list.innerHTML = data.items.map(i => `<li class="${escapeHtml(i.state)}" data-key="${escapeHtml(i.key)}">`
        + `<span class="mark" aria-hidden="true">${marks[i.state] || ''}</span>`
        + `<span class="item">${escapeHtml(i.item)}</span><span class="detail">${escapeHtml(i.detail)}</span></li>`).join('');
    }

    function closePackDownload(format) {
      const month = document.getElementById('close-pack-month')?.value;
      const q = new URLSearchParams({ format });
      if (month) q.set('month', month);
      window.location.href = API + '/manager-reports/close-pack?' + q.toString();
    }

    document.getElementById('close-pack-month')?.addEventListener('change', loadClosePack);
    document.getElementById('close-pack-zip')?.addEventListener('click', () => closePackDownload('zip'));
    document.querySelectorAll('.close-pack-file').forEach((btn) => btn.addEventListener('click', () => closePackDownload(btn.dataset.format)));

    function exportManagerReportJson() {
      if (!lastManagerReport) { showAlert(t('runReportFirst'), true); return; }
      const type = (lastManagerReport.report_type || 'report');
      downloadTextFile(`${type}.json`, JSON.stringify(lastManagerReport, null, 2), 'application/json');
    }

    function exportManagerReportCsv() {
      if (!lastManagerReport) { showAlert(t('runReportFirst'), true); return; }
      const csv = reportToCsv(lastManagerReport);
      if (!csv) { showAlert(t('noTabularRowsCsv'), true); return; }
      const type = (lastManagerReport.report_type || 'report');
      downloadTextFile(`${type}.csv`, csv, 'text/csv');
    }

    function exportManagerReportPdf() {
      if (!lastManagerReport) { showAlert(t('runReportFirst'), true); return; }
      const chartImg = (managerReportChart && mgrReportChartEl) ? mgrReportChartEl.toDataURL('image/png') : '';
      openReportPrintWindow(lastManagerReport, chartImg);
    }

    let _extraChartInstances = [];
    function _destroyExtraCharts() {
      _extraChartInstances.forEach(c => { try { c.destroy(); } catch(_){} });
      _extraChartInstances = [];
      const wrap = document.getElementById('mgr-extra-charts');
      const inner = document.getElementById('mgr-extra-charts-inner');
      if (wrap) wrap.style.display = 'none';
      if (inner) inner.innerHTML = '';
    }

    function _addExtraChart(title, chartCfg, onClick) {
      const wrap = document.getElementById('mgr-extra-charts');
      const inner = document.getElementById('mgr-extra-charts-inner');
      if (!wrap || !inner) return;
      wrap.style.display = 'block';
      const panel = document.createElement('div');
      panel.className = 'panel';
      panel.style.cssText = 'padding:1rem; position:relative;';
      const h = document.createElement('h3');
      h.style.cssText = 'margin:0 0 0.5rem;font-size:0.95rem;display:flex;justify-content:space-between;align-items:center;gap:0.5rem;';
      const titleSpan = document.createElement('span');
      titleSpan.textContent = title;
      h.appendChild(titleSpan);
      // If zoom plugin options are present in chartCfg, expose a small reset button
      // and a hint so the user discovers the drag-to-zoom interaction.
      const hasZoom = !!(chartCfg && chartCfg.options && chartCfg.options.plugins && chartCfg.options.plugins.zoom);
      if (hasZoom) {
        const hint = document.createElement('span');
        hint.style.cssText = 'font-size:0.72rem;color:var(--text-muted);font-weight:400;';
        hint.textContent = t('zoomHint');
        h.appendChild(hint);
        const resetBtn = document.createElement('button');
        resetBtn.type = 'button';
        resetBtn.className = 'btn btn-secondary btn-sm';
        resetBtn.style.cssText = 'padding:2px 8px;font-size:0.72rem;';
        resetBtn.textContent = t('btnResetZoom');
        resetBtn.dataset.role = 'reset-zoom';
        h.appendChild(resetBtn);
      }
      const canvasWrap = document.createElement('div');
      canvasWrap.style.cssText = 'position:relative;height:260px;';
      const canvas = document.createElement('canvas');
      canvasWrap.appendChild(canvas);
      panel.appendChild(h);
      panel.appendChild(canvasWrap);
      inner.appendChild(panel);
      if (onClick) chartCfg.options = { ...(chartCfg.options || {}), onClick };
      if (typeof Chart === 'undefined') return null;  // chart lib missing → skip the panel
      const chart = new Chart(canvas, chartCfg);
      _extraChartInstances.push(chart);
      // Wire the reset-zoom button (only present when zoom is enabled).
      const resetBtn = panel.querySelector('button[data-role="reset-zoom"]');
      if (resetBtn) {
        resetBtn.addEventListener('click', () => {
          try { chart.resetZoom && chart.resetZoom(); } catch (_) {}
        });
      }
      return chart;
    }

    // The inputs a report's supplementary charts were drawn for: a redraw in
    // another language fetches the same periods, whatever the form says now.
    function managerChartInputs() {
      return {
        fromDate: mgrFromDateEl.value || '',
        toDate: mgrToDateEl.value || '',
        granularity: mgrPeriodGranularityEl ? mgrPeriodGranularityEl.value : 'monthly',
        chartCurrency: document.getElementById('mgr-currency')?.value || '',
      };
    }

    function renderManagerReportChart(report, inputs) {
      if (!mgrReportChartPanelEl || !mgrReportChartEl) return;
      _destroyExtraCharts();
      const chart = renderReportChart(mgrReportChartEl, report, managerReportChart);
      managerReportChart = chart;
      if (mgrReportChartTitleEl) {
        const spec = makeReportChartSpec(report);
        mgrReportChartTitleEl.textContent = spec ? (spec.title || t('reportChartTitle')) : t('reportChartTitle');
      }
      mgrReportChartPanelEl.style.display = chart ? 'block' : 'none';

      // Supplementary charts per report type
      const rt = (report.report_type || '').toLowerCase();
      const { fromDate, toDate, granularity, chartCurrency } = inputs || managerChartInputs();
      const palette = ['#0f766e', '#0ea5e9', '#eab308', '#f97316', '#8b5cf6', '#ef4444', '#10b981', '#64748b'];

      if (rt === 'balance_sheet') {
        // Trend chart: assets/liabilities/equity over time
        const q = new URLSearchParams();
        if (fromDate) q.set('from_date', fromDate);
        if (toDate) q.set('to_date', toDate);
        q.set('granularity', granularity);
        if (chartCurrency) q.set('currency', chartCurrency);
        fetch(API + '/manager-reports/financial/balance-sheet-periods?' + q.toString())
          .then(r => r.json()).then(data => {
            const periods = data.periods || [];
            if (periods.length < 2) return;
            _addExtraChart(t('chartBalanceSheetTrend'), {
              type: 'line',
              data: {
                labels: periods.map(p => formatPeriodKey(p.period)),
                datasets: [
                  { label: t('legendAssets'), data: periods.map(p => p.assets), borderColor: palette[0], backgroundColor: 'rgba(15,118,110,0.12)', fill: true, tension: 0.3 },
                  { label: t('legendLiabilities'), data: periods.map(p => p.liabilities), borderColor: '#c62828', backgroundColor: 'rgba(198,40,40,0.08)', fill: true, tension: 0.3 },
                  { label: t('legendEquity'), data: periods.map(p => p.equity), borderColor: palette[1], backgroundColor: 'rgba(14,165,233,0.08)', fill: true, tension: 0.3 },
                ]
              },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' }, zoom: zoomPluginOptions() }, scales: { y: { ticks: { callback: v => formatNum(v) } } } }
            }, (e, els) => {
              if (!els.length) return;
              const idx = els[0].index;
              const dsIdx = els[0].datasetIndex;
              const prefixes = ['11,12,13,14,15', '21,22,23,24', '31,32,33'][dsIdx];
              if (prefixes) showTransactionDrilldown(t(['legendAssets', 'legendLiabilities', 'legendEquity'][dsIdx]) + ' — ' + formatPeriodKey(periods[idx].period), { account_code_prefix: prefixes, to_date: periods[idx].date });
            });
            // Net worth trend
            _addExtraChart(t('chartNetWorthOverTime'), {
              type: 'bar',
              data: {
                labels: periods.map(p => formatPeriodKey(p.period)),
                datasets: [{
                  label: t('legendNetWorth'),
                  data: periods.map(p => p.net_worth),
                  backgroundColor: periods.map(p => p.net_worth >= 0 ? 'rgba(15,118,110,0.7)' : 'rgba(198,40,40,0.7)')
                }]
              },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { ticks: { callback: v => formatNum(v) } } } }
            });
          }).catch(() => {});
      }

      if (rt === 'income_statement') {
        // Margin analysis donut
        const totals = report.totals || {};
        const grossProfit = totals.gross_profit || 0;
        const opex = totals.operating_expenses || 0;
        const otherExp = totals.other_expenses || 0;
        const netProfit = totals.net_profit || 0;
        if (grossProfit || opex || otherExp) {
          _addExtraChart(t('chartCostProfitBreakdown'), {
            type: 'doughnut',
            data: {
              labels: [t('legendNetProfit'), t('legendCogs'), t('legendOperatingExpenses'), t('legendOtherExpenses')],
              datasets: [{ label: t('legendBreakdown'), data: [Math.max(0, netProfit), totals.cogs || 0, opex, otherExp], backgroundColor: [palette[0], palette[3], palette[5], palette[4]] }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
          });
        }
        // Revenue vs Expenses trend from sales data
        const q = new URLSearchParams();
        if (fromDate) q.set('from_date', fromDate);
        if (toDate) q.set('to_date', toDate);
        q.set('granularity', granularity);
        if (chartCurrency) q.set('currency', chartCurrency);
        fetch(API + '/manager-reports/sales/trend?' + q.toString())
          .then(r => r.json()).then(data => {
            const periods = data.periods || [];
            if (periods.length < 2) return;
            _addExtraChart(t('chartSalesTrend'), {
              type: 'bar',
              data: {
                labels: periods.map(p => formatPeriodKey(p.period)),
                datasets: [{ label: t('legendSales'), data: periods.map(p => p.sales_amount), backgroundColor: palette[0] }]
              },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { y: { ticks: { callback: v => formatNum(v) } } } }
            });
          }).catch(() => {});
      }

      if (rt === 'cash_flow_statement') {
        // Cash flow periods trend
        const q = new URLSearchParams();
        if (fromDate) q.set('from_date', fromDate);
        if (toDate) q.set('to_date', toDate);
        q.set('granularity', granularity);
        if (chartCurrency) q.set('currency', chartCurrency);
        fetch(API + '/manager-reports/financial/cash-flow-periods?' + q.toString())
          .then(r => r.json()).then(data => {
            const periods = data.periods || [];
            if (periods.length < 2) return;
            _addExtraChart(t('chartCashInOutOverTime'), {
              type: 'bar',
              data: {
                labels: periods.map(p => formatPeriodKey(p.period)),
                datasets: [
                  { label: t('legendInflow'), data: periods.map(p => p.inflow), backgroundColor: 'rgba(15,118,110,0.75)' },
                  { label: t('legendOutflow'), data: periods.map(p => p.outflow), backgroundColor: 'rgba(198,40,40,0.65)' }
                ]
              },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { y: { ticks: { callback: v => formatNum(v) } } } }
            });
            _addExtraChart(t('chartNetCashFlowTrend'), {
              type: 'line',
              data: {
                labels: periods.map(p => formatPeriodKey(p.period)),
                datasets: [{
                  label: t('legendNetCashFlow'),
                  data: periods.map(p => p.net),
                  borderColor: palette[0], backgroundColor: 'rgba(15,118,110,0.12)', fill: true, tension: 0.3,
                  pointBackgroundColor: periods.map(p => p.net >= 0 ? palette[0] : '#c62828')
                }]
              },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, zoom: zoomPluginOptions() }, scales: { y: { ticks: { callback: v => formatNum(v) } } } }
            });
          }).catch(() => {});
      }

      // ──────────── Iran / UK locale-specific extra charts ─────────────
      // Each set: composition donut(s), waterfall / cascading bar, and a
      // time-series trend line where there's a periods endpoint we can
      // re-use. Time-series charts opt into the zoom plugin so the user
      // can drag-select a range to zoom in.
      const _findRow = (key) => (report.rows || []).find(r => r && r.key === key) || null;
      const _amt = (row, field) => (row && row[field] != null) ? Number(row[field]) : 0;

      // Iranian statement rows carry both labels: show the one for the UI's language.
      const _fa = currentLanguage === 'fa';
      const ROW_LABEL = (r) => (r && ((_fa ? r.label_fa : r.label_en) || r.label_fa || r.label_en || r.label || r.key)) || '';

      const _compositionPie = (title, rowKeys, signFn) => {
        const items = rowKeys
          .map(k => _findRow(k))
          .filter(r => r && (r.row_type === 'line'))
          .map(r => ({ label: ROW_LABEL(r), value: signFn ? signFn(_amt(r, 'amount_current')) : Math.abs(_amt(r, 'amount_current')) }))
          .filter(it => it.value > 0);
        if (items.length < 2) return;
        _addExtraChart(title, {
          type: 'doughnut',
          data: {
            labels: items.map(it => it.label),
            datasets: [{ data: items.map(it => it.value), backgroundColor: palette }],
          },
          options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } },
        });
      };

      const _comparisonBar = (title, keys, drilldownPrefix) => {
        const rows = keys.map(k => _findRow(k)).filter(r => r);
        if (!rows.length) return;
        _addExtraChart(title, {
          type: 'bar',
          data: {
            labels: rows.map(ROW_LABEL),
            datasets: [
              { label: t('labelCurrent'), data: rows.map(r => _amt(r, 'amount_current')), backgroundColor: palette[0] },
              { label: t('labelPrior'), data: rows.map(r => _amt(r, 'amount_prior')), backgroundColor: palette[1] },
            ],
          },
          options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { y: { ticks: { callback: v => formatNum(v) } } } },
        }, drilldownPrefix ? (e, els) => {
          if (!els.length) return;
          const row = rows[els[0].index];
          if (row) showTransactionDrilldown(ROW_LABEL(row), { account_code_prefix: drilldownPrefix, from_date: fromDate, to_date: toDate });
        } : undefined);
      };

      // Trend chart factory — used by both Iran and UK BS/IS/CF.
      const _addPeriodsTrend = (endpoint, title, datasetSpecs, yLabel) => {
        const q = new URLSearchParams();
        if (fromDate) q.set('from_date', fromDate);
        if (toDate) q.set('to_date', toDate);
        q.set('granularity', granularity);
        if (chartCurrency) q.set('currency', chartCurrency);
        fetch(API + endpoint + '?' + q.toString())
          .then(r => r.json()).then(data => {
            const periods = data.periods || [];
            if (periods.length < 2) return;
            _addExtraChart(title, {
              type: 'line',
              data: {
                labels: periods.map(p => formatPeriodKey(p.period)),
                datasets: datasetSpecs.map((ds, i) => ({
                  label: ds.label,
                  data: periods.map(p => Number(p[ds.field] || 0)),
                  borderColor: ds.color || palette[i],
                  backgroundColor: ds.bg || `${ds.color || palette[i]}1f`,
                  fill: ds.fill !== false, tension: 0.3,
                })),
              },
              options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: 'bottom' }, zoom: zoomPluginOptions() },
                scales: { y: { ticks: { callback: v => formatNum(v) } } },
              },
            });
          }).catch(() => {});
      };

      // ── Iran / UK Balance Sheet ─────────────────────────────────────
      if (rt === 'iran_balance_sheet' || rt === 'uk_balance_sheet') {
        const isUK = rt === 'uk_balance_sheet';
        // Composition donuts: assets / liabilities / equity
        const assetKeys = isUK
          ? ['fa_intangibles', 'fa_tangibles', 'fa_investments', 'ca_stocks', 'ca_debtors', 'ca_cash']
          : ['ca_cash', 'ca_st_investments', 'ca_trade_receivables', 'ca_inventory', 'ca_prepayments', 'ca_held_for_sale',
             'nca_ppe', 'nca_investment_property', 'nca_intangibles', 'nca_lt_investments', 'nca_lt_receivables'];
        const liabKeys = isUK
          ? ['cl_creditors', 'ncl_creditors', 'ncl_provisions']
          : ['cl_trade_payables', 'cl_tax_payable', 'cl_dividends_payable', 'cl_st_loans', 'cl_provisions', 'cl_advances',
             'ncl_lt_payables', 'ncl_lt_loans', 'ncl_deferred_tax', 'ncl_employee_benefits'];
        const equityKeys = isUK
          ? ['eq_share_capital', 'eq_share_premium', 'eq_revaluation_reserve', 'eq_other_reserves', 'eq_pl_account']
          : ['eq_capital', 'eq_share_premium', 'eq_legal_reserve', 'eq_other_reserves', 'eq_revaluation_surplus', 'eq_retained_earnings', 'eq_treasury_stock'];
        _compositionPie(t('chartAssetComposition'), assetKeys);
        _compositionPie(t('chartLiabilityComposition'), liabKeys);
        _compositionPie(t('chartEquityComposition'), equityKeys);
        // Trend across periods (uses default-locale endpoint; both locales share the chart of accounts ranges via classify_account_code).
        _addPeriodsTrend('/manager-reports/financial/balance-sheet-periods', t('chartBSTrend'),
          [
            { field: 'assets', label: t('legendAssets'), color: palette[0] },
            { field: 'liabilities', label: t('legendLiabilities'), color: '#c62828' },
            { field: 'equity', label: t('legendEquity'), color: palette[1] },
          ]);
      }

      // ── Iran / UK Income Statement / Profit and Loss ────────────────
      if (rt === 'iran_income_statement' || rt === 'uk_profit_and_loss') {
        const isUK = rt === 'uk_profit_and_loss';
        // Profit waterfall: gross → operating → before-tax → net (already done as the primary chart)
        // Additional: expense breakdown donut
        const expenseKeys = isUK
          ? ['cost_of_sales', 'distribution_costs', 'admin_expenses', 'interest_payable', 'tax_on_profit']
          : ['cogs', 'opex_sga', 'impairment_receivables', 'other_operating_expenses', 'financial_expenses', 'tax_current_year', 'tax_prior_years'];
        _compositionPie(t('chartExpenseComposition'), expenseKeys, v => Math.abs(v));
        // Revenue / income comparison
        const revKeys = isUK ? ['turnover', 'other_operating_income', 'investment_income', 'interest_receivable']
                              : ['revenue_operating', 'other_operating_income', 'non_operating_net'];
        _comparisonBar(t('chartRevenueLines'), revKeys, isUK ? '4' : '41,42,43');
        // Sales trend (period-aware): only if we have a sales endpoint — works for any locale
        _addPeriodsTrend('/manager-reports/sales/trend', t('chartSalesTrend'),
          [{ field: 'sales_amount', label: t('legendSales'), color: palette[0] }]);
      }

      // ── Iran / UK Cash Flow ─────────────────────────────────────────
      if (rt === 'iran_cash_flow' || rt === 'uk_cash_flow') {
        const isUK = rt === 'uk_cash_flow';
        // Cash reconciliation waterfall
        const recRows = ['opening_cash',
                         isUK ? 'operating_net' : 'operating_net',
                         isUK ? 'investing_net' : 'investing_net',
                         isUK ? 'financing_net' : 'financing_net',
                         isUK ? 'fx_effect' : 'fx_rate_effect',
                         isUK ? 'closing_cash' : 'closing_cash'].map(k => _findRow(k)).filter(r => r);
        if (recRows.length >= 4) {
          _addExtraChart(t('chartCashReconciliation'), {
            type: 'bar',
            data: {
              labels: recRows.map(r => _localizeDatesInLabel(ROW_LABEL(r))),
              datasets: [{
                label: t('legendMovement'),
                data: recRows.map(r => _amt(r, 'amount_current')),
                backgroundColor: recRows.map(r => _amt(r, 'amount_current') >= 0 ? palette[0] : '#c62828'),
              }],
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { ticks: { callback: v => formatNum(v) } } } },
          });
        }
        // Investing breakdown
        const invKeys = isUK
          ? ['inv_ppe_inflow', 'inv_ppe_outflow', 'inv_intangibles_inflow', 'inv_intangibles_outflow', 'inv_investments_inflow', 'inv_investments_outflow']
          : ['inv_ppe_inflow', 'inv_ppe_outflow', 'inv_intangibles_inflow', 'inv_intangibles_outflow',
             'inv_lt_investments_inflow', 'inv_lt_investments_outflow', 'inv_st_investments_inflow', 'inv_st_investments_outflow',
             'inv_loans_to_others_outflow', 'inv_loans_to_others_inflow'];
        _compositionPie(t('chartInvestingActivity'), invKeys, v => Math.abs(v));
        // Financing breakdown
        const finKeys = isUK
          ? ['fin_share_capital_inflow', 'fin_share_premium_inflow', 'fin_borrowings_inflow', 'fin_borrowings_outflow', 'fin_lease_outflow', 'fin_dividends_outflow']
          : ['fin_capital_inflow', 'fin_share_premium_inflow', 'fin_st_loans_inflow', 'fin_st_loans_outflow',
             'fin_loans_interest_outflow_placeholder', 'fin_dividends_outflow'];
        _compositionPie(t('chartFinancingActivity'), finKeys, v => Math.abs(v));
        // Period trend: inflow / outflow / net using the default cash-flow-periods endpoint
        _addPeriodsTrend('/manager-reports/financial/cash-flow-periods', t('chartCashFlowOverTime'),
          [
            { field: 'inflow', label: t('legendInflow'), color: palette[0] },
            { field: 'outflow', label: t('legendOutflow'), color: '#c62828' },
            { field: 'net', label: t('legendNet'), color: palette[1] },
          ]);
      }

      // ── Iran / UK Comprehensive Income ──────────────────────────────
      if (rt === 'iran_comprehensive_income' || rt === 'uk_comprehensive_income') {
        const npKey = rt === 'uk_comprehensive_income' ? 'profit_for_year' : 'net_profit';
        const ociKey = 'oci_total';
        const totalKey = rt === 'uk_comprehensive_income' ? 'total_comprehensive_income' : 'comprehensive_income';
        _comparisonBar(t('chartNpOciTotal'), [npKey, ociKey, totalKey]);
      }

      // ── Iran / UK Changes in Equity ─────────────────────────────────
      if (rt === 'iran_changes_in_equity' || rt === 'uk_changes_in_equity') {
        // Stacked bar showing equity-component balances at opening vs closing.
        const findEqRow = (key) => (report.rows || []).find(r => r && r.key === key);
        const openingRow = findEqRow(rt.startsWith('uk') ? 'opening' : 'opening_balance');
        const closingRow = findEqRow(rt.startsWith('uk') ? 'closing' : 'closing_balance');
        const components = report.components || [];
        if (openingRow && closingRow && components.length) {
          const cellsOf = (row) => Object.fromEntries((row.cells || []).map(c => [c.component, c.amount]));
          const openCells = cellsOf(openingRow);
          const closeCells = cellsOf(closingRow);
          _addExtraChart(t('chartEquityComponents'), {
            type: 'bar',
            data: {
              labels: components.map(c => (_fa ? c.label_fa : c.label_en) || c.label_fa || c.label || c.key),
              datasets: [
                { label: t('labelOpening'), data: components.map(c => Number(openCells[c.key] || 0)), backgroundColor: palette[1] },
                { label: t('labelClosing'), data: components.map(c => Number(closeCells[c.key] || 0)), backgroundColor: palette[0] },
              ],
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { y: { ticks: { callback: v => formatNum(v) } } } },
          });
        }
      }

      if (rt === 'trial_balance' || rt === 'general_ledger') {
        // Top debit vs credit accounts horizontal bar
        const rows = (report.rows || []).slice().sort((a, b) => (b.debit_turnover + b.credit_turnover) - (a.debit_turnover + a.credit_turnover)).slice(0, 15);
        if (rows.length > 3) {
          _addExtraChart(t('chartTopAccountsDrCr'), {
            type: 'bar',
            data: {
              labels: rows.map(r => r.account_code + ' ' + (r.account_name || '').slice(0, 20)),
              datasets: [
                { label: t('tableDebit'), data: rows.map(r => r.debit_turnover || 0), backgroundColor: palette[0] },
                { label: t('tableCredit'), data: rows.map(r => r.credit_turnover || 0), backgroundColor: palette[1] }
              ]
            },
            options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { x: { ticks: { callback: v => formatNum(v) } } } }
          }, (e, els) => {
            if (!els.length) return;
            const row = rows[els[0].index];
            if (row && row.account_code) showTransactionDrilldown(row.account_name || row.account_code, { account_code: row.account_code, from_date: fromDate, to_date: toDate });
          });
          // Net balance bar
          _addExtraChart(t('chartNetBalanceByAccount'), {
            type: 'bar',
            data: {
              labels: rows.map(r => r.account_code),
              datasets: [{
                label: t('legendNetBalance'),
                data: rows.map(r => (r.debit_balance || 0) - (r.credit_balance || 0)),
                backgroundColor: rows.map(r => ((r.debit_balance || 0) - (r.credit_balance || 0)) >= 0 ? palette[0] : '#c62828')
              }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { ticks: { callback: v => formatNum(v) } } } }
          }, (e, els) => {
            if (!els.length) return;
            const row = rows[els[0].index];
            if (row && row.account_code) showTransactionDrilldown(row.account_name || row.account_code, { account_code: row.account_code, from_date: fromDate, to_date: toDate });
          });
        }
      }

      if (rt.includes('sales') || rt.includes('purchase')) {
        // Sales/Purchase trend over time
        const q = new URLSearchParams();
        if (fromDate) q.set('from_date', fromDate);
        if (toDate) q.set('to_date', toDate);
        q.set('granularity', granularity);
        const filterVal = mgrProductFilterEl ? (mgrProductFilterEl.value || '').trim() : '';
        if (filterVal) q.set('product_name', filterVal);
        fetch(API + '/manager-reports/sales/trend?' + q.toString())
          .then(r => r.json()).then(data => {
            const periods = data.periods || [];
            if (periods.length < 2) return;
            const purchase = rt.includes('purchase');
            _addExtraChart(t(purchase ? 'chartPurchaseTrendOverTime' : 'chartSalesTrendOverTime') + (filterVal ? ' — ' + filterVal : ''), {
              type: 'bar',
              data: {
                labels: periods.map(p => formatPeriodKey(p.period)),
                datasets: [
                  { label: t(purchase ? 'legendPurchaseAmount' : 'legendSalesAmount'), data: periods.map(p => p.sales_amount), backgroundColor: palette[0], yAxisID: 'y' },
                  { label: t('legendQuantity'), data: periods.map(p => p.quantity), type: 'line', borderColor: palette[3], backgroundColor: 'transparent', yAxisID: 'y1', tension: 0.3 }
                ]
              },
              options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: 'bottom' } },
                scales: { y: { position: 'left', ticks: { callback: v => formatNum(v) } }, y1: { position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: t('axisQty') } } }
              }
            });
          }).catch(() => {});
      }

      if (rt === 'debtor_creditor') {
        // Debtors vs Creditors donut — the report lists each side with its aging
        // and total (it used to be one "rows" list with a net_delta; the charts
        // kept reading that and never drew).
        const debtors = (report.debtors || []).map(r => ({ ...r, role: 'debtor', net_delta: r.total || 0 }));
        const creditors = (report.creditors || []).map(r => ({ ...r, role: 'creditor', net_delta: -(r.total || 0) }));
        const rows = [...debtors, ...creditors];
        const totalDebt = debtors.reduce((s, r) => s + Math.abs(r.net_delta || 0), 0);
        const totalCred = creditors.reduce((s, r) => s + Math.abs(r.net_delta || 0), 0);
        if (totalDebt || totalCred) {
          _addExtraChart(t('chartReceivablesVsPayables'), {
            type: 'doughnut',
            data: {
              labels: [t('legendReceivablesDebtors'), t('legendPayablesCreditors')],
              datasets: [{ label: t('legendArVsAp'), data: [totalDebt, totalCred], backgroundColor: [palette[0], palette[5]] }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
          });
        }
        // Top entities
        const topEntities = rows.slice().sort((a, b) => Math.abs(b.net_delta || 0) - Math.abs(a.net_delta || 0)).slice(0, 10);
        if (topEntities.length > 2) {
          _addExtraChart(t('chartTopEntities'), {
            type: 'bar',
            data: {
              labels: topEntities.map(r => (r.entity_name || t('labelUnknownEntity')).slice(0, 20)),
              datasets: [{
                label: t('legendNetAmount'),
                data: topEntities.map(r => r.net_delta || 0),
                backgroundColor: topEntities.map(r => r.role === 'debtor' ? palette[0] : palette[5])
              }]
            },
            options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { callback: v => formatNum(v) } } } }
          });
        }
      }
    }

    function renderInventoryReportChart(report) {
      if (!invReportChartPanelEl || !invReportChartEl) return;
      const chart = renderReportChart(invReportChartEl, report, inventoryReportChart);
      inventoryReportChart = chart;
      if (invReportChartTitleEl) {
        const spec = makeReportChartSpec(report);
        invReportChartTitleEl.textContent = spec ? (spec.title || t('inventoryChartTitle')) : t('inventoryChartTitle');
      }
      invReportChartPanelEl.style.display = chart ? 'block' : 'none';
    }

    async function runInventoryReport(type) {
      try {
        const endpoint = type === 'movement'
          ? '/manager-reports/inventory/movements'
          : '/manager-reports/inventory/balance';
        const q = new URLSearchParams();
        if (type === 'movement') {
          if (invFromDateEl && invFromDateEl.value) q.set('from_date', invFromDateEl.value);
          if (invToDateEl && invToDateEl.value) q.set('to_date', invToDateEl.value);
          q.set('page', '1');
          q.set('page_size', '500');
        } else {
          if (invToDateEl && invToDateEl.value) q.set('to_date', invToDateEl.value);
        }
        const url = API + endpoint + (q.toString() ? ('?' + q.toString()) : '');
        if (invRunBalanceBtn) invRunBalanceBtn.disabled = true;
        if (invRunMovementBtn) invRunMovementBtn.disabled = true;
        const res = await fetch(url);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || t('failedRunInventoryReport'), true);
          return;
        }
        lastInventoryReport = data;
        if (invReportPreviewEl) {
          if (type === 'balance') {
            _renderInventoryBalanceReport(data);
          } else {
            _renderInventoryMovementReport(data);
          }
        }
        if (invReportJsonEl) invReportJsonEl.textContent = JSON.stringify(data, null, 2);
        renderInventoryReportChart(data);
        if (invExportJsonBtn) invExportJsonBtn.disabled = false;
        if (invExportCsvBtn) invExportCsvBtn.disabled = false;
        if (invExportPdfBtn) invExportPdfBtn.disabled = false;
      } catch (err) {
        showAlert(t('inventoryReportError') + ': ' + err.message, true);
      } finally {
        if (invRunBalanceBtn) invRunBalanceBtn.disabled = false;
        if (invRunMovementBtn) invRunMovementBtn.disabled = false;
      }
    }

    function _renderInventoryBalanceReport(data) {
      const rows = data.rows || [];
      const totals = data.totals || {};
      const period = reportPeriodText(data);
      if (!rows.length) { invReportPreviewEl.innerHTML = '<p style="color:var(--text-muted);padding:0.5rem;">' + t('noDataYet') + '</p>'; return; }

      const totalValue = rows.reduce((s, r) => s + (r.inventory_value || 0), 0);
      const totalCOGS = rows.reduce((s, r) => s + (r.cogs || 0), 0);
      const totalQty = rows.reduce((s, r) => s + (r.on_hand_qty || 0), 0);

      invReportPreviewEl.innerHTML = `
        ${period ? `<div class="report-meta" style="margin-bottom:0.75rem;">${escapeHtml(t('periodLabel'))}: ${escapeHtml(period)}</div>` : ''}
        <div class="detail-summary" style="margin-bottom:1rem;">
          <div><span>Total Items</span><strong>${rows.length}</strong></div>
          <div><span>Total On-Hand</span><strong>${totalQty.toLocaleString()}</strong></div>
          <div><span>Inventory Value</span><strong>${formatNum(totalValue)} ${currencyUnit()}</strong></div>
          <div><span>Total COGS</span><strong>${formatNum(totalCOGS)} ${currencyUnit()}</strong></div>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-bottom:0.5rem;">
          <button type="button" class="btn btn-secondary btn-sm" data-action="export-table" data-target="inv-report-preview" data-name="Inventory_Balance" data-format="csv">CSV</button>
          <button type="button" class="btn btn-secondary btn-sm" data-action="export-table" data-target="inv-report-preview" data-name="Inventory_Balance" data-format="pdf">PDF</button>
        </div>
        <div style="max-height:400px;overflow:auto;">
        <table class="detail-table">
          <thead><tr>
            <th>Item</th><th>SKU</th><th>Unit</th><th class="num">In</th><th class="num">Out</th>
            <th class="num">On Hand</th><th class="num">Avg Cost</th><th class="num">Value</th><th class="num">COGS</th>
          </tr></thead>
          <tbody>${rows.map(r => {
            const valPct = totalValue > 0 ? Math.round(r.inventory_value / totalValue * 100) : 0;
            return `<tr>
              <td><strong>${escapeHtml(r.item_name)}</strong></td>
              <td>${escapeHtml(r.sku || '—')}</td>
              <td>${escapeHtml(r.unit || 'unit')}</td>
              <td class="num">${r.qty_in.toLocaleString()}</td>
              <td class="num">${r.qty_out.toLocaleString()}</td>
              <td class="num" style="font-weight:600;">${r.on_hand_qty.toLocaleString()}</td>
              <td class="num">${formatNum(r.average_cost)}</td>
              <td class="num">
                <div style="display:flex;align-items:center;gap:0.4rem;justify-content:flex-end;">
                  ${formatNum(r.inventory_value)}
                  <span style="display:inline-block;width:40px;height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden;">
                    <span style="display:block;height:100%;width:${valPct}%;background:var(--primary);border-radius:3px;"></span>
                  </span>
                </div>
              </td>
              <td class="num" style="color:${r.cogs > 0 ? '#c62828' : 'var(--text)'};">${formatNum(r.cogs)}</td>
            </tr>`;
          }).join('')}</tbody>
          <tfoot><tr style="font-weight:700;background:#f1f5f9;">
            <td colspan="3">Total</td>
            <td class="num">${rows.reduce((s,r)=>s+r.qty_in,0).toLocaleString()}</td>
            <td class="num">${rows.reduce((s,r)=>s+r.qty_out,0).toLocaleString()}</td>
            <td class="num">${totalQty.toLocaleString()}</td>
            <td class="num">—</td>
            <td class="num">${formatNum(totalValue)}</td>
            <td class="num" style="color:#c62828;">${formatNum(totalCOGS)}</td>
          </tr></tfoot>
        </table>
        </div>
      `;
    }

    function _renderInventoryMovementReport(data) {
      const rows = data.rows || [];
      const period = reportPeriodText(data);
      if (!rows.length) { invReportPreviewEl.innerHTML = '<p style="color:var(--text-muted);padding:0.5rem;">' + t('noDataYet') + '</p>'; return; }

      const totalIn = rows.filter(r => r.movement_type === 'IN').reduce((s, r) => s + r.movement_value, 0);
      const totalOut = rows.filter(r => r.movement_type === 'OUT').reduce((s, r) => s + r.movement_value, 0);
      const qtyIn = rows.filter(r => r.movement_type === 'IN').reduce((s, r) => s + r.quantity, 0);
      const qtyOut = rows.filter(r => r.movement_type === 'OUT').reduce((s, r) => s + r.quantity, 0);

      // Group by item for summary
      const byItem = {};
      rows.forEach(r => {
        if (!byItem[r.item_name]) byItem[r.item_name] = { in: 0, out: 0, adj: 0, value: 0 };
        if (r.movement_type === 'IN') { byItem[r.item_name].in += r.quantity; byItem[r.item_name].value += r.movement_value; }
        else if (r.movement_type === 'OUT') { byItem[r.item_name].out += r.quantity; byItem[r.item_name].value -= r.movement_value; }
        else byItem[r.item_name].adj += r.quantity;
      });

      const typeColor = t => t === 'IN' ? '#2e7d32' : t === 'OUT' ? '#c62828' : '#e65100';
      const typeBg = t => t === 'IN' ? '#dcfce7' : t === 'OUT' ? '#fee2e2' : '#fff3e0';

      invReportPreviewEl.innerHTML = `
        ${period ? `<div class="report-meta" style="margin-bottom:0.75rem;">${escapeHtml(t('periodLabel'))}: ${escapeHtml(period)}</div>` : ''}
        <div class="detail-summary" style="margin-bottom:1rem;">
          <div><span>Total Movements</span><strong>${rows.length}</strong></div>
          <div><span>Qty In</span><strong style="color:#2e7d32;">+${qtyIn.toLocaleString()}</strong></div>
          <div><span>Qty Out</span><strong style="color:#c62828;">-${qtyOut.toLocaleString()}</strong></div>
          <div><span>Value In</span><strong style="color:#2e7d32;">${formatNum(totalIn)}</strong></div>
          <div><span>Value Out</span><strong style="color:#c62828;">${formatNum(totalOut)}</strong></div>
        </div>

        <h4 style="margin:0.75rem 0 0.3rem;font-size:0.9rem;">Summary by Item</h4>
        <div style="max-height:180px;overflow:auto;margin-bottom:1rem;">
        <table class="mini-table"><thead><tr><th>Item</th><th class="num">In</th><th class="num">Out</th><th class="num">Adj</th><th class="num">Net Value</th></tr></thead>
          <tbody>${Object.entries(byItem).map(([name, v]) => `<tr>
            <td><strong>${escapeHtml(name)}</strong></td>
            <td class="num" style="color:#2e7d32;">+${v.in.toLocaleString()}</td>
            <td class="num" style="color:#c62828;">-${v.out.toLocaleString()}</td>
            <td class="num">${v.adj.toLocaleString()}</td>
            <td class="num" style="font-weight:600;">${formatNum(v.value)}</td>
          </tr>`).join('')}</tbody>
        </table>
        </div>

        <h4 style="margin:0.75rem 0 0.3rem;font-size:0.9rem;">Movement Log</h4>
        <div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-bottom:0.5rem;">
          <input type="text" id="inv-mv-search" placeholder="Search movements..." style="width:200px;padding:0.35rem 0.6rem;font-size:0.85rem;margin:0;">
          <button type="button" class="btn btn-secondary btn-sm" data-action="export-table" data-target="inv-report-preview" data-name="Inventory_Movements" data-format="csv">CSV</button>
          <button type="button" class="btn btn-secondary btn-sm" data-action="export-table" data-target="inv-report-preview" data-name="Inventory_Movements" data-format="pdf">PDF</button>
        </div>
        <div id="inv-mv-table-wrap" style="max-height:350px;overflow:auto;">
        <table class="detail-table"><thead><tr>
          <th>${t('labelDate')}</th><th>Item</th><th>Type</th><th class="num">${t('labelQuantity')}</th>
          <th class="num">Unit Cost</th><th class="num">Value</th><th>${t('labelReference')}</th><th>${t('labelDescription')}</th>
        </tr></thead>
          <tbody>${rows.map(r => `<tr>
            <td>${escapeHtml(r.movement_date)}</td>
            <td><strong>${escapeHtml(r.item_name)}</strong></td>
            <td><span style="display:inline-block;padding:0.15rem 0.5rem;border-radius:4px;font-size:0.78rem;font-weight:600;color:${typeColor(r.movement_type)};background:${typeBg(r.movement_type)};">${escapeHtml(r.movement_type)}</span></td>
            <td class="num">${r.quantity.toLocaleString()}</td>
            <td class="num">${formatNum(r.unit_cost)}</td>
            <td class="num" style="font-weight:600;">${formatNum(r.movement_value)}</td>
            <td>${escapeHtml(r.reference || '—')}</td>
            <td>${escapeHtml(r.description || '—')}</td>
          </tr>`).join('')}</tbody>
        </table>
        </div>
      `;

      // Wire up movement search
      const searchEl = document.getElementById('inv-mv-search');
      if (searchEl) {
        searchEl.oninput = () => {
          const q = searchEl.value.trim().toLowerCase();
          const tableRows = document.querySelectorAll('#inv-mv-table-wrap tbody tr');
          tableRows.forEach(tr => {
            tr.style.display = !q || tr.textContent.toLowerCase().includes(q) ? '' : 'none';
          });
        };
      }
    }

    function exportInventoryReportJson() {
      if (!lastInventoryReport) { showAlert(t('runInventoryReportFirst'), true); return; }
      const fileName = reportFileBaseName(lastInventoryReport) + '.json';
      downloadTextFile(fileName, JSON.stringify(lastInventoryReport, null, 2), 'application/json');
    }

    function exportInventoryReportCsv() {
      if (!lastInventoryReport) { showAlert(t('runInventoryReportFirst'), true); return; }
      const csv = reportToCsv(lastInventoryReport);
      if (!csv) { showAlert(t('noTabularRowsCsv'), true); return; }
      const fileName = reportFileBaseName(lastInventoryReport) + '.csv';
      downloadTextFile(fileName, csv, 'text/csv');
    }

    function exportInventoryReportPdf() {
      if (!lastInventoryReport) { showAlert(t('runInventoryReportFirst'), true); return; }
      const chartImg = (inventoryReportChart && invReportChartEl) ? invReportChartEl.toDataURL('image/png') : '';
      openReportPrintWindow(lastInventoryReport, chartImg);
    }

    async function runManagerReport() {
      try {
        const type = (mgrReportTypeEl.value || '').trim();
        const endpoint = managerEndpointFor(type);
        const ir = (window.__REPORTING_LOCALE === 'ir');
        const q = new URLSearchParams();
        if (type === 'balance_sheet' && ir) {
          // Iranian balance-sheet endpoint uses `as_of` / `comparative_as_of`.
          if (mgrToDateEl.value) q.set('as_of', mgrToDateEl.value);
          if (mgrFromDateEl.value) q.set('comparative_as_of', mgrFromDateEl.value);
        } else if (type === 'balance_sheet') {
          if (mgrToDateEl.value) q.set('to_date', mgrToDateEl.value);
          if (mgrFromDateEl.value) q.set('comparative_to_date', mgrFromDateEl.value);
        } else {
          if (mgrFromDateEl.value) q.set('from_date', mgrFromDateEl.value);
          if (mgrToDateEl.value) q.set('to_date', mgrToDateEl.value);
        }
        const currencyEl = document.getElementById('mgr-currency');
        const currency = currencyEl ? currencyEl.value : '';
        if (currency) q.set('currency', currency);
        q.set('page', '1');
        q.set('page_size', '120');
        const url = API + endpoint + (q.toString() ? ('?' + q.toString()) : '');
        const chartInputs = managerChartInputs();
        mgrRunBtn.disabled = true;
        const res = await fetch(url);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || t('failedRunManagerReport'), true);
          return;
        }
        lastManagerReport = data;
        renderManagerReport(data, currency, chartInputs);
        if (mgrExportJsonBtn) mgrExportJsonBtn.disabled = false;
        if (mgrExportCsvBtn) mgrExportCsvBtn.disabled = false;
        if (mgrExportPdfBtn) mgrExportPdfBtn.disabled = false;
      } catch (err) {
        showAlert(t('managerReportError') + ': ' + err.message, true);
      } finally {
        mgrRunBtn.disabled = false;
      }
    }

    // What the manager page shows: the report, its currency and chart inputs,
    // kept so a language change can draw it again (mgrRelocalize).
    let _mgrShown = null;

    function renderManagerReport(data, currency, chartInputs) {
      _mgrShown = { data, currency, chartInputs };
      const period = reportPeriodText(data);
      // When no currency filter is selected and data spans multiple currencies,
      // show a banner warning that numbers are summed across currencies and
      // offer a one-click switch to a single-currency view.
      let mixWarning = '';
      if (currency === 'ALL') {
        const others = Array.isArray(data.other_currencies) ? data.other_currencies : [];
        const buttons = others.map(ccy => `<button type="button" class="btn btn-secondary btn-sm mgr-ccy-switch" data-ccy="${escapeHtml(ccy)}">${escapeHtml(t('currencyViewOnly').replace('{currency}', ccy))}</button>`).join(' ');
        mixWarning = `<div class="report-meta" style="margin-bottom:0.6rem;">${escapeHtml(baseViewNoteText())}
            ${buttons ? `<div style="margin-top:0.35rem;display:flex;gap:0.35rem;flex-wrap:wrap;">${buttons}</div>` : ''}
          </div>`;
      } else if (!currency && data && data.currency) {
        // Server resolved a single-currency view (reporting currency by
        // default): say which, and offer the other currencies separately.
        const others = Array.isArray(data.other_currencies) ? data.other_currencies : [];
        if (others.length) {
          const buttons = others.map(ccy => `<button type="button" class="btn btn-secondary btn-sm mgr-ccy-switch" data-ccy="${escapeHtml(ccy)}">${escapeHtml(t('currencyViewOnly').replace('{currency}', ccy))}</button>`).join(' ');
          mixWarning = `<div class="report-meta" style="margin-bottom:0.6rem;">${escapeHtml(t('currencyViewNote').replace('{currency}', data.currency).replace('{others}', others.join(', ')))}
            <div style="margin-top:0.35rem;display:flex;gap:0.35rem;flex-wrap:wrap;">${buttons}</div>
          </div>`;
        }
      } else if (!currency) {
        const meta = window.__FX_META;
        const used = (meta && Array.isArray(meta.used_currencies)) ? meta.used_currencies : [];
        if (used.length > 1) {
          const buttons = used.map(ccy => `<button type="button" class="btn btn-secondary btn-sm mgr-ccy-switch" data-ccy="${escapeHtml(ccy)}"><span class="ccy-badge ccy-${escapeHtml(ccy)}">${escapeHtml(ccy)}</span> only</button>`).join(' ');
          mixWarning = `<div style="background:#fef3c7;border:1px solid #fcd34d;color:#92400e;padding:0.55rem 0.75rem;border-radius:8px;margin-bottom:0.6rem;font-size:0.85rem;">
            ⚠️ No currency filter selected. Numbers below sum ${used.join(', ')} as raw integers, which is not meaningful. Pick a currency:
            <div style="margin-top:0.35rem;display:flex;gap:0.35rem;flex-wrap:wrap;">${buttons}</div>
          </div>`;
        }
      }
      mgrReportPreviewEl.innerHTML = `
        ${mixWarning}
        ${period ? `<div class="report-meta">${escapeHtml(t('periodLabel'))}: ${escapeHtml(period)}${currency ? ' · <span class="ccy-badge ccy-' + escapeHtml(currency) + '">' + escapeHtml(currency === 'ALL' ? t('currencyAllInBase').replace('{base}', baseCurrencyCode()) : currency) + '</span>' : ''}</div>` : ''}
        ${renderReportPreviewHtml(data)}
      `;
      // Wire up the "switch currency" buttons in the warning banner
      mgrReportPreviewEl.querySelectorAll('.mgr-ccy-switch').forEach(b => {
        b.addEventListener('click', () => {
          const ccy = b.dataset.ccy;
          const sel = document.getElementById('mgr-currency');
          if (sel && ccy) {
            sel.value = ccy;
            runManagerReport();
          }
        });
      });
      mgrReportJsonEl.textContent = JSON.stringify(data, null, 2);
      renderManagerReportChart(data, chartInputs);
    }

    // Called by applyLanguage: the report on screen, drawn again in the new
    // language (titles, legends, statement row labels, period names). Not a
    // report another panel put on the page since (the payables view).
    function mgrRelocalize() {
      if (!_mgrShown || lastManagerReport !== _mgrShown.data || !mgrReportPreviewEl) return;
      renderManagerReport(_mgrShown.data, _mgrShown.currency, _mgrShown.chartInputs);
    }

    async function loadManagerInventoryItems(highlightId) {
      if (!onPage('inventory')) return;
      if (typeof highlightId !== 'string' && typeof highlightId !== 'number') highlightId = null;
      if (!mgrMvItemEl) return;
      try {
        const res = await fetch(API + '/manager-reports/inventory/items');
        const rows = await res.json().catch(() => ([]));
        mgrMvItemEl.innerHTML = `<option value="">${escapeHtml(t('optionSelectItem'))}</option>`;
        (rows || []).forEach(i => {
          const opt = document.createElement('option');
          opt.value = i.id;
          opt.textContent = (i.sku ? (i.sku + ' - ') : '') + i.name;
          mgrMvItemEl.appendChild(opt);
        });
        // Render items list
        const listEl = document.getElementById('inv-items-list');
        if (listEl && rows.length) {
          listEl.innerHTML = `<div style="font-size:0.82rem;color:var(--text-muted);margin-bottom:0.3rem;">${escapeHtml(tf('invItemsRegistered', { n: formatNum(rows.length) }))}</div>
          <div style="display:flex;flex-wrap:wrap;gap:0.4rem;">${rows.map(i =>
            `<span data-item-id="${escapeHtml(String(i.id))}" style="display:inline-flex;align-items:center;gap:0.3rem;padding:0.2rem 0.6rem;background:#f1f5f9;border-radius:6px;font-size:0.8rem;border:1px solid var(--border);">
              <strong>${escapeHtml(i.name)}</strong>${i.sku ? ` <span style="color:var(--text-muted);">(${escapeHtml(i.sku)})</span>` : ''}${i.barcode ? ` <span style="color:var(--text-muted);" dir="ltr">▮ ${escapeHtml(i.barcode)}</span>` : ''}
              ${i.list_price ? ` — ${formatNum(i.list_price)} ${currencyUnit()}` : ''}
            </span>`
          ).join('')}</div>`;
          if (highlightId) flashRow(listEl.querySelector('[data-item-id="' + CSS.escape(String(highlightId)) + '"]'));
        } else if (listEl) {
          listEl.innerHTML = '<div style="font-size:0.82rem;color:var(--text-muted);">' + escapeHtml(t('invNoItemsYet')) + '</div>';
        }
      } catch (_) {}
    }

    async function addManagerInventoryItem() {
      const name = (mgrInvItemNameEl.value || '').trim();
      if (!name) {
        showAlert('Inventory item name is required.', true);
        return;
      }
      try {
        mgrAddItemBtn.disabled = true;
        const res = await fetch(API + '/manager-reports/inventory/items', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name,
            sku: (mgrInvItemSkuEl.value || '').trim() || null,
            unit: (mgrInvItemUnitEl.value || 'unit').trim() || 'unit',
            barcode: (document.getElementById('mgr-inv-item-barcode')?.value || '').trim() || null,
            reorder_level: document.getElementById('mgr-inv-item-reorder')?.value === ''
              ? null : Number(document.getElementById('mgr-inv-item-reorder')?.value || 0)
          })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || 'Failed to add inventory item.', true);
          return;
        }
        mgrInvItemNameEl.value = '';
        mgrInvItemSkuEl.value = '';
        ['mgr-inv-item-barcode', 'mgr-inv-item-reorder'].forEach((id) => { const el = document.getElementById(id); if (el) el.value = ''; });
        showAlert('Inventory item added.');
        await loadManagerInventoryItems(data.id);
        if (typeof loadStockPanel === 'function') loadStockPanel();
      } catch (err) {
        showAlert('Error adding inventory item: ' + err.message, true);
      } finally {
        mgrAddItemBtn.disabled = false;
      }
    }

    async function addManagerInventoryMovement() {
      const itemId = (mgrMvItemEl.value || '').trim();
      if (!itemId) {
        showAlert('Select inventory item first.', true);
        return;
      }
      try {
        mgrAddMvBtn.disabled = true;
        const payload = {
          item_id: itemId,
          movement_date: (
            (invToDateEl && invToDateEl.value)
            || (mgrToDateEl && mgrToDateEl.value)
            || new Date().toISOString().slice(0, 10)
          ),
          movement_type: (mgrMvTypeEl.value || 'IN'),
          quantity: parseFloat(mgrMvQtyEl.value || '0'),
          unit_cost: parseInt(mgrMvCostEl.value || '0', 10) || 0
        };
        const res = await fetch(API + '/manager-reports/inventory/movements', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || 'Failed to add inventory movement.', true);
          return;
        }
        showAlert('Inventory movement added.');
        if (typeof stockShowValuation === 'function') stockShowValuation().catch(() => {});
      } catch (err) {
        showAlert('Error adding movement: ' + err.message, true);
      } finally {
        mgrAddMvBtn.disabled = false;
      }
    }

    // ═══════ Personal-finance dashboard (role: personal) ═══════
    // Reuses /reports/owner-dashboard (cash, burn, expense_by_category,
    // monthly_expense_series) and /budgets — rendered in personal language.
    let _pdCatChart = null;
    let _pdTrendChart = null;
    const _PD_PALETTE = ['#2f6f62', '#e0a458', '#7d9fc2', '#c26b6b', '#8fbf9f',
                        '#b58ecc', '#d98e73', '#6bb0c2', '#c2b26b', '#9aa5b1'];

    async function pdFillBudgetCategories() {
      const sel = document.getElementById('pd-budget-category');
      if (!sel || sel.options.length) return;
      try {
        const res = await fetch(API + '/manager-reports/accounts/list');
        if (!res.ok) return;
        const accs = await res.json();
        sel.innerHTML = accs
          .filter(a => (a.code || '').length > 2 && (a.code.startsWith('61') || a.code.startsWith('62')))
          .map(a => `<option value="${escapeHtml(a.name)}">${escapeHtml(a.name)}</option>`)
          .join('');
      } catch (_) { /* offline */ }
    }

    async function pdLoadBudgets() {
      const wrap = document.getElementById('pd-budget-wrap');
      if (!wrap) return;
      const monthEl = document.getElementById('pd-budget-month');
      if (monthEl && !monthEl.value) monthEl.value = currentMonthKey();
      const monthVal = monthEl ? monthEl.value : currentMonthKey();
      try {
        const res = await fetch(API + '/budgets/actual-vs-budget?month=' + encodeURIComponent(monthVal));
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'budget error');
        const rows = data.rows || [];
        if (!rows.length) {
          wrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('pdNoBudgets')) + '</p>';
          return;
        }
        wrap.innerHTML = rows.map(r => {
          const pct = Math.max(0, Math.min(150, Number(r.utilization_pct) || 0));
          const color = pct >= 100 ? 'var(--danger)' : (pct >= 85 ? 'var(--accent)' : 'var(--success)');
          return `
            <div style="margin-bottom:0.55rem;">
              <div style="display:flex; justify-content:space-between; font-size:0.85rem;">
                <span>${escapeHtml(r.category)}</span>
                <span>${formatNum(r.actual_amount)} / ${formatNum(r.limit_amount)} (${escapeHtml(String(r.utilization_pct))}%)</span>
              </div>
              <div style="background:var(--border); border-radius:6px; height:8px; overflow:hidden;">
                <div style="width:${Math.min(100, pct)}%; height:100%; background:${color};"></div>
              </div>
            </div>`;
        }).join('');
      } catch (_) {
        wrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('pdNoBudgets')) + '</p>';
      }
    }

    // ─── Proactive insights panel (owner + personal dashboards) ───
    async function loadInsightsPanel(wrapId) {
      const wrap = document.getElementById(wrapId);
      if (!wrap) return;
      try {
        const res = await fetch(API + '/insights');
        if (!res.ok) { wrap.innerHTML = ''; return; }
        const data = await res.json();
        const items = data.insights || [];
        if (!items.length) {
          wrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('insightsEmpty')) + '</p>';
          return;
        }
        const levelClass = { high: 'high', warning: 'medium', info: 'low' };
        wrap.innerHTML = items.map(i => `
          <div class="insight-item" style="border:1px solid var(--border); border-radius:10px; padding:0.55rem; margin-bottom:0.45rem;">
            <span class="alert-chip ${escapeHtml(levelClass[i.severity] || 'low')}">${escapeHtml(t('insightSeverity_' + (i.severity || 'info')))}</span>
            <strong style="display:block; margin-top:0.25rem;">${escapeHtml(i.title || '')}</strong>
            <div style="font-size:0.82rem; color:var(--text-muted);">${escapeHtml(i.message || '')}</div>
            <div style="display:flex; gap:0.4rem; margin-top:0.4rem; flex-wrap:wrap;">
              ${i.page ? `<button type="button" class="btn btn-secondary btn-sm insight-open" data-page="${escapeHtml(i.page)}">${escapeHtml(t('insightsOpen'))}</button>` : ''}
              <button type="button" class="btn btn-secondary btn-sm insight-ask" data-title="${escapeHtml(i.title || '')}">${escapeHtml(t('insightsAsk'))}</button>
            </div>
          </div>`).join('');
        wrap.querySelectorAll('.insight-open').forEach(b => b.addEventListener('click', () => {
          const page = b.dataset.page;
          if (page && typeof showPage === 'function') { showPage(page); if (typeof loadPageData === 'function') loadPageData(page); }
        }));
        wrap.querySelectorAll('.insight-ask').forEach(b => b.addEventListener('click', () => {
          if (typeof window.aiChatAsk === 'function') window.aiChatAsk(tf('insightsAskMsg', { title: b.dataset.title || '' }));
        }));
      } catch (_) { wrap.innerHTML = ''; }
    }

    // --- 13-week cash forecast: summary, week details and what-if (roadmap §5.3) ---
    function forecastItemLabel(i) {
      const kind = t('forecastKind_' + i.kind);
      const head = i.name ? kind + ' ' + i.name : kind;
      return i.entity_name ? head + ' — ' + i.entity_name : head;
    }

    function renderForecastSummary(rows) {
      const el = document.getElementById('forecast-summary');
      if (!el) return;
      if (!rows.length) { el.textContent = ''; el.classList.remove('forecast-risk'); return; }
      // the first lowest week, as the server reports it
      const low = rows.reduce((a, r) => (r.projected_cash < a.projected_cash ? r : a), rows[0]);
      const neg = rows.find((r) => r.projected_cash < 0);
      const lowest = tf('forecastLowest', { amount: formatNum(low.projected_cash), week: formatDisplayDate(low.week_start) });
      el.textContent = neg ? tf('forecastNegative', { week: formatDisplayDate(neg.week_start) }) + ' · ' + lowest : lowest;
      el.classList.toggle('forecast-risk', !!neg);
    }

    function forecastLearnedHtml(data) {
      const learned = data.learned || {};
      const lines = [learned.company_days_late > 0
        ? tf('forecastLearned', { days: learned.company_days_late }) : t('forecastLearnedOnTime')];
      (learned.customers || []).filter((c) => c.days_late > 0).slice(0, 5).forEach((c) => {
        lines.push(tf('forecastLateCustomer', { name: c.name || '—', days: c.days_late, n: c.paid_invoices }));
      });
      const d = data.doubtful || {};
      if (d.count) lines.push(tf('forecastDoubtful', { n: d.count, days: d.after_days, amount: formatNum(d.total) }));
      const b = data.baseline || {};
      lines.push(b.weeks_of_history >= 4
        ? tf('forecastUsualWeek', { inflow: formatNum(b.inflow), outflow: formatNum(b.outflow), weeks: b.weeks_of_history })
        : t('forecastNoHistory'));
      return '<div class="forecast-learned">' + lines.map((l) => '<div>' + escapeHtml(l) + '</div>').join('') + '</div>';
    }

    function forecastWeeksHtml(weeks) {
      return weeks.map((w) => {
        const items = (w.items || []).map((i) => '<li class="' + (i.amount < 0 ? 'fc-out' : 'fc-in') + '">'
          + '<span>' + escapeHtml(formatDisplayDate(i.date)) + '</span>'
          + '<span>' + escapeHtml(forecastItemLabel(i)) + '</span>'
          + (i.overdue ? '<span class="alert-chip medium">' + escapeHtml(t('forecastOverdue')) + '</span>' : '')
          + (i.days_late ? '<span class="fc-note">(' + escapeHtml(tf('forecastPaysLate', { days: i.days_late })) + ')</span>' : '')
          + (i.covers_invoice ? '<span class="fc-note">(' + escapeHtml(tf('forecastCovers', { number: i.covers_invoice })) + ')</span>' : '')
          + '<span class="fc-amt">' + escapeHtml(formatNum(i.amount)) + '</span></li>').join('');
        const usual = (w.baseline_in || w.baseline_out)
          ? '<div class="fc-note">' + escapeHtml(tf('forecastUsualLine', { inflow: formatNum(w.baseline_in), outflow: formatNum(w.baseline_out) })) + '</div>'
          : '';
        return '<details class="fc-week' + (w.risk ? ' fc-risk' : '') + '"><summary><strong>'
          + escapeHtml(formatDisplayDate(w.week_start)) + '</strong> — '
          + escapeHtml(tf('forecastWeekLine', { inflow: formatNum(w.inflow), outflow: formatNum(w.outflow), closing: formatNum(w.closing) }))
          + '</summary>' + (items ? '<ul class="fc-items">' + items + '</ul>' : '<div class="fc-note">' + escapeHtml(t('forecastNoItems')) + '</div>')
          + usual + '</details>';
      }).join('');
    }

    // What the what-if form offers: the cheques/installments and invoices the
    // forecast is counting on, and the parties behind them.
    function forecastChoices(data) {
      const seen = new Set();
      const commits = [];
      const invoices = [];
      const parties = new Map();
      (data.weeks || []).forEach((w) => (w.items || []).forEach((i) => {
        if (i.entity_id && i.entity_name) parties.set(i.entity_id, i.entity_name);
        if (!i.source_id || seen.has(i.kind + i.source_id)) return;
        seen.add(i.kind + i.source_id);
        if (/^(cheque|installment)_/.test(i.kind)) commits.push(i);
        else if (i.kind === 'invoice_in' || i.kind === 'bill_out') invoices.push(i);
      }));
      invoices.sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
      return { commits: commits.slice(0, 40), invoices: invoices.slice(0, 40),
               parties: [...parties.entries()].sort((a, b) => String(a[1]).localeCompare(String(b[1]))) };
    }

    function forecastWhatIfHtml(data) {
      const c = forecastChoices(data);
      const pick = (name, i) => '<label class="fc-pick"><input type="checkbox" name="' + name + '" value="' + escapeHtml(i.source_id) + '"> '
        + escapeHtml(formatDisplayDate(i.date) + ' · ' + forecastItemLabel(i) + ' · ' + formatNum(i.amount)) + '</label>';
      const none = '<div class="fc-note">' + escapeHtml(t('forecastNothingToPick')) + '</div>';
      const options = c.parties.map(([id, n]) => '<option value="' + escapeHtml(id) + '">' + escapeHtml(n) + '</option>').join('');
      return '<form id="fc-whatif" class="fc-whatif">'
        + '<h4>' + escapeHtml(t('forecastWhatIf')) + '</h4>'
        + '<fieldset><legend>' + escapeHtml(t('forecastBounce')) + '</legend>' + (c.commits.length ? c.commits.map((i) => pick('bounce', i)).join('') : none) + '</fieldset>'
        + '<fieldset><legend>' + escapeHtml(t('forecastSkip')) + '</legend>' + (c.invoices.length ? c.invoices.map((i) => pick('skip', i)).join('') : none) + '</fieldset>'
        + '<fieldset><legend>' + escapeHtml(t('forecastDelay')) + '</legend>'
        + '<select name="delay_entity" aria-label="' + escapeHtml(t('forecastPickParty')) + '"><option value="">' + escapeHtml(t('forecastPickParty')) + '</option>' + options + '</select>'
        + '<input type="number" name="delay_days" min="1" max="365" value="30" style="width:6rem;" aria-label="' + escapeHtml(t('forecastDelay') + ' — ' + t('forecastDelayDays')) + '"> ' + escapeHtml(t('forecastDelayDays')) + '</fieldset>'
        + '<fieldset><legend>' + escapeHtml(t('forecastOneOff')) + '</legend>'
        + '<select name="oneoff_sign" aria-label="' + escapeHtml(t('forecastOneOff')) + '"><option value="-1">' + escapeHtml(t('forecastOneOffOut')) + '</option><option value="1">' + escapeHtml(t('forecastOneOffIn')) + '</option></select>'
        + '<input type="number" name="oneoff_amount" min="0" step="1" placeholder="0" style="width:9rem;" aria-label="' + escapeHtml(t('labelAmount')) + '">'
        + '<input type="date" name="oneoff_date" aria-label="' + escapeHtml(t('labelDate')) + '">'
        + '<input type="text" name="oneoff_label" maxlength="120" placeholder="' + escapeHtml(t('forecastOneOffLabel')) + '" aria-label="' + escapeHtml(t('forecastOneOffLabel')) + '"></fieldset>'
        + '<div style="display:flex; gap:0.5rem; margin-top:0.5rem;"><button type="submit" class="btn btn-primary btn-sm">' + escapeHtml(t('forecastRun')) + '</button>'
        + '<button type="reset" class="btn btn-secondary btn-sm">' + escapeHtml(t('forecastReset')) + '</button></div>'
        + '<div id="fc-result"></div></form>';
    }

    function forecastScenarioPayload(form, data) {
      const payload = {
        currency: data.currency || null,
        weeks: (data.weeks || []).length || 13,
        bounce_commitments: [...form.querySelectorAll('input[name=bounce]:checked')].map((x) => x.value),
        skip_invoices: [...form.querySelectorAll('input[name=skip]:checked')].map((x) => x.value),
        delays: [],
        one_offs: [],
      };
      const entity = form.elements.delay_entity.value;
      const days = parseInt(form.elements.delay_days.value, 10);
      if (entity && days) payload.delays.push({ entity_id: entity, days });
      const amount = parseInt(form.elements.oneoff_amount.value, 10);
      const on = form.elements.oneoff_date.value;
      if (amount > 0 && on) {
        payload.one_offs.push({ on, amount: amount * parseInt(form.elements.oneoff_sign.value, 10),
                                label: form.elements.oneoff_label.value.trim() || null });
      }
      const empty = !payload.bounce_commitments.length && !payload.skip_invoices.length
        && !payload.delays.length && !payload.one_offs.length;
      return empty ? null : payload;
    }

    function forecastScenarioHtml(r) {
      const b = r.base;
      const s = r.scenario;
      const cell = (v) => '<td>' + escapeHtml(formatNum(v)) + '</td>';
      const head = '<tr><th></th><th>' + escapeHtml(t('forecastBase')) + '</th><th>' + escapeHtml(t('forecastScenario'))
        + '</th><th>' + escapeHtml(t('forecastDifference')) + '</th></tr>';
      const summary = '<table class="mini-table"><thead>' + head + '</thead><tbody>'
        + '<tr><td>' + escapeHtml(t('forecastLowestShort')) + '</td>' + cell(b.lowest.closing) + cell(s.lowest.closing) + cell(r.lowest_difference) + '</tr>'
        + '<tr><td>' + escapeHtml(t('forecastEnd')) + '</td>' + cell(b.closing_cash) + cell(s.closing_cash) + cell(r.closing_difference) + '</tr>'
        + '</tbody></table>';
      const weeks = '<table class="mini-table" style="margin-top:0.5rem;"><thead><tr><th>' + escapeHtml(t('forecastWeek')) + '</th><th>'
        + escapeHtml(t('forecastBase')) + '</th><th>' + escapeHtml(t('forecastScenario')) + '</th><th>' + escapeHtml(t('forecastDifference'))
        + '</th></tr></thead><tbody>'
        + b.weeks.map((w, k) => '<tr' + (s.weeks[k].risk ? ' class="fc-risk-text"' : '') + '><td>' + escapeHtml(formatDisplayDate(w.week_start)) + '</td>'
          + cell(w.closing) + cell(s.weeks[k].closing) + cell(r.difference[k].closing_difference) + '</tr>').join('')
        + '</tbody></table>';
      const neg = s.first_negative_week
        ? '<p class="fc-risk-text">' + escapeHtml(tf('forecastNegative', { week: formatDisplayDate(s.first_negative_week) })) + '</p>' : '';
      const notes = (r.scenario_notes || []).map((n) => '<div class="fc-note">' + escapeHtml(n) + '</div>').join('');
      return neg + summary + weeks + notes;
    }

    async function loadForecastExplorer() {
      const body = document.getElementById('forecast-explorer-body');
      if (!body) return;
      body.innerHTML = '<p class="fc-note">' + escapeHtml(t('loading')) + '</p>';
      const ccy = window.__DASH_CCY || '';
      try {
        const res = await fetch(API + '/reports/cash-forecast' + (ccy ? '?currency=' + encodeURIComponent(ccy) : ''));
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'cash forecast error');
        body.innerHTML = forecastLearnedHtml(data) + forecastWeeksHtml(data.weeks || []) + forecastWhatIfHtml(data);
        const form = body.querySelector('#fc-whatif');
        const out = form.querySelector('#fc-result');
        form.addEventListener('reset', () => { out.innerHTML = ''; });
        form.addEventListener('submit', async (ev) => {
          ev.preventDefault();
          const payload = forecastScenarioPayload(form, data);
          if (!payload) { out.innerHTML = '<p class="fc-note">' + escapeHtml(t('forecastScenarioEmpty')) + '</p>'; return; }
          out.innerHTML = '<p class="fc-note">' + escapeHtml(t('loading')) + '</p>';
          try {
            const r = await fetch(API + '/reports/cash-forecast/scenario', {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
            });
            const result = await r.json();
            if (!r.ok) throw new Error(result.detail || 'scenario error');
            out.innerHTML = forecastScenarioHtml(result);
          } catch (_) {
            out.innerHTML = '<p class="fc-note">' + escapeHtml(t('forecastError')) + '</p>';
          }
        });
      } catch (_) {
        body.innerHTML = '<p class="fc-note">' + escapeHtml(t('forecastError')) + '</p>';
      }
    }

    async function loadPersonalDashboard() {
      const grid = document.getElementById('pd-kpi-grid');
      if (!grid) return;
      loadInsightsPanel('pd-insights-wrap');
      try {
        if (!window.__FX_META) { try { await loadFxMetadata(); } catch (_) { /* offline */ } }
        await loadReportingCurrency();
        const res = await fetch(API + '/reports/owner-dashboard');
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'dashboard error');
        const kpis = data.kpis || [];
        const kv = (key) => kpis.find(k => k.key === key) || {};
        // "Spent this month" = the current month's actual from the expense
        // series (burn_rate is a trailing average, not this month's number).
        const thisMonth = currentMonthKey();         // a Jalali month for an Iranian company (§3.5)
        const monthRow = (data.monthly_expense_series || []).find(r => r.period === thisMonth);
        const spentCard = monthRow
          ? { value: monthRow.value, unit: kv('burn_rate').unit }
          : kv('burn_rate');
        const cards = [
          { label: t('kpiCashOnHand'), k: kv('cash_on_hand') },
          { label: t('pdKpiSpent'), k: spentCard },
          { label: t('pdKpiSaved'), k: kv('monthly_net_profit') },
        ];
        grid.innerHTML = cards.map(c => `
          <div class="kpi-card">
            <div class="label">${escapeHtml(c.label)}</div>
            <div class="value">${escapeHtml(formatKpiValue(c.k.value, c.k.unit))}</div>
          </div>
        `).join('');

        const cats = data.expense_by_category || [];
        renderMiniTable('pd-cat-wrap', ['category', 'amount'],
          cats.map(r => [escapeHtml(r.category), formatNum(r.amount)]));
        const catCanvas = document.getElementById('pd-cat-chart');
        if (typeof Chart !== 'undefined' && catCanvas && cats.length) {
          if (_pdCatChart) _pdCatChart.destroy();
          _pdCatChart = new Chart(catCanvas, {
            type: 'doughnut',
            data: {
              labels: cats.map(r => r.category),
              datasets: [{ data: cats.map(r => r.amount), backgroundColor: _PD_PALETTE }],
            },
            options: { plugins: { legend: { position: 'bottom' } } },
          });
        }

        const series = data.monthly_expense_series || [];
        const trendCanvas = document.getElementById('pd-trend-chart');
        if (typeof Chart !== 'undefined' && trendCanvas && series.length) {
          if (_pdTrendChart) _pdTrendChart.destroy();
          _pdTrendChart = new Chart(trendCanvas, {
            type: 'bar',
            data: {
              labels: series.map(r => formatPeriodKey(r.period)),
              datasets: [{ data: series.map(r => r.value), backgroundColor: _PD_PALETTE[0] }],
            },
            options: { plugins: { legend: { display: false } } },
          });
        }
      } catch (_) {
        grid.innerHTML = '<p class="empty-state">' + escapeHtml(t('errorLoadingOwnerDashboard')) + '</p>';
      }
      pdFillBudgetCategories();
      pdLoadBudgets();
      loadNetWorth();
      loadReportCard();
      loadGoals();
      loadHousehold();
    }

    // ═══════ A shared household (personal, roadmap §4.12) ═══════
    async function loadHousehold() {
      const members = document.getElementById('hh-members');
      if (!members) return;
      let d;
      try {
        const res = await fetch(API + '/personal/household');
        if (!res.ok) { document.getElementById('pd-household').hidden = true; return; }   // not personal books
        d = await res.json();
      } catch (_) { return; }
      document.getElementById('pd-household').hidden = false;
      members.innerHTML = d.members.filter(m => m.active).map(m => `<li><span>${rcName(m.username)}`
        + `${m.you ? ` <span class="hh-meta">(${escapeHtml(t('hhYou'))})</span>` : ''}</span>`
        + (m.you ? '' : `<button type="button" class="btn btn-secondary btn-sm hh-remove" data-id="${escapeHtml(m.id)}">${escapeHtml(t('hhRemove'))}</button>`)
        + '</li>').join('');
      document.getElementById('hh-invites').innerHTML = d.invites.map(i => `<li><span>${rcName(i.name || i.email || t('hhSomeone'))}`
        + ` <span class="hh-meta">${escapeHtml(tf(i.emailed ? 'hhInvitedEmailed' : 'hhInvitedLink', { date: i.expires_at.slice(0, 10) }))}</span></span>`
        + `<button type="button" class="btn btn-secondary btn-sm hh-cancel" data-id="${escapeHtml(i.id)}">${escapeHtml(t('hhCancel'))}</button></li>`).join('');
      document.getElementById('hh-add').hidden = d.room <= 0;
    }

    document.getElementById('hh-send')?.addEventListener('click', async () => {
      const name = document.getElementById('hh-name').value.trim() || null;
      const email = document.getElementById('hh-email').value.trim() || null;
      try {
        const res = await fetch(API + '/personal/household/invites', { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, email }) });
        const d = await readJsonSafe(res);
        if (!res.ok) { showAlert((d && d.detail) ? d.detail : t('hhFailed'), true); return; }
        const url = d.link.startsWith('http') ? d.link : (location.origin + d.link);
        document.getElementById('hh-link-input').value = url;
        document.getElementById('hh-link-note').textContent = t(d.emailed ? 'hhLinkEmailed' : 'hhLinkCopy');
        document.getElementById('hh-link').hidden = false;
        document.getElementById('hh-name').value = '';
        document.getElementById('hh-email').value = '';
        document.getElementById('hh-add').open = false;
        await loadHousehold();
      } catch (_) { showAlert(t('hhFailed'), true); }
    });
    document.getElementById('hh-copy')?.addEventListener('click', async () => {
      const input = document.getElementById('hh-link-input');
      try { await navigator.clipboard.writeText(input.value); showAlert(t('hhCopied')); }
      catch (_) { input.select(); }
    });
    document.getElementById('pd-household')?.addEventListener('click', async (e) => {
      const cancel = e.target.closest('.hh-cancel');
      const remove = e.target.closest('.hh-remove');
      if (!cancel && !remove) return;
      if (remove && !(await uiConfirm({ message: t('hhRemoveConfirm'), confirmLabel: t('hhRemove'), danger: true }))) return;
      const url = cancel ? '/personal/household/invites/' + cancel.dataset.id : '/personal/household/members/' + remove.dataset.id;
      try {
        const res = await fetch(API + url, { method: 'DELETE' });
        if (!res.ok) { const d = await readJsonSafe(res); showAlert((d && d.detail) ? d.detail : t('hhFailed'), true); }
        await loadHousehold();
      } catch (_) { showAlert(t('hhFailed'), true); }
    });

    // ═══════ Monthly report card + savings goals (personal, roadmap §4.12) ═══════
    function rcLang() { return currentLanguage === 'fa' ? 'fa' : 'en'; }
    // Names from the chart can be Persian inside an English line (and the other
    // way round); numbers with a sign flip in right-to-left text. Isolate both.
    function rcName(text) { return `<bdi>${escapeHtml(text)}</bdi>`; }
    function rcSigned(n, suffix = '') {
      return '\u2066' + (n > 0 ? '+' : '') + formatNum(n) + suffix + '\u2069';
    }

    async function loadReportCard() {
      const sel = document.getElementById('rc-month');
      const figs = document.getElementById('rc-figures');
      if (!sel || !figs) return;
      const q = new URLSearchParams({ lang: rcLang() });
      if (sel.value) q.set('month', sel.value);
      let d;
      try {
        const res = await fetch(API + '/personal/report-card?' + q.toString());
        if (!res.ok) throw new Error(String(res.status));
        d = await res.json();
      } catch (e) {
        figs.innerHTML = '<p class="empty-state">' + escapeHtml(t('rcFailed')) + '</p>';
        return;
      }
      if (sel.dataset.lang !== d.lang || !sel.options.length) {
        const picked = sel.value || d.month;
        sel.innerHTML = d.months.map(m => `<option value="${escapeHtml(m.key)}">${escapeHtml(m.label)}</option>`).join('');
        sel.value = [...sel.options].some(o => o.value === picked) ? picked : d.month;
        sel.dataset.lang = d.lang;
      }
      const note = document.getElementById('rc-note');
      note.hidden = !d.note;
      note.textContent = d.note || '';
      const prev = d.previous || {};
      const fig = (label, value, sub, cls) => `<div class="rc-fig ${cls || ''}"><div class="lbl">${escapeHtml(label)}</div>`
        + `<div class="val">${escapeHtml(value)}</div>${sub ? `<div class="sub">${escapeHtml(sub)}</div>` : ''}</div>`;
      const rate = (r) => (r === null || r === undefined) ? '—' : `${formatNum(r)}%`;
      figs.innerHTML = [
        fig(t('rcIncome'), formatNum(d.income), tf('rcLastMonth', { v: formatNum(prev.income || 0) })),
        fig(t('rcSpending'), formatNum(d.spending), d.average_spending_3m == null ? '' : tf('rcAverage', { v: formatNum(d.average_spending_3m) })),
        fig(t('rcSaved'), formatNum(d.saved), tf('rcLastMonth', { v: formatNum(prev.saved || 0) }), d.saved >= 0 ? 'good' : 'bad'),
        fig(t('rcRate'), rate(d.savings_rate), tf('rcLastMonth', { v: rate(prev.savings_rate) })),
      ].join('');
      const marks = { true: ['ok', '✓'], false: ['warn', '!'], null: ['info', '•'] };
      document.getElementById('rc-checks').innerHTML = d.checks.map(c => {
        const [cls, mark] = marks[String(c.ok)];
        return `<li class="${cls}" data-key="${escapeHtml(c.key)}"><span class="mark" aria-hidden="true">${mark}</span>`
          + `<span class="item">${escapeHtml(c.item)}</span><span class="detail">${escapeHtml(c.detail)}</span></li>`;
      }).join('');
      const bits = [];
      if (d.categories.length) {
        bits.push(`<p><strong>${escapeHtml(t('rcTopCategories'))}:</strong> ` + d.categories.map(c =>
          `${rcName(c.category)} <bdi dir="ltr">${escapeHtml(formatNum(c.amount))}</bdi>`
          + (c.change_pct == null ? '' : ` <span class="goal-meta">(${escapeHtml(rcSigned(c.change_pct, '%'))})</span>`)).join(' · ') + '</p>');
      }
      if (d.biggest_rise) {
        bits.push(`<p>${escapeHtml(tf('rcBiggestRise', { cat: '\u2068' + d.biggest_rise.category + '\u2069', v: rcSigned(d.biggest_rise.increase) }))}</p>`);
      }
      const nw = d.net_worth || {};
      bits.push(`<p>${escapeHtml(tf('rcNetWorth', { v: rcSigned(nw.change || 0), end: formatNum(nw.end || 0) }))}</p>`);
      if (d.goals.length) {
        bits.push(`<p>${escapeHtml(t('goalsTitle'))}: ` + d.goals.map(g => `${rcName(g.name)} <bdi dir="ltr">${escapeHtml(formatNum(g.percent))}%</bdi>`).join(' · ') + '</p>');
      }
      document.getElementById('rc-details').innerHTML = bits.join('');
    }

    async function goalFillAccounts() {
      const sel = document.getElementById('goal-account');
      if (!sel || sel.options.length) return;
      try {
        const res = await fetch(API + '/manager-reports/accounts/list');
        if (!res.ok) return;
        const accs = await res.json();
        sel.innerHTML = accs.filter(a => (a.code || '').startsWith('1'))
          .map(a => `<option value="${escapeHtml(a.code)}">${escapeHtml(a.code)} — ${escapeHtml(a.name)}</option>`).join('');
      } catch (_) { /* offline */ }
    }

    async function loadGoals() {
      const wrap = document.getElementById('goals-list');
      if (!wrap) return;
      goalFillAccounts();
      let rows = [];
      try {
        const res = await fetch(API + '/personal/goals');
        rows = res.ok ? await res.json() : [];
      } catch (_) { rows = []; }
      if (!rows.length) {
        wrap.innerHTML = '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('goalsEmpty')) + '</p>';
        return;
      }
      wrap.innerHTML = rows.map(g => {
        let badge = '';
        if (g.reached) badge = `<span class="goal-badge ok">${escapeHtml(t('goalReached'))}</span>`;
        else if (g.on_track === true) badge = `<span class="goal-badge ok">${escapeHtml(t('goalOnTrack'))}</span>`;
        else if (g.on_track === false) badge = `<span class="goal-badge warn">${escapeHtml(t('goalBehind'))}</span>`;
        const meta = [tf('goalProgress', { v: formatNum(g.current), target: formatNum(g.target_amount) })];
        if (!g.reached && g.needed_per_month != null) meta.push(tf('goalNeeded', { v: formatNum(g.needed_per_month), date: g.target_date }));
        if (!g.reached && g.pace_per_month > 0) meta.push(tf('goalPace', { v: formatNum(g.pace_per_month) }));
        return `<div class="goal${g.reached ? ' reached' : ''}" data-id="${escapeHtml(g.id)}">
          <div class="goal-top"><span class="goal-name">${rcName(g.name)}</span>
            <span>${badge} <button type="button" class="btn btn-secondary btn-sm goal-del" data-id="${escapeHtml(g.id)}" aria-label="${escapeHtml(t('goalDelete'))}">×</button></span></div>
          <div class="goal-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Number(g.percent)}"><span style="width:${Math.max(0, Math.min(100, Number(g.percent)))}%"></span></div>
          <div class="goal-meta"><bdi dir="ltr">${escapeHtml(formatNum(g.percent))}%</bdi> · ${rcName(g.account_name || g.account_code)} · ${meta.map(m => `<bdi>${escapeHtml(m)}</bdi>`).join(' · ')}</div>
        </div>`;
      }).join('');
    }

    document.getElementById('rc-month')?.addEventListener('change', loadReportCard);
    document.getElementById('goal-save')?.addEventListener('click', async () => {
      const name = document.getElementById('goal-name').value.trim();
      const account_code = document.getElementById('goal-account').value;
      const target_amount = parseInt(document.getElementById('goal-target').value || '0', 10);
      const target_date = document.getElementById('goal-date').value || null;
      if (!name || !account_code || !(target_amount > 0)) { showAlert(t('goalMissing'), true); return; }
      try {
        const res = await fetch(API + '/personal/goals', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, account_code, target_amount, target_date }) });
        const d = await readJsonSafe(res);
        if (!res.ok) { showAlert((d && d.detail) ? d.detail : t('goalFailed'), true); return; }
        showAlert(t('goalSaved'));
        ['goal-name', 'goal-target', 'goal-date'].forEach(id => { document.getElementById(id).value = ''; });
        document.getElementById('goal-add').open = false;
        await loadGoals();
      } catch (_) { showAlert(t('goalFailed'), true); }
    });
    document.getElementById('goals-list')?.addEventListener('click', async (e) => {
      const btn = e.target.closest('.goal-del');
      if (!btn) return;
      if (!(await uiConfirm({ message: t('goalDeleteConfirm'), confirmLabel: t('goalDelete'), danger: true }))) return;
      try {
        await fetch(API + '/personal/goals/' + btn.dataset.id, { method: 'DELETE' });
        await loadGoals();
      } catch (_) { /* ignore */ }
    });

    (function wirePersonalDashboard() {
      const saveBtn = document.getElementById('pd-budget-save');
      const monthEl = document.getElementById('pd-budget-month');
      if (monthEl) monthEl.addEventListener('change', pdLoadBudgets);
      if (saveBtn) saveBtn.addEventListener('click', async () => {
        const month = (monthEl && monthEl.value) || currentMonthKey();
        const category = document.getElementById('pd-budget-category')?.value || '';
        const limit = Number(document.getElementById('pd-budget-limit')?.value || 0);
        if (!category || !(limit > 0)) return;
        try {
          const res = await fetch(API + '/budgets', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ month, category, limit_amount: limit }),
          });
          if (res.ok) { showAlert(t('btnSaveBudget') + ' ✓'); pdLoadBudgets(); }
          else { const d = await res.json().catch(() => ({})); showAlert(d.detail || 'error', true); }
        } catch (_) { showAlert('error', true); }
      });
    })();

    // ═══════ Net worth (personal) ═══════
    // Assets minus liabilities, with gold/FX restated at current rates. The
    // books stay at cost; this panel is the "what is it worth now" view.
    let _nwTrendChart = null;

    async function nwFillAccountOptions() {
      const sel = document.getElementById('nw-h-account');
      if (!sel || sel.options.length) return;
      try {
        const res = await fetch(API + '/manager-reports/accounts/list');
        if (!res.ok) return;
        const accs = await res.json();
        // Only asset accounts can hold gold/currency.
        sel.innerHTML = accs
          .filter(a => (a.code || '').startsWith('1'))
          .map(a => `<option value="${escapeHtml(a.code)}">${escapeHtml(a.code)} — ${escapeHtml(a.name)}</option>`)
          .join('');
        // Default to the gold savings account when the chart has one.
        const gold = accs.find(a => a.code === '1130');
        if (gold) sel.value = '1130';
      } catch (_) { /* offline */ }
    }

    async function nwLoadHoldings() {
      const wrap = document.getElementById('nw-holdings-wrap');
      if (!wrap) return;
      try {
        const rows = await (await fetch(API + '/personal/holdings')).json();
        if (!rows.length) {
          wrap.innerHTML = '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('nwNoHoldings')) + '</p>';
          return;
        }
        wrap.innerHTML = `<table class="mini-table"><tbody>${rows.map(r => `
          <tr>
            <td>${escapeHtml(r.account_name || r.account_code)}</td>
            <td>${escapeHtml(String(r.quantity))} ${escapeHtml(r.unit)}</td>
            <td style="text-align:end;"><button class="btn btn-secondary btn-sm nw-h-del" data-id="${escapeHtml(r.id)}">×</button></td>
          </tr>`).join('')}</tbody></table>`;
      } catch (_) {
        wrap.innerHTML = '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('nwNoHoldings')) + '</p>';
      }
    }

    async function loadNetWorth() {
      const headline = document.getElementById('nw-headline');
      if (!headline) return;
      try {
        const d = await (await fetch(API + '/personal/net-worth')).json();
        const gain = d.unrealized_gain || 0;
        const gainColor = gain > 0 ? 'var(--success)' : (gain < 0 ? 'var(--danger)' : 'var(--text-muted)');
        headline.innerHTML = `
          <div style="font-size:1.6rem; font-weight:700;">${escapeHtml(formatNum(d.net_worth))} ${escapeHtml(d.currency)}</div>
          <div style="font-size:0.82rem; color:var(--text-muted);">
            ${escapeHtml(t('nwAssets'))}: ${escapeHtml(formatNum(d.total_assets))} ·
            ${escapeHtml(t('nwDebts'))}: ${escapeHtml(formatNum(d.total_liabilities))}
          </div>
          ${gain ? `<div style="font-size:0.82rem; color:${gainColor};">${escapeHtml(t('nwUnrealized'))}: ${escapeHtml(formatNum(gain))}</div>` : ''}
          ${(d.missing_rates || []).length ? `<div class="alert-chip medium" style="margin-top:0.35rem;">${escapeHtml(tf('nwMissingRate', { units: d.missing_rates.join(', ') }))}</div>` : ''}
        `;

        renderMiniTable('nw-breakdown', ['item', 'value'],
          [...d.assets, ...d.liabilities].map(l => [
            escapeHtml(l.account_name) + (l.revalued ? ' ★' : ''),
            formatNum(l.market_value),
          ]));

        const canvas = document.getElementById('nw-trend-chart');
        const trend = d.trend || [];
        if (typeof Chart !== 'undefined' && canvas && trend.length) {
          if (_nwTrendChart) _nwTrendChart.destroy();
          _nwTrendChart = new Chart(canvas, {
            type: 'line',
            data: {
              labels: trend.map(r => r.period),
              datasets: [{
                data: trend.map(r => r.value),
                borderColor: _PD_PALETTE[0],
                backgroundColor: 'rgba(47,111,98,0.12)',
                fill: true, tension: 0.25, pointRadius: 2,
              }],
            },
            options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } },
          });
        }
      } catch (_) {
        headline.innerHTML = '<p class="empty-state">' + escapeHtml(t('errorLoadingOwnerDashboard')) + '</p>';
      }
      nwFillAccountOptions();
      nwLoadHoldings();
    }

    (function wireNetWorth() {
      const saveBtn = document.getElementById('nw-h-save');
      if (saveBtn) saveBtn.addEventListener('click', async () => {
        const account_code = document.getElementById('nw-h-account')?.value || '';
        const unit = document.getElementById('nw-h-unit')?.value || '';
        const quantity = Number(document.getElementById('nw-h-qty')?.value || 0);
        if (!account_code || !unit || !(quantity >= 0)) return;
        try {
          const res = await fetch(API + '/personal/holdings', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_code, unit, quantity }),
          });
          if (!res.ok) {
            const d = await res.json().catch(() => ({}));
            showAlert(d.detail || 'error', true);
            return;
          }
          showAlert(t('nwHoldingSaved'));
          await loadNetWorth();
        } catch (_) { showAlert('error', true); }
      });

      const wrap = document.getElementById('nw-holdings-wrap');
      if (wrap) wrap.addEventListener('click', async (e) => {
        const btn = e.target.closest('.nw-h-del');
        if (!btn) return;
        try {
          await fetch(API + '/personal/holdings/' + btn.dataset.id, { method: 'DELETE' });
          await loadNetWorth();
        } catch (_) { /* ignore */ }
      });
    })();
