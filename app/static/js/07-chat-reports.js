
    function appendChatMessage(role, content, options) {
      const div = document.createElement('div');
      div.className = 'chat-msg ' + role + (options && options.loading ? ' loading' : '');
      div.setAttribute('dir', 'auto');
      div.textContent = content;
      if (options && options.id) div.id = options.id;
      div.classList.add('message-in');
      chatMessagesEl.appendChild(div);
      chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
      setTimeout(() => div.classList.remove('message-in'), 260);
      return div;
    }

    function renderObjectAsMiniTable(obj) {
      if (!obj || typeof obj !== 'object') return '';
      const keys = Object.keys(obj);
      if (!keys.length) return '';
      const rows = keys.map(k => `<tr><td>${escapeHtml(localizeReportFieldName(k))}</td><td>${escapeHtml(formatNum(obj[k] ?? 0))}</td></tr>`).join('');
      return `<div class="report-preview-wrap"><table class="mini-table"><thead><tr><th>${escapeHtml(t('fieldNameLabel'))}</th><th>${escapeHtml(t('fieldValue'))}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function shortenUuid(v) {
      const s = String(v || '');
      if (/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(s)) return s.slice(0, 8) + '…' + s.slice(-4);
      return s;
    }

    function formatReportCell(v, key) {
      if (v == null) return '—';
      if (typeof v === 'number') return formatNum(v);
      if (typeof v === 'boolean') return v ? t('yes') : t('no');
      if (Array.isArray(v)) return escapeHtml(v.map(String).join(', '));
      if (typeof v === 'object') return escapeHtml(Object.entries(v).map(([k2, v2]) => `${k2}: ${v2}`).join(', '));
      const s = String(v);
      if ((key || '').toLowerCase().includes('id')) return escapeHtml(shortenUuid(s));
      return escapeHtml(localizeDynamicText(s));
    }

    function reportPeriodText(report) {
      const p = (report && report.period) || {};
      const from = p.from || p.from_date;
      const to = p.to || p.to_date;
      if (from && to) return `${from} → ${to}`;
      if (to) return `${t('asOfDate')} ${to}`;
      if (from) return `${t('labelFrom')} ${from}`;
      return '';
    }

    function flattenStatementNodes(nodes, out, sectionName, level = 0) {
      (nodes || []).forEach(n => {
        out.push({
          section: sectionName,
          account_code: n.account_code || '',
          account_name: ((n.account_name || '') + (level > 0 ? ` (${level})` : '')),
          balance: n.balance || 0
        });
        if (Array.isArray(n.children) && n.children.length) flattenStatementNodes(n.children, out, sectionName, level + 1);
      });
    }

    function reportToTableData(report) {
      if (!report || typeof report !== 'object') return { headers: [], rows: [] };
      if (Array.isArray(report.rows) && report.rows.length) {
        const first = report.rows[0];
        const headers = Object.keys(first);
        const rows = report.rows.map(r => headers.map(k => r[k]));
        return { headers, rows };
      }
      if (Array.isArray(report.items) && report.items.length) {
        const first = report.items[0];
        if (Array.isArray(first.lines)) {
          const headers = ['date', 'reference', 'description', 'account_code', 'account_name', 'debit', 'credit', 'line_description'];
          const rows = [];
          report.items.forEach(item => {
            (item.lines || []).forEach(ln => {
              rows.push([
                item.date,
                item.reference || '',
                item.description || '',
                ln.account_code,
                ln.account_name,
                ln.debit || 0,
                ln.credit || 0,
                ln.line_description || ''
              ]);
            });
          });
          return { headers, rows };
        }
        const headers = Object.keys(first).filter(k => k !== 'lines');
        const rows = report.items.map(r => headers.map(k => r[k]));
        return { headers, rows };
      }
      if (report.sections && typeof report.sections === 'object') {
        const flat = [];
        Object.keys(report.sections).forEach(k => {
          const sec = report.sections[k] || {};
          flattenStatementNodes(sec.items || [], flat, sec.label || k);
        });
        if (flat.length) {
          const headers = ['section', 'account_code', 'account_name', 'balance'];
          const rows = flat.map(r => headers.map(k => r[k]));
          return { headers, rows };
        }
      }
      if (report.totals && typeof report.totals === 'object') {
        const headers = ['metric', 'value'];
        const rows = Object.entries(report.totals).map(([k, v]) => [k, v]);
        return { headers, rows };
      }
      return { headers: [], rows: [] };
    }

    function makeReportChartSpec(report) {
      if (!report || typeof report !== 'object') return null;
      const rt = report.report_type || '';
      const palette = ['#0f766e', '#0ea5e9', '#eab308', '#f97316', '#8b5cf6', '#ef4444', '#10b981', '#64748b'];
      const safe = (n) => Number.isFinite(Number(n)) ? Number(n) : 0;

      if (rt === 'balance_sheet') {
        const totals = report.totals || {};
        return {
          type: 'doughnut',
          title: t('chartBalanceSheetMix'),
          data: {
            labels: [t('sectionAssets'), t('sectionLiabilities'), t('sectionEquity')],
            datasets: [{ label: t('chartBalanceSheetMix'), data: [safe(totals.assets), safe(totals.liabilities), safe(totals.equity)], backgroundColor: palette.slice(0, 3) }]
          }
        };
      }
      if (rt === 'income_statement') {
        const totals = report.totals || {};
        return {
          type: 'bar',
          title: t('chartIncomeStatement'),
          data: {
            labels: [t('fieldRevenue'), t('fieldCOGS'), t('fieldOpEx'), t('fieldOtherExpenses'), t('fieldNetProfit')],
            datasets: [{
              label: t('chartIncomeStatement'),
              data: [safe(totals.revenue), safe(totals.cogs), safe(totals.operating_expenses), safe(totals.other_expenses), safe(totals.net_profit)],
              backgroundColor: [palette[1], palette[3], palette[5], palette[4], palette[0]]
            }]
          }
        };
      }
      if (rt === 'cash_flow_statement') {
        const s = report.sections || {};
        return {
          type: 'bar',
          title: t('chartCashFlow'),
          data: {
            labels: [t('fieldOperating'), t('fieldInvesting'), t('fieldFinancing'), t('fieldNetChange')],
            datasets: [{
              label: t('chartCashFlow'),
              data: [safe((s.operating || {}).net), safe((s.investing || {}).net), safe((s.financing || {}).net), safe((report.totals || {}).net_change)],
              backgroundColor: [palette[0], palette[1], palette[4], palette[2]]
            }]
          }
        };
      }
      if (rt === 'trial_balance' || rt === 'general_ledger') {
        const rows = (report.rows || []).slice().sort((a, b) => ((b.debit_turnover + b.credit_turnover) - (a.debit_turnover + a.credit_turnover))).slice(0, 10);
        if (!rows.length) return null;
        return {
          type: 'bar',
          title: t('chartTopAccountsByTurnover'),
          data: {
            labels: rows.map(r => r.account_code),
            datasets: [
              { label: t('tableDebit'), data: rows.map(r => safe(r.debit_turnover)), backgroundColor: palette[0] },
              { label: t('tableCredit'), data: rows.map(r => safe(r.credit_turnover)), backgroundColor: palette[1] }
            ]
          }
        };
      }
      if (rt === 'account_ledger' || rt === 'cash_bank_statement' || rt === 'person_running_balance') {
        const rows = (report.items || report.rows || []).slice(0, 80);
        if (!rows.length) return null;
        return {
          type: 'line',
          title: t('chartRunningBalance'),
          data: {
            labels: rows.map(r => r.date),
            datasets: [{ label: t('fieldBalance'), data: rows.map(r => safe(r.running_balance)), borderColor: palette[0], backgroundColor: 'rgba(15,118,110,0.18)', fill: true, tension: 0.25 }]
          }
        };
      }
      if (rt === 'inventory_balance') {
        const rows = (report.rows || []).slice().sort((a, b) => (b.inventory_value - a.inventory_value)).slice(0, 10);
        if (!rows.length) return null;
        return {
          type: 'bar',
          title: t('chartInventoryValueByItem'),
          data: { labels: rows.map(r => r.item_name), datasets: [{ label: t('fieldInventoryValue'), data: rows.map(r => safe(r.inventory_value)), backgroundColor: palette[0] }] }
        };
      }
      if (rt === 'inventory_movement') {
        const rows = (report.rows || []).slice(0, 80);
        if (!rows.length) return null;
        return {
          type: 'line',
          title: t('chartInventoryMovements'),
          data: { labels: rows.map(r => r.movement_date), datasets: [{ label: t('labelQuantity'), data: rows.map(r => safe(r.quantity)), borderColor: palette[1], fill: false, tension: 0.2 }] }
        };
      }
      if (rt.includes('sales') || rt.includes('purchase')) {
        const rows = (report.rows || []).slice(0, 12);
        if (!rows.length) return null;
        const first = rows[0] || {};
        const hasSalesAmount = Object.prototype.hasOwnProperty.call(first, 'sales_amount');
        const hasAmount = Object.prototype.hasOwnProperty.call(first, 'amount');
        const labelKey = Object.prototype.hasOwnProperty.call(first, 'product_name') ? 'product_name' : 'invoice_number';
        const valueKey = hasSalesAmount ? 'sales_amount' : (hasAmount ? 'amount' : null);
        if (!valueKey) return null;
        return {
          type: 'bar',
          title: t('chartSalesPurchase'),
          data: { labels: rows.map(r => r[labelKey] || t('itemWord')), datasets: [{ label: t(hasSalesAmount ? 'fieldSalesAmount' : 'labelAmount'), data: rows.map(r => safe(r[valueKey])), backgroundColor: palette[2] }] }
        };
      }
      // ──── Locale-specific (Iran, UK) ────
      // The Iran/UK statements expose ordered `rows[]` with `key`, so we pick
      // out the relevant subtotal/total lines and build a chart from them.
      const _findRow = (key) => (report.rows || []).find(r => r && r.key === key) || null;
      const _amt = (row, field) => row ? safe(row[field]) : 0;

      if (rt === 'iran_balance_sheet' || rt === 'uk_balance_sheet') {
        const isUK = rt === 'uk_balance_sheet';
        const totalAssets = _findRow(isUK ? 'total_assets_less_cl' : 'total_assets')
                          || (isUK ? _findRow('total_fixed_assets') : null);
        const totalLiab = _findRow(isUK ? 'creditors_after_one_year' : 'total_liabilities');
        const totalEquity = _findRow(isUK ? 'total_capital_reserves' : 'total_equity');
        // For UK we need a meaningful "assets" magnitude — use fixed + current.
        let assetsCur = 0, assetsPri = 0;
        if (isUK) {
          const fa = _findRow('total_fixed_assets');
          const ca = _findRow('total_current_assets');
          assetsCur = _amt(fa, 'amount_current') + _amt(ca, 'amount_current');
          assetsPri = _amt(fa, 'amount_prior') + _amt(ca, 'amount_prior');
        } else {
          assetsCur = _amt(totalAssets, 'amount_current');
          assetsPri = _amt(totalAssets, 'amount_prior');
        }
        const liabCur = Math.abs(_amt(totalLiab, 'amount_current'));
        const liabPri = Math.abs(_amt(totalLiab, 'amount_prior'));
        const eqCur = _amt(totalEquity, 'amount_current');
        const eqPri = _amt(totalEquity, 'amount_prior');
        if ((assetsCur + liabCur + eqCur) === 0) return null;
        return {
          type: 'bar',
          title: isUK ? (t('chartBalanceSheetCompare') || 'Balance Sheet — period comparison') : t('chartBalanceSheetMix'),
          data: {
            labels: [t('sectionAssets') || 'Assets', t('sectionLiabilities') || 'Liabilities', t('sectionEquity') || 'Equity'],
            datasets: [
              { label: t('labelCurrent') || 'Current', data: [assetsCur, liabCur, eqCur], backgroundColor: palette[0] },
              { label: t('labelPrior') || 'Prior', data: [assetsPri, liabPri, eqPri], backgroundColor: palette[1] },
            ]
          }
        };
      }

      if (rt === 'iran_income_statement' || rt === 'uk_profit_and_loss') {
        const isUK = rt === 'uk_profit_and_loss';
        const gross = _findRow('gross_profit');
        const operating = _findRow('operating_profit');
        const beforeTax = _findRow('profit_before_tax');
        const net = _findRow(isUK ? 'profit_for_year' : 'net_profit');
        const cur = [_amt(gross, 'amount_current'), _amt(operating, 'amount_current'), _amt(beforeTax, 'amount_current'), _amt(net, 'amount_current')];
        const pri = [_amt(gross, 'amount_prior'), _amt(operating, 'amount_prior'), _amt(beforeTax, 'amount_prior'), _amt(net, 'amount_prior')];
        if (cur.every(v => v === 0) && pri.every(v => v === 0)) return null;
        return {
          type: 'bar',
          title: isUK ? (t('chartProfitWaterfall') || 'Profit waterfall') : t('chartIncomeStatement'),
          data: {
            labels: [t('labelGrossShort') || 'Gross', t('labelOperatingShort') || 'Operating', t('labelBeforeTaxShort') || 'Before tax', t('labelNetShort') || 'Net'],
            datasets: [
              { label: t('labelCurrent') || 'Current', data: cur, backgroundColor: palette[0] },
              { label: t('labelPrior') || 'Prior', data: pri, backgroundColor: palette[1] },
            ]
          }
        };
      }

      if (rt === 'iran_cash_flow' || rt === 'uk_cash_flow') {
        const op = _findRow('operating_net');
        const inv = _findRow('investing_net');
        const fin = _findRow('financing_net');
        const net = _findRow('net_cash_change');
        const cur = [_amt(op, 'amount_current'), _amt(inv, 'amount_current'), _amt(fin, 'amount_current'), _amt(net, 'amount_current')];
        const pri = [_amt(op, 'amount_prior'), _amt(inv, 'amount_prior'), _amt(fin, 'amount_prior'), _amt(net, 'amount_prior')];
        if (cur.every(v => v === 0) && pri.every(v => v === 0)) return null;
        return {
          type: 'bar',
          title: t('chartCashFlow'),
          data: {
            labels: [t('fieldOperating') || 'Operating', t('fieldInvesting') || 'Investing', t('fieldFinancing') || 'Financing', t('fieldNetChange') || 'Net change'],
            datasets: [
              { label: t('labelCurrent') || 'Current', data: cur, backgroundColor: palette[0] },
              { label: t('labelPrior') || 'Prior', data: pri, backgroundColor: palette[1] },
            ]
          }
        };
      }

      if (rt === 'iran_comprehensive_income' || rt === 'uk_comprehensive_income') {
        const np = _findRow(rt === 'uk_comprehensive_income' ? 'profit_for_year' : 'net_profit');
        const oci = _findRow(rt === 'uk_comprehensive_income' ? 'oci_total' : 'oci_total');
        const total = _findRow(rt === 'uk_comprehensive_income' ? 'total_comprehensive_income' : 'comprehensive_income');
        const cur = [_amt(np, 'amount_current'), _amt(oci, 'amount_current'), _amt(total, 'amount_current')];
        const pri = [_amt(np, 'amount_prior'), _amt(oci, 'amount_prior'), _amt(total, 'amount_prior')];
        if (cur.every(v => v === 0) && pri.every(v => v === 0)) return null;
        return {
          type: 'bar',
          title: t('chartNpOciTotal') || 'Comprehensive income',
          data: {
            labels: [t('labelNetProfitShort') || 'Net profit', t('labelOciNetTax') || 'OCI (net of tax)', t('labelTotalComprehensive') || 'Total comprehensive'],
            datasets: [
              { label: t('labelCurrent') || 'Current', data: cur, backgroundColor: palette[0] },
              { label: t('labelPrior') || 'Prior', data: pri, backgroundColor: palette[1] },
            ]
          }
        };
      }
      return null;
    }

    function renderReportChart(canvas, report, existingChart) {
      if (!canvas || typeof Chart === 'undefined') return null;
      const spec = makeReportChartSpec(report);
      if (existingChart) {
        try { existingChart.destroy(); } catch (_) {}
      }
      if (!spec) return null;
      const chart = new Chart(canvas, {
        type: spec.type,
        data: spec.data,
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom' },
            title: { display: true, text: spec.title }
          },
          scales: spec.type === 'line' || spec.type === 'bar' ? {
            y: { ticks: { callback: (v) => formatNum(v) } }
          } : undefined
        }
      });
      return chart;
    }

    // Render Iranian-standard statements (ordered `rows` with subtotal/total/header
    // row_type markers, indent_level, and negative-presentation flag). Produces a
    // compact RTL table with Persian headers مطابق PDF استاندارد ایران.
    function renderIranStatementTable(report) {
      const rows = Array.isArray(report.rows) ? report.rows : [];
      // Income Statement uses `period`/`comparative_period` (from-to windows);
      // Balance Sheet uses `as_of`/`comparative_as_of`/`comparative_beginning_as_of` (snapshot dates).
      const periodDate = (report.period && (report.period.to || report.period.to_date)) || report.as_of || '';
      const priorDate = (report.comparative_period && (report.comparative_period.to || report.comparative_period.to_date)) || report.comparative_as_of || '';
      const beginningDate = report.comparative_beginning_as_of || '';
      // Show beginning-of-prior column only when at least one row carries it (Balance Sheet only).
      const hasBeginning = !!beginningDate && rows.some(r => r && r.amount_prior_beginning != null);
      const fmtParens = (amount, forceParens) => {
        if (amount == null) return '-';
        if (amount === 0) return '·';
        const n = Math.abs(amount).toLocaleString();
        return (amount < 0 || forceParens) ? '(' + n + ')' : n;
      };
      const fmtPct = (v) => {
        if (v == null) return '-';
        if (v === 0) return '·';
        const n = Math.abs(Math.round(v)).toLocaleString();
        return v < 0 ? '(' + n + ')' : n;
      };
      const bodyRows = rows.map(r => {
        const isHeader = r.row_type === 'header';
        const isSubtotal = r.row_type === 'subtotal';
        const isTotal = r.row_type === 'total';
        const indent = Math.max(0, (r.indent_level || 0)) * 14;
        const baseStyle = 'padding:6px 8px; border-bottom:1px solid #eee;';
        const weight = (isSubtotal || isTotal) ? 'font-weight:700;' : '';
        const bg = isHeader ? 'background:#f3f4f6;' : (isTotal ? 'background:#eef4ff;' : (isSubtotal ? 'background:#fafafa;' : ''));
        const color = isHeader ? 'color:#334155;' : '';
        const labelCell = `<td style="${baseStyle} ${weight} ${bg} ${color} text-align:right; padding-right:${8 + indent}px;" dir="rtl">${escapeHtml(r.label_fa || '')}</td>`;
        if (isHeader) {
          const blanks = hasBeginning ? 4 : 3;
          return `<tr>${labelCell}${`<td style="${baseStyle} ${bg}"></td>`.repeat(blanks)}</tr>`;
        }
        const curCell = `<td style="${baseStyle} ${weight} ${bg} text-align:center; font-variant-numeric:tabular-nums;">${escapeHtml(fmtParens(r.amount_current, r.is_negative_presentation))}</td>`;
        const priorCell = `<td style="${baseStyle} ${weight} ${bg} text-align:center; font-variant-numeric:tabular-nums;">${escapeHtml(fmtParens(r.amount_prior, r.is_negative_presentation))}</td>`;
        const beginningCell = hasBeginning
          ? `<td style="${baseStyle} ${weight} ${bg} text-align:center; font-variant-numeric:tabular-nums;">${escapeHtml(fmtParens(r.amount_prior_beginning, r.is_negative_presentation))}</td>`
          : '';
        const pctCell = `<td style="${baseStyle} ${weight} ${bg} text-align:center; font-variant-numeric:tabular-nums; color:${(r.change_pct != null && r.change_pct < 0) ? '#b91c1c' : '#334155'};">${escapeHtml(fmtPct(r.change_pct))}</td>`;
        return `<tr>${labelCell}${curCell}${priorCell}${beginningCell}${pctCell}</tr>`;
      }).join('');

      const titleMap = {
        iran_income_statement: 'صورت سود و زیان',
        iran_balance_sheet: 'صورت وضعیت مالی',
        iran_changes_in_equity: 'صورت تغییرات در حقوق مالکانه',
        iran_comprehensive_income: 'صورت سود و زیان جامع',
        iran_cash_flow: 'صورت جریان‌های نقدی',
      };
      const title = titleMap[report.report_type] || 'گزارش';
      const isBalanceSheet = report.report_type === 'iran_balance_sheet';
      const datePrefix = isBalanceSheet ? 'به تاریخ' : 'دوره منتهی به';
      const curHeader = periodDate ? `${datePrefix} ${escapeHtml(formatDisplayDate(periodDate))}` : '';
      const priorHeader = priorDate ? `${isBalanceSheet ? 'تجدید ارائه شده به تاریخ' : 'دوره منتهی به'} ${escapeHtml(formatDisplayDate(priorDate))}` : '';
      const beginningHeader = hasBeginning ? `تجدید ارائه شده به تاریخ ${escapeHtml(formatDisplayDate(beginningDate))}` : '';

      return `
        <div dir="rtl" style="font-family: Tahoma, 'IRANSans', Arial, sans-serif;">
          <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:0.4rem;">
            <h3 style="margin:0;">${escapeHtml(title)}</h3>
            <span style="font-size:0.82rem; color:var(--text-muted);">${escapeHtml(iranAmountsNote())}</span>
          </div>
          <div class="report-preview-wrap">
            <table class="mini-table" style="width:100%; border-collapse:collapse;">
              <thead>
                <tr style="background:#f1f5f9;">
                  <th style="padding:8px; text-align:right;" dir="rtl">شرح</th>
                  <th style="padding:8px; text-align:center; min-width:140px;">${curHeader || '-'}</th>
                  <th style="padding:8px; text-align:center; min-width:140px;">${priorHeader || '-'}</th>
                  ${hasBeginning ? `<th style="padding:8px; text-align:center; min-width:140px;">${beginningHeader}</th>` : ''}
                  <th style="padding:8px; text-align:center; min-width:90px;">درصد تغییر</th>
                </tr>
              </thead>
              <tbody>${bodyRows}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    // Changes in Equity is a matrix: rows = movement events, columns = equity components
    // (سرمایه, اندوخته قانونی, سود انباشته, …) + total. Uses a different schema
    // (cells[]) than the other Iranian statements, so it gets its own renderer.
    // Replace any ISO-date substrings inside an arbitrary label with their
    // calendar-aware display form. Used for matrix labels like
    // "مانده در 2024-12-31" that embed dates the backend serialises as ISO.
    function _localizeDatesInLabel(s) {
      if (!s) return '';
      return String(s).replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (m) => formatDisplayDate(m));
    }

    function renderIranEquityMatrix(report) {
      const components = Array.isArray(report.components) ? report.components : [];
      const rows = Array.isArray(report.rows) ? report.rows : [];
      const periodFrom = report.period && (report.period.from || report.period.from_date) || '';
      const periodTo = report.period && (report.period.to || report.period.to_date) || '';
      const fmtParens = (n) => {
        if (n == null) return '-';
        if (n === 0) return '·';
        const s = Math.abs(n).toLocaleString();
        return n < 0 ? '(' + s + ')' : s;
      };

      const header = `
        <tr style="background:#f1f5f9;">
          <th style="padding:8px; text-align:right; min-width:240px;" dir="rtl">شرح</th>
          ${components.map(c => `<th style="padding:8px; text-align:center; font-size:0.82rem; min-width:90px;" dir="rtl">${escapeHtml(c.label_fa || c.key)}</th>`).join('')}
          <th style="padding:8px; text-align:center; min-width:100px; background:#e8f0fe;" dir="rtl">جمع کل</th>
        </tr>
      `;
      const body = rows.map(r => {
        const isHeader = r.row_type === 'header';
        const isSubtotal = r.row_type === 'subtotal';
        const isTotal = r.row_type === 'total';
        const baseStyle = 'padding:6px 8px; border-bottom:1px solid #eee; font-variant-numeric:tabular-nums;';
        const weight = (isSubtotal || isTotal) ? 'font-weight:700;' : '';
        const bg = isHeader ? 'background:#f3f4f6;' : (isTotal ? 'background:#eef4ff;' : (isSubtotal ? 'background:#fafafa;' : ''));
        const cellsByComponent = Object.fromEntries((r.cells || []).map(c => [c.component, c.amount]));
        const labelCell = `<td style="${baseStyle} ${weight} ${bg} text-align:right;" dir="rtl">${escapeHtml(_localizeDatesInLabel(r.label_fa || ''))}</td>`;
        const valueCells = components.map(c => {
          const v = cellsByComponent[c.key];
          return `<td style="${baseStyle} ${weight} ${bg} text-align:center;">${escapeHtml(fmtParens(v))}</td>`;
        }).join('');
        const totalCell = `<td style="${baseStyle} ${weight} ${bg} text-align:center; background:${isTotal ? '#d8e7ff' : (isSubtotal ? '#f0f4fa' : '#eef4ff')};">${escapeHtml(fmtParens(r.total))}</td>`;
        return `<tr>${labelCell}${valueCells}${totalCell}</tr>`;
      }).join('');

      return `
        <div dir="rtl" style="font-family: Tahoma, 'IRANSans', Arial, sans-serif;">
          <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:0.4rem;">
            <h3 style="margin:0;">صورت تغییرات در حقوق مالکانه</h3>
            <span style="font-size:0.82rem; color:var(--text-muted);">
              ${periodFrom ? `از ${escapeHtml(periodFrom)} تا ${escapeHtml(periodTo)} · ` : ''}${escapeHtml(iranAmountsNote())}
            </span>
          </div>
          <div class="report-preview-wrap" style="overflow-x:auto;">
            <table class="mini-table" style="width:100%; border-collapse:collapse;">
              <thead>${header}</thead>
              <tbody>${body}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    function renderUKStatementTable(report) {
      const rows = Array.isArray(report.rows) ? report.rows : [];
      const periodDate = (report.period && (report.period.to || report.period.to_date)) || report.as_of || '';
      const priorDate = (report.comparative_period && (report.comparative_period.to || report.comparative_period.to_date)) || report.comparative_as_of || '';
      const fmtParens = (amount, forceParens) => {
        if (amount == null) return '-';
        if (amount === 0) return '·';
        const n = Math.abs(amount).toLocaleString();
        return (amount < 0 || forceParens) ? '(' + n + ')' : n;
      };
      const bodyRows = rows.map(r => {
        const isHeader = r.row_type === 'header';
        const isSubtotal = r.row_type === 'subtotal';
        const isTotal = r.row_type === 'total';
        const indent = Math.max(0, (r.indent_level || 0)) * 14;
        const baseStyle = 'padding:6px 8px; border-bottom:1px solid #eee;';
        const weight = (isSubtotal || isTotal) ? 'font-weight:700;' : '';
        const bg = isHeader ? 'background:#f3f4f6;' : (isTotal ? 'background:#eef4ff;' : (isSubtotal ? 'background:#fafafa;' : ''));
        const color = isHeader ? 'color:#334155;' : '';
        const labelCell = `<td style="${baseStyle} ${weight} ${bg} ${color} text-align:left; padding-left:${8 + indent}px;">${escapeHtml(r.label || '')}</td>`;
        if (isHeader) {
          return `<tr>${labelCell}<td style="${baseStyle} ${bg}"></td><td style="${baseStyle} ${bg}"></td></tr>`;
        }
        const curCell = `<td style="${baseStyle} ${weight} ${bg} text-align:right; font-variant-numeric:tabular-nums;">${escapeHtml(fmtParens(r.amount_current, r.is_negative_presentation))}</td>`;
        const priorCell = `<td style="${baseStyle} ${weight} ${bg} text-align:right; font-variant-numeric:tabular-nums;">${escapeHtml(fmtParens(r.amount_prior, r.is_negative_presentation))}</td>`;
        return `<tr>${labelCell}${curCell}${priorCell}</tr>`;
      }).join('');

      const titleMap = {
        uk_balance_sheet: 'Statement of Financial Position (FRS 102 1A)',
        uk_profit_and_loss: 'Profit and Loss Account (FRS 102 1A)',
        uk_comprehensive_income: 'Statement of Comprehensive Income',
        uk_changes_in_equity: 'Statement of Changes in Equity',
        uk_cash_flow: 'Statement of Cash Flows',
      };
      const title = titleMap[report.report_type] || 'Report';
      const isBalanceSheet = report.report_type === 'uk_balance_sheet';
      const datePrefix = isBalanceSheet ? 'As at' : 'Year ended';
      const curHeader = periodDate ? `${datePrefix} ${escapeHtml(formatDisplayDate(periodDate))}` : '';
      const priorHeader = priorDate ? `${datePrefix} ${escapeHtml(formatDisplayDate(priorDate))}` : '';

      return `
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
          <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:0.4rem;">
            <h3 style="margin:0;">${escapeHtml(title)}</h3>
            <span style="font-size:0.82rem; color:var(--text-muted);">All amounts in £</span>
          </div>
          <div class="report-preview-wrap">
            <table class="mini-table" style="width:100%; border-collapse:collapse;">
              <thead>
                <tr style="background:#f1f5f9;">
                  <th style="padding:8px; text-align:left;">Description</th>
                  <th style="padding:8px; text-align:right; min-width:140px;">${curHeader || '-'}</th>
                  <th style="padding:8px; text-align:right; min-width:140px;">${priorHeader || '-'}</th>
                </tr>
              </thead>
              <tbody>${bodyRows}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    function renderUKEquityMatrix(report) {
      const components = Array.isArray(report.components) ? report.components : [];
      const rows = Array.isArray(report.rows) ? report.rows : [];
      const fmtParens = (n) => {
        if (n == null) return '-';
        if (n === 0) return '·';
        const s = Math.abs(n).toLocaleString();
        return n < 0 ? '(' + s + ')' : s;
      };
      const header = `
        <tr style="background:#f1f5f9;">
          <th style="padding:8px; text-align:left; min-width:240px;">Description</th>
          ${components.map(c => `<th style="padding:8px; text-align:right; font-size:0.82rem; min-width:90px;">${escapeHtml(c.label || c.key)}</th>`).join('')}
          <th style="padding:8px; text-align:right; min-width:100px; background:#e8f0fe;">Total</th>
        </tr>
      `;
      const body = rows.map(r => {
        const isHeader = r.row_type === 'header';
        const isSubtotal = r.row_type === 'subtotal';
        const isTotal = r.row_type === 'total';
        const baseStyle = 'padding:6px 8px; border-bottom:1px solid #eee; font-variant-numeric:tabular-nums;';
        const weight = (isSubtotal || isTotal) ? 'font-weight:700;' : '';
        const bg = isHeader ? 'background:#f3f4f6;' : (isTotal ? 'background:#eef4ff;' : (isSubtotal ? 'background:#fafafa;' : ''));
        const cellsByComponent = Object.fromEntries((r.cells || []).map(c => [c.component, c.amount]));
        const labelCell = `<td style="${baseStyle} ${weight} ${bg} text-align:left;">${escapeHtml(_localizeDatesInLabel(r.label || ''))}</td>`;
        const valueCells = components.map(c => {
          const v = cellsByComponent[c.key];
          return `<td style="${baseStyle} ${weight} ${bg} text-align:right;">${escapeHtml(fmtParens(v))}</td>`;
        }).join('');
        const totalCell = `<td style="${baseStyle} ${weight} ${bg} text-align:right; background:${isTotal ? '#d8e7ff' : (isSubtotal ? '#f0f4fa' : '#eef4ff')};">${escapeHtml(fmtParens(r.total))}</td>`;
        return `<tr>${labelCell}${valueCells}${totalCell}</tr>`;
      }).join('');

      return `
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
          <h3 style="margin:0 0 0.4rem;">Statement of Changes in Equity</h3>
          <div class="report-preview-wrap" style="overflow-x:auto;">
            <table class="mini-table" style="width:100%; border-collapse:collapse;">
              <thead>${header}</thead>
              <tbody>${body}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    function renderReportPreviewHtml(report) {
      if (!report || typeof report !== 'object') return '<p class="empty-state">' + escapeHtml(t('noReportData')) + '</p>';
      const rt = report.report_type || '';
      if (rt === 'iran_changes_in_equity') {
        return renderIranEquityMatrix(report);
      }
      if (rt === 'iran_income_statement' || rt === 'iran_balance_sheet'
          || rt === 'iran_comprehensive_income' || rt === 'iran_cash_flow') {
        return renderIranStatementTable(report);
      }
      if (rt === 'uk_changes_in_equity') {
        return renderUKEquityMatrix(report);
      }
      if (rt === 'uk_balance_sheet' || rt === 'uk_profit_and_loss'
          || rt === 'uk_comprehensive_income' || rt === 'uk_cash_flow') {
        return renderUKStatementTable(report);
      }
      if (report.sections && typeof report.sections === 'object') {
        const cards = Object.keys(report.sections).map(k => {
          const sec = report.sections[k] || {};
          const items = (sec.items || []).slice(0, 8);
          const table = items.length
            ? `<div class="report-preview-wrap"><table class="mini-table"><thead><tr><th>${escapeHtml(t('tableCode'))}</th><th>${escapeHtml(t('fieldAccount'))}</th><th>${escapeHtml(t('fieldBalance'))}</th></tr></thead><tbody>${items.map(it => `<tr><td>${formatReportCell(it.account_code, 'account_code')}</td><td>${formatReportCell(it.account_name, 'account_name')}</td><td>${formatReportCell(it.balance, 'balance')}</td></tr>`).join('')}</tbody></table></div>`
            : '<div class="empty-state" style="padding:0.35rem;">' + escapeHtml(t('noRowsInSection')) + '</div>';
          const sectionNameMap = { assets: 'sectionAssets', liabilities: 'sectionLiabilities', equity: 'sectionEquity', revenues: 'sectionRevenues', expenses: 'sectionExpenses' };
          return `<div class="panel" style="margin-bottom:0.45rem;">
            <strong>${escapeHtml(sectionNameMap[k] ? t(sectionNameMap[k]) : (sec.label || k))}</strong>
            <div style="font-size:0.8rem; color:var(--text-muted);">${escapeHtml(sec.label_fa || '')}</div>
            <div style="font-size:0.92rem; margin-top:0.2rem;">${escapeHtml(t('tableTotal'))}: ${formatNum(sec.total || sec.net || 0)}</div>
            ${table}
          </div>`;
        }).join('');
        return cards || '<p class="empty-state">' + escapeHtml(t('noSectionRows')) + '</p>';
      }
      const tableData = reportToTableData(report);
      if (tableData.headers.length && tableData.rows.length) {
        const headers = tableData.headers;
        const body = tableData.rows.slice(0, 80).map(r => `<tr>${headers.map((h, i) => `<td>${formatReportCell(r[i], h)}</td>`).join('')}</tr>`).join('');
        const head = headers.map(h => `<th>${escapeHtml(localizeReportFieldName(h))}</th>`).join('');
        return `<div class="report-preview-wrap"><table class="mini-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
      }
      if (report.totals && typeof report.totals === 'object') {
        return renderObjectAsMiniTable(report.totals);
      }
      // Render any remaining object as a friendly key-value card instead of raw JSON
      const entries = Object.entries(report).filter(([k, v]) => v != null && k !== 'report_type' && k !== 'period');
      if (entries.length) {
        const rows = entries.map(([k, v]) => {
          let display;
          if (typeof v === 'object' && !Array.isArray(v)) {
            display = Object.entries(v).filter(([, sv]) => sv != null).map(([sk, sv]) => `<div style="display:flex;justify-content:space-between;padding:2px 0;"><span style="color:var(--text-muted);font-size:0.82rem;">${escapeHtml(localizeReportFieldName(sk))}</span><span>${escapeHtml(typeof sv === 'number' ? formatNum(sv) : String(sv))}</span></div>`).join('');
            return `<tr><td style="vertical-align:top;font-weight:600;">${escapeHtml(localizeReportFieldName(k))}</td><td>${display || '—'}</td></tr>`;
          }
          if (Array.isArray(v)) {
            if (v.length === 0) return '';
            if (typeof v[0] === 'object') {
              const keys = Object.keys(v[0]);
              const head = keys.map(h => `<th>${escapeHtml(localizeReportFieldName(h))}</th>`).join('');
              const body = v.slice(0, 50).map(row => `<tr>${keys.map(h => `<td>${formatReportCell(row[h], h)}</td>`).join('')}</tr>`).join('');
              display = `<div class="report-preview-wrap"><table class="mini-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
            } else {
              display = v.map(item => escapeHtml(String(item))).join(', ');
            }
            return `<tr><td style="vertical-align:top;font-weight:600;">${escapeHtml(localizeReportFieldName(k))}</td><td>${display}</td></tr>`;
          }
          display = typeof v === 'number' ? formatNum(v) : escapeHtml(String(v));
          return `<tr><td style="font-weight:600;">${escapeHtml(localizeReportFieldName(k))}</td><td>${display}</td></tr>`;
        }).filter(Boolean).join('');
        return `<div class="report-preview-wrap"><table class="mini-table"><tbody>${rows}</tbody></table></div>`;
      }
      return '<p class="empty-state">' + escapeHtml(t('noReportData')) + '</p>';
    }

    function _reportQuickSummary(report) {
      if (!report || !report.totals) return '';
      const t = report.totals;
      const parts = [];
      if (report.report_type === 'balance_sheet') {
        if (t.assets != null) parts.push('Assets: ' + (t.assets || 0).toLocaleString());
        if (t.liabilities != null) parts.push('Liabilities: ' + (t.liabilities || 0).toLocaleString());
        if (t.equity != null) parts.push('Equity: ' + (t.equity || 0).toLocaleString());
      } else if (report.report_type === 'income_statement') {
        if (t.revenue != null) parts.push('Revenue: ' + (t.revenue || 0).toLocaleString());
        if (t.net_profit != null) parts.push('Net Profit: ' + (t.net_profit || 0).toLocaleString());
      } else if (report.report_type === 'cash_flow_statement') {
        if (t.operating != null) parts.push('Operating: ' + (t.operating || 0).toLocaleString());
        if (t.net_cash_change != null) parts.push('Net Change: ' + (t.net_cash_change || 0).toLocaleString());
      }
      return parts.join(' · ');
    }
    function _reportAnalysisHtml(report) {
      if (!report || !report.analysis) return '';
      const a = report.analysis;
      let html = '';
      if (a.summary) html += `<div style="font-size:0.85rem;color:var(--text);margin:0.3rem 0;">${escapeHtml(a.summary)}</div>`;
      if (a.warnings && a.warnings.length) {
        html += '<div style="margin:0.3rem 0;">';
        a.warnings.forEach(w => { html += `<div style="font-size:0.82rem;color:#c62828;">⚠ ${escapeHtml(w)}</div>`; });
        html += '</div>';
      }
      if (a.ratios && Object.keys(a.ratios).length) {
        const chips = Object.entries(a.ratios).filter(([,v]) => v != null).map(([k,v]) => {
          const label = k.replace(/_/g, ' ').replace(/\bpct\b/, '%');
          const val = typeof v === 'number' ? (Math.abs(v) > 100 ? v.toLocaleString() : v.toFixed(2)) : v;
          return `<span style="display:inline-block;padding:2px 8px;margin:2px;border-radius:12px;background:#e3f2fd;font-size:0.78rem;">${escapeHtml(label)}: ${val}</span>`;
        });
        html += `<div style="margin:0.3rem 0;">${chips.join('')}</div>`;
      }
      return html;
    }
    function appendChatReport(report) {
      const div = document.createElement('div');
      div.className = 'chat-msg assistant report-msg';
      div.setAttribute('dir', 'auto');
      const period = reportPeriodText(report);
      const chartId = 'chat-report-chart-' + Math.random().toString(16).slice(2);
      const hasCsvRows = (() => {
        const data = reportToTableData(report);
        return Array.isArray(data.rows) && data.rows.length > 0;
      })();
      const quickSummary = _reportQuickSummary(report);
      const analysisHtml = _reportAnalysisHtml(report);
      const detailId = 'report-detail-' + Math.random().toString(16).slice(2);
      div.innerHTML = `
        <div style="font-weight:700; margin-bottom:0.35rem;">${escapeHtml(t('reportResult'))}</div>
        ${period ? `<div class="report-meta">${escapeHtml(t('periodLabel'))}: ${escapeHtml(period)}</div>` : ''}
        ${quickSummary ? `<div style="font-size:0.88rem;color:var(--text-muted);margin:0.25rem 0;">${escapeHtml(quickSummary)}</div>` : ''}
        ${analysisHtml}
        <details id="${detailId}">
          <summary style="cursor:pointer;font-size:0.85rem;color:var(--primary);margin:0.3rem 0;">Show full report</summary>
          <div>${renderReportPreviewHtml(report)}</div>
          <div class="panel report-chart-panel" style="display:none;">
            <h3>${escapeHtml(t('chartWord'))}</h3>
            <canvas id="${chartId}" height="200"></canvas>
          </div>
        </details>
        <div class="report-actions" style="margin-top:0.5rem;">
          <button type="button" class="btn btn-secondary btn-sm chat-report-export-json">${escapeHtml(t('btnExportJson'))}</button>
          <button type="button" class="btn btn-secondary btn-sm chat-report-export-csv" ${hasCsvRows ? '' : 'disabled'}>${escapeHtml(t('btnExportCsv'))}</button>
          <button type="button" class="btn btn-secondary btn-sm chat-report-export-pdf">${escapeHtml(t('btnExportPdf'))}</button>
        </div>
      `;
      chatMessagesEl.appendChild(div);
      div.classList.add('message-in');
      const detailsEl = div.querySelector('#' + detailId);
      let chartRendered = false;
      function renderChartOnOpen() {
        if (chartRendered) return;
        const chartCanvas = div.querySelector('#' + chartId);
        const chartPanel = div.querySelector('.report-chart-panel');
        const chart = renderReportChart(chartCanvas, report, null);
        if (chartPanel && chart) chartPanel.style.display = 'block';
        chartRendered = true;
      }
      if (detailsEl) detailsEl.addEventListener('toggle', () => { if (detailsEl.open) renderChartOnOpen(); });
      const base = reportFileBaseName(report);
      const jsonBtn = div.querySelector('.chat-report-export-json');
      const csvBtn = div.querySelector('.chat-report-export-csv');
      const pdfBtn = div.querySelector('.chat-report-export-pdf');
      if (jsonBtn) {
        jsonBtn.addEventListener('click', () => {
          downloadTextFile(`${base}.json`, JSON.stringify(report, null, 2), 'application/json');
        });
      }
      if (csvBtn && hasCsvRows) {
        csvBtn.addEventListener('click', () => {
          const csv = reportToCsv(report);
          if (!csv) {
            showAlert(t('noTabularRowsCsv'), true);
            return;
          }
          downloadTextFile(`${base}.csv`, csv, 'text/csv');
        });
      }
      if (pdfBtn) {
        pdfBtn.addEventListener('click', () => {
          const chartImg = chartCanvas ? chartCanvas.toDataURL('image/png') : '';
          openReportPrintWindow(report, chartImg);
        });
      }
      chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
      setTimeout(() => div.classList.remove('message-in'), 260);
    }

    function extractMessageText(text) {
      if (typeof text !== 'string') return text || "I didn't understand.";
      const t = text.trim();
      if (t.startsWith('{') || t.startsWith('[')) {
        try {
          const o = JSON.parse(t);
          if (o && typeof o === 'object' && !Array.isArray(o)) {
            // Try common response shapes: {message, content, text, response, answer}
            for (const key of ['message', 'content', 'text', 'response', 'answer', 'reply']) {
              if (typeof o[key] === 'string' && o[key].trim()) return o[key].trim();
            }
            // OpenAI choices format
            if (o.choices && o.choices[0] && o.choices[0].message && typeof o.choices[0].message.content === 'string') {
              return o.choices[0].message.content.trim();
            }
            // Fallback: find any long string value
            for (const k of Object.keys(o)) {
              if (typeof o[k] === 'string' && o[k].length > 10) return o[k];
            }
          }
        } catch (_) {}
      }
      return t;
    }

    function fillFormFromSuggestion(data) {
      document.getElementById('date').value = data.date || new Date().toISOString().slice(0, 10);
      if (typeof updateJalaliHint === 'function') updateJalaliHint();
      document.getElementById('reference').value = data.reference || '';
      document.getElementById('description').value = data.description || '';
      const lines = data.lines || [];
      while (linesTbody.querySelectorAll('.line-row').length > 0) linesTbody.firstElementChild.remove();
      for (let i = 0; i < lines.length; i++) {
        const ln = lines[i];
        const tr = document.createElement('tr');
        tr.className = 'line-row';
        tr.innerHTML = `
          <td><input type="text" class="line-code" value="${(ln.account_code || '').replace(/"/g, '&quot;')}" required></td>
          <td><input type="number" class="line-debit" min="0" value="${ln.debit || 0}" step="1"></td>
          <td><input type="number" class="line-credit" min="0" value="${ln.credit || 0}" step="1"></td>
          <td><input type="text" class="line-desc" value="${(ln.line_description || '').replace(/"/g, '&quot;')}"></td>
          <td><button type="button" class="btn btn-secondary remove-line">Remove</button></td>
        `;
        linesTbody.appendChild(tr);
      }
    }

    // Old inline-voucher quick-action chips wiring — element removed when
    // the inline chat was merged into the dedicated AI Chat page. The new
    // chips on /#ai-accountant get wired by the aiAccountant IIFE later.

    // Voucher balance bar live updater
    function updateVoucherBalanceBar() {
      const rows = document.querySelectorAll('#lines-body tr');
      let totalDebit = 0, totalCredit = 0;
      rows.forEach(r => {
        totalDebit += parseInt(r.querySelector('.line-debit')?.value) || 0;
        totalCredit += parseInt(r.querySelector('.line-credit')?.value) || 0;
      });
      const dEl = document.getElementById('bal-debit');
      const cEl = document.getElementById('bal-credit');
      const diffEl = document.getElementById('bal-diff');
      if (dEl) dEl.textContent = 'Debit: ' + totalDebit.toLocaleString();
      if (cEl) cEl.textContent = 'Credit: ' + totalCredit.toLocaleString();
      if (diffEl) {
        const diff = totalDebit - totalCredit;
        if (diff === 0) {
          diffEl.textContent = 'Balanced ✓';
          diffEl.className = 'balance-diff balanced';
        } else {
          diffEl.textContent = 'Diff: ' + diff.toLocaleString();
          diffEl.className = 'balance-diff unbalanced';
        }
      }
    }
    document.getElementById('lines-tbody').addEventListener('input', updateVoucherBalanceBar);

    // Old voucher-inline chat handler (POST /transactions/chat with form-fill)
    // was removed. The "Open AI chat" button on the Vouchers page now
    // navigates to the AI Chat page instead — see openAiChatInlineBtn wiring.
    // appendChatMessage / extractMessageText / appendChatReport helpers are
    // intentionally kept so nothing else that called them breaks at boot.

    let lastSavedTransactionId = null;

    function buildConfirmationSummary(date, description, lines, currency) {
      const debitLines = lines.filter(l => l.debit > 0);
      const creditLines = lines.filter(l => l.credit > 0);
      const ccyLabel = currency ? ` ${currency}` : '';
      let summary = `Date: ${date}\n`;
      if (currency) summary += `Currency: ${currency}\n`;
      if (description) summary += `Description: ${description}\n`;
      summary += '\nDebit entries:\n';
      debitLines.forEach(l => { summary += `  ${l.account_code}: ${l.debit.toLocaleString()}${ccyLabel} ${l.line_description ? '(' + l.line_description + ')' : ''}\n`; });
      summary += '\nCredit entries:\n';
      creditLines.forEach(l => { summary += `  ${l.account_code}: ${l.credit.toLocaleString()}${ccyLabel} ${l.line_description ? '(' + l.line_description + ')' : ''}\n`; });
      summary += `\nTotal: ${lines.reduce((s, l) => s + l.debit, 0).toLocaleString()}${ccyLabel}\n`;
      return summary;
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const date = document.getElementById('date').value;
      const reference = document.getElementById('reference').value.trim() || null;
      const description = document.getElementById('description').value.trim() || null;
      const currency = (document.getElementById('txn-currency')?.value || preferredFormCurrency());
      const rows = Array.from(linesTbody.querySelectorAll('.line-row'));
      const lines = rows.map(tr => ({
        account_code: tr.querySelector('.line-code').value.trim(),
        debit: parseInt(tr.querySelector('.line-debit').value, 10) || 0,
        credit: parseInt(tr.querySelector('.line-credit').value, 10) || 0,
        line_description: tr.querySelector('.line-desc').value.trim() || null
      })).filter(l => l.account_code);
      if (lines.length < 2) {
        showAlert('Please add at least two lines with an account code.', true);
        return;
      }
      const totalDebit = lines.reduce((s, l) => s + l.debit, 0);
      const totalCredit = lines.reduce((s, l) => s + l.credit, 0);
      if (totalDebit !== totalCredit) {
        showAlert('Total debits must equal total credits. (Debit: ' + formatNum(totalDebit) + ', Credit: ' + formatNum(totalCredit) + ')', true);
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
      const confirmMsg = buildConfirmationSummary(date, description, lines, currency);
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
            lines,
            entity_links,
            attachment_ids: selectedAttachments.map(a => a.id),
          })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || 'Error saving voucher. ' + res.status, true);
          return;
        }
        lastSavedTransactionId = data.id || null;
        showAlert('Voucher saved. Ledger updated.');
        loadLedger();
        loadEntities();
        loadOwnerDashboard();
        resetVoucherForm();
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      } finally {
        submitBtn.disabled = false;
        document.getElementById('results-wrap').classList.remove('loading');
      }
    });

    let ledgerData = null;
    let chartTurnover = null;
    let chartBalance = null;

    resultsTbody.addEventListener('click', (e) => {
      const tr = e.target.closest('tr.ledger-row');
      if (tr && tr.dataset.accountCode) openAccountDetail(tr.dataset.accountCode);
    });
