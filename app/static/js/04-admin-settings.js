
    function syncAIFieldsByProvider() {
      const p = (aiProviderSelect && aiProviderSelect.value) || 'lmstudio';
      const custom = p === 'custom';
      aiBaseInput.disabled = !custom;
      aiBaseInput.placeholder = custom ? 'https://api.example.com/openai/v1' : (p === 'metis' ? 'Auto: https://api.metisai.ir/openai/v1' : 'Auto: LM Studio URL');
    }

    async function loadAIConfig() {
      if (!isSuperadmin) return;
      try {
        const res = await fetch(API + '/admin/ai-config');
        const cfg = await res.json().catch(() => ({}));
        if (!res.ok) return;
        if (aiProviderSelect) aiProviderSelect.value = cfg.provider || 'lmstudio';
        if (aiModelInput) aiModelInput.value = (cfg.active && cfg.active.model) ? cfg.active.model : '';
        if (aiBaseInput) aiBaseInput.value = (cfg.active && cfg.active.base_url) ? cfg.active.base_url : '';
        if (aiKeyInput) aiKeyInput.value = '';
        syncAIFieldsByProvider();
      } catch (_) {}
    }

    async function loadAnthropicConfig() {
      if (!isSuperadmin) return;
      const modelEl = document.getElementById('anthropic-model-input');
      const baseEl = document.getElementById('anthropic-base-input');
      const keyEl = document.getElementById('anthropic-key-input');
      const statusEl = document.getElementById('anthropic-status');
      if (!modelEl || !baseEl || !keyEl) return;
      try {
        const res = await fetch(API + '/admin/anthropic-config');
        const cfg = await res.json().catch(() => ({}));
        if (!res.ok) return;
        modelEl.value = cfg.model || cfg.default_model || '';
        baseEl.value = (cfg.base_url && cfg.base_url !== cfg.default_base_url) ? cfg.base_url : '';
        baseEl.placeholder = cfg.default_base_url || 'https://api.anthropic.com';
        modelEl.placeholder = cfg.default_model || 'claude-opus-4-6';
        keyEl.value = '';
        if (statusEl) {
          statusEl.textContent = cfg.has_api_key
            ? 'API key is configured. Leave the field empty to keep it.'
            : 'No API key set — the AI accountant will return an error until you add one.';
        }
      } catch (_) {}
    }

    async function saveAnthropicConfig() {
      const modelEl = document.getElementById('anthropic-model-input');
      const baseEl = document.getElementById('anthropic-base-input');
      const keyEl = document.getElementById('anthropic-key-input');
      const btn = document.getElementById('anthropic-save-btn');
      const statusEl = document.getElementById('anthropic-status');
      if (!modelEl || !btn) return;
      const payload = {};
      // Send all three fields. Backend treats empty strings on base_url
      // as "fall back to default"; on api_key, empty is "keep current",
      // and "-" clears.
      const modelVal = (modelEl.value || '').trim();
      if (modelVal) payload.model = modelVal;
      payload.base_url = (baseEl.value || '').trim();
      const keyVal = (keyEl.value || '').trim();
      if (keyVal) payload.api_key = keyVal;
      try {
        btn.disabled = true;
        const res = await fetch(API + '/admin/anthropic-config', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          if (statusEl) statusEl.innerHTML = '<span style="color:#b91c1c;">' + escapeHtml(data.detail || 'Failed to save.') + '</span>';
          return;
        }
        if (statusEl) statusEl.innerHTML = '<span style="color:#059669;">Saved.</span>';
        keyEl.value = '';
        loadAnthropicConfig();
      } catch (err) {
        if (statusEl) statusEl.innerHTML = '<span style="color:#b91c1c;">' + escapeHtml('Connection error: ' + err.message) + '</span>';
      } finally {
        btn.disabled = false;
      }
    }

    // ── Chat-provider-shape (auto / anthropic / openai) ──
    // Toggles visibility of the Anthropic fields below; "openai" hides
    // them and shows a one-line hint pointing at the OpenAI-shape
    // section above.
    function _applyChatShapeVisibility(effective) {
      const anthropicWrap = document.getElementById('anthropic-fields-wrap');
      const openaiHint = document.getElementById('openai-shape-chat-hint');
      if (!anthropicWrap || !openaiHint) return;
      const usingOpenai = effective === 'openai';
      anthropicWrap.style.display = usingOpenai ? 'none' : '';
      openaiHint.style.display = usingOpenai ? 'block' : 'none';
    }

    async function loadChatProviderShape() {
      if (!isSuperadmin) return;
      const sel = document.getElementById('chat-shape-select');
      const hint = document.getElementById('chat-shape-hint');
      if (!sel) return;
      try {
        const r = await fetch(API + '/admin/chat-provider-shape');
        const data = await r.json().catch(() => ({}));
        if (!r.ok) return;
        sel.value = data.shape || '';
        _applyChatShapeVisibility(data.effective || 'anthropic');
        if (hint) {
          const baseHint = t('chatShapeHint') || hint.textContent;
          const note = data.shape === ''
            ? ` Currently: ${data.effective} (auto).`
            : '';
          hint.textContent = baseHint + note;
        }
      } catch (_) {}
    }

    async function saveChatProviderShape() {
      const sel = document.getElementById('chat-shape-select');
      const btn = document.getElementById('chat-shape-save-btn');
      const hint = document.getElementById('chat-shape-hint');
      if (!sel || !btn) return;
      btn.disabled = true;
      try {
        const r = await fetch(API + '/admin/chat-provider-shape', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ shape: sel.value }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          if (hint) hint.innerHTML = '<span style="color:#b91c1c;">' + escapeHtml(data.detail || 'Failed to save.') + '</span>';
          return;
        }
        _applyChatShapeVisibility(data.effective || 'anthropic');
        if (hint) hint.innerHTML = '<span style="color:#059669;">Saved.</span> Currently using <strong>' + escapeHtml(data.effective) + '</strong>.';
      } catch (err) {
        if (hint) hint.innerHTML = '<span style="color:#b91c1c;">' + escapeHtml('Connection error: ' + err.message) + '</span>';
      } finally {
        btn.disabled = false;
      }
    }

    async function saveAIConfig() {
      const payload = {
        provider: aiProviderSelect.value,
        model: (aiModelInput.value || '').trim(),
      };
      if (aiProviderSelect.value === 'custom') {
        payload.base_url = (aiBaseInput.value || '').trim();
      }
      const key = (aiKeyInput.value || '').trim();
      if (key) payload.api_key = key;
      try {
        aiSaveBtn.disabled = true;
        const res = await fetch(API + '/admin/ai-config', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showAlert(data.detail || 'Failed to update AI settings.', true); return; }
        showAlert('AI settings updated.');
        aiKeyInput.value = '';
        loadAIConfig();
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      } finally {
        aiSaveBtn.disabled = false;
      }
    }

    function renderUsersTable(users) {
      if (!usersWrapEl) return;
      if (!Array.isArray(users) || !users.length) {
        usersWrapEl.innerHTML = '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('usersNoUsers')) + '</p>';
        return;
      }
      usersWrapEl.innerHTML = `
        <table class="results-table" style="font-size:0.85rem;">
          <thead>
            <tr>
              <th>${escapeHtml(t('usersUsername'))}</th>
              <th>${escapeHtml(t('usersRole'))}</th>
              <th>${escapeHtml(t('usersStatus'))}</th>
              <th>${escapeHtml(t('tfaColumn'))}</th>
              <th>${escapeHtml(t('usersActions'))}</th>
            </tr>
          </thead>
          <tbody>
            ${users.map((u) => `
              <tr>
                <td>${escapeHtml(u.username)}${u.entity_name ? `<br><span style="color:var(--text-muted);font-size:0.78rem;">${escapeHtml(u.entity_name)}</span>` : ''}</td>
                <td>
                  <select class="user-role-select" data-id="${escapeHtml(u.id)}" style="font-size:0.8rem;padding:2px 4px;">
                    ${ROLE_ORDER.map((r) => `<option value="${r}"${u.role === r ? ' selected' : ''}>${escapeHtml(roleLabel(r))}</option>`).join('')}
                  </select>
                </td>
                <td>${u.is_active ? escapeHtml(t('usersActive')) : escapeHtml(t('usersDisabled'))}</td>
                <td>${u.two_factor ? escapeHtml(t('tfaOn')) : '<span style="color:var(--text-muted);">' + escapeHtml(t('tfaOff')) + '</span>'}</td>
                <td>
                  <button type="button" class="btn btn-secondary btn-sm user-pw-btn" data-id="${escapeHtml(u.id)}">${escapeHtml(t('usersResetPassword'))}</button>
                  <button type="button" class="btn btn-secondary btn-sm user-active-btn" data-id="${escapeHtml(u.id)}" data-active="${u.is_active ? '1' : '0'}">${u.is_active ? escapeHtml(t('usersDeactivate')) : escapeHtml(t('usersActivate'))}</button>
                  ${u.two_factor ? `<button type="button" class="btn btn-secondary btn-sm user-2fa-btn" data-id="${escapeHtml(u.id)}" data-username="${escapeHtml(u.username)}">${escapeHtml(t('tfaReset'))}</button>` : ''}
                  <button type="button" class="btn btn-danger btn-sm user-del-btn" data-id="${escapeHtml(u.id)}" data-username="${escapeHtml(u.username)}">${escapeHtml(t('usersDelete'))}</button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }

    // ═══════ Company branding (sidebar logo/name + profile summary) ═══════
    let _companyName = '';
    function _setBrandLogo(container, hasLogo, monogramLetter, brandColor, logoUrl) {
      if (!container) return;
      const letter = escapeHtml((monogramLetter || 'C').toUpperCase());
      const makeMono = () => {
        const s = document.createElement('span');
        s.className = 'brand-monogram';
        if (brandColor) s.style.background = brandColor;
        s.textContent = letter;
        return s;
      };
      container.innerHTML = '';
      if (hasLogo) {
        // Build via DOM (not an innerHTML string): the monogram markup contains
        // double quotes that would otherwise break an inline error-handler attribute
        // and leak stray text next to the logo.
        const img = document.createElement('img');
        img.alt = '';
        img.src = (logoUrl || (API + '/admin/company-profile/logo')) + '?t=' + Date.now();
        img.onerror = () => { container.innerHTML = ''; container.appendChild(makeMono()); };
        container.appendChild(img);
      } else {
        container.appendChild(makeMono());
      }
    }
    function applyCompanyBranding(p) {
      p = p || {};
      const name = (p.legal_name || (p.company && p.company.name) || _companyName || 'Company').trim();
      _companyName = name || _companyName;
      const sideName = document.getElementById('sidebar-company-name');
      if (sideName) sideName.textContent = name;
      _setBrandLogo(document.getElementById('sidebar-brand-logo'), p.has_logo, name[0], p.brand_color);
      // Read-only summary on Settings → Company profile (if rendered).
      const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val || '—'; };
      set('cp-sum-name', name);
      set('cp-sum-address', p.address);
      set('cp-sum-taxid', p.tax_id ? (t('cpTaxId') + ': ' + p.tax_id) : '');
      const contact = [p.email, p.phone, p.website].filter(Boolean).join(' · ');
      set('cp-sum-contact', contact);
      _setBrandLogo(document.getElementById('cp-sum-logo'), p.has_logo, name[0], p.brand_color);
    }
    async function loadCompanyBranding() {
      try {
        const res = await fetch(API + '/admin/company-profile');
        if (!res.ok) return;
        applyCompanyBranding(await res.json());
      } catch (_) {}
    }

    // ═══════ Password show/hide toggle ═══════
    const _EYE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    const _EYE_OFF = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
    function attachPasswordToggle(input) {
      if (!input || input.dataset.pwToggle) return null;
      input.dataset.pwToggle = '1';
      const wrap = document.createElement('span');
      wrap.className = 'pw-wrap';
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);
      const btn = document.createElement('button');
      btn.type = 'button';  // never submits the form
      btn.className = 'pw-toggle';
      btn.setAttribute('aria-pressed', 'false');
      btn.setAttribute('aria-label', t('showPassword'));
      btn.innerHTML = _EYE;
      btn.addEventListener('click', () => {
        const show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        btn.setAttribute('aria-pressed', show ? 'true' : 'false');
        btn.setAttribute('aria-label', t(show ? 'hidePassword' : 'showPassword'));
        btn.innerHTML = show ? _EYE_OFF : _EYE;
      });
      wrap.appendChild(btn);
      return btn;
    }
    ['co-password', 'new-user-password', 'ui-prompt-input'].forEach((id) => {
      attachPasswordToggle(document.getElementById(id));
    });

    async function loadCurrentUser() {
      try {
        const res = await fetch(API + '/auth/me');
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.user) return;
        if (settingsUserNameEl) settingsUserNameEl.textContent = data.user.username || '-';
        const tbUser = document.getElementById('topbar-user-name');
        if (tbUser) tbUser.textContent = data.user.username || '—';
        if (typeof setTwoFactorHint === 'function') setTwoFactorHint(data.user);
        // Signed in with a recovery code (login page) → say how many are left.
        try {
          const left = sessionStorage.getItem('aa_tfa_recovery_left');
          if (left !== null) {
            sessionStorage.removeItem('aa_tfa_recovery_left');
            setTimeout(() => showAlert(tf('tfaRecoveryUsed', { n: left }), Number(left) <= 2), 600);
          }
        } catch (_) { /* storage blocked: nothing to show */ }
        if (settingsUserRoleEl) settingsUserRoleEl.textContent = data.user.is_admin ? t('usersAdmin') : t('usersUser');
        const lang = (data.user.preferred_language || localStorage.getItem('aa_ui_language') || 'en').toLowerCase();
        applyLanguage(lang, true);
        if (settingsUserRoleEl) settingsUserRoleEl.textContent = data.user.is_admin ? t('usersAdmin') : t('usersUser');
        // Show the current company name in the header.
        const badge = document.getElementById('company-badge');
        if (badge && data.company && data.company.name) {
          badge.textContent = data.company.name;
          badge.style.display = '';
        } else if (badge) {
          badge.style.display = 'none';
        }
        // Sidebar brand: company name now (baseline), logo/legal-name after the
        // profile loads. Replaces the old hardcoded "Aline Books".
        _companyName = (data.company && data.company.name) || _companyName;
        const sideName = document.getElementById('sidebar-company-name');
        if (sideName && _companyName) sideName.textContent = _companyName;
        loadCompanyBranding();
        // Reveal the Companies console only for the super-admin/provisioner.
        isSuperadmin = !!data.user.is_superadmin;
        currentRole = (data.user.role || 'owner').toLowerCase();
        // AI provider wiring is platform-wide → super-admin only. Owners get a
        // note instead of controls that would 403.
        const aiSec = document.getElementById('ai-providers-section');
        const aiNote = document.getElementById('ai-providers-note');
        if (aiSec) aiSec.style.display = isSuperadmin ? '' : 'none';
        if (aiNote) aiNote.style.display = isSuperadmin ? 'none' : '';
        if (isSuperadmin) { loadAIConfig(); loadAnthropicConfig(); loadChatProviderShape(); }
        const navCo = document.getElementById('nav-companies');
        if (navCo) navCo.style.display = isSuperadmin ? '' : 'none';
        // Role-aware nav: hide what this role can't use (server still enforces).
        applyRoleAccess();
        // First login after an update → short tour of what changed (once).
        if (data.whats_new && !data.whats_new.seen && typeof openWhatsNew === 'function') {
          setTimeout(() => openWhatsNew(data.whats_new, { markSeen: true }), 400);
        }
        // Land on a page this role may actually see. If the cold-load page is
        // off-limits, drop to the role's home; honour a valid deep link.
        const landed = (location.hash || '#dashboard').slice(1);
        if (isSuperadmin && location.hash === '#companies') {
          showPage('companies');
        } else if (!canSeePage(landed) || !validPages.has(landed)) {
          showPage(roleHome());
          loadPageData(roleHome());
        }
        if (currentRole === 'owner') {
          loadUsers(); populateEntityLinkOptions(); loadDigestSettings(); loadApiKeys();
          loadAIConfig(); loadAnthropicConfig(); loadAIUsage();
        }
        if (isSuperadmin) loadAILimits();
      } catch (_) {
        applyLanguage(localStorage.getItem('aa_ui_language') || 'en', false);
      }
    }

    async function loadUsers() {
      // Employees created after login must be linkable too (QA 6.5): refresh
      // the "link to employee" options every time the user table loads.
      if (typeof populateEntityLinkOptions === 'function') populateEntityLinkOptions();
      try {
        const res = await fetch(API + '/admin/users');
        const data = await res.json().catch(() => []);
        if (!res.ok) {
          usersWrapEl.innerHTML = '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('usersNoPermission')) + '</p>';
          return;
        }
        renderUsersTable(data);
      } catch (err) {
        usersWrapEl.innerHTML = '<p class="empty-state" style="padding:0.4rem;">' + escapeHtml(t('usersLoadError')) + '</p>';
      }
    }

    async function createUser() {
      const username = (newUserUsernameEl && newUserUsernameEl.value || '').trim();
      const password = (newUserPasswordEl && newUserPasswordEl.value || '').trim();
      const role = (newUserRoleEl && newUserRoleEl.value) || 'employee';
      const entityId = (newUserEntityEl && newUserEntityEl.value) || null;
      if (!username || !password) {
        showAlert(t('usernamePasswordRequired'), true);
        return;
      }
      try {
        createUserBtn.disabled = true;
        const body = { username, password, role, preferred_language: ((uiLanguageSelectEl && uiLanguageSelectEl.value) || 'en'), is_active: true };
        if (entityId) body.entity_id = entityId;
        const res = await fetch(API + '/admin/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showAlert(data.detail || 'Failed to create user.', true);
          return;
        }
        showAlert(t('usersCreated'));
        newUserUsernameEl.value = '';
        newUserPasswordEl.value = '';
        if (newUserEntityEl) newUserEntityEl.value = '';
        loadUsers();
      } catch (err) {
        showAlert('Connection error: ' + err.message, true);
      } finally {
        createUserBtn.disabled = false;
      }
    }

    async function handleUserTableAction(e) {
      const tfaBtn = e.target.closest('.user-2fa-btn');
      if (tfaBtn) {
        const username = tfaBtn.dataset.username || '';
        if (!(await uiConfirm({ message: tf('tfaConfirmReset', { name: username }), confirmLabel: t('tfaReset'), danger: true }))) return;
        try {
          const res = await fetch(API + '/admin/users/' + encodeURIComponent(tfaBtn.dataset.id) + '/reset-2fa', { method: 'POST' });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { showAlert(data.detail || t('tfaFailed'), true); return; }
          showAlert(tf('tfaResetDone', { name: username }));
          loadUsers();
        } catch (err) {
          showAlert('Connection error: ' + err.message, true);
        }
        return;
      }
      const delBtn = e.target.closest('.user-del-btn');
      if (delBtn) {
        const id = delBtn.dataset.id;
        const username = delBtn.dataset.username || 'this user';
        if (!(await uiConfirm({ message: tf('confirmDeleteUser', { name: username }), confirmLabel: t('btnDelete'), danger: true }))) return;
        try {
          const res = await fetch(API + '/admin/users/' + encodeURIComponent(id), { method: 'DELETE' });
          const data = await res.json().catch(() => ({}));
          if (!res.ok && res.status !== 204) {
            showAlert(data.detail || 'Failed to delete user.', true);
            return;
          }
          showAlert(t('usersDeleted'));
          loadUsers();
        } catch (err) {
          showAlert('Connection error: ' + err.message, true);
        }
        return;
      }

      const activeBtn = e.target.closest('.user-active-btn');
      if (activeBtn) {
        const id = activeBtn.dataset.id;
        const nextActive = activeBtn.dataset.active !== '1';
        try {
          const res = await fetch(API + '/admin/users/' + encodeURIComponent(id), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: nextActive }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            showAlert(data.detail || 'Failed to update user.', true);
            return;
          }
          showAlert(t('usersRoleUpdated'));
          loadUsers();
        } catch (err) {
          showAlert('Connection error: ' + err.message, true);
        }
        return;
      }

      const pwBtn = e.target.closest('.user-pw-btn');
      if (pwBtn) {
        const id = pwBtn.dataset.id;
        const nextPassword = await uiPrompt({ title: t('resetPasswordTitle'), message: t('enterNewPassword'), type: 'password' });
        if (nextPassword == null) return;
        if (!nextPassword.trim()) {
          showAlert(t('passwordCannotBeEmpty'), true);
          return;
        }
        try {
          const res = await fetch(API + '/admin/users/' + encodeURIComponent(id), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: nextPassword }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            showAlert(data.detail || 'Failed to reset password.', true);
            return;
          }
          showAlert(t('usersPasswordReset'));
        } catch (err) {
          showAlert('Connection error: ' + err.message, true);
        }
      }
    }

    // ═══════ AI usage (Settings, owner) and AI limits (super-admin) ═══════
    // Server: app/services/ai_usage.py — tokens over any 24 hours, 0 = unlimited.
    function aiTokens(n) {
      const v = Number(n) || 0;
      if (v >= 1e6) return (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + 'M';
      if (v >= 1e3) return Math.round(v / 1e3) + 'k';
      return String(v);
    }
    function aiUsd(n) { const v = Number(n) || 0; return '$' + v.toFixed(v < 1 ? 4 : 2); }
    function aiMeter(used, budget) {
      if (!budget) return '';
      const pct = Math.min(100, Math.round(((Number(used) || 0) / budget) * 100));
      const cls = pct >= 100 ? 'over' : (pct >= 80 ? 'warn' : '');
      return `<div class="ai-meter ${cls}" role="img" aria-label="${pct}%"><span style="width:${pct}%"></span></div>`;
    }
    function aiPurposeLabel(key) {
      const k = { chat: 'aiPurposeChat', ocr: 'aiPurposeOcr', suggest: 'aiPurposeSuggest', categorize: 'aiPurposeCategorize' }[key];
      return k ? t(k) : key;
    }
    async function loadAIUsage() {
      const sum = document.getElementById('ai-usage-summary');
      if (!sum) return;
      try {
        const res = await fetch(API + '/admin/ai-usage?days=30');
        const d = await res.json().catch(() => ({}));
        if (!res.ok) { sum.textContent = d.detail || t('aiUsageLoadFailed'); return; }
        renderAIUsage(d);
      } catch (_) { sum.textContent = t('aiUsageLoadFailed'); }
    }
    function renderAIUsage(d) {
      const c = d.company || {};
      const rpm = d.requests_per_minute || {};
      document.getElementById('ai-usage-summary').innerHTML = `
        <div class="ai-usage-stat"><div class="k">${escapeHtml(t('aiUsageCompany24h'))}</div>
          <div class="v" dir="ltr">${escapeHtml(aiTokens(c.tokens_24h))} / ${c.budget ? escapeHtml(aiTokens(c.budget)) : '∞'}</div>${aiMeter(c.tokens_24h, c.budget)}</div>
        <div class="ai-usage-stat"><div class="k">${escapeHtml(tf('aiUsageCostPeriod', { days: d.period_days }))}</div>
          <div class="v" dir="ltr">${escapeHtml(aiUsd(d.cost_usd_period))}</div>
          <div style="font-size:0.74rem;color:var(--text-muted);margin-top:0.2rem;">${escapeHtml(t('aiUsageCostNote'))}</div></div>
        <div class="ai-usage-stat"><div class="k">${escapeHtml(t('aiUsageRate'))}</div>
          <div class="v" dir="ltr">${escapeHtml(String(rpm.user || '∞'))} · ${escapeHtml(String(rpm.company || '∞'))}</div>
          <div style="font-size:0.74rem;color:var(--text-muted);margin-top:0.2rem;">${escapeHtml(t('aiUsageRateHint'))}</div></div>`;
      const inp = document.getElementById('ai-user-budget');
      if (inp) {
        inp.value = d.user_budget_is_default ? '' : String(d.user_budget);
        inp.placeholder = d.user_budget_is_default ? String(d.user_budget) : '';
      }
      const hint = document.getElementById('ai-user-budget-hint');
      if (hint) hint.textContent = d.user_budget_is_default
        ? tf('aiUsageDefaultHint', { n: d.user_budget ? aiTokens(d.user_budget) : '∞' })
        : t('aiUsageZeroHint');
      const rows = d.users || [];
      document.getElementById('ai-usage-users').innerHTML = rows.length ? `
        <table class="results-table" style="font-size:0.84rem;">
          <thead><tr><th>${escapeHtml(t('usersUsername'))}</th><th>${escapeHtml(t('aiUsage24h'))}</th>
            <th>${escapeHtml(tf('aiUsagePeriodTokens', { days: d.period_days }))}</th><th>${escapeHtml(t('aiUsageRequests'))}</th><th>${escapeHtml(t('aiUsageCost'))}</th></tr></thead>
          <tbody>${rows.map(u => `<tr><td>${escapeHtml(u.username)}</td>
            <td dir="ltr">${escapeHtml(aiTokens(u.tokens_24h))}${d.user_budget ? ' / ' + escapeHtml(aiTokens(d.user_budget)) : ''}${aiMeter(u.tokens_24h, d.user_budget)}</td>
            <td dir="ltr">${escapeHtml(aiTokens(u.tokens_period))}</td><td dir="ltr">${escapeHtml(String(u.requests_period))}</td>
            <td dir="ltr">${escapeHtml(aiUsd(u.cost_usd_period))}</td></tr>`).join('')}</tbody>
        </table>` : `<p class="empty-state" style="padding:0.4rem;">${escapeHtml(t('aiUsageNone'))}</p>`;
      const table = (title, list, label) => `
        <div style="flex:1 1 18rem;"><h3 style="font-size:0.9rem;margin:0.4rem 0;">${escapeHtml(title)}</h3>
          <table class="results-table" style="font-size:0.82rem;"><thead><tr><th></th><th>${escapeHtml(t('aiUsageTokens'))}</th><th>${escapeHtml(t('aiUsageCalls'))}</th><th>${escapeHtml(t('aiUsageCost'))}</th></tr></thead>
          <tbody>${(list || []).map(r => `<tr><td>${escapeHtml(label(r.key))}</td><td dir="ltr">${escapeHtml(aiTokens(r.tokens))}</td><td dir="ltr">${escapeHtml(String(r.calls))}</td>
            <td dir="ltr">${r.unpriced_calls && r.unpriced_calls === r.calls ? escapeHtml(t('aiUsageUnpriced')) : escapeHtml(aiUsd(r.cost_usd))}</td></tr>`).join('')}</tbody></table></div>`;
      document.getElementById('ai-usage-breakdown').innerHTML = (d.by_purpose || []).length
        ? table(t('aiUsageByPurpose'), d.by_purpose, aiPurposeLabel) + table(t('aiUsageByModel'), d.by_model, (k) => k)
        : '';
    }
    (function wireAIUsage() {
      const btn = document.getElementById('ai-user-budget-save');
      if (btn) btn.addEventListener('click', async () => {
        const raw = (document.getElementById('ai-user-budget').value || '').trim();
        const v = raw === '' ? null : Number(raw);
        if (v !== null && (!Number.isFinite(v) || v < 0)) { showAlert(t('aiUsageBadNumber'), true); return; }
        btn.disabled = true;
        try {
          const res = await fetch(API + '/admin/ai-usage/user-budget', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_daily_tokens: v === null ? null : Math.round(v) }),
          });
          const d = await res.json().catch(() => ({}));
          if (!res.ok) { showAlert(d.detail || t('aiUsageSaveFailed'), true); return; }
          renderAIUsage(d);
          showAlert(t('aiUsageSaved'));
        } catch (_) { showAlert(t('aiUsageSaveFailed'), true); } finally { btn.disabled = false; }
      });
    })();

    async function loadAILimits() {
      const card = document.getElementById('ai-limits-card');
      if (!card) return;
      try {
        const res = await fetch(API + '/admin/ai-limits');
        const d = await res.json().catch(() => ({}));
        if (!res.ok) { card.style.display = 'none'; return; }
        card.style.display = '';
        document.getElementById('ai-lim-company').value = d.company_daily_tokens;
        document.getElementById('ai-lim-user').value = d.user_daily_tokens;
        document.getElementById('ai-lim-user-rpm').value = d.user_requests_per_minute;
        document.getElementById('ai-lim-company-rpm').value = d.company_requests_per_minute;
        document.getElementById('ai-lim-pricing').value = JSON.stringify(d.pricing || {}, null, 1);
      } catch (_) { card.style.display = 'none'; }
    }
    (function wireAILimits() {
      const btn = document.getElementById('ai-lim-save');
      if (!btn) return;
      btn.addEventListener('click', async () => {
        const status = document.getElementById('ai-lim-status');
        let pricing;
        try { pricing = JSON.parse(document.getElementById('ai-lim-pricing').value || '{}'); }
        catch (_) { status.textContent = t('aiLimitsBadJson'); return; }
        const num = (id) => Math.max(0, Math.round(Number(document.getElementById(id).value) || 0));
        btn.disabled = true;
        try {
          const res = await fetch(API + '/admin/ai-limits', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              company_daily_tokens: num('ai-lim-company'), user_daily_tokens: num('ai-lim-user'),
              user_requests_per_minute: num('ai-lim-user-rpm'), company_requests_per_minute: num('ai-lim-company-rpm'),
              pricing,
            }),
          });
          const d = await res.json().catch(() => ({}));
          if (!res.ok) { status.textContent = (typeof d.detail === 'string' ? d.detail : t('aiUsageSaveFailed')); return; }
          status.textContent = t('aiUsageSaved');
          loadAILimits();
        } catch (_) { status.textContent = t('aiUsageSaveFailed'); } finally { btn.disabled = false; }
      });
    })();
