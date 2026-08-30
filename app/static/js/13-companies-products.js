
    // ═══════ Company profile & branding ═══════
    const CP_FIELDS = ['legal_name','brand_color','tax_id','registration_number',
      'economic_code','national_id','province','city','postal_code','bank_account_no','iban',
      'email','phone','website','invoice_number_prefix','address','bank_details',
      'default_payment_terms','invoice_footer'];
    async function loadCompanyProfile() {
      if (!document.getElementById('cp-save')) return;
      try {
        const res = await fetch(API + '/admin/company-profile');
        if (!res.ok) return;
        const d = await res.json();
        CP_FIELDS.forEach(f => {
          const el = document.getElementById('cp-' + f);
          if (el) el.value = d[f] || (f === 'brand_color' ? '#0f766e' : '');
        });
        applyCompanyBranding(d);  // refresh the read-only summary + sidebar
      } catch (_) {}
    }
    (function wireCompanyProfile() {
      const saveBtn = document.getElementById('cp-save');
      if (!saveBtn) return;
      saveBtn.addEventListener('click', async () => {
        const status = document.getElementById('cp-status');
        const body = {};
        CP_FIELDS.forEach(f => { const el = document.getElementById('cp-' + f); if (el) body[f] = el.value; });
        try {
          const res = await fetch(API + '/admin/company-profile', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
          });
          if (!res.ok) { const e = await res.json().catch(() => ({})); showAlert(e.detail || t('cpSaveFailed'), true); return; }
          // Upload logo / signature if chosen.
          for (const [id, path] of [['cp-logo-file','/admin/company-profile/logo'],
                                    ['cp-signature-file','/admin/company-profile/signature']]) {
            const fileEl = document.getElementById(id);
            if (fileEl && fileEl.files && fileEl.files[0]) {
              const fd = new FormData(); fd.append('file', fileEl.files[0]);
              const up = await fetch(API + path, { method: 'POST', body: fd });
              if (!up.ok) { const e = await up.json().catch(() => ({})); showAlert(e.detail || t('cpSaveFailed'), true); return; }
              fileEl.value = '';
            }
          }
          if (status) { status.textContent = t('cpSaved'); setTimeout(() => { status.textContent = ''; }, 3000); }
          showAlert(t('cpSaved'));
        } catch (_) { showAlert(t('cpSaveFailed'), true); }
      });
    })();

    // ═══════ Companies console (super-admin only) ═══════
    async function loadCompanies() {
      const tbody = document.getElementById('companies-tbody');
      if (!tbody) return;
      try {
        const res = await fetch(API + '/admin/companies');
        const data = await res.json().catch(() => []);
        if (!res.ok || !Array.isArray(data)) { tbody.innerHTML = ''; return; }
        tbody.innerHTML = data.map(c => {
          const suspended = c.status === 'suspended';
          const toggleLabel = suspended ? t('companiesReactivate') : t('companiesSuspend');
          const nextStatus = suspended ? 'active' : 'suspended';
          // Plain <img>; the monogram fallback is wired via JS below (an inline
          // onerror string can leak stray text — see _setBrandLogo).
          const letter = escapeHtml((c.name || 'C')[0].toUpperCase());
          const logo = `<img class="co-logo" data-letter="${letter}" src="${API}/admin/companies/${escapeHtml(c.id)}/logo" alt="" style="width:26px;height:26px;border-radius:6px;object-fit:contain;flex:0 0 26px;">`;
          return `<tr>
            <td><span style="display:inline-flex; align-items:center; gap:0.5rem;">${logo}${escapeHtml(c.name)}</span></td>
            <td>${escapeHtml(c.locale)}</td>
            <td>${escapeHtml(c.base_currency)}</td>
            <td>${escapeHtml(c.login_username || '-')}</td>
            <td>${escapeHtml(suspended ? t('companiesStatusSuspended') : t('companiesStatusActive'))}</td>
            <td>
              <button type="button" class="btn btn-secondary btn-sm co-toggle" data-id="${escapeHtml(c.id)}" data-status="${nextStatus}">${escapeHtml(toggleLabel)}</button>
              <button type="button" class="btn btn-secondary btn-sm co-reset" data-id="${escapeHtml(c.id)}">${escapeHtml(t('companiesResetPw'))}</button>
            </td>
          </tr>`;
        }).join('');
        tbody.querySelectorAll('img.co-logo').forEach(img => {
          img.onerror = () => {
            const s = document.createElement('span');
            s.className = 'brand-monogram';
            s.style.cssText = 'width:26px;height:26px;flex:0 0 26px;font-size:12px';
            s.textContent = img.dataset.letter || 'C';
            img.replaceWith(s);
          };
        });
      } catch (_) { tbody.innerHTML = ''; }
    }

    (function wireCompaniesConsole() {
      // Default the base-currency dropdown from the chosen locale (uk→GBP, else IRR).
      const coLocale = document.getElementById('co-locale');
      const coCurrency = document.getElementById('co-currency');
      const syncCoCurrency = () => {
        if (!coLocale || !coCurrency) return;
        coCurrency.value = coLocale.value === 'uk' ? 'GBP' : 'IRR';
      };
      if (coLocale) coLocale.addEventListener('change', syncCoCurrency);
      syncCoCurrency();
      const createBtn = document.getElementById('co-create');
      if (createBtn) createBtn.addEventListener('click', async () => {
        const name = (document.getElementById('co-name').value || '').trim();
        const locale = document.getElementById('co-locale').value;
        const kind = document.getElementById('co-kind')?.value || 'business';
        const base_currency = (document.getElementById('co-currency').value || '').trim();
        const username = (document.getElementById('co-username').value || '').trim();
        const password = document.getElementById('co-password').value || '';
        if (!name || !username || !password) { showAlert(t('companiesMissingFields'), true); return; }
        try {
          const res = await fetch(API + '/admin/companies', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, locale, kind, base_currency, username, password }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { showAlert(data.detail || t('companiesCreateFailed'), true); return; }
          showAlert(t('companiesCreated'));
          document.getElementById('co-name').value = '';
          document.getElementById('co-username').value = '';
          document.getElementById('co-password').value = '';
          loadCompanies();
        } catch (_) { showAlert(t('companiesCreateFailed'), true); }
      });
      const tbody = document.getElementById('companies-tbody');
      if (tbody) tbody.addEventListener('click', async (e) => {
        const toggle = e.target.closest('.co-toggle');
        const reset = e.target.closest('.co-reset');
        if (toggle) {
          const id = toggle.getAttribute('data-id');
          const status = toggle.getAttribute('data-status');
          try {
            const res = await fetch(API + '/admin/companies/' + id, {
              method: 'PATCH', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ status }),
            });
            if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(d.detail || t('companiesUpdateFailed'), true); return; }
            showAlert(t('companiesUpdated'));
            loadCompanies();
          } catch (_) { showAlert(t('companiesUpdateFailed'), true); }
        } else if (reset) {
          const id = reset.getAttribute('data-id');
          const pw = await uiPrompt({ title: t('companiesResetPw'), message: t('companiesNewPwPrompt'), type: 'password' });
          if (!pw) return;
          try {
            const res = await fetch(API + '/admin/companies/' + id + '/reset-password', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ password: pw }),
            });
            if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(d.detail || t('companiesResetFailed'), true); return; }
            showAlert(t('companiesResetOk'));
          } catch (_) { showAlert(t('companiesResetFailed'), true); }
        }
      });
    })();

    // ═══════ Period close & adjustments ═══════
    async function loadClosedPeriod() {
      const inp = document.getElementById('closed-period-input');
      const status = document.getElementById('closed-period-status');
      if (!inp) return;
      try {
        const r = await fetch(API + '/admin/closed-period');
        if (!r.ok) return;
        const d = await r.json();
        inp.value = d.closed_period || '';
        if (status) status.textContent = d.closed_period ? tf('periodLockedThrough', { date: d.closed_period }) : t('periodOpen');
      } catch (_) {}
    }
    async function _saveClosedPeriod(value) {
      try {
        const r = await fetch(API + '/admin/closed-period', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ closed_period: value }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { showAlert(d.detail || t('periodLockError'), true); return; }
        showAlert(t('periodLockSaved'));
        loadClosedPeriod();
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    }
    document.getElementById('closed-period-save')?.addEventListener('click', () => {
      _saveClosedPeriod(document.getElementById('closed-period-input').value || '');
    });
    document.getElementById('closed-period-clear')?.addEventListener('click', () => _saveClosedPeriod(''));

    async function loadAdjustments() {
      const wrap = document.getElementById('adjustments-list');
      if (!wrap) return;
      try {
        const r = await fetch(API + '/adjustments');
        if (!r.ok) return;
        const rows = await r.json();
        if (!rows.length) { wrap.innerHTML = '<span style="color:var(--text-muted);">' + escapeHtml(t('adjNone')) + '</span>'; return; }
        wrap.innerHTML = rows.map((a) => {
          const sched = (a.kind === 'prepayment' || a.kind === 'depreciation')
            ? ` — ${a.periods_posted}/${a.periods}` + (a.net_book_value != null ? `, ${t('adjNbv')}: ${formatNum(a.net_book_value)}` : '')
            : (a.reversal_transaction_id ? ` — ${t('adjAutoReversed')}` : '');
          const canRelease = (a.kind === 'prepayment' || a.kind === 'depreciation') && a.status === 'active';
          return `<div style="padding:0.3rem 0; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; gap:0.5rem;">
            <span>${escapeHtml(t('adj_' + a.kind) || a.kind)}: ${escapeHtml(a.description || '')} (${formatNum(a.amount)} ${escapeHtml(a.currency)})${escapeHtml(sched)}</span>
            ${canRelease ? `<button type="button" class="btn btn-secondary btn-sm adj-release" data-id="${a.id}">${escapeHtml(t('adjRelease'))}</button>` : ''}
          </div>`;
        }).join('');
      } catch (_) {}
    }
    document.getElementById('adjustments-list')?.addEventListener('click', async (e) => {
      const btn = e.target.closest('.adj-release');
      if (!btn) return;
      try {
        const r = await fetch(API + '/adjustments/' + encodeURIComponent(btn.dataset.id) + '/release', { method: 'POST' });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { showAlert(d.detail || t('adjReleaseError'), true); return; }
        showAlert(t('adjReleased'));
        loadAdjustments(); loadLedger();
      } catch (err) { showAlert('Connection error: ' + err.message, true); }
    });
    async function _postAdjustment(url, body) {
      try {
        const r = await fetch(API + url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { showAlert(d.detail || t('adjError'), true); return false; }
        showAlert(t('adjRecorded'));
        loadAdjustments(); loadLedger(); loadOwnerDashboard();
        return true;
      } catch (err) { showAlert('Connection error: ' + err.message, true); return false; }
    }
    document.getElementById('accr-save')?.addEventListener('click', () => {
      const amount = parseInt(document.getElementById('accr-amount').value, 10);
      const d = document.getElementById('accr-date').value;
      if (!amount || amount <= 0 || !d) { showAlert(t('invAmountInvalid'), true); return; }
      _postAdjustment('/adjustments/accrual', {
        amount, date: d, description: document.getElementById('accr-desc').value || null,
        currency: currencyUnit(), auto_reverse: document.getElementById('accr-reverse').checked,
      });
    });
    document.getElementById('prep-save')?.addEventListener('click', () => {
      const amount = parseInt(document.getElementById('prep-amount').value, 10);
      const periods = parseInt(document.getElementById('prep-periods').value, 10);
      const d = document.getElementById('prep-date').value;
      if (!amount || amount <= 0 || !periods || periods < 1 || !d) { showAlert(t('invAmountInvalid'), true); return; }
      _postAdjustment('/adjustments/prepayment', {
        amount, periods, start_date: d, description: document.getElementById('prep-desc').value || null, currency: currencyUnit(),
      });
    });
    document.getElementById('dep-save')?.addEventListener('click', () => {
      const cost = parseInt(document.getElementById('dep-cost').value, 10);
      const periods = parseInt(document.getElementById('dep-life').value, 10);
      const residual = parseInt(document.getElementById('dep-residual').value, 10) || 0;
      const d = document.getElementById('dep-date').value;
      if (!cost || cost <= 0 || !periods || periods < 1 || !d) { showAlert(t('invAmountInvalid'), true); return; }
      _postAdjustment('/adjustments/depreciation', {
        cost, periods, residual, start_date: d, currency: currencyUnit(),
      });
    });
    document.getElementById('top-nav').addEventListener('click', (e) => {
      const btn = e.target.closest('.nav-btn[data-page]');
      if (!btn) return;
      loadPageData(btn.dataset.page);
    });

    // ═══════ Account Datalist for Manager Form ═══════
    let accountDatalistLoaded = false;
    async function loadAccountDatalist() {
      if (accountDatalistLoaded) return;
      try {
        const res = await fetch(API + '/manager-reports/accounts/list');
        if (!res.ok) return;
        const accs = await res.json();
        const dl = document.getElementById('mgr-account-datalist');
        if (!dl) return;
        dl.innerHTML = '';
        accs.forEach(a => {
          const opt = document.createElement('option');
          opt.value = a.code;
          opt.textContent = `${a.code} — ${a.name}`;
          dl.appendChild(opt);
        });
        accountDatalistLoaded = true;
      } catch (_) {}
    }

    // ═══════ Product/Entity Datalist for Manager Form ═══════
    let productEntityDatalistLoaded = false;
    async function loadProductEntityDatalist() {
      if (productEntityDatalistLoaded) return;
      try {
        const dl = document.getElementById('mgr-product-entity-datalist');
        if (!dl) return;
        dl.innerHTML = '';
        // Load products and entities in parallel
        const [prodRes, entRes] = await Promise.all([
          fetch(API + '/manager-reports/products/names').catch(() => null),
          fetch(API + '/manager-reports/entities/search').catch(() => null),
        ]);
        const products = prodRes && prodRes.ok ? await prodRes.json() : [];
        const entities = entRes && entRes.ok ? await entRes.json() : [];
        products.forEach(name => {
          const opt = document.createElement('option');
          opt.value = name;
          opt.textContent = name + ' (product)';
          dl.appendChild(opt);
        });
        entities.forEach(e => {
          const opt = document.createElement('option');
          opt.value = e.name;
          opt.textContent = e.name + ' (' + e.type + ')';
          dl.appendChild(opt);
        });
        productEntityDatalistLoaded = true;
      } catch (_) {}
    }

    // ═══════ CEO Mode Module ═══════
    let ceoTrendChart = null, ceoProfitChart = null, ceoExpenseChart = null, ceoBalanceChart = null;

    async function loadCEOReport() {
      try {
        const res = await fetch(bsAPI + '/ceo/report');
        if (!res.ok) return;
        const d = await res.json();
        // Sync the global currency from the server's response (auto-detects
        // GBP for UK locale, IRR for Iran, etc.) before formatting any
        // of the KPI cards below.
        if (d.currency) window.__REPORTING_CURRENCY = d.currency;
        const ccy = currencyUnit();

        document.getElementById('ceo-grade').textContent = d.health_grade;
        document.getElementById('ceo-grade').style.color = d.health_grade <= 'B' ? '#2e7d32' : d.health_grade <= 'C' ? '#f57f17' : '#c62828';
        document.getElementById('ceo-risk').textContent = d.risk_score + '/100';
        document.getElementById('ceo-risk').style.color = d.risk_score <= 30 ? '#2e7d32' : d.risk_score <= 60 ? '#f57f17' : '#c62828';
        document.getElementById('ceo-runway').textContent = d.cash_runway_months + ' mo';
        document.getElementById('ceo-margin').textContent = d.profit_margin + '%';
        document.getElementById('ceo-margin').style.color = d.profit_margin >= 0 ? '#2e7d32' : '#c62828';

        // KPI cards
        const kpiGrid = document.getElementById('ceo-kpis');
        kpiGrid.innerHTML = '';
        const kpiItems = [
          { label: 'Total Revenue (12m)', value: d.revenue_total, unit: ccy, color: '#2e7d32' },
          { label: 'Net Profit (12m)', value: d.profit_total, unit: ccy, color: d.profit_total >= 0 ? '#2e7d32' : '#c62828' },
          { label: 'Cash Position', value: d.cash_position, unit: ccy, color: d.cash_position >= 0 ? '#0f766e' : '#c62828' },
          { label: 'Burn Rate', value: d.burn_rate, unit: ccy + '/mo', color: 'var(--text)' },
          { label: 'Liability Ratio', value: (d.liability_ratio * 100).toFixed(1) + '%', unit: '', color: d.liability_ratio > 0.6 ? '#c62828' : 'var(--text)' },
        ];
        kpiItems.forEach(k => {
          const div = document.createElement('div');
          div.className = 'panel';
          div.style.cssText = 'padding:0.6rem;text-align:center;';
          const displayVal = typeof k.value === 'number' ? k.value.toLocaleString() : k.value;
          div.innerHTML = `<div style="font-size:0.72rem;color:var(--text-muted);">${escapeHtml(localizeDynamicText(k.label))}</div>
            <div style="font-size:1.1rem;font-weight:700;color:${k.color};">${displayVal} ${k.unit}</div>`;
          kpiGrid.appendChild(div);
        });

        // Alerts
        const alertsEl = document.getElementById('ceo-alerts');
        alertsEl.innerHTML = '';
        (d.alerts || []).forEach(a => {
          const color = a.severity === 'critical' ? '#c62828' : '#f57f17';
          const div = document.createElement('div');
          div.style.cssText = `padding:0.5rem 0.75rem;margin-bottom:0.4rem;border-left:4px solid ${color};background:#fafafa;border-radius:4px;`;
          div.innerHTML = `<strong style="color:${color}">${escapeHtml(a.title)}</strong><br><span style="font-size:0.85rem;">${escapeHtml(a.body)}</span>`;
          alertsEl.appendChild(div);
        });

        // AR/AP
        document.getElementById('ceo-ar').textContent = (d.accounts_receivable || 0).toLocaleString() + ' ' + ccy;
        document.getElementById('ceo-ap').textContent = (d.accounts_payable || 0).toLocaleString() + ' ' + ccy;

        // Balance sheet summary
        document.getElementById('ceo-assets').textContent = (d.total_assets || 0).toLocaleString();
        document.getElementById('ceo-liabilities').textContent = (d.total_liabilities || 0).toLocaleString();
        document.getElementById('ceo-equity').textContent = (d.total_equity || 0).toLocaleString();

        // Charts
        if (typeof Chart !== 'undefined') {
          const palette = ['#0f766e', '#c62828', '#0ea5e9', '#eab308', '#8b5cf6', '#f97316', '#10b981', '#64748b'];

          // Revenue vs Expenses trend
          if (ceoTrendChart) try { ceoTrendChart.destroy(); } catch(_){}
          const months = (d.monthly_revenue || []).map(m => m.month);
          ceoTrendChart = new Chart(document.getElementById('ceo-trend-chart'), {
            type: 'bar', data: {
              labels: months,
              datasets: [
                { label: 'Revenue', data: (d.monthly_revenue || []).map(m => m.amount), backgroundColor: '#0f766e' },
                { label: 'Expenses', data: (d.monthly_expenses || []).map(m => m.amount), backgroundColor: '#c62828' }
              ]
            }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { y: { ticks: { callback: v => formatNum(v) } } },
              onClick: (e, els) => {
                if (!els.length) return;
                const i = els[0].index;
                const di = els[0].datasetIndex;
                const mo = months[i];
                if (di === 0) {
                  showTransactionDrilldown(t('fieldRevenue') + ' — ' + mo, { account_code_prefix: '41,42,43', month: mo });
                } else {
                  showTransactionDrilldown(t('fieldCost') + ' — ' + mo, { account_code_prefix: '51,52,53,61,62', month: mo });
                }
              }
            }
          });

          // Profit trend
          if (ceoProfitChart) try { ceoProfitChart.destroy(); } catch(_){}
          ceoProfitChart = new Chart(document.getElementById('ceo-profit-chart'), {
            type: 'line', data: {
              labels: months,
              datasets: [{ label: 'Net Profit', data: (d.monthly_profit || []).map(m => m.amount), borderColor: '#0f766e', backgroundColor: 'rgba(15,118,110,0.15)', fill: true, tension: 0.3 }]
            }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { y: { ticks: { callback: v => formatNum(v) } } },
              onClick: (e, els) => { if (els.length) showTransactionDrilldown(t('fieldNetProfit') + ' — ' + months[els[0].index], { account_code_prefix: '41,42,43,51,52,53,61,62', month: months[els[0].index] }); }
            }
          });

          // Top expenses donut
          if (ceoExpenseChart) try { ceoExpenseChart.destroy(); } catch(_){}
          const topExp = d.top_expenses || [];
          ceoExpenseChart = new Chart(document.getElementById('ceo-expense-chart'), {
            type: 'doughnut', data: {
              labels: topExp.map(e => e.category),
              datasets: [{ data: topExp.map(e => e.amount), backgroundColor: palette }]
            }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } },
              onClick: (e, els) => {
                if (!els.length) return;
                const idx = els[0].index;
                const exp = topExp[idx];
                if (exp.account_code) {
                  showTransactionDrilldown(exp.category, { account_code: exp.account_code });
                } else {
                  showChartDrilldown('Top Expenses', exp.category, topExp);
                }
              }
            }
          });

          // Balance sheet donut
          if (ceoBalanceChart) try { ceoBalanceChart.destroy(); } catch(_){}
          const bsBreakdowns = [d.assets_breakdown || [], d.liabilities_breakdown || [], d.equity_breakdown || []];
          ceoBalanceChart = new Chart(document.getElementById('ceo-balance-chart'), {
            type: 'doughnut', data: {
              labels: ['Assets', 'Liabilities', 'Equity'],
              datasets: [{ data: [Math.abs(d.total_assets || 0), Math.abs(d.total_liabilities || 0), Math.abs(d.total_equity || 0)], backgroundColor: ['#0f766e', '#c62828', '#0ea5e9'] }]
            }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } },
              onClick: (e, els) => {
                if (els.length) {
                  const idx = els[0].index;
                  const label = ['Assets', 'Liabilities', 'Equity'][idx];
                  const prefixes = [['11','12','13','14','15'], ['21','22','23','24'], ['31','32','33']][idx];
                  const breakdown = bsBreakdowns[idx];
                  if (breakdown && breakdown.length) {
                    showChartDrilldown('Balance Sheet', label, breakdown);
                  } else {
                    showTransactionDrilldown(t('chartBalanceSheetMix') + ' — ' + label, { account_code_prefix: prefixes.join(',') });
                  }
                }
              }
            }
          });
        }
      } catch (e) { console.warn('CEO report load failed:', e); }
    }

    // ═══════ Chart Drill-Down ═══════
    let chartModalChart = null;
    function showChartDrilldown(title, label, ...data) {
      const modal = document.getElementById('chart-drilldown-modal');
      document.getElementById('chart-modal-title').textContent = title + (label ? ' — ' + label : '');
      const body = document.getElementById('chart-modal-body');
      body.innerHTML = '';
      document.getElementById('chart-modal-txn-area').style.display = 'none';
      document.getElementById('chart-modal-canvas').style.display = 'block';
      data.forEach(item => {
        if (!item) return;
        if (Array.isArray(item) && item.length > 0) {
          const keys = Object.keys(item[0]);
          const tbl = document.createElement('table');
          tbl.className = 'mini-table';
          const hasCode = keys.includes('code') || keys.includes('account_code');
          tbl.innerHTML = '<thead><tr>' + keys.map(k => `<th>${escapeHtml(localizeReportFieldName(k))}</th>`).join('') + '</tr></thead><tbody>'
            + item.map((row, ri) => {
              const code = row.code || row.account_code || '';
              return `<tr style="${hasCode ? 'cursor:pointer;' : ''}" ${hasCode ? `data-account-code="${escapeHtml(code)}" data-account-name="${escapeHtml(row.name || row.account_name || '')}"` : ''}>` + keys.map(k => {
                const v = row[k];
                return `<td>${typeof v === 'number' ? v.toLocaleString() : escapeHtml(String(v ?? '—'))}</td>`;
              }).join('') + '</tr>';
            }).join('')
            + '</tbody></table>';
          // Click handler for rows with account codes → drill into transactions
          if (hasCode) {
            tbl.addEventListener('click', (e) => {
              const tr = e.target.closest('tr[data-account-code]');
              if (tr) {
                const code = tr.dataset.accountCode;
                const name = tr.dataset.accountName;
                showTransactionDrilldown(name || code, { account_code: code });
              }
            });
          }
          const numKey = keys.find(k => k === 'balance' || k === 'amount');
          if (numKey) {
            const total = item.reduce((s, r) => s + (r[numKey] || 0), 0);
            const totalDiv = document.createElement('div');
            totalDiv.style.cssText = 'margin-top:0.4rem;font-size:0.9rem;font-weight:600;';
            totalDiv.textContent = `Total: ${total.toLocaleString()} ${currencyUnit()}`;
            body.appendChild(tbl);
            body.appendChild(totalDiv);
          } else {
            body.appendChild(tbl);
          }
        } else if (typeof item === 'object' && !Array.isArray(item)) {
          const tbl = document.createElement('table');
          tbl.className = 'mini-table';
          const keys = Object.keys(item);
          const vals = Object.values(item);
          tbl.innerHTML = '<thead><tr>' + keys.map(k => `<th>${escapeHtml(localizeReportFieldName(k))}</th>`).join('') + '</tr></thead><tbody><tr>' + vals.map((v, i) => {
            const k = keys[i];
            const isClickable = (k === 'metric' || k === 'category') && item._drillParams;
            const drillKey = v === 'Revenue' ? 'revenue' : v === 'Expenses' ? 'expense' : null;
            if (isClickable && drillKey && item._drillParams[drillKey]) {
              return `<td style="cursor:pointer;color:var(--primary);text-decoration:underline;" data-drill-key="${drillKey}">${typeof v === 'number' ? v.toLocaleString() : escapeHtml(String(v ?? ''))}</td>`;
            }
            return `<td>${typeof v === 'number' ? v.toLocaleString() : escapeHtml(String(v ?? ''))}</td>`;
          }).join('') + '</tr></tbody>';
          if (item._drillParams) {
            tbl.addEventListener('click', (e) => {
              const td = e.target.closest('td[data-drill-key]');
              if (td) {
                const params = item._drillParams[td.dataset.drillKey];
                if (params) showTransactionDrilldown(td.textContent, params);
              }
            });
          }
          body.appendChild(tbl);
        }
      });
      if (!body.children.length) {
        body.innerHTML = '<p style="color:var(--text-muted);">No detailed data available.</p>';
      }
      // Always add a "View all transactions" link if we can infer account codes
      const _bsSectionPrefixes = { 'Assets': '11,12,13,14,15', 'Liabilities': '21,22,23,24', 'Equity': '31,32,33' };
      if (_bsSectionPrefixes[label]) {
        const viewBtn = document.createElement('button');
        viewBtn.className = 'btn btn-primary btn-sm';
        viewBtn.style.cssText = 'margin-top:0.75rem;';
        viewBtn.textContent = 'View all ' + label + ' transactions';
        viewBtn.onclick = () => showTransactionDrilldown(title + ' — ' + label, { account_code_prefix: _bsSectionPrefixes[label] });
        body.appendChild(viewBtn);
      }
      modal.style.display = 'block';
    }

    // ═══════ Transaction Drill-Down (with search/filter/pagination) ═══════
    let _drilldownParams = {};
    let _drilldownPage = 1;
    let _drilldownDebounce = null;

    function showTransactionDrilldown(title, params) {
      const modal = document.getElementById('chart-drilldown-modal');
      document.getElementById('chart-modal-title').textContent = title;
      document.getElementById('chart-modal-body').innerHTML = '';
      document.getElementById('chart-modal-canvas').style.display = 'none';
      const txnArea = document.getElementById('chart-modal-txn-area');
      txnArea.style.display = 'block';

      const searchEl = document.getElementById('drilldown-search');
      const fromEl = document.getElementById('drilldown-from');
      const toEl = document.getElementById('drilldown-to');
      searchEl.value = '';
      fromEl.value = params.from_date || '';
      toEl.value = params.to_date || '';

      _drilldownParams = { ...params };
      _drilldownPage = 1;

      // Wire up search with debounce
      searchEl.oninput = () => {
        clearTimeout(_drilldownDebounce);
        _drilldownDebounce = setTimeout(() => { _drilldownPage = 1; _fetchDrilldown(); }, 300);
      };
      fromEl.onchange = () => { _drilldownPage = 1; _fetchDrilldown(); };
      toEl.onchange = () => { _drilldownPage = 1; _fetchDrilldown(); };

      modal.style.display = 'block';
      _fetchDrilldown();
    }

    async function _fetchDrilldown() {
      const wrap = document.getElementById('drilldown-table-wrap');
      const summaryEl = document.getElementById('drilldown-summary');
      const pagEl = document.getElementById('drilldown-pagination');
      wrap.innerHTML = '<p style="color:var(--text-muted);padding:0.5rem;">Loading...</p>';

      const q = new URLSearchParams();
      if (_drilldownParams.account_code) q.set('account_code', _drilldownParams.account_code);
      if (_drilldownParams.account_code_prefix) q.set('account_code_prefix', _drilldownParams.account_code_prefix);
      if (_drilldownParams.month) q.set('month', _drilldownParams.month);
      // Inherit the manager reports currency filter so drill-downs match the
      // currency the user was looking at when they clicked.
      const _mgrCcy = document.getElementById('mgr-currency')?.value;
      if (_mgrCcy) q.set('currency', _mgrCcy);

      const fromVal = document.getElementById('drilldown-from').value;
      const toVal = document.getElementById('drilldown-to').value;
      if (fromVal) q.set('from_date', fromVal);
      if (toVal) q.set('to_date', toVal);

      const searchVal = document.getElementById('drilldown-search').value.trim();
      if (searchVal) q.set('search', searchVal);

      q.set('page', String(_drilldownPage));
      q.set('page_size', '50');

      try {
        const res = await fetch(API + '/reports/transactions/search?' + q.toString());
        const data = await res.json();
        if (!res.ok) { wrap.innerHTML = '<p style="color:#c62828;">Error loading transactions.</p>'; return; }

        summaryEl.textContent = `${data.total_count} transaction${data.total_count !== 1 ? 's' : ''} | ${t('fieldTotalDebit')}: ${formatNum(data.total_debit)} | ${t('fieldTotalCredit')}: ${formatNum(data.total_credit)}`;

        if (!data.rows.length) {
          wrap.innerHTML = '<p style="color:var(--text-muted);padding:0.5rem;">' + escapeHtml(t('noDataYet')) + '</p>';
          pagEl.innerHTML = '';
          return;
        }

        // Detect if the rows span multiple currencies — if so, show a currency column
        const ccySet = new Set((data.rows || []).map(r => (r.currency || 'IRR').toUpperCase()));
        const showCcyCol = ccySet.size > 1;
        const baseHeaders = [t('labelDate'), t('labelReference'), t('labelDescription'), t('tableAccountCode'), t('fieldAccount')];
        const amtHeaders = showCcyCol
          ? ['Curr', t('tableDebit'), t('tableCredit')]
          : [t('tableDebit'), t('tableCredit')];
        const headers = [...baseHeaders, ...amtHeaders, 'Entities'];
        wrap.innerHTML = `<table class="mini-table"><thead><tr>${headers.map(h => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>${
          data.rows.map(r => {
            const ccy = (r.currency || 'IRR').toUpperCase();
            const debitCell = r.debit ? formatMoney(r.debit, ccy) : '—';
            const creditCell = r.credit ? formatMoney(r.credit, ccy) : '—';
            const ccyCell = showCcyCol ? `<td><span class="ccy-badge ccy-${escapeHtml(ccy)}">${escapeHtml(ccy)}</span></td>` : '';
            return `<tr>
              <td>${escapeHtml(r.date)}</td>
              <td>${escapeHtml(r.reference || '—')}</td>
              <td>${escapeHtml(r.description || r.line_description || '—')}</td>
              <td>${escapeHtml(r.account_code)}</td>
              <td>${escapeHtml(r.account_name)}</td>
              ${ccyCell}
              <td>${debitCell}</td>
              <td>${creditCell}</td>
              <td>${(r.entity_names || []).map(n => escapeHtml(n)).join(', ') || '—'}</td>
            </tr>`;
          }).join('')
        }</tbody></table>`;
        // Also show a single currency label above the table when all rows share one
        if (!showCcyCol && data.rows.length && data.rows[0].currency) {
          summaryEl.textContent = `${data.total_count} transaction${data.total_count !== 1 ? 's' : ''} (${data.rows[0].currency}) | ${t('fieldTotalDebit')}: ${formatNum(data.total_debit)} | ${t('fieldTotalCredit')}: ${formatNum(data.total_credit)}`;
        }

        // Pagination
        const totalPages = Math.ceil(data.total_count / data.page_size);
        if (totalPages > 1) {
          pagEl.innerHTML = `
            <button class="btn btn-secondary btn-sm" ${_drilldownPage <= 1 ? 'disabled' : ''} onclick="_drilldownPage--;_fetchDrilldown();">&#8592; Prev</button>
            <span style="font-size:0.85rem;">${data.page} / ${totalPages}</span>
            <button class="btn btn-secondary btn-sm" ${_drilldownPage >= totalPages ? 'disabled' : ''} onclick="_drilldownPage++;_fetchDrilldown();">Next &#8594;</button>
          `;
        } else {
          pagEl.innerHTML = '';
        }
      } catch (e) {
        wrap.innerHTML = '<p style="color:#c62828;">Error: ' + escapeHtml(e.message) + '</p>';
      }
    }

    let _lastDrilldownRows = [];
    async function _exportDrilldown(format) {
      // Fetch ALL rows (no pagination) for export
      const q = new URLSearchParams();
      if (_drilldownParams.account_code) q.set('account_code', _drilldownParams.account_code);
      if (_drilldownParams.account_code_prefix) q.set('account_code_prefix', _drilldownParams.account_code_prefix);
      if (_drilldownParams.month) q.set('month', _drilldownParams.month);
      // Inherit the manager reports currency filter so drill-downs match the
      // currency the user was looking at when they clicked.
      const _mgrCcy = document.getElementById('mgr-currency')?.value;
      if (_mgrCcy) q.set('currency', _mgrCcy);
      const fromVal = document.getElementById('drilldown-from').value;
      const toVal = document.getElementById('drilldown-to').value;
      if (fromVal) q.set('from_date', fromVal);
      if (toVal) q.set('to_date', toVal);
      const searchVal = document.getElementById('drilldown-search').value.trim();
      if (searchVal) q.set('search', searchVal);
      q.set('page', '1'); q.set('page_size', '10000');
      try {
        const res = await fetch(API + '/reports/transactions/search?' + q.toString());
        const data = await res.json();
        const rows = data.rows || [];
        const title = document.getElementById('chart-modal-title').textContent;
        if (format === 'csv') {
          let csv = 'Date,Reference,Description,Account Code,Account Name,Debit,Credit,Entities\n';
          rows.forEach(r => { csv += `${r.date},"${(r.reference||'').replace(/"/g,'""')}","${(r.description||'').replace(/"/g,'""')}",${r.account_code},"${(r.account_name||'').replace(/"/g,'""')}",${r.debit||0},${r.credit||0},"${(r.entity_names||[]).join('; ')}"\n`; });
          downloadTextFile('transactions.csv', csv, 'text/csv');
        } else {
          const w = window.open('', '_blank');
          if (!w) { showAlert(t('allowPopupsPdf'), true); return; }
          w.document.write(`<html><head><title>${escapeHtml(title)}</title><style>body{font-family:sans-serif;padding:20px;font-size:11px}table{width:100%;border-collapse:collapse}th,td{border:1px solid #ccc;padding:4px 6px;text-align:left}.num{text-align:right}h2{margin:0 0 8px}</style></head><body>
            <h2>${escapeHtml(title)}</h2>
            <p>${data.total_count} transactions | Debit: ${formatNum(data.total_debit)} | Credit: ${formatNum(data.total_credit)}</p>
            <table><thead><tr><th>Date</th><th>Ref</th><th>Description</th><th>Code</th><th>Account</th><th class="num">Debit</th><th class="num">Credit</th><th>Entities</th></tr></thead><tbody>
            ${rows.map(r=>`<tr><td>${escapeHtml(r.date)}</td><td>${escapeHtml(r.reference||'')}</td><td>${escapeHtml(r.description||'')}</td><td>${escapeHtml(r.account_code)}</td><td>${escapeHtml(r.account_name||'')}</td><td class="num">${formatNum(r.debit||0)}</td><td class="num">${formatNum(r.credit||0)}</td><td>${(r.entity_names||[]).join(', ')}</td></tr>`).join('')}
            </tbody></table></body></html>`);
          w.document.close();
          setTimeout(() => w.print(), 300);
        }
      } catch(e) { console.error('Export error:', e); }
    }

    // Generic table export helper for Products section
    function _exportTableFromEl(containerEl, title, format) {
      const table = containerEl.querySelector('table');
      if (!table) return;
      const headers = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
      const rows = [...table.querySelectorAll('tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent.trim()));
      if (format === 'csv') {
        let csv = headers.join(',') + '\n';
        rows.forEach(r => { csv += r.map(c => `"${c.replace(/"/g,'""')}"`).join(',') + '\n'; });
        downloadTextFile((title||'export').replace(/\s+/g, '_') + '.csv', csv, 'text/csv');
      } else {
        const w = window.open('', '_blank');
        if (!w) { showAlert(t('allowPopupsPdf'), true); return; }
        w.document.write(`<html><head><title>${escapeHtml(title)}</title><style>body{font-family:sans-serif;padding:20px;font-size:12px}table{width:100%;border-collapse:collapse}th,td{border:1px solid #ccc;padding:5px 7px;text-align:left}h2{margin:0 0 8px}</style></head><body>
          <h2>${escapeHtml(title)}</h2>
          <table><thead><tr>${headers.map(h=>`<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>
          ${rows.map(r=>`<tr>${r.map(c=>`<td>${escapeHtml(c)}</td>`).join('')}</tr>`).join('')}
          </tbody></table></body></html>`);
        w.document.close();
        setTimeout(() => w.print(), 300);
      }
    }

    // ═══════ Products & Relationships Hub ═══════
    let _prodTab = 'catalog';
    let _prodChart = null;
    let _prodCatalogData = null;

    // Tab switching
    document.querySelectorAll('.prod-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.prod-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        _prodTab = tab.dataset.tab;
        _renderProductsTab();
      });
    });

    document.getElementById('prod-load-btn').addEventListener('click', () => _renderProductsTab());

    function _prodQueryParams() {
      const q = new URLSearchParams();
      const from = document.getElementById('prod-from-date').value;
      const to = document.getElementById('prod-to-date').value;
      const search = document.getElementById('prod-search').value.trim();
      const entityType = document.getElementById('prod-entity-type').value;
      if (from) q.set('from_date', from);
      if (to) q.set('to_date', to);
      if (search) q.set('search', search);
      if (entityType) q.set('entity_type', entityType);
      return q.toString();
    }

    async function loadProductsCatalog() {
      _prodTab = 'catalog';
      document.querySelectorAll('.prod-tab').forEach(t => t.classList.remove('active'));
      document.querySelector('.prod-tab[data-tab="catalog"]').classList.add('active');
      _renderProductsTab();
    }

    async function _renderProductsTab() {
      const content = document.getElementById('prod-content');
      const chartPanel = document.getElementById('prod-chart-panel');
      const detailPanel = document.getElementById('prod-detail-panel');
      detailPanel.style.display = 'none';
      chartPanel.style.display = 'none';
      content.innerHTML = '<p style="color:var(--text-muted);padding:0.5rem;">Loading...</p>';

      try {
        if (_prodTab === 'catalog') await _renderCatalog(content, chartPanel);
        else if (_prodTab === 'clients') await _renderEntityMatrix(content, chartPanel, 'client');
        else if (_prodTab === 'suppliers') await _renderEntityMatrix(content, chartPanel, 'supplier');
        else if (_prodTab === 'profitability') await _renderProfitability(content, chartPanel);
      } catch (e) {
        content.innerHTML = '<p style="color:#c62828;padding:0.5rem;">Error: ' + escapeHtml(e.message) + '</p>';
      }
    }

    async function _renderCatalog(content, chartPanel) {
      const q = _prodQueryParams();
      const res = await fetch(API + '/products/catalog' + (q ? '?' + q : ''));
      const data = await res.json();
      _prodCatalogData = data;

      if (!data.items || !data.items.length) {
        content.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('noDataYet')) + '</p>';
        return;
      }

      content.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;margin-bottom:0.5rem;">
          <span style="font-size:0.85rem;color:var(--text-muted);">
            ${data.items.length} products | ${t('fieldRevenue')}: ${formatNum(data.total_revenue)} | ${t('fieldCost')}: ${formatNum(data.total_cost)} | ${t('fieldProfit')}: ${formatNum(data.total_profit)} ${currencyUnit()}
          </span>
          <span>
            <button class="btn btn-secondary btn-sm" onclick="_exportTableFromEl(document.getElementById('prod-content'),'Products_Catalog','csv')">CSV</button>
            <button class="btn btn-secondary btn-sm" onclick="_exportTableFromEl(document.getElementById('prod-content'),'Products_Catalog','pdf')">PDF</button>
          </span>
        </div>
        <div style="max-height:400px;overflow:auto;">
        <table class="mini-table">
          <thead><tr>
            <th>${t('fieldProduct')}</th><th>SKU</th><th>${t('fieldRevenue')}</th><th>${t('fieldCost')}</th><th>${t('fieldProfit')}</th><th>${t('fieldMarginPct')}</th><th>${t('fieldClientCount')}</th><th>${t('fieldSupplierCount')}</th>
          </tr></thead>
          <tbody>${data.items.map(p => `
            <tr style="cursor:pointer;" data-product="${escapeHtml(p.product_name)}">
              <td><strong>${escapeHtml(p.product_name)}</strong></td>
              <td>${escapeHtml(p.sku || '—')}</td>
              <td>${formatNum(p.total_sales_revenue)}</td>
              <td>${formatNum(p.total_purchase_cost)}</td>
              <td style="color:${p.gross_profit >= 0 ? '#2e7d32' : '#c62828'};">${formatNum(p.gross_profit)}</td>
              <td>${p.margin_pct != null ? p.margin_pct + '%' : '—'}</td>
              <td>${p.client_count}</td>
              <td>${p.supplier_count}</td>
            </tr>
          `).join('')}</tbody>
        </table>
        </div>
      `;

      // Click handler for product rows
      content.querySelector('table').addEventListener('click', (e) => {
        const tr = e.target.closest('tr[data-product]');
        if (tr) _showProductDetail(tr.dataset.product);
      });

      // Chart: top 10 by revenue
      _renderProdChart(chartPanel, 'bar',
        data.items.slice(0, 10).map(p => p.product_name),
        [
          { label: t('fieldRevenue'), data: data.items.slice(0, 10).map(p => p.total_sales_revenue), backgroundColor: '#0f766e' },
          { label: t('fieldCost'), data: data.items.slice(0, 10).map(p => p.total_purchase_cost), backgroundColor: '#c62828' },
        ]
      );
    }

    async function _showProductDetail(productName) {
      const panel = document.getElementById('prod-detail-panel');
      const body = document.getElementById('prod-detail-body');
      document.getElementById('prod-detail-title').textContent = productName;
      body.innerHTML = '<p style="color:var(--text-muted);">Loading...</p>';
      panel.style.display = 'block';

      try {
        const q = _prodQueryParams();
        const res = await fetch(API + '/products/detail/' + encodeURIComponent(productName) + (q ? '?' + q : ''));
        const d = await res.json();

        body.innerHTML = `
          <div style="display:flex;gap:1.5rem;flex-wrap:wrap;margin-bottom:1rem;">
            <div class="kpi-card"><div class="label">${t('fieldRevenue')}</div><div class="value">${formatNum(d.total_revenue)}</div></div>
            <div class="kpi-card"><div class="label">${t('fieldCost')}</div><div class="value">${formatNum(d.total_cost)}</div></div>
            <div class="kpi-card"><div class="label">${t('fieldProfit')}</div><div class="value" style="color:${d.gross_profit >= 0 ? '#2e7d32' : '#c62828'};">${formatNum(d.gross_profit)}</div></div>
            <div class="kpi-card"><div class="label">${t('fieldMarginPct')}</div><div class="value">${d.margin_pct != null ? d.margin_pct + '%' : '—'}</div></div>
          </div>

          ${d.clients.length ? `
            <h4 style="margin:0.75rem 0 0.3rem;">Clients (${d.clients.length})</h4>
            <table class="mini-table"><thead><tr><th>${t('labelName')}</th><th>${t('fieldRevenue')}</th><th>Invoices</th></tr></thead><tbody>
              ${d.clients.map(c => `<tr><td>${escapeHtml(c.name)}</td><td>${formatNum(c.revenue)}</td><td>${c.invoice_count}</td></tr>`).join('')}
            </tbody></table>
          ` : ''}

          ${d.suppliers.length ? `
            <h4 style="margin:0.75rem 0 0.3rem;">Suppliers (${d.suppliers.length})</h4>
            <table class="mini-table"><thead><tr><th>${t('labelName')}</th><th>${t('fieldCost')}</th><th>Invoices</th></tr></thead><tbody>
              ${d.suppliers.map(s => `<tr><td>${escapeHtml(s.name)}</td><td>${formatNum(s.cost)}</td><td>${s.invoice_count}</td></tr>`).join('')}
            </tbody></table>
          ` : ''}

          ${d.monthly_series.length ? `
            <h4 style="margin:0.75rem 0 0.3rem;">Monthly Trend</h4>
            <table class="mini-table"><thead><tr><th>${t('fieldWeek')}</th><th>${t('fieldRevenue')}</th><th>${t('fieldCost')}</th></tr></thead><tbody>
              ${d.monthly_series.map(m => `<tr><td>${escapeHtml(m.month)}</td><td>${formatNum(m.revenue)}</td><td>${formatNum(m.cost)}</td></tr>`).join('')}
            </tbody></table>
          ` : ''}

          ${d.invoices.length ? `
            <h4 style="margin:0.75rem 0 0.3rem;">Invoices (${d.invoices.length})</h4>
            <div style="max-height:250px;overflow:auto;">
            <table class="mini-table"><thead><tr><th>#</th><th>Type</th><th>${t('labelDate')}</th><th>Entity</th><th>Qty</th><th>Price</th><th>${t('tableTotal')}</th></tr></thead><tbody>
              ${d.invoices.map(inv => `<tr>
                <td>${escapeHtml(inv.number)}</td>
                <td><span class="alert-chip ${inv.kind === 'sales' ? 'low' : 'medium'}">${escapeHtml(inv.kind)}</span></td>
                <td>${escapeHtml(inv.issue_date)}</td>
                <td>${escapeHtml(inv.entity_name || '—')}</td>
                <td>${inv.quantity}</td>
                <td>${formatNum(inv.unit_price)}</td>
                <td>${formatNum(inv.line_total)}</td>
              </tr>`).join('')}
            </tbody></table>
            </div>
          ` : ''}
        `;
      } catch (e) {
        body.innerHTML = '<p style="color:#c62828;">Error: ' + escapeHtml(e.message) + '</p>';
      }
    }

    async function _renderEntityMatrix(content, chartPanel, entityType) {
      const q = new URLSearchParams();
      const from = document.getElementById('prod-from-date').value;
      const to = document.getElementById('prod-to-date').value;
      if (from) q.set('from_date', from);
      if (to) q.set('to_date', to);
      q.set('entity_type', entityType);
      const search = document.getElementById('prod-search').value.trim();
      if (search) q.set('search', search);

      const res = await fetch(API + '/products/entity-matrix?' + q.toString());
      const data = await res.json();

      if (!data.relationships.length) {
        content.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('noDataYet')) + '</p>';
        return;
      }

      const isClient = entityType === 'client';
      const valLabel = isClient ? t('fieldRevenue') : t('fieldCost');
      const valKey = isClient ? 'total_revenue' : 'total_cost';

      content.innerHTML = `
        <div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-bottom:0.5rem;">
          <button class="btn btn-secondary btn-sm" onclick="_exportTableFromEl(document.getElementById('prod-content'),'${isClient?'Client':'Supplier'}_Matrix','csv')">CSV</button>
          <button class="btn btn-secondary btn-sm" onclick="_exportTableFromEl(document.getElementById('prod-content'),'${isClient?'Client':'Supplier'}_Matrix','pdf')">PDF</button>
        </div>
        <div style="max-height:450px;overflow:auto;">
        <table class="mini-table">
          <thead><tr>
            <th>${t('fieldEntityName')}</th><th>${t('labelEntityType')}</th><th>Products</th><th>${valLabel}</th>${isClient ? `<th>${t('fieldProfit')}</th>` : ''}
          </tr></thead>
          <tbody>${data.relationships.map(r => `
            <tr>
              <td><strong>${escapeHtml(r.entity_name)}</strong></td>
              <td>${escapeHtml(r.entity_type)}</td>
              <td>${r.products.map(p => `<span style="display:inline-block;background:#e2e8f0;border-radius:4px;padding:0.1rem 0.4rem;margin:0.1rem;font-size:0.78rem;">${escapeHtml(p.product_name)} (${formatNum(isClient ? p.revenue : p.cost)})</span>`).join(' ')}</td>
              <td>${formatNum(r[valKey])}</td>
              ${isClient ? `<td style="color:${r.profit >= 0 ? '#2e7d32' : '#c62828'};">${formatNum(r.profit)}</td>` : ''}
            </tr>
          `).join('')}</tbody>
        </table>
        </div>
      `;

      // Chart: top entities
      const top = data.relationships.slice(0, 10);
      _renderProdChart(chartPanel, 'bar',
        top.map(r => r.entity_name),
        isClient ? [
          { label: t('fieldRevenue'), data: top.map(r => r.total_revenue), backgroundColor: '#0f766e' },
          { label: t('fieldCost'), data: top.map(r => r.total_cost), backgroundColor: '#c62828' },
        ] : [
          { label: t('fieldCost'), data: top.map(r => r.total_cost), backgroundColor: '#c62828' },
        ]
      );
    }

    async function _renderProfitability(content, chartPanel) {
      const q = _prodQueryParams();
      const res = await fetch(API + '/products/profitability' + (q ? '?' + q : ''));
      const data = await res.json();

      if (!data.by_product.length) {
        content.innerHTML = '<p class="empty-state" style="padding:0.5rem;">' + escapeHtml(t('noDataYet')) + '</p>';
        return;
      }

      content.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;margin-bottom:0.5rem;">
          <span style="font-size:0.85rem;color:var(--text-muted);">
            ${t('fieldRevenue')}: ${formatNum(data.total_revenue)} | ${t('fieldCost')}: ${formatNum(data.total_cost)} | ${t('fieldProfit')}: ${formatNum(data.total_profit)} | Avg Margin: ${data.avg_margin != null ? data.avg_margin + '%' : '—'}
          </span>
          <span>
            <button class="btn btn-secondary btn-sm" onclick="_exportTableFromEl(document.getElementById('prod-content'),'Profitability','csv')">CSV</button>
            <button class="btn btn-secondary btn-sm" onclick="_exportTableFromEl(document.getElementById('prod-content'),'Profitability','pdf')">PDF</button>
          </span>
        </div>
        <h4 style="margin:0.5rem 0 0.3rem;">By Product</h4>
        <div style="max-height:300px;overflow:auto;">
        <table class="mini-table">
          <thead><tr><th>Product</th><th>${t('fieldRevenue')}</th><th>${t('fieldCost')}</th><th>${t('fieldProfit')}</th><th>${t('fieldMarginPct')}</th><th>Top Client</th></tr></thead>
          <tbody>${data.by_product.map(p => `
            <tr>
              <td><strong>${escapeHtml(p.product_name)}</strong></td>
              <td>${formatNum(p.revenue)}</td>
              <td>${formatNum(p.cost)}</td>
              <td style="color:${p.profit >= 0 ? '#2e7d32' : '#c62828'};font-weight:600;">${formatNum(p.profit)}</td>
              <td><span style="background:${p.margin_pct != null && p.margin_pct >= 20 ? '#dcfce7' : p.margin_pct != null && p.margin_pct < 0 ? '#fee2e2' : '#f1f5f9'};padding:0.15rem 0.5rem;border-radius:4px;font-size:0.82rem;">${p.margin_pct != null ? p.margin_pct + '%' : '—'}</span></td>
              <td>${escapeHtml(p.top_client || '—')}</td>
            </tr>
          `).join('')}</tbody>
        </table>
        </div>

        ${data.by_client_product.length ? `
          <h4 style="margin:0.75rem 0 0.3rem;">By Client × Product</h4>
          <div style="max-height:250px;overflow:auto;">
          <table class="mini-table">
            <thead><tr><th>Client</th><th>Product</th><th>${t('fieldRevenue')}</th><th>${t('fieldCost')}</th><th>${t('fieldProfit')}</th></tr></thead>
            <tbody>${data.by_client_product.slice(0, 50).map(cp => `
              <tr>
                <td>${escapeHtml(cp.client_name)}</td>
                <td>${escapeHtml(cp.product_name)}</td>
                <td>${formatNum(cp.revenue)}</td>
                <td>${formatNum(cp.cost)}</td>
                <td style="color:${cp.profit >= 0 ? '#2e7d32' : '#c62828'};">${formatNum(cp.profit)}</td>
              </tr>
            `).join('')}</tbody>
          </table>
          </div>
        ` : ''}
      `;

      // Chart: profit by product
      const top = data.by_product.slice(0, 10);
      _renderProdChart(chartPanel, 'bar',
        top.map(p => p.product_name),
        [
          { label: t('fieldRevenue'), data: top.map(p => p.revenue), backgroundColor: '#0f766e' },
          { label: t('fieldCost'), data: top.map(p => p.cost), backgroundColor: '#c62828' },
          { label: t('fieldProfit'), data: top.map(p => p.profit), backgroundColor: '#0ea5e9' },
        ]
      );
    }

    function _renderProdChart(chartPanel, type, labels, datasets) {
      if (typeof Chart === 'undefined') return;
      chartPanel.style.display = 'block';
      if (_prodChart) try { _prodChart.destroy(); } catch(_){}
      _prodChart = new Chart(document.getElementById('prod-chart'), {
        type,
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom' } },
          scales: { y: { ticks: { callback: v => formatNum(v) } } }
        }
      });
    }

    // ═══════ Enhanced Audit Module ═══════
    let auditFindings = [];

    // Domain tab switching
    document.querySelectorAll('.audit-domain-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.audit-domain-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        renderAuditFindings(tab.dataset.domain);
      });
    });

    function renderAuditFindings(domain) {
      const filtered = domain === 'all' ? auditFindings : auditFindings.filter(f => f.domain === domain);
      const findingsList = document.getElementById('audit-findings-list');
      findingsList.innerHTML = '';
      if (!filtered.length) {
        findingsList.innerHTML = '<p style="color:#2e7d32;">No findings in this domain.</p>';
        return;
      }
      filtered.forEach((f, idx) => {
        const color = f.severity === 'critical' ? '#c62828' : f.severity === 'warning' ? '#f57f17' : '#1565c0';
        const domainLabel = f.domain === 'treasury' ? 'Treasury' : f.domain === 'managerial' ? 'Managerial' : 'Financial';
        const div = document.createElement('div');
        div.style.cssText = `padding:0.5rem 0.75rem;margin-bottom:0.4rem;border-left:4px solid ${color};background:#fafafa;border-radius:4px;cursor:pointer;transition:background 0.15s;`;
        div.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;">
          <div><strong style="color:${color}">${escapeHtml(f.severity.toUpperCase())}</strong> <span style="font-size:0.75rem;background:#e3f2fd;padding:0.1rem 0.4rem;border-radius:4px;">${domainLabel}</span> — <strong>${escapeHtml(f.title)}</strong></div>
          <span style="font-size:0.75rem;color:var(--text-muted);">Click to inspect</span>
        </div>
        <div style="font-size:0.85rem;color:var(--text-muted);margin-top:0.2rem;">${escapeHtml(f.detail)}</div>
        ${f.amount ? `<div style="font-size:0.8rem;margin-top:0.15rem;">Amount: <strong>${Number(f.amount).toLocaleString()} ${currencyUnit()}</strong></div>` : ''}`;
        div.addEventListener('mouseenter', () => div.style.background = '#f0f4f8');
        div.addEventListener('mouseleave', () => div.style.background = '#fafafa');
        div.addEventListener('click', () => openAuditDrilldown(f, idx));
        findingsList.appendChild(div);
      });
    }

    function openAuditDrilldown(finding, idx) {
      const modal = document.getElementById('audit-drilldown-modal');
      document.getElementById('audit-modal-title').textContent = finding.title;
      const body = document.getElementById('audit-modal-body');
      const domainLabel = finding.domain === 'treasury' ? 'Treasury (خزانه‌داری)' : finding.domain === 'managerial' ? 'Managerial (حسابداری مدیریتی)' : 'Financial (حسابداری مالی)';
      body.innerHTML = `
        <div style="margin-bottom:0.5rem;"><span style="font-size:0.8rem;background:#e3f2fd;padding:0.15rem 0.5rem;border-radius:4px;">${domainLabel}</span></div>
        <p><strong>Severity:</strong> <span style="color:${finding.severity === 'critical' ? '#c62828' : '#f57f17'}">${finding.severity.toUpperCase()}</span></p>
        <p><strong>Category:</strong> ${escapeHtml(finding.category)}</p>
        <p><strong>Detail:</strong> ${escapeHtml(finding.detail)}</p>
        ${finding.amount ? `<p><strong>Amount:</strong> ${Number(finding.amount).toLocaleString()} ${currencyUnit()}</p>` : ''}
        ${finding.entity_id ? `<p><strong>Entity ID:</strong> ${escapeHtml(finding.entity_id)}</p>` : ''}
      `;
      const statusEl = document.getElementById('audit-modal-status');
      statusEl.textContent = finding._status ? `Status: ${finding._status}` : '';
      document.getElementById('audit-modal-verify-btn').onclick = () => { finding._status = 'verified'; statusEl.textContent = 'Marked as Verified'; statusEl.style.color = '#2e7d32'; };
      document.getElementById('audit-modal-flag-btn').onclick = () => { finding._status = 'flagged'; statusEl.textContent = 'Flagged for Review'; statusEl.style.color = '#f57f17'; };
      document.getElementById('audit-modal-dismiss-btn').onclick = () => { finding._status = 'dismissed'; statusEl.textContent = 'Dismissed'; statusEl.style.color = 'var(--text-muted)'; };
      modal.style.display = 'block';
    }

    // Save liability threshold
    document.getElementById('audit-save-threshold-btn').addEventListener('click', async () => {
      const val = document.getElementById('audit-liability-threshold').value;
      if (!val) return;
      try {
        await fetch(bsAPI + '/settings', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'liability_threshold', value: val })
        });
        showAlert('Liability threshold saved.');
      } catch (e) { showAlert('Failed to save threshold.', true); }
    });

    // ═══════ Enhanced Manager Report (product filter + cash flow periods + AP) ═══════
    const mgrProductFilterEl = document.getElementById('mgr-product-filter');
    const mgrPeriodGranularityEl = document.getElementById('mgr-period-granularity');

    // Override runManagerReport to support new report types and filters
    const _origRunManagerReport = runManagerReport;
    runManagerReport = async function() {
      const type = (mgrReportTypeEl.value || '').trim();

      // Accounts payable handler
      if (type === 'accounts_payable') {
        try {
          const q = new URLSearchParams();
          if (mgrFromDateEl.value) q.set('from_date', mgrFromDateEl.value);
          if (mgrToDateEl.value) q.set('to_date', mgrToDateEl.value);
          mgrRunBtn.disabled = true;
          const res = await fetch(API + '/manager-reports/operational/accounts-payable?' + q.toString());
          const data = await res.json();
          if (!res.ok) { showAlert(data.detail || 'Failed', true); return; }
          lastManagerReport = data;
          const items = data.items || [];
          let html = '<div class="report-preview-wrap"><table class="mini-table"><thead><tr><th>Invoice</th><th>Vendor</th><th>Amount</th><th>Due Date</th><th>Status</th><th>Aging</th><th>Days Overdue</th></tr></thead><tbody>';
          items.forEach(it => {
            const overColor = it.days_overdue > 60 ? '#c62828' : it.days_overdue > 30 ? '#f57f17' : 'var(--text)';
            html += `<tr><td>${escapeHtml(it.invoice_number || '')}</td><td>${escapeHtml(it.vendor)}</td><td>${formatNum(it.amount)}</td><td>${it.due_date || '—'}</td><td><span class="badge">${it.status}</span></td><td>${it.aging_bucket}</td><td style="color:${overColor};font-weight:600;">${it.days_overdue}</td></tr>`;
          });
          html += '</tbody></table></div>';
          html += `<div style="margin-top:0.5rem;font-size:0.9rem;"><strong>Total Payable:</strong> ${formatNum(data.total)} (${data.count} items)</div>`;
          mgrReportPreviewEl.innerHTML = html;
          mgrReportJsonEl.textContent = JSON.stringify(data, null, 2);
          // AP chart by aging
          if (typeof Chart !== 'undefined' && items.length) {
            const buckets = {};
            items.forEach(it => { buckets[it.aging_bucket] = (buckets[it.aging_bucket] || 0) + it.amount; });
            const chartPanel = document.getElementById('mgr-report-chart-panel');
            chartPanel.style.display = 'block';
            if (managerReportChart) try { managerReportChart.destroy(); } catch(_){}
            managerReportChart = new Chart(document.getElementById('mgr-report-chart'), {
              type: 'doughnut', data: {
                labels: Object.keys(buckets),
                datasets: [{ label: 'AP Aging', data: Object.values(buckets), backgroundColor: ['#0f766e', '#f57f17', '#c62828', '#8b5cf6'] }]
              }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' }, title: { display: true, text: 'AP by Aging Bucket' } },
                onClick: (e, els) => { if (els.length) { const key = Object.keys(buckets)[els[0].index]; showChartDrilldown('AP Aging', key, { bucket: key, amount: buckets[key] }); } }
              }
            });
          }
          if (mgrExportJsonBtn) mgrExportJsonBtn.disabled = false;
          if (mgrExportCsvBtn) mgrExportCsvBtn.disabled = false;
        } catch (err) { showAlert('Error: ' + err.message, true); }
        finally { mgrRunBtn.disabled = false; }
        return;
      }

      // For sales/purchase reports, append product_name filter
      if (type.includes('sales') || type.includes('purchase')) {
        const filterVal = (mgrProductFilterEl.value || '').trim();
        if (filterVal) {
          const origFetch = window.fetch;
          const patchedFetch = (url, opts) => {
            if (typeof url === 'string' && url.includes('/manager-reports/')) {
              const sep = url.includes('?') ? '&' : '?';
              url += sep + 'product_name=' + encodeURIComponent(filterVal);
            }
            return origFetch(url, opts);
          };
          window.fetch = patchedFetch;
          try { await _origRunManagerReport(); } finally { window.fetch = origFetch; }
          return;
        }
      }

      await _origRunManagerReport();
    };

    // ═══════ Clickable Charts Enhancement ═══════
    // Override renderReportChart to add click handlers
    const _origRenderReportChart = renderReportChart;
    renderReportChart = function(canvas, report, existingChart) {
      const chart = _origRenderReportChart(canvas, report, existingChart);
      if (chart && report) {
        // Extract date filters from the report period OR from the form fields
        const rp = report.period || {};
        const _fromDate = rp.from_date || mgrFromDateEl.value || '';
        const _toDate = rp.to_date || mgrToDateEl.value || '';
        chart.options.onClick = (e, els) => {
          if (!els.length) return;
          const idx = els[0].index;
          const dsIdx = els[0].datasetIndex;
          const label = chart.data.labels ? chart.data.labels[idx] : '';
          const rt = (report.report_type || '').toLowerCase();
          const rows = report.rows || [];
          const matchRow = rows[idx];
          if (matchRow && matchRow.account_code) {
            showTransactionDrilldown(label || matchRow.account_name || rt, { account_code: matchRow.account_code, from_date: _fromDate, to_date: _toDate });
          } else if (rt.includes('balance_sheet')) {
            const bsPrefixMap = { 'Assets': '11,12,13,14,15', 'Liabilities': '21,22,23,24', 'Equity': '31,32,33' };
            const sectionLabel = label || (matchRow && matchRow.section) || '';
            const prefix = bsPrefixMap[sectionLabel]
              || bsPrefixMap[Object.keys(bsPrefixMap).find(k => t('section' + k).toLowerCase() === sectionLabel.toLowerCase())]
              || ['11,12,13,14,15', '21,22,23,24', '31,32,33'][idx]
              || '';
            if (prefix) showTransactionDrilldown(sectionLabel || 'Balance Sheet', { account_code_prefix: prefix, to_date: _toDate });
            else showChartDrilldown(rt, label, matchRow || {});
          } else if (rt.includes('income')) {
            // Map each bar to its specific account prefix
            const incPrefixes = ['41,42,43', '51', '61', '62', '41,42,43,51,61,62'];
            const prefix = incPrefixes[idx] || '41,42,43,51,52,53,61,62';
            showTransactionDrilldown(label || rt, { account_code_prefix: prefix, from_date: _fromDate, to_date: _toDate });
          } else if (rt.includes('cash_flow')) {
            showTransactionDrilldown(label || rt, { account_code_prefix: '1110', from_date: _fromDate, to_date: _toDate });
          } else {
            const value = chart.data.datasets[dsIdx] ? chart.data.datasets[dsIdx].data[idx] : 0;
            const dsLabel = chart.data.datasets[dsIdx] ? chart.data.datasets[dsIdx].label : '';
            showChartDrilldown(rt, label, { label, dataset: dsLabel, value });
          }
        };
        chart.update();
      }
      return chart;
    };

    // ═══════ Price Management ═══════
    async function loadPriceMgmtItems() {
      try {
        const res = await fetch(API + '/manager-reports/inventory/items');
        const items = await res.json();
        const sel = document.getElementById('price-mgmt-item');
        if (!sel) return;
        sel.innerHTML = '<option value="">Select item</option>';
        (items || []).forEach(i => {
          const opt = document.createElement('option');
          opt.value = i.id;
          opt.textContent = (i.sku ? i.sku + ' - ' : '') + i.name + (i.list_price ? ' (current: ' + i.list_price.toLocaleString() + ' ' + currencyUnit() + ')' : '');
          opt.dataset.price = i.list_price || 0;
          sel.appendChild(opt);
        });
      } catch(_){}
    }

    document.getElementById('price-mgmt-item').addEventListener('change', function() {
      const opt = this.selectedOptions[0];
      if (opt && opt.dataset.price) {
        document.getElementById('price-mgmt-value').value = opt.dataset.price;
      }
    });

    document.getElementById('price-mgmt-save-btn').addEventListener('click', async () => {
      const itemId = document.getElementById('price-mgmt-item').value;
      const price = document.getElementById('price-mgmt-value').value;
      const statusEl = document.getElementById('price-mgmt-status');
      if (!itemId) { statusEl.textContent = 'Select an item first.'; statusEl.style.color = '#c62828'; return; }
      try {
        const res = await fetch(API + '/manager-reports/inventory/items/' + itemId + '/price?list_price=' + price, { method: 'PATCH' });
        const data = await res.json();
        if (res.ok) {
          statusEl.textContent = `Price updated: ${data.name} — ${data.old_price.toLocaleString()} -> ${data.new_price.toLocaleString()} ${currencyUnit()}`;
          statusEl.style.color = '#2e7d32';
          loadPriceMgmtItems();
        } else {
          statusEl.textContent = data.detail || 'Failed to update price.';
          statusEl.style.color = '#c62828';
        }
      } catch (e) { statusEl.textContent = 'Error: ' + e.message; statusEl.style.color = '#c62828'; }
    });

    // ═══════ Enhanced Audit (overwrite handler) ═══════
    // Remove old handler and re-add with domain support
    const auditRunBtn2 = document.getElementById('audit-run-btn');
    const newAuditBtn = auditRunBtn2.cloneNode(true);
    auditRunBtn2.parentNode.replaceChild(newAuditBtn, auditRunBtn2);
    newAuditBtn.addEventListener('click', async () => {
      try {
        newAuditBtn.disabled = true;
        const res = await fetch(bsAPI + '/audit/report');
        const data = await res.json();
        document.getElementById('audit-scores').style.display = 'flex';
        document.getElementById('audit-integrity-score').textContent = data.integrity_score;
        document.getElementById('audit-integrity-score').style.color = data.integrity_score >= 80 ? '#2e7d32' : data.integrity_score >= 50 ? '#f57f17' : '#c62828';
        document.getElementById('audit-health-score').textContent = data.health_score;
        document.getElementById('audit-health-score').style.color = data.health_score >= 80 ? '#2e7d32' : data.health_score >= 50 ? '#f57f17' : '#c62828';
        document.getElementById('audit-checks-summary').textContent = `${data.checks_passed} passed / ${data.checks_failed} failed`;

        auditFindings = (data.findings || []).map(f => ({
          ...f,
          domain: f.domain || 'financial',
          _status: null
        }));

        const findingsWrap = document.getElementById('audit-findings-wrap');
        findingsWrap.style.display = 'block';

        // Reset domain tabs
        document.querySelectorAll('.audit-domain-tab').forEach(t => t.classList.remove('active'));
        document.querySelector('.audit-domain-tab[data-domain="all"]').classList.add('active');

        renderAuditFindings('all');
      } catch (e) { showAlert('Audit failed: ' + e.message, true); }
      finally { newAuditBtn.disabled = false; }
    });
