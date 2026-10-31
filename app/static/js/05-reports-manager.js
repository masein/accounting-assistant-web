
    function renderMiniTable(elId, headers, rows) {
      const el = document.getElementById(elId);
      if (!rows || !rows.length) {
        el.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('noDataYet')) + '</p>';
        return;
      }
      const th = headers.map(h => `<th>${escapeHtml(localizeReportFieldName(h))}</th>`).join('');
      // a date stays on one line (the forecast's weeks broke as "2026-09-" / "28")
      const isDate = (c) => typeof c === 'string' && /^[\u2066-\u2069]?\d{4}[-/]\d{2}[-/]\d{2}[\u2066-\u2069]?$/.test(c);
      const tr = rows.map(r => `<tr>${r.map(c => `<td${isDate(c) ? ' class="date-cell"' : ''}>${c && c.__html ? c.__html : (typeof c === 'string' ? escapeHtml(localizeDynamicText(c)) : c)}</td>`).join('')}</tr>`).join('');
      el.innerHTML = `<table class="mini-table"><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`;
    }

    // Dashboard tabs (deep browser test, 2026-10-02, #36: twelve sections ran
    // to 4,400 px). The KPIs, smart alerts and what changed stay on top; one
    // section opens under them. A section's own fetches (the budgets, the
    // missing references, the forecast details) run when its tab is open, or
    // the next time it opens after a change.
    const DASH_TABS = ['cash', 'arap', 'spend', 'books'];
    const DASH_TAB_KEY = 'aa_dashboard_tab';
    const dashTabLoaders = {
      cash: () => { const ex = document.getElementById('forecast-explorer'); if (ex && ex.open) loadForecastExplorer(); },
      spend: () => loadBudgets(),
      books: () => loadMissingReferences(),
    };
    const dashTabStale = new Set(Object.keys(dashTabLoaders));
    function dashTabCurrent() {
      const on = document.querySelector('#dash-tabs [role="tab"][aria-selected="true"]');
      return on ? on.dataset.tab : 'cash';
    }
    function dashTabRefresh(tab) {
      if (!dashTabLoaders[tab]) return;
      if (onPage('dashboard') && dashTabCurrent() === tab) { dashTabStale.delete(tab); dashTabLoaders[tab](); }
      else dashTabStale.add(tab);
    }
    // a phone's bar scrolls sideways to the chosen tab — never the page up or down
    function dashTabReveal(b) {
      const bar = b.parentElement;
      const br = bar.getBoundingClientRect(), tr = b.getBoundingClientRect();
      if (!br.width) return;
      if (tr.left < br.left) bar.scrollLeft -= br.left - tr.left + 16;
      else if (tr.right > br.right) bar.scrollLeft += tr.right - br.right + 16;
    }
    function selectDashTab(tab, { focus = false, remember = true, load = true } = {}) {
      if (!DASH_TABS.includes(tab)) tab = 'cash';
      document.querySelectorAll('#dash-tabs [role="tab"]').forEach((b) => {
        const on = b.dataset.tab === tab;
        b.setAttribute('aria-selected', on ? 'true' : 'false');
        b.tabIndex = on ? 0 : -1;
        if (on && focus) b.focus();
        if (on) dashTabReveal(b);
      });
      DASH_TABS.forEach((k) => {
        const panel = document.getElementById('dash-panel-' + k);
        if (panel) panel.hidden = k !== tab;
      });
      if (remember) { try { localStorage.setItem(DASH_TAB_KEY, tab); } catch (_) { /* storage blocked */ } }
      if (load && dashTabStale.has(tab) && onPage('dashboard')) { dashTabStale.delete(tab); dashTabLoaders[tab](); }
    }
    function initDashTabs() {
      const bar = document.getElementById('dash-tabs');
      if (!bar || bar.dataset.bound) return;
      bar.dataset.bound = '1';
      bar.addEventListener('click', (e) => {
        const b = e.target.closest('[role="tab"]');
        if (b) selectDashTab(b.dataset.tab);
      });
      // ←/→ move along the bar (mirrored in RTL), Home/End to the ends
      bar.addEventListener('keydown', (e) => {
        const tabs = [...bar.querySelectorAll('[role="tab"]')];
        const i = tabs.indexOf(document.activeElement);
        if (i < 0) return;
        const rtl = getComputedStyle(bar).direction === 'rtl';
        const step = { ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1 }[e.key];
        let j = null;
        if (step) j = (i + step + tabs.length) % tabs.length;
        else if (e.key === 'Home') j = 0;
        else if (e.key === 'End') j = tabs.length - 1;
        if (j === null) return;
        e.preventDefault();
        selectDashTab(tabs[j].dataset.tab, { focus: true });
      });
      // which ends have tabs past them (scrollLeft runs negative in RTL)
      const edges = () => {
        const max = bar.scrollWidth - bar.clientWidth;
        const pos = Math.abs(bar.scrollLeft);
        bar.classList.toggle('more-before', max > 1 && pos > 1);
        bar.classList.toggle('more-after', max > 1 && max - pos > 1);
      };
      bar.addEventListener('scroll', edges, { passive: true });
      if (typeof ResizeObserver === 'function') {
        const ro = new ResizeObserver(edges);           // the bar, and each label as the language changes
        ro.observe(bar);
        bar.querySelectorAll('[role="tab"]').forEach((b) => ro.observe(b));
      }
      let saved = null;
      try { saved = localStorage.getItem(DASH_TAB_KEY); } catch (_) { /* storage blocked */ }
      selectDashTab(saved || 'cash', { remember: false, load: false });
      edges();
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
        if (!res.ok) throw new Error(data.detail || t('msgFailed'));
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
        // the figure and its currency code apart, the code smaller: «21,875.49 GBP» put
        // its code on a second line in a narrow card (scenario S3)
        const kpiValueHtml = (k) => {
          const unit = String(k.unit || '');
          if (k.value == null || !/^[A-Z]{3}$/.test(unit)) return escapeHtml(formatKpiValue(k.value, k.unit));
          return `<span class="kpi-num">${escapeHtml(formatAmount(k.value, unit))}</span> <span class="kpi-unit">${escapeHtml(unit)}</span>`;
        };
        kpiGrid.innerHTML = (data.kpis || []).map(k => `
          <div class="kpi-card">
            <div class="label">${escapeHtml(kpiLabelMap[k.key] ? t(kpiLabelMap[k.key]) : localizeDynamicText(k.label))}</div>
            <div class="value">${kpiValueHtml(k)}</div>
          </div>
        `).join('');

        renderMiniTable(
          'forecast-wrap',
          ['week_start', 'projected_inflow', 'projected_outflow', 'projected_net', 'projected_cash', 'risk'],
          (data.forecast_13_weeks || []).map(r => [
            escapeHtml(formatDisplayDate(r.week_start)),
            formatAmount(r.projected_inflow),
            formatAmount(r.projected_outflow),
            formatAmount(r.projected_net),
            formatAmount(r.projected_cash),
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
        dashTabRefresh('cash');

        loadInsightsPanel('insights-wrap');
        const alertsWrap = document.getElementById('alerts-wrap');
        const alerts = data.alerts || [];
        if (!alerts.length) {
          alertsWrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('noActiveAlerts')) + '</p>';
        } else {
          alertsWrap.innerHTML = alerts.map(a => `
            <div style="border:1px solid var(--border); border-radius:10px; padding:0.55rem; margin-bottom:0.45rem;">
              <span class="alert-chip ${escapeHtml(a.level)}">${escapeHtml(t({ high: 'alertLevelHigh', medium: 'alertLevelMedium', low: 'alertLevelLow' }[a.level] || 'alertLevelLow'))}</span>
              <strong style="display:block; margin-top:0.25rem;">${escapeHtml(localizeDynamicText(a.title))}</strong>
              <div style="font-size:0.82rem; color:var(--text-muted);">${escapeHtml(localizeDynamicText(a.message))}</div>
            </div>
          `).join('');
        }

        renderMiniTable(
          'ar-aging-wrap',
          ['client', 'current', 'days_31_60', 'days_60_plus', 'total'],
          (data.ar_aging || []).map(r => [escapeHtml(r.name), formatAmount(r.current), formatAmount(r.days_31_60), formatAmount(r.days_60_plus), formatAmount(r.total)])
        );
        renderMiniTable(
          'ap-aging-wrap',
          ['vendor', 'current', 'days_31_60', 'days_60_plus', 'total'],
          (data.ap_aging || []).map(r => [escapeHtml(r.name), formatAmount(r.current), formatAmount(r.days_31_60), formatAmount(r.days_60_plus), formatAmount(r.total)])
        );
        renderMiniTable(
          'expense-category-wrap',
          ['category', 'amount'],
          (data.expense_by_category || []).map(r => [escapeHtml(r.category), formatAmount(r.amount)])
        );
        renderMiniTable(
          'vendor-spend-wrap',
          ['vendor', 'amount'],
          (data.spend_by_vendor || []).map(r => [escapeHtml(r.vendor), formatAmount(r.amount)])
        );
        renderMiniTable(
          'profitability-wrap',
          ['client', 'revenue', 'cost', 'profit', 'margin_pct'],
          (data.profitability_by_client || []).map(r => [escapeHtml(localizeDynamicText(r.client)), formatAmount(r.revenue), formatAmount(r.cost), formatAmount(r.profit), (r.margin_pct == null ? '—' : String(r.margin_pct))])
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
          ? `${localizeDynamicText(topProfit.client)} (${formatAmount(topProfit.profit || 0)} ${currencyUnit()})`
          : t('na');
        // A list in the page's own type, dated in the company's calendar — it
        // was a monospace block dated "(2026-10-02)" in a Jalali company.
        const kpi = (key) => (data.kpis || []).find((k) => k.key === key)?.value;
        const overdue = (rows) => (rows || []).reduce((a, r) => a + (r.days_31_60 || 0) + (r.days_60_plus || 0), 0);
        const facts = [
          [t('kpiCashOnHand'), `${formatAmount(kpi('cash_on_hand') || 0)} ${currencyUnit()}`],
          [t('ownerNetProfitMonth'), `${formatAmount(kpi('monthly_net_profit') || 0)} ${currencyUnit()}`],
          [t('kpiMonthlyBurnRate'), `${formatAmount(kpi('burn_rate') || 0)} ${currencyUnit()}/${t('monthWord')}`],
          [t('kpiRunway'), formatKpiValue(kpi('runway_months'), 'months')],
          [t('ownerOverdueAR'), `${formatAmount(overdue(data.ar_aging))} ${currencyUnit()}`],
          [t('ownerOverdueAP'), `${formatAmount(overdue(data.ap_aging))} ${currencyUnit()}`],
          [t('ownerDataHealth'), localDigits(`${data.health_score || 0}/100`)],
          [t('ownerMostProfitableClient'), topProfitText],
        ];
        document.getElementById('owner-pack').innerHTML =
          `<div class="owner-pack-date">${escapeHtml(joinParts([t('ownerPackTitle'), formatDisplayDate(localIsoDate(new Date()))]))}</div>` +
          `<ul>${facts.map(([k, v]) => `<li><span>${escapeHtml(k)}</span> <strong>${escapeHtml(v)}</strong></li>`).join('')}</ul>` +
          `<div class="owner-pack-actions">${escapeHtml(t('ownerPriorityActions'))}</div>` +
          `<ol>${['ownerAction1', 'ownerAction2', 'ownerAction3'].map((k) => `<li>${escapeHtml(t(k))}</li>`).join('')}</ol>`;
        dashTabRefresh('books');
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
        case 'account_ledger': return '/manager-reports/books/account-ledger/' + encodeURIComponent((mgrAccountCodeEl.value || window.__DEFAULT_CASH_CODE || '1110').trim() || '1110');
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
            ${chartImg ? `<img src="${chartImg}" alt="${escapeHtml(t('reportChartAlt'))}">` : ''}
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
      const lang = ['fa', 'en', 'es', 'ar'].includes(currentLanguage) ? currentLanguage : 'en';
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

    // ═══════ What changed on the balance sheet (scenarios AA1–AA6) ═══════
    // Assets rose from 27.95 to 47 billion rials and nothing said why: a section opens
    // into its lines by change, a line into its accounts, an account into its entries.
    const _BS_SECTIONS = [
      { prefixes: ['nca_', 'ca_', 'fa_'], label: 'sectionAssets' },
      { prefixes: ['ncl_', 'cl_'], label: 'sectionLiabilities' },
      { prefixes: ['eq_'], label: 'sectionEquity' },
    ];
    const _bsStatement = (report) => ((report.report_type || '').startsWith('uk_') ? 'uk' : 'iran');
    const _bsLabel = (row) => row.label || (currentLanguage !== 'fa' && row.label_en ? row.label_en : row.label_fa) || row.key;
    const _bsCcy = (report) => (report.metadata && report.metadata.currency) || '';
    const _bsSigned = (n, ccy) => {
      const v = Number(n) || 0;
      // a left-to-right run: in Persian the sign stays before the figure («۴۴٬۳۴۰٬…+» it read)
      return '\u2066' + (v > 0 ? '+' : v < 0 ? '−' : '') + formatAmount(Math.abs(v), ccy || undefined) + '\u2069';
    };
    const _bsPct = (part, whole) => (whole ? localDigits(Math.round(part / Math.abs(whole) * 100) + '%') : '—');
    const _bsDayAfter = (iso) => {
      const d = new Date(iso + 'T00:00:00Z');
      d.setUTCDate(d.getUTCDate() + 1);
      return d.toISOString().slice(0, 10);
    };
    function _bsModal(title) {
      document.getElementById('chart-modal-title').textContent = title;
      document.getElementById('chart-modal-canvas').style.display = 'none';
      document.getElementById('chart-modal-txn-area').style.display = 'none';
      document.getElementById('chart-drilldown-modal').style.display = 'block';
      return document.getElementById('chart-modal-body');
    }
    function _bsChangeCell(v, ccy) {
      const color = v > 0 ? 'var(--success, #15803d)' : (v < 0 ? 'var(--danger, #b91c1c)' : 'var(--text-muted)');
      return `<td class="num" style="color:${color};font-weight:600;white-space:nowrap;">${escapeHtml(_bsSigned(v, ccy))}</td>`;
    }
    // the lines of one section, biggest change first
    function openBalanceSection(report, section) {
      const sec = _BS_SECTIONS[section];
      if (!sec) return;
      const ccy = _bsCcy(report);
      const lines = (report.rows || [])
        .filter((r) => r.row_type === 'line' && sec.prefixes.some((p) => (r.key || '').startsWith(p)))
        .map((r) => ({ row: r, cur: Number(r.amount_current) || 0, pri: Number(r.amount_prior) || 0 }))
        .filter((x) => x.cur || x.pri)
        .map((x) => ({ ...x, change: x.cur - x.pri }))
        .sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
      const cur = lines.reduce((n, x) => n + x.cur, 0);
      const pri = lines.reduce((n, x) => n + x.pri, 0);
      const change = cur - pri;
      const body = _bsModal(t(sec.label));
      const from = formatDisplayDate(report.comparative_as_of || '');
      const to = formatDisplayDate(report.as_of || '');
      const moved = lines.filter((x) => x.change);
      body.innerHTML = `<p class="bsc-summary">${escapeHtml(tf('bscSummary', { section: t(sec.label), prior: formatAmount(pri, ccy || undefined),
          from, current: formatAmount(cur, ccy || undefined), to, change: _bsSigned(change, ccy), pct: _bsPct(change, pri) }))}</p>`
        + (moved.length ? `<div style="overflow-x:auto;"><table class="mini-table bsc-table"><thead><tr>
            <th>${escapeHtml(t('bscLine'))}</th><th>${escapeHtml(from)}</th><th>${escapeHtml(to)}</th>
            <th>${escapeHtml(t('bscChange'))}</th><th>${escapeHtml(t('bscShare'))}</th></tr></thead><tbody>${
          moved.map((x) => `<tr data-bs-line="${escapeHtml(x.row.key)}" style="cursor:pointer;">
            <td dir="auto">${escapeHtml(_bsLabel(x.row))}</td>
            <td class="num">${escapeHtml(formatAmount(x.pri, ccy || undefined))}</td>
            <td class="num">${escapeHtml(formatAmount(x.cur, ccy || undefined))}</td>
            ${_bsChangeCell(x.change, ccy)}
            <td class="num">${escapeHtml(_bsPct(x.change, change))}</td></tr>`).join('')}</tbody></table></div>
            <p class="fc-note">${escapeHtml(t('bscLineHint'))}</p>`
          : `<p class="fc-note">${escapeHtml(t('bscNoChange'))}</p>`);
      body.querySelectorAll('tr[data-bs-line]').forEach((tr) => tr.addEventListener('click', () =>
        openBalanceLine(report, tr.dataset.bsLine, () => openBalanceSection(report, section))));
    }
    // the accounts behind one line, on both dates; an account opens its entries between them
    async function openBalanceLine(report, key, back) {
      const row = (report.rows || []).find((r) => r.key === key) || { key };
      const ccy = _bsCcy(report);
      const body = _bsModal(_bsLabel(row));
      body.innerHTML = `<p class="fc-note">${escapeHtml(t('loading'))}</p>`;
      const q = new URLSearchParams();
      if (report.as_of) q.set('as_of', report.as_of);
      if (report.comparative_as_of) q.set('comparative_as_of', report.comparative_as_of);
      if (ccy) q.set('currency', ccy);
      let d = null;
      try {
        const res = await fetch(API + '/manager-reports/financial/' + _bsStatement(report) + '/balance-sheet/lines/'
          + encodeURIComponent(key) + '?' + q.toString());
        d = res.ok ? await res.json() : null;
      } catch (_) { d = null; }
      if (!d) { body.innerHTML = `<p class="fc-note">${escapeHtml(t('msgFailed'))}</p>`; return; }
      const from = formatDisplayDate(d.comparative_as_of);
      const to = formatDisplayDate(d.as_of);
      const backBtn = back ? `<button type="button" class="btn btn-secondary btn-sm bsc-back" style="margin-bottom:0.6rem;">${escapeHtml(t('bscBack'))}</button>` : '';
      body.innerHTML = backBtn
        + `<p class="bsc-summary">${escapeHtml(tf('bscSummary', { section: _bsLabel(row), prior: formatAmount(d.total.prior, ccy || undefined),
            from, current: formatAmount(d.total.current, ccy || undefined), to, change: _bsSigned(d.total.change, ccy),
            pct: _bsPct(d.total.change, d.total.prior) }))}</p>`
        + (d.accounts.length ? `<div style="overflow-x:auto;"><table class="mini-table bsc-table"><thead><tr>
            <th>${escapeHtml(t('bscAccount'))}</th><th>${escapeHtml(from)}</th><th>${escapeHtml(to)}</th>
            <th>${escapeHtml(t('bscChange'))}</th></tr></thead><tbody>${
          d.accounts.map((a) => {
            const name = a.kind === 'pl_to_date' ? t('bscPlToDate') : joinParts([localCode(a.code), a.name]);
            const open = a.code ? ` data-code="${escapeHtml(a.code)}" data-name="${escapeHtml(a.name || a.code)}" style="cursor:pointer;"` : '';
            return `<tr${open}><td dir="auto">${escapeHtml(name)}</td>
              <td class="num">${escapeHtml(formatAmount(a.prior, ccy || undefined))}</td>
              <td class="num">${escapeHtml(formatAmount(a.current, ccy || undefined))}</td>${_bsChangeCell(a.change, ccy)}</tr>`;
          }).join('')}</tbody></table></div>
          <p class="fc-note">${escapeHtml(t('bscAccountHint'))}</p>`
          : `<p class="fc-note">${escapeHtml(t('bscNoChange'))}</p>`);
      const b = body.querySelector('.bsc-back');
      if (b) b.addEventListener('click', back);
      body.querySelectorAll('tr[data-code]').forEach((tr) => tr.addEventListener('click', () =>
        showTransactionDrilldown(tr.dataset.name, { account_code: tr.dataset.code, from_date: _bsDayAfter(d.comparative_as_of), to_date: d.as_of })));
    }

    function renderManagerReportChart(report, inputs) {
      if (!mgrReportChartPanelEl || !mgrReportChartEl) return;
      _destroyExtraCharts();
      const chart = renderReportChart(mgrReportChartEl, report, managerReportChart);
      managerReportChart = chart;
      // a balance sheet's bars open what changed in that section (scenario AA1)
      const bsType = (report.report_type || '').toLowerCase();
      if (chart && (bsType === 'iran_balance_sheet' || bsType === 'uk_balance_sheet')) {
        chart.options.onClick = (e, els) => { if (els.length) openBalanceSection(report, els[0].index); };
        chart.options.onHover = (e, els) => { if (e.native && e.native.target) e.native.target.style.cursor = els.length ? 'pointer' : 'default'; };
        chart.options.plugins.subtitle = { display: true, text: t('bscChartHint'), padding: { bottom: 8 } };
        chart.update('none');
      }
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
        // every line ranked by how much it moved between the two dates (scenario AA5)
        const moved = (report.rows || []).filter((r) => r.row_type === 'line')
          .map((r) => ({ r, change: (Number(r.amount_current) || 0) - (Number(r.amount_prior) || 0) }))
          .filter((x) => x.change).sort((a, b) => Math.abs(b.change) - Math.abs(a.change)).slice(0, 12);
        if (moved.length) {
          _addExtraChart(t('chartBalanceChange'), {
            type: 'bar',
            data: { labels: moved.map((x) => _bsLabel(x.r)),
                    datasets: [{ label: t('bscChange'), data: moved.map((x) => x.change),
                                 backgroundColor: moved.map((x) => (x.change > 0 ? palette[0] : palette[3])) }] },
            options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
                       scales: { x: { ticks: { callback: (v) => formatNum(v) } } } },
          }, (e, els) => { if (els.length) openBalanceLine(report, moved[els[0].index].r.key, null); });
        }
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
              labels: rows.map(r => localCode(r.account_code) + ' ' + (r.account_name || '').slice(0, 20)),
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
              labels: rows.map(r => localCode(r.account_code)),
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
          <div><span>${escapeHtml(t('invTotalItems'))}</span><strong>${localDigits(rows.length)}</strong></div>
          <div><span>${escapeHtml(t('invTotalOnHand'))}</span><strong>${totalQty.toLocaleString(numLocale())}</strong></div>
          <div><span>${escapeHtml(t('fieldInventoryValue'))}</span><strong>${formatAmount(totalValue)} ${currencyUnit()}</strong></div>
          <div><span>${escapeHtml(t('invTotalCogs'))}</span><strong>${formatAmount(totalCOGS)} ${currencyUnit()}</strong></div>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-bottom:0.5rem;">
          <button type="button" class="btn btn-secondary btn-sm" data-action="export-table" data-target="inv-report-preview" data-name="Inventory_Balance" data-format="csv">CSV</button>
          <button type="button" class="btn btn-secondary btn-sm" data-action="export-table" data-target="inv-report-preview" data-name="Inventory_Balance" data-format="pdf">PDF</button>
        </div>
        <div style="max-height:400px;overflow:auto;">
        <table class="detail-table">
          <thead><tr>
            <th>${escapeHtml(t('stockColItem'))}</th><th>${escapeHtml(t('labelSKU'))}</th><th>${escapeHtml(t('labelUnit'))}</th><th class="num">${escapeHtml(t('invColIn'))}</th><th class="num">${escapeHtml(t('invColOut'))}</th>
            <th class="num">${escapeHtml(t('stockColOnHand'))}</th><th class="num">${escapeHtml(t('invColAvgCost'))}</th><th class="num">${escapeHtml(t('stockColValue'))}</th><th class="num">${escapeHtml(t('fieldCOGS'))}</th>
          </tr></thead>
          <tbody>${rows.map(r => {
            const valPct = totalValue > 0 ? Math.round(r.inventory_value / totalValue * 100) : 0;
            return `<tr>
              <td><strong>${escapeHtml(r.item_name)}</strong></td>
              <td>${escapeHtml(r.sku || '—')}</td>
              <td>${escapeHtml(r.unit || t('invUnitDefault'))}</td>
              <td class="num">${r.qty_in.toLocaleString(numLocale())}</td>
              <td class="num">${r.qty_out.toLocaleString(numLocale())}</td>
              <td class="num" style="font-weight:600;">${r.on_hand_qty.toLocaleString(numLocale())}</td>
              <td class="num">${formatAmount(r.average_cost)}</td>
              <td class="num">
                <div style="display:flex;align-items:center;gap:0.4rem;justify-content:flex-end;">
                  ${formatAmount(r.inventory_value)}
                  <span style="display:inline-block;width:40px;height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden;">
                    <span style="display:block;height:100%;width:${valPct}%;background:var(--primary);border-radius:3px;"></span>
                  </span>
                </div>
              </td>
              <td class="num" style="color:${r.cogs > 0 ? '#c62828' : 'var(--text)'};">${formatAmount(r.cogs)}</td>
            </tr>`;
          }).join('')}</tbody>
          <tfoot><tr style="font-weight:700;background:#f1f5f9;">
            <td colspan="3">${escapeHtml(t('tableTotal'))}</td>
            <td class="num">${rows.reduce((s,r)=>s+r.qty_in,0).toLocaleString(numLocale())}</td>
            <td class="num">${rows.reduce((s,r)=>s+r.qty_out,0).toLocaleString(numLocale())}</td>
            <td class="num">${totalQty.toLocaleString(numLocale())}</td>
            <td class="num">—</td>
            <td class="num">${formatAmount(totalValue)}</td>
            <td class="num" style="color:#c62828;">${formatAmount(totalCOGS)}</td>
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
          <div><span>${escapeHtml(t('invTotalMovements'))}</span><strong>${localDigits(rows.length)}</strong></div>
          <div><span>${escapeHtml(t('invQtyIn'))}</span><strong style="color:#2e7d32;"><bdi dir="ltr">+${qtyIn.toLocaleString(numLocale())}</bdi></strong></div>
          <div><span>${escapeHtml(t('invQtyOut'))}</span><strong style="color:#c62828;"><bdi dir="ltr">-${qtyOut.toLocaleString(numLocale())}</bdi></strong></div>
          <div><span>${escapeHtml(t('invValueIn'))}</span><strong style="color:#2e7d32;">${formatAmount(totalIn)}</strong></div>
          <div><span>${escapeHtml(t('invValueOut'))}</span><strong style="color:#c62828;">${formatAmount(totalOut)}</strong></div>
        </div>

        <h4 style="margin:0.75rem 0 0.3rem;font-size:0.9rem;">${escapeHtml(t('invSummaryByItem'))}</h4>
        <div style="max-height:180px;overflow:auto;margin-bottom:1rem;">
        <table class="mini-table"><thead><tr><th>${escapeHtml(t('stockColItem'))}</th><th class="num">${escapeHtml(t('invColIn'))}</th><th class="num">${escapeHtml(t('invColOut'))}</th><th class="num">${escapeHtml(t('invColAdj'))}</th><th class="num">${escapeHtml(t('invColNetValue'))}</th></tr></thead>
          <tbody>${Object.entries(byItem).map(([name, v]) => `<tr>
            <td><strong>${escapeHtml(name)}</strong></td>
            <td class="num" style="color:#2e7d32;"><bdi dir="ltr">+${v.in.toLocaleString(numLocale())}</bdi></td>
            <td class="num" style="color:#c62828;"><bdi dir="ltr">-${v.out.toLocaleString(numLocale())}</bdi></td>
            <td class="num">${v.adj.toLocaleString(numLocale())}</td>
            <td class="num" style="font-weight:600;">${formatAmount(v.value)}</td>
          </tr>`).join('')}</tbody>
        </table>
        </div>

        <h4 style="margin:0.75rem 0 0.3rem;font-size:0.9rem;">${escapeHtml(t('invMovementLog'))}</h4>
        <div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-bottom:0.5rem;">
          <input type="text" id="inv-mv-search" placeholder="${escapeHtml(t('invSearchMovements'))}" style="width:200px;padding:0.35rem 0.6rem;font-size:0.85rem;margin:0;">
          <button type="button" class="btn btn-secondary btn-sm" data-action="export-table" data-target="inv-report-preview" data-name="Inventory_Movements" data-format="csv">CSV</button>
          <button type="button" class="btn btn-secondary btn-sm" data-action="export-table" data-target="inv-report-preview" data-name="Inventory_Movements" data-format="pdf">PDF</button>
        </div>
        <div id="inv-mv-table-wrap" style="max-height:350px;overflow:auto;">
        <table class="detail-table"><thead><tr>
          <th>${t('labelDate')}</th><th>${escapeHtml(t('stockColItem'))}</th><th>${escapeHtml(t('labelType'))}</th><th class="num">${t('labelQuantity')}</th>
          <th class="num">${escapeHtml(t('stockColUnitCost'))}</th><th class="num">${escapeHtml(t('stockColValue'))}</th><th>${t('labelReference')}</th><th>${t('labelDescription')}</th>
        </tr></thead>
          <tbody>${rows.map(r => `<tr>
            <td>${escapeHtml(formatDisplayDate(r.movement_date))}</td>
            <td><strong>${escapeHtml(r.item_name)}</strong></td>
            <td><span style="display:inline-block;padding:0.15rem 0.5rem;border-radius:4px;font-size:0.78rem;font-weight:600;color:${typeColor(r.movement_type)};background:${typeBg(r.movement_type)};">${escapeHtml(({ IN: t('movementIn'), OUT: t('movementOut'), ADJUSTMENT: t('movementAdjustment') })[r.movement_type] || r.movement_type)}</span></td>
            <td class="num">${r.quantity.toLocaleString(numLocale())}</td>
            <td class="num">${formatAmount(r.unit_cost)}</td>
            <td class="num" style="font-weight:600;">${formatAmount(r.movement_value)}</td>
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
        const uk = (window.__REPORTING_LOCALE === 'uk');
        const q = new URLSearchParams();
        if (type === 'balance_sheet' && (ir || uk)) {
          // The Iranian and the UK balance sheets read `as_of` / `comparative_as_of`: a UK sheet was
          // sent `to_date`, which it ignored, and always compared today with a year before (QA 2026-10-16)
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
        const buttons = others.map(ccy => `<button type="button" class="btn btn-secondary btn-sm mgr-ccy-switch" data-ccy="${escapeHtml(ccy)}">${escapeHtml(tf('currencyViewOnly', { currency: ccy }))}</button>`).join(' ');
        mixWarning = `<div class="report-meta" style="margin-bottom:0.6rem;">${escapeHtml(baseViewNoteText())}
            ${buttons ? `<div style="margin-top:0.35rem;display:flex;gap:0.35rem;flex-wrap:wrap;">${buttons}</div>` : ''}
          </div>`;
      } else if (!currency && data && data.currency) {
        // Server resolved a single-currency view (reporting currency by
        // default): say which, and offer the other currencies separately.
        const others = Array.isArray(data.other_currencies) ? data.other_currencies : [];
        if (others.length) {
          const buttons = others.map(ccy => `<button type="button" class="btn btn-secondary btn-sm mgr-ccy-switch" data-ccy="${escapeHtml(ccy)}">${escapeHtml(tf('currencyViewOnly', { currency: ccy }))}</button>`).join(' ');
          mixWarning = `<div class="report-meta" style="margin-bottom:0.6rem;">${escapeHtml(tf('currencyViewNote', { currency: data.currency, others: joinList(others) }))}
            <div style="margin-top:0.35rem;display:flex;gap:0.35rem;flex-wrap:wrap;">${buttons}</div>
          </div>`;
        }
      } else if (!currency) {
        const meta = window.__FX_META;
        const used = (meta && Array.isArray(meta.used_currencies)) ? meta.used_currencies : [];
        if (used.length > 1) {
          const buttons = used.map(ccy => `<button type="button" class="btn btn-secondary btn-sm mgr-ccy-switch" data-ccy="${escapeHtml(ccy)}">${escapeHtml(t('mgrCcyOnly')).replace('{ccy}', `<span class="ccy-badge ccy-${escapeHtml(ccy)}">${escapeHtml(ccy)}</span>`)}</button>`).join(' ');
          mixWarning = `<div style="background:#fef3c7;border:1px solid #fcd34d;color:#92400e;padding:0.55rem 0.75rem;border-radius:8px;margin-bottom:0.6rem;font-size:0.85rem;">
            ⚠️ ${escapeHtml(tf('mgrNoCurrencyWarning', { list: used.join(', ') }))}
            <div style="margin-top:0.35rem;display:flex;gap:0.35rem;flex-wrap:wrap;">${buttons}</div>
          </div>`;
        }
      }
      mgrReportPreviewEl.innerHTML = `
        ${mixWarning}
        ${period ? `<div class="report-meta">${escapeHtml(t('periodLabel'))}: ${escapeHtml(period)}${currency ? partSep() + '<span class="ccy-badge ccy-' + escapeHtml(currency) + '">' + escapeHtml(currency === 'ALL' ? tf('currencyAllInBase', { base: baseCurrencyCode() }) : currency) + '</span>' : ''}</div>` : ''}
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
      // a balance-sheet line opens the accounts behind it (scenario AA2)
      mgrReportPreviewEl.querySelectorAll('tr[data-bs-line]').forEach((tr) => {
        tr.style.cursor = 'pointer';
        tr.title = t('bscRowHint');
        tr.addEventListener('click', () => openBalanceLine(data, tr.dataset.bsLine, null));
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
              <strong>${escapeHtml(i.name)}</strong>${i.sku ? ` <span style="color:var(--text-muted);">(<bdi>${escapeHtml(i.sku)}</bdi>)</span>` : ''}${i.barcode ? ` <span style="color:var(--text-muted);" dir="ltr">▮ ${escapeHtml(i.barcode)}</span>` : ''}
              ${i.list_price ? ` — ${formatAmount(i.list_price)} ${currencyUnit()}` : ''}
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
        showAlert(t('msgInvItemNameRequired'), true);
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
          showAlert(data.detail || t('msgInvItemAddFailed'), true);
          return;
        }
        mgrInvItemNameEl.value = '';
        mgrInvItemSkuEl.value = '';
        ['mgr-inv-item-barcode', 'mgr-inv-item-reorder'].forEach((id) => { const el = document.getElementById(id); if (el) el.value = ''; });
        showAlert(t('msgInvItemAdded'));
        await loadManagerInventoryItems(data.id);
        if (typeof loadStockPanel === 'function') loadStockPanel();
      } catch (err) {
        showAlert(t('msgInvItemAddError') + err.message, true);
      } finally {
        mgrAddItemBtn.disabled = false;
      }
    }

    async function addManagerInventoryMovement() {
      const itemId = (mgrMvItemEl.value || '').trim();
      if (!itemId) {
        showAlert(t('msgInvSelectItem'), true);
        return;
      }
      try {
        mgrAddMvBtn.disabled = true;
        const payload = {
          item_id: itemId,
          movement_date: (
            (invToDateEl && invToDateEl.value)
            || (mgrToDateEl && mgrToDateEl.value)
            || localIsoDate(new Date())
          ),
          movement_type: (mgrMvTypeEl.value || 'IN'),
          quantity: parseFloat(mgrMvQtyEl.value || '0'),
          unit_cost: parseMoney(mgrMvCostEl.value || '0')
        };
        const res = await fetch(API + '/manager-reports/inventory/movements', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || t('msgInvMoveAddFailed'), true);
          return;
        }
        showAlert(t('msgInvMoveAdded'));
        if (typeof stockShowValuation === 'function') stockShowValuation().catch(() => {});
      } catch (err) {
        showAlert(t('msgInvMoveAddError') + err.message, true);
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
          .filter(a => { const k = a.class_code || a.code || ''; return k.length > 2 && (k.startsWith('61') || k.startsWith('62')); })
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
        if (!res.ok) throw new Error(data.detail || t('msgFailed'));
        const rows = data.rows || [];
        if (!rows.length) {
          wrap.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('pdNoBudgets')) + '</p>';
          return;
        }
        // each in its own currency, named when there are several (docs/PLAN_CURRENCIES.md phase 4)
        const several = new Set(rows.map(r => r.currency)).size > 1
          || !document.getElementById('pd-budget-currency-cell')?.hidden;
        wrap.innerHTML = rows.map(r => {
          const pct = Math.max(0, Math.min(150, Number(r.utilization_pct) || 0));
          const color = pct >= 100 ? 'var(--danger)' : (pct >= 85 ? 'var(--accent)' : 'var(--success)');
          return `
            <div style="margin-bottom:0.55rem;">
              <div style="display:flex; justify-content:space-between; font-size:0.85rem;">
                <span>${escapeHtml(r.label || r.category)}</span>
                <span><bdi>${formatAmount(r.actual_amount, r.currency)} / ${formatAmount(r.limit_amount, r.currency)}${several ? ' ' + escapeHtml(r.currency || '') : ''}</bdi> (${escapeHtml(String(r.utilization_pct))}%)</span>
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
      const lowest = tf('forecastLowest', { amount: formatAmount(low.projected_cash), week: formatDisplayDate(low.week_start) });
      el.textContent = neg ? joinParts([tf('forecastNegative', { week: formatDisplayDate(neg.week_start) }), lowest]) : lowest;
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
      if (d.count) lines.push(tf('forecastDoubtful', { n: d.count, days: d.after_days, amount: formatAmount(d.total) }));
      const b = data.baseline || {};
      lines.push(b.weeks_of_history >= 4
        ? tf('forecastUsualWeek', { inflow: formatAmount(b.inflow), outflow: formatAmount(b.outflow), weeks: b.weeks_of_history })
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
          + '<span class="fc-amt">' + escapeHtml(formatAmount(i.amount)) + '</span></li>').join('');
        const usual = (w.baseline_in || w.baseline_out)
          ? '<div class="fc-note">' + escapeHtml(tf('forecastUsualLine', { inflow: formatAmount(w.baseline_in), outflow: formatAmount(w.baseline_out) })) + '</div>'
          : '';
        return '<details class="fc-week' + (w.risk ? ' fc-risk' : '') + '"><summary><strong>'
          + escapeHtml(formatDisplayDate(w.week_start)) + '</strong> — '
          + escapeHtml(tf('forecastWeekLine', { inflow: formatAmount(w.inflow), outflow: formatAmount(w.outflow), closing: formatAmount(w.closing) }))
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
        + escapeHtml(joinParts([formatDisplayDate(i.date), forecastItemLabel(i), formatAmount(i.amount)])) + '</label>';
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
      const amount = parseMoney(form.elements.oneoff_amount.value);
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
      const cell = (v) => '<td>' + escapeHtml(formatAmount(v)) + '</td>';
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
        if (!res.ok) throw new Error(data.detail || t('msgFailed'));
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
            if (!r.ok) throw new Error(result.detail || t('msgFailed'));
            out.innerHTML = forecastScenarioHtml(result);
          } catch (_) {
            out.innerHTML = '<p class="fc-note">' + escapeHtml(t('forecastError')) + '</p>';
          }
        });
      } catch (_) {
        body.innerHTML = '<p class="fc-note">' + escapeHtml(t('forecastError')) + '</p>';
      }
    }

    // A person's books: the currency they count in (scenario Z3). A company's stays with the platform admin.
    async function loadBooksCurrency() {
      const panel = document.getElementById('books-currency-panel');
      if (!panel) return;
      panel.hidden = currentRole !== 'personal';
      if (panel.hidden) return;
      const res = await fetch(API + '/personal/currency');
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { panel.hidden = true; return; }
      const sel = document.getElementById('bc-select');
      sel.innerHTML = (d.options || []).map((o) => `<option value="${escapeHtml(o.code)}">${escapeHtml(o.name)} (${escapeHtml(o.code)})</option>`).join('');
      sel.value = d.currency;
      sel.dataset.current = d.currency;
      // once the books have entries their currency stays (they'd count as nothing without a rate)
      sel.disabled = !!d.locked;
      document.getElementById('bc-save').hidden = !!d.locked;
      document.getElementById('bc-status').textContent = tf('bcNow', { name: d.name || d.currency })
        + (d.locked ? ' ' + t('bcLocked') : '');
    }
    async function saveBooksCurrency() {
      const sel = document.getElementById('bc-select');
      const code = sel.value;
      if (!code || code === sel.dataset.current) return;
      const label = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent : code;
      if (!(await uiConfirm({ message: tf('bcConfirm', { name: label }), confirmLabel: t('bcSave') }))) { sel.value = sel.dataset.current; return; }
      const res = await fetch(API + '/personal/currency', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ currency: code }) });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { showAlert(typeof d.detail === 'string' ? d.detail : t('msgFailed'), true); return; }
      sel.dataset.current = d.currency;
      document.getElementById('bc-status').textContent = tf('bcNow', { name: d.name || d.currency });
      await loadReportingCurrency(true);                // every page's amounts in it now
      loadPersonalDashboard();
      showAlert(tf('bcDone', { name: d.name || d.currency }));
    }
    (function wireBooksCurrency() {
      const btn = document.getElementById('bc-save');
      if (btn) btn.addEventListener('click', () => saveBooksCurrency().catch(() => {}));
    })();

    // Each currency the books hold, on its own (docs/PLAN_CURRENCIES.md phase 2): a row of figures for each,
    // and the details — categories, the months, the report card — in the one chosen. Omani rials with no
    // rate counted as nothing when everything was in the books' currency.
    let _pdCurrency = null;
    async function pdLoadCurrencies() {
      try {
        const res = await fetch(API + '/personal/currencies');
        if (!res.ok) return [];
        return (await res.json()).currencies || [];
      } catch (_) { return []; }
    }
    function pdRenderCurrencies(list) {
      const rows = document.getElementById('pd-ccy-rows');
      const pick = document.getElementById('pd-ccy-pick');
      if (!rows || !pick) return;
      // the figures come from here even in one currency: a person's cash is the bank and the wallets
      // (the dashboard's counted a company's, and left the wallet out)
      const several = list.length > 1;
      rows.hidden = !list.length;
      pick.hidden = !several;
      if (!several) pick.innerHTML = '';
      const card = (label, v, ccy) => `<div class="kpi-card"><div class="label">${escapeHtml(label)}</div>`
        + `<div class="value">${escapeHtml(formatKpiValue(v, ccy))}</div></div>`;
      rows.innerHTML = list.map(c => `<div class="pd-ccy-row" data-ccy="${escapeHtml(c.currency)}">`
        + (several ? `<div class="pd-ccy-name">${escapeHtml(c.name)}</div>` : '') + '<div class="kpi-grid">'
        + card(t('kpiCashOnHand'), c.cash, c.currency) + card(t('pdKpiSpent'), c.spent, c.currency)
        + card(t('pdKpiSaved'), c.saved, c.currency) + '</div></div>').join('');
      // the budget form's currency, when there are several
      const bcell = document.getElementById('pd-budget-currency-cell');
      const bsel = document.getElementById('pd-budget-currency');
      if (bcell && bsel) {
        bcell.hidden = !several;
        bsel.innerHTML = list.map(c => `<option value="${escapeHtml(c.currency)}">${escapeHtml(c.name)}</option>`).join('');
        if (_pdCurrency) bsel.value = _pdCurrency;
      }
      if (!several) return;
      pick.innerHTML = `<span class="pd-ccy-label">${escapeHtml(t('pdCcyShow'))}</span>` + list.map(c =>
        `<button type="button" class="pd-ccy-btn" data-ccy="${escapeHtml(c.currency)}" aria-pressed="${c.currency === _pdCurrency}">`
        + `${escapeHtml(c.name)}</button>`).join('');
    }
    document.getElementById('pd-ccy-pick')?.addEventListener('click', (e) => {
      const btn = e.target.closest('.pd-ccy-btn');
      if (!btn || btn.dataset.ccy === _pdCurrency) return;
      _pdCurrency = btn.dataset.ccy;
      document.querySelectorAll('#pd-ccy-pick .pd-ccy-btn').forEach(b => b.setAttribute('aria-pressed', String(b === btn)));
      const bsel = document.getElementById('pd-budget-currency');
      if (bsel) bsel.value = _pdCurrency;
      pdLoadDetail();
      loadReportCard();
      loadNetWorth();
    });

    async function loadPersonalDashboard() {
      const grid = document.getElementById('pd-kpi-grid');
      if (!grid) return;
      loadInsightsPanel('pd-insights-wrap');
      loadBooksCurrency().catch(() => {});
      const list = await pdLoadCurrencies();
      if (!list.some(c => c.currency === _pdCurrency)) _pdCurrency = list.length ? list[0].currency : null;
      pdRenderCurrencies(list);
      grid.hidden = list.length > 0;            // each currency's own row instead
      await pdLoadDetail();
      pdFillBudgetCategories();
      pdLoadBudgets();
      loadNetWorth();
      loadReportCard();
      loadGoals();
      loadHousehold();
    }

    async function pdLoadDetail() {
      const grid = document.getElementById('pd-kpi-grid');
      if (!grid) return;
      const ccy = _pdCurrency || undefined;
      try {
        if (!window.__FX_META) { try { await loadFxMetadata(); } catch (_) { /* offline */ } }
        await loadReportingCurrency();
        const res = await fetch(API + '/reports/owner-dashboard' + (ccy ? '?currency=' + encodeURIComponent(ccy) : ''));
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || t('msgFailed'));
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
          cats.map(r => [escapeHtml(r.category), formatAmount(r.amount, ccy)]));
        const catCanvas = document.getElementById('pd-cat-chart');
        if (_pdCatChart && !cats.length) { _pdCatChart.destroy(); _pdCatChart = null; }
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
        if (_pdTrendChart && !series.length) { _pdTrendChart.destroy(); _pdTrendChart = null; }
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
    function rcLang() { return ['fa', 'en', 'es', 'ar'].includes(currentLanguage) ? currentLanguage : 'en'; }
    // Names from the chart can be Persian inside an English line (and the other
    // way round); numbers with a sign flip in right-to-left text. Isolate both.
    function rcName(text) { return `<bdi>${escapeHtml(text)}</bdi>`; }
    function rcSigned(n, suffix = '', ccy) {
      return '\u2066' + localDigits((n > 0 ? '+' : '') + formatAmount(n, ccy) + suffix) + '\u2069';   // «٪» in Persian
    }

    async function loadReportCard() {
      const sel = document.getElementById('rc-month');
      const figs = document.getElementById('rc-figures');
      if (!sel || !figs) return;
      const q = new URLSearchParams({ lang: rcLang() });
      if (sel.value) q.set('month', sel.value);
      if (_pdCurrency) q.set('currency', _pdCurrency);
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
      const cur = d.currency;                     // the month in one currency's own amounts
      const amt = (v) => formatAmount(v, cur);
      figs.innerHTML = [
        fig(t('rcIncome'), amt(d.income), tf('rcLastMonth', { v: amt(prev.income || 0) })),
        fig(t('rcSpending'), amt(d.spending), d.average_spending_3m == null ? '' : tf('rcAverage', { v: amt(d.average_spending_3m) })),
        fig(t('rcSaved'), amt(d.saved), tf('rcLastMonth', { v: amt(prev.saved || 0) }), d.saved >= 0 ? 'good' : 'bad'),
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
          `${rcName(c.category)} <bdi dir="ltr">${escapeHtml(amt(c.amount))}</bdi>`
          + (c.change_pct == null ? '' : ` <span class="goal-meta">(${escapeHtml(rcSigned(c.change_pct, '%'))})</span>`)).join(partSep()) + '</p>');
      }
      if (d.biggest_rise) {
        bits.push(`<p>${escapeHtml(tf('rcBiggestRise', { cat: '\u2068' + d.biggest_rise.category + '\u2069', v: rcSigned(d.biggest_rise.increase, '', cur) }))}</p>`);
      }
      const nw = d.net_worth || {};
      // net worth is in the books' currency until it is per currency (docs/PLAN_CURRENCIES.md phase 3)
      if (!nw.currency || nw.currency === cur) {
        bits.push(`<p>${escapeHtml(tf('rcNetWorth', { v: rcSigned(nw.change || 0, '', cur), end: amt(nw.end || 0) }))}</p>`);
      }
      if (d.goals.length) {
        bits.push(`<p>${escapeHtml(t('goalsTitle'))}: ` + d.goals.map(g => `${rcName(g.name)} <bdi dir="ltr">${escapeHtml(localDigits(formatNum(g.percent) + '%'))}</bdi>`).join(partSep()) + '</p>');
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
        sel.innerHTML = accs.filter(a => (a.class_code || a.code || '').startsWith('1'))           // assets, by class
          .map(a => `<option value="${escapeHtml(a.code)}">${escapeHtml(localCode(a.code))} — ${escapeHtml(a.name)}</option>`).join('');
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
        // in the currency of the account it saves into (docs/PLAN_CURRENCIES.md phase 3)
        const amt = (v) => formatAmount(v, g.currency) + (g.currency ? ' ' + g.currency : '');
        const meta = [tf('goalProgress', { v: formatAmount(g.current, g.currency), target: amt(g.target_amount) })];
        if (!g.reached && g.needed_per_month != null) meta.push(tf('goalNeeded', { v: amt(g.needed_per_month), date: formatDisplayDate(g.target_date) }));
        if (!g.reached && g.pace_per_month > 0) meta.push(tf('goalPace', { v: amt(g.pace_per_month) }));
        return `<div class="goal${g.reached ? ' reached' : ''}" data-id="${escapeHtml(g.id)}">
          <div class="goal-top"><span class="goal-name">${rcName(g.name)}</span>
            <span>${badge} <button type="button" class="btn btn-secondary btn-sm goal-del" data-id="${escapeHtml(g.id)}" aria-label="${escapeHtml(t('goalDelete'))}">×</button></span></div>
          <div class="goal-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Number(g.percent)}"><span style="width:${Math.max(0, Math.min(100, Number(g.percent)))}%"></span></div>
          <div class="goal-meta"><bdi dir="ltr">${escapeHtml(localDigits(formatNum(g.percent) + '%'))}</bdi>${partSep()}${rcName(g.account_name || g.account_code)}${partSep()}${meta.map(m => `<bdi>${escapeHtml(m)}</bdi>`).join(partSep())}</div>
        </div>`;
      }).join('');
    }

    document.getElementById('rc-month')?.addEventListener('change', loadReportCard);
    document.getElementById('goal-save')?.addEventListener('click', async () => {
      const name = document.getElementById('goal-name').value.trim();
      const account_code = document.getElementById('goal-account').value;
      const target_amount = parseMoney(document.getElementById('goal-target').value || '0');
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
        const cell = document.getElementById('pd-budget-currency-cell');
        const currency = cell && !cell.hidden ? (document.getElementById('pd-budget-currency')?.value || null) : null;
        try {
          const res = await fetch(API + '/budgets', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ month, category, limit_amount: limit, ...(currency ? { currency } : {}) }),
          });
          if (res.ok) { showAlert(t('btnSaveBudget') + ' ✓'); pdLoadBudgets(); }
          else { const d = await res.json().catch(() => ({})); showAlert(d.detail || t('msgFailed'), true); }
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
          .filter(a => (a.class_code || a.code || '').startsWith('1'))
          .map(a => `<option value="${escapeHtml(a.code)}">${escapeHtml(localCode(a.code))} — ${escapeHtml(a.name)}</option>`)
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
            <td>${escapeHtml(r.account_name || localCode(r.account_code))}</td>
            <td>${escapeHtml(String(r.quantity))} ${escapeHtml(r.unit)}</td>
            <td style="text-align:end;"><button class="btn btn-secondary btn-sm nw-h-del" data-id="${escapeHtml(r.id)}" aria-label="${escapeHtml(t('btnDelete'))}" title="${escapeHtml(t('btnDelete'))}">×</button></td>
          </tr>`).join('')}</tbody></table>`;
      } catch (_) {
        wrap.innerHTML = '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('nwNoHoldings')) + '</p>';
      }
    }

    async function loadNetWorth() {
      const headline = document.getElementById('nw-headline');
      if (!headline) return;
      try {
        // the currency chosen above, in its own amounts; the others listed under it (docs/PLAN_CURRENCIES.md)
        const d = await (await fetch(API + '/personal/net-worth' + (_pdCurrency ? '?currency=' + encodeURIComponent(_pdCurrency) : ''))).json();
        const gain = d.unrealized_gain || 0;
        const others = (d.by_currency || []).filter(x => x.currency !== d.currency);
        const gainColor = gain > 0 ? 'var(--success)' : (gain < 0 ? 'var(--danger)' : 'var(--text-muted)');
        headline.innerHTML = `
          <div style="font-size:1.6rem; font-weight:700;">${escapeHtml(formatAmount(d.net_worth, d.currency))} ${escapeHtml(d.currency)}</div>
          <div style="font-size:0.82rem; color:var(--text-muted);">
            ${escapeHtml(t('nwAssets'))}: ${escapeHtml(formatAmount(d.total_assets, d.currency))}${partSep()}${escapeHtml(t('nwDebts'))}: ${escapeHtml(formatAmount(d.total_liabilities, d.currency))}
          </div>
          ${gain ? `<div style="font-size:0.82rem; color:${gainColor};">${escapeHtml(t('nwUnrealized'))}: ${escapeHtml(formatAmount(gain, d.currency))}</div>` : ''}
          ${(d.missing_rates || []).length ? `<div class="alert-chip medium" style="margin-top:0.35rem;">${escapeHtml(tf('nwMissingRate', { units: d.missing_rates.join(', ') }))}</div>` : ''}
          ${others.length ? `<div class="nw-others">${escapeHtml(t('nwOthers'))}: ${others.map(x =>
            `<bdi>${escapeHtml(formatAmount(x.net_worth, x.currency))} ${escapeHtml(x.name || x.currency)}</bdi>`).join(partSep())}</div>` : ''}
        `;

        renderMiniTable('nw-breakdown', ['account_name', 'value'],          // «Item» had no translation
          [...d.assets, ...d.liabilities].map(l => [
            escapeHtml(l.account_name) + (l.revalued ? ' ★' : ''),
            formatAmount(l.market_value, d.currency),
          ]));

        const canvas = document.getElementById('nw-trend-chart');
        const trend = d.trend || [];
        if (_nwTrendChart && !trend.length) { _nwTrendChart.destroy(); _nwTrendChart = null; }
        if (typeof Chart !== 'undefined' && canvas && trend.length) {
          if (_nwTrendChart) _nwTrendChart.destroy();
          _nwTrendChart = new Chart(canvas, {
            type: 'line',
            data: {
              labels: trend.map(r => formatPeriodKey(r.period)),   // «آبان ۲۰۲۶», not «2026-11»
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
            showAlert(d.detail || t('msgFailed'), true);
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
