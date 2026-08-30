
    function syncAIFieldsByProvider() {
      const p = (aiProviderSelect && aiProviderSelect.value) || 'lmstudio';
      const custom = p === 'custom';
      aiBaseInput.disabled = !custom;
      aiBaseInput.placeholder = custom ? 'https://api.example.com/openai/v1' : (p === 'metis' ? 'Auto: https://api.metisai.ir/openai/v1' : 'Auto: LM Studio URL');
    }

    async function loadAIConfig() {
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
                <td>
                  <button type="button" class="btn btn-secondary btn-sm user-pw-btn" data-id="${escapeHtml(u.id)}">${escapeHtml(t('usersResetPassword'))}</button>
                  <button type="button" class="btn btn-secondary btn-sm user-active-btn" data-id="${escapeHtml(u.id)}" data-active="${u.is_active ? '1' : '0'}">${u.is_active ? escapeHtml(t('usersDeactivate')) : escapeHtml(t('usersActivate'))}</button>
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
        // double quotes that would otherwise break an inline onerror="" handler
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
        const navCo = document.getElementById('nav-companies');
        if (navCo) navCo.style.display = isSuperadmin ? '' : 'none';
        // Role-aware nav: hide what this role can't use (server still enforces).
        applyRoleAccess();
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
          loadAIConfig(); loadAnthropicConfig();
        }
      } catch (_) {
        applyLanguage(localStorage.getItem('aa_ui_language') || 'en', false);
      }
    }

    async function loadUsers() {
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
