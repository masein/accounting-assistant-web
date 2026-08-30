
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

    async function loadOwnerDashboard() {
      try {
        // Make sure the reporting currency is resolved before the first paint,
        // so figures are labelled in the company's currency (GBP for UK, etc.)
        // rather than the default IRR.
        if (!window.__FX_META) { try { await loadFxMetadata(); } catch (_) { /* offline */ } }
        await loadReportingCurrency();
        // Honour a global currency selector if one is present; falls back to no filter.
        const dashCcy = document.getElementById('mgr-currency')?.value
          || window.__FX_META?.reporting_currency
          || '';
        const url = API + '/reports/owner-dashboard' + (dashCcy ? ('?currency=' + encodeURIComponent(dashCcy)) : '');
        const res = await fetch(url);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'owner dashboard error');

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
        hint.textContent = t('zoomHint') || 'drag to zoom · shift+wheel · alt+drag to pan';
        h.appendChild(hint);
        const resetBtn = document.createElement('button');
        resetBtn.type = 'button';
        resetBtn.className = 'btn btn-secondary btn-sm';
        resetBtn.style.cssText = 'padding:2px 8px;font-size:0.72rem;';
        resetBtn.textContent = t('btnResetZoom') || 'Reset zoom';
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

    function renderManagerReportChart(report) {
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
      const fromDate = mgrFromDateEl.value || '';
      const toDate = mgrToDateEl.value || '';
      const granularity = mgrPeriodGranularityEl ? mgrPeriodGranularityEl.value : 'monthly';
      const chartCurrency = document.getElementById('mgr-currency')?.value || '';
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
            _addExtraChart('Balance Sheet Trend', {
              type: 'line',
              data: {
                labels: periods.map(p => p.period),
                datasets: [
                  { label: 'Assets', data: periods.map(p => p.assets), borderColor: palette[0], backgroundColor: 'rgba(15,118,110,0.12)', fill: true, tension: 0.3 },
                  { label: 'Liabilities', data: periods.map(p => p.liabilities), borderColor: '#c62828', backgroundColor: 'rgba(198,40,40,0.08)', fill: true, tension: 0.3 },
                  { label: 'Equity', data: periods.map(p => p.equity), borderColor: palette[1], backgroundColor: 'rgba(14,165,233,0.08)', fill: true, tension: 0.3 },
                ]
              },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' }, zoom: zoomPluginOptions() }, scales: { y: { ticks: { callback: v => formatNum(v) } } } }
            }, (e, els) => {
              if (!els.length) return;
              const idx = els[0].index;
              const dsIdx = els[0].datasetIndex;
              const prefixes = ['11,12,13,14,15', '21,22,23,24', '31,32,33'][dsIdx];
              if (prefixes) showTransactionDrilldown(['Assets', 'Liabilities', 'Equity'][dsIdx] + ' — ' + periods[idx].period, { account_code_prefix: prefixes, to_date: periods[idx].date });
            });
            // Net worth trend
            _addExtraChart('Net Worth Over Time', {
              type: 'bar',
              data: {
                labels: periods.map(p => p.period),
                datasets: [{
                  label: 'Net Worth (Assets − Liabilities)',
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
          _addExtraChart('Cost & Profit Breakdown', {
            type: 'doughnut',
            data: {
              labels: ['Net Profit', 'COGS', 'Operating Expenses', 'Other Expenses'],
              datasets: [{ label: 'Breakdown', data: [Math.max(0, netProfit), totals.cogs || 0, opex, otherExp], backgroundColor: [palette[0], palette[3], palette[5], palette[4]] }]
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
            _addExtraChart('Sales Revenue Trend', {
              type: 'bar',
              data: {
                labels: periods.map(p => p.period),
                datasets: [{ label: 'Sales Revenue', data: periods.map(p => p.sales_amount), backgroundColor: palette[0] }]
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
            _addExtraChart('Cash Inflow vs Outflow Over Time', {
              type: 'bar',
              data: {
                labels: periods.map(p => p.period),
                datasets: [
                  { label: 'Inflow', data: periods.map(p => p.inflow), backgroundColor: 'rgba(15,118,110,0.75)' },
                  { label: 'Outflow', data: periods.map(p => p.outflow), backgroundColor: 'rgba(198,40,40,0.65)' }
                ]
              },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { y: { ticks: { callback: v => formatNum(v) } } } }
            });
            _addExtraChart('Net Cash Flow Trend', {
              type: 'line',
              data: {
                labels: periods.map(p => p.period),
                datasets: [{
                  label: 'Net Cash Flow',
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

      const ROW_LABEL = (r) => (r && (r.label_fa || r.label_en || r.label || r.key)) || '';

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
              { label: t('labelCurrent') || 'Current', data: rows.map(r => _amt(r, 'amount_current')), backgroundColor: palette[0] },
              { label: t('labelPrior') || 'Prior', data: rows.map(r => _amt(r, 'amount_prior')), backgroundColor: palette[1] },
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
                labels: periods.map(p => p.period),
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
        _compositionPie(t('chartAssetComposition') || 'Asset composition', assetKeys);
        _compositionPie(t('chartLiabilityComposition') || 'Liabilities composition', liabKeys);
        _compositionPie(t('chartEquityComposition') || 'Equity composition', equityKeys);
        // Trend across periods (uses default-locale endpoint; both locales share the chart of accounts ranges via classify_account_code).
        _addPeriodsTrend('/manager-reports/financial/balance-sheet-periods', t('chartBSTrend') || 'Assets / Liabilities / Equity over time',
          [
            { field: 'assets', label: t('legendAssets') || 'Assets', color: palette[0] },
            { field: 'liabilities', label: t('legendLiabilities') || 'Liabilities', color: '#c62828' },
            { field: 'equity', label: t('legendEquity') || 'Equity', color: palette[1] },
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
        _compositionPie(t('chartExpenseComposition') || 'Expense composition', expenseKeys, v => Math.abs(v));
        // Revenue / income comparison
        const revKeys = isUK ? ['turnover', 'other_operating_income', 'investment_income', 'interest_receivable']
                              : ['revenue_operating', 'other_operating_income', 'non_operating_net'];
        _comparisonBar(t('chartRevenueLines') || 'Revenue / income lines', revKeys, isUK ? '4' : '41,42,43');
        // Sales trend (period-aware): only if we have a sales endpoint — works for any locale
        _addPeriodsTrend('/manager-reports/sales/trend', t('chartSalesTrend') || 'Sales revenue trend',
          [{ field: 'sales_amount', label: t('legendSales') || 'Sales', color: palette[0] }]);
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
          _addExtraChart(t('chartCashReconciliation') || 'Cash reconciliation', {
            type: 'bar',
            data: {
              labels: recRows.map(r => _localizeDatesInLabel(ROW_LABEL(r))),
              datasets: [{
                label: t('legendMovement') || 'Movement',
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
        _compositionPie(t('chartInvestingActivity') || 'Investing activity (cash magnitude)', invKeys, v => Math.abs(v));
        // Financing breakdown
        const finKeys = isUK
          ? ['fin_share_capital_inflow', 'fin_share_premium_inflow', 'fin_borrowings_inflow', 'fin_borrowings_outflow', 'fin_lease_outflow', 'fin_dividends_outflow']
          : ['fin_capital_inflow', 'fin_share_premium_inflow', 'fin_st_loans_inflow', 'fin_st_loans_outflow',
             'fin_loans_interest_outflow_placeholder', 'fin_dividends_outflow'];
        _compositionPie(t('chartFinancingActivity') || 'Financing activity (cash magnitude)', finKeys, v => Math.abs(v));
        // Period trend: inflow / outflow / net using the default cash-flow-periods endpoint
        _addPeriodsTrend('/manager-reports/financial/cash-flow-periods', t('chartCashFlowOverTime') || 'Cash flow over time',
          [
            { field: 'inflow', label: t('legendInflow') || 'Inflow', color: palette[0] },
            { field: 'outflow', label: t('legendOutflow') || 'Outflow', color: '#c62828' },
            { field: 'net', label: t('legendNet') || 'Net', color: palette[1] },
          ]);
      }

      // ── Iran / UK Comprehensive Income ──────────────────────────────
      if (rt === 'iran_comprehensive_income' || rt === 'uk_comprehensive_income') {
        const npKey = rt === 'uk_comprehensive_income' ? 'profit_for_year' : 'net_profit';
        const ociKey = 'oci_total';
        const totalKey = rt === 'uk_comprehensive_income' ? 'total_comprehensive_income' : 'comprehensive_income';
        _comparisonBar(t('chartNpOciTotal') || 'Net profit / OCI / Comprehensive', [npKey, ociKey, totalKey]);
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
          _addExtraChart(t('chartEquityComponents') || 'Equity components: opening vs closing', {
            type: 'bar',
            data: {
              labels: components.map(c => c.label_fa || c.label || c.key),
              datasets: [
                { label: t('labelOpening') || 'Opening', data: components.map(c => Number(openCells[c.key] || 0)), backgroundColor: palette[1] },
                { label: t('labelClosing') || 'Closing', data: components.map(c => Number(closeCells[c.key] || 0)), backgroundColor: palette[0] },
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
          _addExtraChart('Top Accounts: Debit vs Credit', {
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
          _addExtraChart('Net Balance by Account', {
            type: 'bar',
            data: {
              labels: rows.map(r => r.account_code),
              datasets: [{
                label: 'Net Balance',
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
            const label = rt.includes('purchase') ? 'Purchase' : 'Sales';
            _addExtraChart(label + ' Trend Over Time' + (filterVal ? ' — ' + filterVal : ''), {
              type: 'bar',
              data: {
                labels: periods.map(p => p.period),
                datasets: [
                  { label: label + ' Amount', data: periods.map(p => p.sales_amount), backgroundColor: palette[0], yAxisID: 'y' },
                  { label: 'Quantity', data: periods.map(p => p.quantity), type: 'line', borderColor: palette[3], backgroundColor: 'transparent', yAxisID: 'y1', tension: 0.3 }
                ]
              },
              options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: 'bottom' } },
                scales: { y: { position: 'left', ticks: { callback: v => formatNum(v) } }, y1: { position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'Qty' } } }
              }
            });
          }).catch(() => {});
      }

      if (rt === 'debtor_creditor') {
        // Debtors vs Creditors donut
        const rows = report.rows || [];
        const debtors = rows.filter(r => r.role === 'debtor');
        const creditors = rows.filter(r => r.role === 'creditor');
        const totalDebt = debtors.reduce((s, r) => s + Math.abs(r.net_delta || 0), 0);
        const totalCred = creditors.reduce((s, r) => s + Math.abs(r.net_delta || 0), 0);
        if (totalDebt || totalCred) {
          _addExtraChart('Receivables vs Payables', {
            type: 'doughnut',
            data: {
              labels: ['Receivables (Debtors)', 'Payables (Creditors)'],
              datasets: [{ label: 'AR vs AP', data: [totalDebt, totalCred], backgroundColor: [palette[0], palette[5]] }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
          });
        }
        // Top entities
        const topEntities = rows.slice().sort((a, b) => Math.abs(b.net_delta || 0) - Math.abs(a.net_delta || 0)).slice(0, 10);
        if (topEntities.length > 2) {
          _addExtraChart('Top Entities by Amount', {
            type: 'bar',
            data: {
              labels: topEntities.map(r => (r.entity_name || 'Unknown').slice(0, 20)),
              datasets: [{
                label: 'Net Amount',
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
          <button class="btn btn-secondary btn-sm" onclick="_exportTableFromEl(document.getElementById('inv-report-preview'),'Inventory_Balance','csv')">CSV</button>
          <button class="btn btn-secondary btn-sm" onclick="_exportTableFromEl(document.getElementById('inv-report-preview'),'Inventory_Balance','pdf')">PDF</button>
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
          <button class="btn btn-secondary btn-sm" onclick="_exportTableFromEl(document.getElementById('inv-report-preview'),'Inventory_Movements','csv')">CSV</button>
          <button class="btn btn-secondary btn-sm" onclick="_exportTableFromEl(document.getElementById('inv-report-preview'),'Inventory_Movements','pdf')">PDF</button>
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
        mgrRunBtn.disabled = true;
        const res = await fetch(url);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || t('failedRunManagerReport'), true);
          return;
        }
        lastManagerReport = data;
        const period = reportPeriodText(data);
        // When no currency filter is selected and data spans multiple currencies,
        // show a banner warning that numbers are summed across currencies and
        // offer a one-click switch to a single-currency view.
        let mixWarning = '';
        if (!currency) {
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
          ${period ? `<div class="report-meta">${escapeHtml(t('periodLabel'))}: ${escapeHtml(period)}${currency ? ' · <span class="ccy-badge ccy-' + escapeHtml(currency) + '">' + escapeHtml(currency) + '</span>' : ''}</div>` : ''}
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
        renderManagerReportChart(data);
        if (mgrExportJsonBtn) mgrExportJsonBtn.disabled = false;
        if (mgrExportCsvBtn) mgrExportCsvBtn.disabled = false;
        if (mgrExportPdfBtn) mgrExportPdfBtn.disabled = false;
      } catch (err) {
        showAlert(t('managerReportError') + ': ' + err.message, true);
      } finally {
        mgrRunBtn.disabled = false;
      }
    }

    async function loadManagerInventoryItems(highlightId) {
      if (typeof highlightId !== 'string' && typeof highlightId !== 'number') highlightId = null;
      if (!mgrMvItemEl) return;
      try {
        const res = await fetch(API + '/manager-reports/inventory/items');
        const rows = await res.json().catch(() => ([]));
        mgrMvItemEl.innerHTML = '<option value="">Select item</option>';
        (rows || []).forEach(i => {
          const opt = document.createElement('option');
          opt.value = i.id;
          opt.textContent = (i.sku ? (i.sku + ' - ') : '') + i.name;
          mgrMvItemEl.appendChild(opt);
        });
        // Render items list
        const listEl = document.getElementById('inv-items-list');
        if (listEl && rows.length) {
          listEl.innerHTML = `<div style="font-size:0.82rem;color:var(--text-muted);margin-bottom:0.3rem;">${rows.length} items registered</div>
          <div style="display:flex;flex-wrap:wrap;gap:0.4rem;">${rows.map(i =>
            `<span data-item-id="${escapeHtml(String(i.id))}" style="display:inline-flex;align-items:center;gap:0.3rem;padding:0.2rem 0.6rem;background:#f1f5f9;border-radius:6px;font-size:0.8rem;border:1px solid var(--border);">
              <strong>${escapeHtml(i.name)}</strong>${i.sku ? ` <span style="color:var(--text-muted);">(${escapeHtml(i.sku)})</span>` : ''}
              ${i.list_price ? ` — ${formatNum(i.list_price)} ${currencyUnit()}` : ''}
            </span>`
          ).join('')}</div>`;
          if (highlightId) flashRow(listEl.querySelector('[data-item-id="' + CSS.escape(String(highlightId)) + '"]'));
        } else if (listEl) {
          listEl.innerHTML = '<div style="font-size:0.82rem;color:var(--text-muted);">No items yet. Add one above.</div>';
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
            unit: (mgrInvItemUnitEl.value || 'unit').trim() || 'unit'
          })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || 'Failed to add inventory item.', true);
          return;
        }
        mgrInvItemNameEl.value = '';
        mgrInvItemSkuEl.value = '';
        showAlert('Inventory item added.');
        await loadManagerInventoryItems(data.id);
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
      if (monthEl && !monthEl.value) monthEl.value = new Date().toISOString().slice(0, 7);
      const monthVal = monthEl ? monthEl.value : new Date().toISOString().slice(0, 7);
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
          const color = pct >= 100 ? 'var(--danger, #c0392b)' : (pct >= 85 ? '#e0a458' : 'var(--accent, #2f6f62)');
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

    async function loadPersonalDashboard() {
      const grid = document.getElementById('pd-kpi-grid');
      if (!grid) return;
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
        const thisMonth = new Date().toISOString().slice(0, 7);
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
              labels: series.map(r => r.period),
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
    }

    (function wirePersonalDashboard() {
      const saveBtn = document.getElementById('pd-budget-save');
      const monthEl = document.getElementById('pd-budget-month');
      if (monthEl) monthEl.addEventListener('change', pdLoadBudgets);
      if (saveBtn) saveBtn.addEventListener('click', async () => {
        const month = (monthEl && monthEl.value) || new Date().toISOString().slice(0, 7);
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
