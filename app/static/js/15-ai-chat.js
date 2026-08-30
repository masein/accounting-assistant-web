
    /* =========================================================================
       AI Accountant — chat panel with structured proposal cards + undo.
       Reads/writes via /ai-accountant/{chat,execute,undo,sessions}.
       ========================================================================= */
    (function aiAccountant() {
      const messagesEl = document.getElementById('ai-acct-messages');
      const inputEl = document.getElementById('ai-acct-input');
      const sendBtn = document.getElementById('ai-acct-send');
      const newSessionBtn = document.getElementById('ai-acct-new-session');
      const statusEl = document.getElementById('ai-acct-status');
      const quickActions = document.getElementById('ai-acct-quick-actions');
      const attachBtn = document.getElementById('ai-acct-attach');
      const fileInput = document.getElementById('ai-acct-file');
      const attachmentsEl = document.getElementById('ai-acct-attachments');
      if (!messagesEl || !sendBtn) return;

      let sessionId = null;
      // ─── ChatGPT-style sessions sidebar ───
      const sessionListEl = document.getElementById('ai-acct-session-list');
      const sessionSearchEl = document.getElementById('ai-acct-session-search');
      const newChatBtn = document.getElementById('ai-acct-new-chat');
      let _sessionsCache = [];
      let _searchTimer = null;

      function _relTime(iso) {
        try {
          const d = new Date(iso);
          const mins = Math.floor((Date.now() - d.getTime()) / 60000);
          if (mins < 1) return t('chatTimeNow');
          if (mins < 60) return mins + ' ' + t('chatTimeMin');
          const hrs = Math.floor(mins / 60);
          if (hrs < 24) return hrs + ' ' + t('chatTimeHour');
          return Math.floor(hrs / 24) + ' ' + t('chatTimeDay');
        } catch (_) { return ''; }
      }
      function _highlight(text, q) {
        const safe = escapeHtml(text);
        if (!q) return safe;
        const idx = safe.toLowerCase().indexOf(escapeHtml(q).toLowerCase());
        if (idx < 0) return safe;
        return safe.slice(0, idx) + '<mark>' + safe.slice(idx, idx + q.length) + '</mark>' + safe.slice(idx + q.length);
      }
      async function loadSessions(q) {
        if (!sessionListEl) return;
        try {
          const url = API + '/ai-accountant/sessions' + (q ? ('?q=' + encodeURIComponent(q)) : '');
          const res = await fetch(url);
          if (!res.ok) return;
          _sessionsCache = await res.json().catch(() => []);
          renderSessionList(q || '');
        } catch (_) { /* sidebar is best-effort */ }
      }
      function renderSessionList(q) {
        if (!sessionListEl) return;
        sessionListEl.innerHTML = '';
        if (!_sessionsCache.length) {
          sessionListEl.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem;padding:0.4rem;">' + escapeHtml(t('chatSessionsEmpty')) + '</div>';
          return;
        }
        _sessionsCache.forEach((sess) => {
          const item = document.createElement('div');
          const active = sess.id === sessionId;
          item.style.cssText = 'display:flex;flex-direction:column;gap:0.1rem;padding:0.4rem 0.5rem;border-radius:6px;cursor:pointer;'
            + (active ? 'background:var(--primary,#0f766e);color:#fff;' : 'background:transparent;');
          const row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:center;gap:0.3rem;';
          const title = document.createElement('span');
          title.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.85rem;';
          title.innerHTML = _highlight(sess.title || t('chatUntitled'), q);
          row.appendChild(title);
          const ren = document.createElement('button');
          ren.type = 'button'; ren.textContent = '✎'; ren.title = t('chatRename');
          ren.style.cssText = 'border:none;background:none;cursor:pointer;font-size:0.8rem;color:inherit;opacity:0.7;padding:0;';
          ren.addEventListener('click', (e) => { e.stopPropagation(); startInlineRename(item, title, sess); });
          row.appendChild(ren);
          const del = document.createElement('button');
          del.type = 'button'; del.textContent = '🗑'; del.title = t('chatDelete');
          del.style.cssText = ren.style.cssText;
          del.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (!window.confirm(t('chatDeleteConfirm'))) return;
            await fetch(API + '/ai-accountant/sessions/' + encodeURIComponent(sess.id), { method: 'DELETE' });
            if (sess.id === sessionId) { sessionId = null; messagesEl.innerHTML = ''; }
            loadSessions(sessionSearchEl ? sessionSearchEl.value.trim() : '');
          });
          row.appendChild(del);
          item.appendChild(row);
          const meta = document.createElement('div');
          meta.style.cssText = 'font-size:0.7rem;opacity:0.75;display:flex;gap:0.4rem;';
          meta.textContent = _relTime(sess.updated_at);
          item.appendChild(meta);
          if (sess.match_snippet) {
            const snip = document.createElement('div');
            snip.style.cssText = 'font-size:0.72rem;opacity:0.85;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
            snip.innerHTML = _highlight(sess.match_snippet, q);
            item.appendChild(snip);
          }
          item.addEventListener('click', () => openSession(sess.id));
          sessionListEl.appendChild(item);
        });
      }
      function startInlineRename(item, titleEl, sess) {
        const input = document.createElement('input');
        input.type = 'text';
        input.value = sess.title || '';
        input.style.cssText = 'flex:1;font-size:0.85rem;min-width:0;';
        titleEl.replaceWith(input);
        input.focus();
        input.select();
        const commit = async () => {
          const val = input.value.trim();
          if (val && val !== sess.title) {
            await fetch(API + '/ai-accountant/sessions/' + encodeURIComponent(sess.id), {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ title: val }),
            });
          }
          loadSessions(sessionSearchEl ? sessionSearchEl.value.trim() : '');
        };
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
          if (e.key === 'Escape') { input.removeEventListener('blur', commit); loadSessions(''); }
        });
        input.addEventListener('blur', commit);
      }
      function _renderStoredMessages(msgs) {
        messagesEl.innerHTML = '';
        msgs.forEach((m) => {
          const c = m.content || {};
          const text = (typeof c.text === 'string' && c.text.trim())
            ? c.text
            : (typeof c.content === 'string' ? c.content : null);  // legacy shape
          if (!text) return;  // skip tool turns / empty tool-call turns
          if (m.role === 'user') appendBubble('user', text);
          else if (m.role === 'assistant') appendBubble('assistant', text);
        });
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
      async function openSession(id) {
        try {
          const mres = await fetch(API + '/ai-accountant/sessions/' + encodeURIComponent(id) + '/messages');
          if (!mres.ok) return;
          const msgs = await mres.json().catch(() => []);
          sessionId = id;
          pendingAttachments = [];
          renderPendingAttachments();
          _renderStoredMessages(msgs);
          renderSessionList(sessionSearchEl ? sessionSearchEl.value.trim() : '');
        } catch (_) { /* keep current view */ }
      }
      async function startNewChat() {
        try {
          const res = await fetch(API + '/ai-accountant/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
          });
          const data = await res.json().catch(() => null);
          sessionId = (res.ok && data && data.id) ? data.id : null;
        } catch (_) { sessionId = null; }
        messagesEl.innerHTML = '';
        pendingAttachments = [];
        renderPendingAttachments();
        statusEl.textContent = t('aiChatNewStarted');
        loadSessions('');
      }
      if (newChatBtn) newChatBtn.addEventListener('click', startNewChat);
      if (sessionSearchEl) {
        sessionSearchEl.addEventListener('input', () => {
          clearTimeout(_searchTimer);
          _searchTimer = setTimeout(() => loadSessions(sessionSearchEl.value.trim()), 300);
        });
      }
      // Restore the newest chat session after a refresh so the conversation
      // (and its context) isn't lost — the backend has kept it all along.
      (async function restoreLatest() {
        await loadSessions('');
        if (_sessionsCache.length) await openSession(_sessionsCache[0].id);
      })();
      const undoTimers = {};  // audit_log_id → timeout handle
      // Quick one-click undo countdown; matches UNDO_WINDOW in execute_service
      // (AI-7). After it elapses the button becomes a persistent reverse.
      const UNDO_WINDOW_SECONDS = 120;
      // Invoice/receipt files uploaded for the NEXT chat turn. Each entry is
      // the AttachmentRead returned by POST /transactions/attachments.
      let pendingAttachments = [];
      const MAX_ATTACH_BYTES = 8 * 1024 * 1024;
      const ALLOWED_ATTACH_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf',
        'text/csv', 'application/csv', 'text/tab-separated-values',
        'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'];
      const ALLOWED_ATTACH_EXTENSIONS = ['.csv', '.tsv', '.xls', '.xlsx', '.pdf', '.jpg', '.jpeg', '.png', '.webp'];
      function _attachTypeOk(file) {
        if (ALLOWED_ATTACH_TYPES.includes(file.type)) return true;
        const name = (file.name || '').toLowerCase();
        return ALLOWED_ATTACH_EXTENSIONS.some((ext) => name.endsWith(ext));
      }
      function _fmtSize(bytes) {
        if (!bytes && bytes !== 0) return '';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
      }

      function renderPendingAttachments() {
        if (!attachmentsEl) return;
        attachmentsEl.innerHTML = '';
        if (!pendingAttachments.length) { attachmentsEl.style.display = 'none'; return; }
        attachmentsEl.style.display = 'flex';
        pendingAttachments.forEach((att) => {
          const chip = document.createElement('span');
          chip.style.cssText = 'display:inline-flex; align-items:center; gap:0.35rem; padding:0.2rem 0.55rem; background:#eef2ff; border:1px solid #c7d2fe; border-radius:14px; font-size:0.8rem; max-width:240px;';
          const isImg = (att.content_type || '').startsWith('image/');
          const isSheet = /csv|excel|spreadsheet|tab-separated/.test(att.content_type || '');
          const name = document.createElement('span');
          name.style.cssText = 'overflow:hidden; text-overflow:ellipsis; white-space:nowrap;';
          const size = _fmtSize(att.size_bytes);
          name.textContent = (isImg ? '🖼 ' : (isSheet ? '📊 ' : '📄 ')) + (att.file_name || 'document') + (size ? ' · ' + size : '');
          chip.appendChild(name);
          const rm = document.createElement('button');
          rm.type = 'button';
          rm.textContent = '✕';
          rm.title = t('aiChatRemoveAttachment');
          rm.setAttribute('aria-label', t('aiChatRemoveAttachment'));
          rm.style.cssText = 'border:none; background:none; cursor:pointer; color:var(--text-muted); font-size:0.9rem; line-height:1; padding:0;';
          rm.addEventListener('click', () => {
            pendingAttachments = pendingAttachments.filter((a) => a.id !== att.id);
            renderPendingAttachments();
          });
          chip.appendChild(rm);
          attachmentsEl.appendChild(chip);
        });
      }

      async function handleAttachFile(file) {
        if (!file) return;
        if (!_attachTypeOk(file)) {
          showAlert(t('aiChatAttachBadType'), true);
          return;
        }
        if (file.size > MAX_ATTACH_BYTES) {
          showAlert(t('aiChatAttachTooLarge'), true);
          return;
        }
        if (attachBtn) attachBtn.disabled = true;
        try {
          const fd = new FormData();
          fd.append('file', file);
          const res = await fetch(API + '/transactions/attachments', { method: 'POST', body: fd });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { showAlert(data.detail || t('aiChatAttachFailed'), true); return; }
          pendingAttachments.push(data);
          renderPendingAttachments();
        } catch (err) {
          showAlert(t('aiChatAttachFailed') + ' ' + err.message, true);
        } finally {
          if (attachBtn) attachBtn.disabled = false;
        }
      }

      // Friendly, professional "thinking" captions shown while the assistant
      // works. Rotated every couple of seconds under the typing indicator.
      // Localised so the caption matches the active interface language.
      const CHAT_THINKING_PHRASES = {
        en: ['Working on it…', 'Looking that up…', 'Reviewing the ledger…', 'Checking the figures…', 'Almost there…'],
        fa: ['در حال انجام…', 'در حال جست‌وجو…', 'بررسی دفتر کل…', 'بررسی ارقام…', 'تقریباً آماده است…'],
        es: ['Trabajando en ello…', 'Consultando los datos…', 'Revisando el libro mayor…', 'Comprobando las cifras…', 'Casi listo…'],
        ar: ['جارٍ العمل على ذلك…', 'جارٍ البحث…', 'مراجعة دفتر الأستاذ…', 'التحقّق من الأرقام…', 'اقتربنا من الانتهاء…'],
      };
      let _thinkingTimer = null;

      // Show an assistant-side typing bubble: three bouncing dots plus a
      // professional caption that rotates next to them, right in the message
      // stream (not in the bottom status line). Returns the wrapper so the
      // caller can remove it once the reply arrives.
      function showTypingIndicator() {
        const lang = (typeof currentLanguage !== 'undefined' && currentLanguage) || 'en';
        const phrases = CHAT_THINKING_PHRASES[lang] || CHAT_THINKING_PHRASES.en;
        const rtl = (typeof RTL_LANGUAGES !== 'undefined' && RTL_LANGUAGES.has && RTL_LANGUAGES.has(lang));

        const wrap = document.createElement('div');
        wrap.style.cssText = (rtl ? 'text-align:right;' : 'text-align:left;') + ' margin:0.4rem 0;';
        wrap.dataset.typing = '1';

        const bubble = document.createElement('div');
        bubble.className = 'typing-bubble';
        bubble.setAttribute('role', 'status');
        bubble.setAttribute('dir', rtl ? 'rtl' : 'ltr');
        bubble.setAttribute('aria-label', phrases[0]);

        const dots = document.createElement('span');
        dots.style.cssText = 'display:inline-flex; gap:4px; align-items:center;';
        dots.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';

        const caption = document.createElement('span');
        caption.style.cssText = 'font-size:0.85rem; color:var(--text-muted); white-space:nowrap;';
        caption.textContent = phrases[0];

        bubble.appendChild(dots);
        bubble.appendChild(caption);
        wrap.appendChild(bubble);
        messagesEl.appendChild(wrap);
        messagesEl.scrollTop = messagesEl.scrollHeight;

        // Clear any stale "N turn(s)" summary from the previous reply.
        if (statusEl) statusEl.textContent = '';

        let i = 0;
        _thinkingTimer = setInterval(() => {
          i = (i + 1) % phrases.length;
          caption.textContent = phrases[i];
          bubble.setAttribute('aria-label', phrases[i]);
        }, 2200);
        return wrap;
      }

      function hideTypingIndicator(wrap) {
        if (_thinkingTimer) { clearInterval(_thinkingTimer); _thinkingTimer = null; }
        if (wrap && wrap.parentNode) wrap.parentNode.removeChild(wrap);
      }

      // Minimal markdown → HTML. Escapes first (XSS-safe), then handles
      // the subset LLMs emit in chat: # / ## / ### headings, **bold**,
      // *italic*, `inline code`, bulleted (- / * / •) and numbered
      // (1. 2. 3.) lists, plus blank-line paragraphs.
      function _renderChatMarkdown(text) {
        const esc = String(text || '').replace(/[&<>"']/g, c => ({
          '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
        }[c]));
        const lines = esc.split(/\r?\n/);
        const out = [];
        let listType = null;  // 'ul' | 'ol' | null
        const closeList = () => { if (listType) { out.push(`</${listType}>`); listType = null; } };
        const inline = (s) =>
          s
            // `code` first so other patterns don't eat it
            .replace(/`([^`]+?)`/g, '<code style="background:#f3f4f6;padding:1px 4px;border-radius:3px;font-size:0.92em;">$1</code>')
            // bold (process before italic so ** doesn't read as nested *)
            .replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>')
            .replace(/__([^_]+?)__/g, '<strong>$1</strong>')
            // italic
            .replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>')
            .replace(/(^|[^_])_([^_\n]+?)_(?!_)/g, '$1<em>$2</em>');
        for (let i = 0; i < lines.length; i++) {
          const raw = lines[i];
          const line = raw.trimEnd();
          if (!line.trim()) { closeList(); out.push(''); continue; }
          let m;
          if ((m = /^(#{1,3})\s+(.*)$/.exec(line))) {
            closeList();
            const level = m[1].length + 2;  // # → h3, ## → h4, ### → h5
            out.push(`<h${level} style="margin:0.4rem 0 0.2rem;font-size:${1.1 - 0.05*(level-3)}em;">${inline(m[2])}</h${level}>`);
            continue;
          }
          if ((m = /^\s*[-*•]\s+(.*)$/.exec(line))) {
            if (listType !== 'ul') { closeList(); out.push('<ul style="margin:0.2rem 0;padding-inline-start:1.4em;">'); listType = 'ul'; }
            out.push(`<li>${inline(m[1])}</li>`);
            continue;
          }
          if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) {
            if (listType !== 'ol') { closeList(); out.push('<ol style="margin:0.2rem 0;padding-inline-start:1.6em;">'); listType = 'ol'; }
            out.push(`<li>${inline(m[1])}</li>`);
            continue;
          }
          closeList();
          out.push(inline(line));
        }
        closeList();
        return out.join('\n').replace(/\n{2,}/g, '<br><br>').replace(/\n/g, '<br>');
      }

      function appendBubble(role, text) {
        const wrap = document.createElement('div');
        wrap.style.cssText = (role === 'user'
          ? 'text-align:right; margin:0.4rem 0;'
          : 'text-align:left; margin:0.4rem 0;');
        const bubble = document.createElement('div');
        const isAssistant = role !== 'user';
        bubble.style.cssText = (role === 'user'
          ? 'display:inline-block; max-width:78%; background:#0f766e; color:#fff; padding:0.5rem 0.75rem; border-radius:14px 14px 4px 14px; text-align:left; white-space:pre-wrap;'
          // Assistant: drop white-space:pre-wrap so the markdown renderer's
          // explicit <br> and block elements lay out correctly.
          : 'display:inline-block; max-width:78%; background:#fff; color:#0f172a; padding:0.5rem 0.75rem; border-radius:14px 14px 14px 4px; border:1px solid var(--border); line-height:1.45;');
        if (isAssistant) {
          bubble.innerHTML = _renderChatMarkdown(text);
        } else {
          bubble.textContent = text;
        }
        wrap.appendChild(bubble);
        messagesEl.appendChild(wrap);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        return wrap;
      }

      // ─── Smart-intake cards (spreadsheet drops) ───
      function appendIntakeCard(intake) {
        const card = document.createElement('div');
        card.style.cssText = 'margin:0.5rem 0; border:1px solid var(--primary,#0f766e); border-inline-start:4px solid var(--primary,#0f766e); border-radius:8px; padding:0.7rem; background:#f0fdfa; font-size:0.88rem;';
        const fmt = (n) => (typeof n === 'number' ? n.toLocaleString() : n);
        let html = '';
        if (intake.kind === 'chart_export') {
          const sm = intake.summary || {};
          const tiers = sm.tiers || {};
          const split = sm.tafsili_split || {};
          const op = sm.opening || {};
          const tierLabels = { group: t('migrationTierGroups'), kol: t('migrationTierKol'), moein: t('migrationTierMoein'), tafsili: t('migrationTierTafsili') };
          html += '<div style="font-weight:600;margin-bottom:0.3rem;">' + escapeHtml(t('chatIntakeChartTitle')) + '</div>';
          html += '<div style="display:flex;gap:1rem;flex-wrap:wrap;">'
            + Object.keys(tiers).map((k) => '<span><strong>' + fmt(tiers[k]) + '</strong> ' + escapeHtml(tierLabels[k] || k) + '</span>').join('')
            + '<span><strong>' + fmt(split.bank_accounts || 0) + '</strong> ' + escapeHtml(t('migrationBankAccounts')) + '</span>'
            + '<span><strong>' + fmt(split.counterparties || 0) + '</strong> ' + escapeHtml(t('migrationCounterparties')) + escapeHtml(_cpTypesLabel(sm.counterparty_types)) + '</span>'
            + '</div>';
          html += '<div style="margin-top:0.3rem;">' + escapeHtml(t('migrationOpeningTotals')) + ': <strong>' + fmt(op.total_debit || 0) + '</strong> / <strong>' + fmt(op.total_credit || 0) + '</strong> — '
            + (op.balanced ? '<span style="color:var(--success,#28a745);">' + escapeHtml(t('migrationBalancedYes')) + '</span>'
                           : '<span style="color:var(--danger,#dc3545);">' + escapeHtml(t('migrationBalancedNo')) + '</span>')
            + '</div>';
          if ((intake.missing_tiers || []).length) {
            const tl = intake.missing_tiers.map((k) => tierLabels[k] || k).join('، ');
            html += '<div style="color:var(--text-muted);margin-top:0.25rem;">' + escapeHtml(t('chatIntakeMissingTiers')) + ' ' + escapeHtml(tl) + '</div>';
          }
          if (intake.already_applied) {
            html += '<div style="color:var(--text-muted);margin-top:0.25rem;">' + escapeHtml(t('chatIntakeAlreadyApplied')) + '</div>';
          }
          const warns = ((sm.validation || {}).warnings || []);
          if (warns.length) {
            html += '<div style="color:var(--text-muted);margin-top:0.25rem;font-size:0.8rem;">' + warns.map(escapeHtml).join('<br>') + '</div>';
          }
        } else if (intake.kind === 'transactions') {
          html += '<div style="font-weight:600;margin-bottom:0.3rem;">' + escapeHtml(t('chatIntakeTxnTitle')) + '</div>';
          html += '<div>' + fmt(intake.total_rows) + ' ' + escapeHtml(t('chatIntakeRows')) + ' · '
            + fmt(intake.total_vouchers) + ' ' + escapeHtml(t('chatIntakeVouchers'))
            + (intake.unmapped_accounts ? ' · <span style="color:var(--danger,#dc3545);">' + fmt(intake.unmapped_accounts) + ' ' + escapeHtml(t('chatIntakeUnmapped')) + '</span>' : '')
            + '</div>';
          const errs = intake.errors || [];
          if (errs.length) {
            html += '<div style="color:var(--danger,#dc3545);margin-top:0.25rem;font-size:0.8rem;">' + errs.slice(0, 5).map(escapeHtml).join('<br>') + '</div>';
          }
        }
        card.innerHTML = html;
        const btnRow = document.createElement('div');
        btnRow.style.cssText = 'margin-top:0.5rem;display:flex;gap:0.5rem;';
        const confirmBtn = document.createElement('button');
        confirmBtn.type = 'button';
        confirmBtn.className = 'btn btn-primary btn-sm';
        confirmBtn.textContent = t('chatIntakeConfirm');
        confirmBtn.addEventListener('click', async () => {
          confirmBtn.disabled = true;
          try {
            let res, data;
            if (intake.kind === 'chart_export') {
              res = await fetch(API + '/migration/import/confirm', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: intake.token, opening_date: intake.default_opening_date || undefined }),
              });
              data = await res.json().catch(() => ({}));
              if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : ((data.detail || {}).message || 'failed'));
              const r = data.result || {};
              const chart = r.chart || {};
              const created = ['group', 'kol', 'moein'].reduce((a, k) => a + ((chart[k] || {}).created || 0), 0);
              const ents = r.entities || {};
              appendBubble('assistant', t('chatIntakeApplied') + ' — ' + created + ' ' + t('migrationAccountsCreated')
                + ', ' + ((ents.banks_created || 0) + (ents.counterparties_created || 0)) + ' ' + t('migrationEntitiesCreated')
                + '. ' + t('migrationJournalPosted') + ': ' + ((r.opening_journal || {}).opening_date || ''));
            } else {
              res = await fetch(API + '/transactions/excel-import/confirm', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  file_token: intake.file_token,
                  jalali_year: intake.jalali_year,
                  account_mappings: intake.account_mappings || [],
                  amount_multiplier: 1,
                  currency: 'IRR',
                }),
              });
              data = await res.json().catch(() => ({}));
              if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'failed');
              appendBubble('assistant', t('chatIntakeApplied') + ' — ' + (data.imported || 0) + ' ' + t('chatIntakeVouchers')
                + ((data.errors || []).length ? ' · ' + data.errors.slice(0, 3).join(' | ') : ''));
            }
            confirmBtn.textContent = '✓';
          } catch (e) {
            appendBubble('assistant', '[error] ' + e.message);
            confirmBtn.disabled = false;
          }
        });
        btnRow.appendChild(confirmBtn);
        card.appendChild(btnRow);
        messagesEl.appendChild(card);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      function appendProposalCard(proposal) {
        const card = document.createElement('div');
        card.style.cssText = 'margin:0.6rem 0; padding:0.75rem 1rem; background:#fff; border:1px solid var(--border); border-left:4px solid #0f766e; border-radius:8px;';
        card.dataset.token = proposal.confirmation_token;

        const title = document.createElement('div');
        title.style.cssText = 'font-weight:600; margin-bottom:0.3rem;';
        title.textContent = t('aiChatProposedAction');
        card.appendChild(title);

        const summary = document.createElement('pre');
        summary.style.cssText = 'font-size:0.82rem; line-height:1.45; white-space:pre-wrap; font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin:0 0 0.5rem 0;';
        summary.textContent = proposal.summary || '';
        card.appendChild(summary);

        // New entities to be created on Confirm (localized, alongside the entry).
        if (Array.isArray(proposal.new_entities) && proposal.new_entities.length) {
          const box = document.createElement('div');
          box.style.cssText = 'font-size:0.8rem; background:#f0fdfa; border:1px solid #99f6e4; border-radius:6px; padding:0.4rem 0.6rem; margin:0 0 0.5rem 0;';
          proposal.new_entities.forEach(ne => {
            const line = document.createElement('div');
            const typeLabel = t('entType_' + ne.type) || ne.type;
            if (ne.type === 'bank' && ne.account_code) {
              const verb = ne.account_existing ? t('aiWillUseAccount') : t('aiWillCreateAccount');
              line.textContent = '➕ ' + tf('aiWillCreateBank', { name: ne.name, verb, code: ne.account_code });
            } else {
              line.textContent = '➕ ' + tf('aiWillCreateEntity', { type: typeLabel, name: ne.name });
            }
            box.appendChild(line);
          });
          card.appendChild(box);
        }

        const buttons = document.createElement('div');
        buttons.style.cssText = 'display:flex; gap:0.5rem; flex-wrap:wrap;';

        const confirmBtn = document.createElement('button');
        confirmBtn.type = 'button';
        confirmBtn.className = 'btn btn-primary btn-sm';
        confirmBtn.textContent = t('btnConfirm');
        confirmBtn.addEventListener('click', () => executeProposal(card, proposal.confirmation_token));

        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'btn btn-secondary btn-sm';
        cancelBtn.textContent = t('btnCancel');
        cancelBtn.addEventListener('click', () => {
          card.style.opacity = '0.5';
          confirmBtn.disabled = true;
          cancelBtn.disabled = true;
          const cancelled = document.createElement('div');
          cancelled.style.cssText = 'font-size:0.8rem;color:var(--text-muted);margin-top:0.4rem;';
          cancelled.textContent = t('aiChatCancelled');
          card.appendChild(cancelled);
        });

        buttons.appendChild(confirmBtn);
        buttons.appendChild(cancelBtn);
        card.appendChild(buttons);
        messagesEl.appendChild(card);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      async function executeProposal(cardEl, token) {
        const confirmBtn = cardEl.querySelector('button.btn-primary');
        const cancelBtn = cardEl.querySelector('button.btn-secondary');
        const confirmLabel = confirmBtn ? confirmBtn.textContent : '';
        if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = t('aiConfirming'); }
        if (cancelBtn) cancelBtn.disabled = true;
        // A hung server must never leave the button dead with no feedback:
        // abort after 30s and surface a clear error.
        const aborter = new AbortController();
        const killer = setTimeout(() => aborter.abort(), 30000);
        try {
          let r;
          try {
            r = await fetch(API + '/ai-accountant/execute', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ confirmation_token: token }),
              signal: aborter.signal,
            });
          } catch (netErr) {
            throw new Error(aborter.signal.aborted ? t('aiConfirmTimeout') : (netErr.message || 'Network error'));
          } finally {
            clearTimeout(killer);
          }
          const data = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(data.detail || ('Execute failed (HTTP ' + r.status + ')'));
          if (confirmBtn) confirmBtn.textContent = confirmLabel;
          const receipt = document.createElement('div');
          receipt.style.cssText = 'margin-top:0.5rem; padding:0.4rem 0.6rem; background:#f0fdf4; border:1px solid #86efac; border-radius:6px; font-size:0.82rem;';
          receipt.innerHTML = '<strong>' + escapeHtml(t('aiUndoRecorded')) + '</strong> ' +
            (data.transaction_id ? `Transaction <code>${escapeHtml(data.transaction_id.slice(0,8))}…</code>` : '') +
            (data.idempotent ? ' <em>' + escapeHtml(t('aiUndoAlreadyCommitted')) + '</em>' : '');
          cardEl.appendChild(receipt);

          if (!data.idempotent) {
            // Quick one-click undo with a countdown. When it elapses the
            // button becomes a persistent "Reverse entry" action (AI-7) so
            // the user always has recourse, never just manual deletion.
            const undoBtn = document.createElement('button');
            undoBtn.type = 'button';
            undoBtn.className = 'btn btn-secondary btn-sm';
            undoBtn.style.marginTop = '0.4rem';
            undoBtn.textContent = tf('aiUndoBtn', { s: UNDO_WINDOW_SECONDS });
            cardEl.appendChild(undoBtn);
            let remaining = UNDO_WINDOW_SECONDS;
            let reverting = false;
            const tick = setInterval(() => {
              remaining -= 1;
              if (remaining <= 0) {
                clearInterval(tick);
                // Switch to the persistent reverse action.
                undoBtn.textContent = t('aiReverseBtn');
              } else {
                undoBtn.textContent = tf('aiUndoBtn', { s: remaining });
              }
            }, 1000);
            undoTimers[data.audit_log_id] = tick;
            undoBtn.addEventListener('click', () => {
              if (reverting) return;
              reverting = true;
              // Within the window → quick undo; after → persistent reverse.
              const persistent = remaining <= 0;
              reverseEntry(cardEl, data.audit_log_id, undoBtn, tick, persistent)
                .finally(() => { reverting = false; });
            });
          }
        } catch (e) {
          const err = document.createElement('div');
          err.style.cssText = 'margin-top:0.4rem; color:#b91c1c; font-size:0.82rem;';
          err.textContent = 'Error: ' + e.message;
          cardEl.appendChild(err);
          showAlert(e.message, true);
          if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = confirmLabel; }
          if (cancelBtn) cancelBtn.disabled = false;
        }
      }

      async function reverseEntry(cardEl, auditLogId, btn, tick, persistent) {
        btn.disabled = true;
        if (tick) clearInterval(tick);
        btn.textContent = t('aiReverseReverting');
        const endpoint = persistent ? '/ai-accountant/reverse' : '/ai-accountant/undo';
        try {
          const r = await fetch(API + endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ audit_log_id: auditLogId }),
          });
          const data = await r.json();
          if (!r.ok) throw new Error(data.detail || t('aiReverseFailed'));
          btn.textContent = t('aiUndoReversed');
          const note = document.createElement('div');
          note.style.cssText = 'margin-top:0.3rem; color:var(--text-muted); font-size:0.78rem;';
          note.textContent = tf('aiUndoReversalNote', { id: (data.reversal_transaction_id || '').slice(0, 8) });
          cardEl.appendChild(note);
        } catch (e) {
          btn.disabled = false;
          btn.textContent = persistent ? t('aiReverseBtn') : tf('aiUndoBtn', { s: 0 });
          const note = document.createElement('div');
          note.style.cssText = 'margin-top:0.3rem; color:#b91c1c; font-size:0.78rem;';
          note.textContent = t('aiReverseFailed') + ': ' + e.message;
          cardEl.appendChild(note);
        }
      }

      // Render the user's turn, showing any attached document names as
      // chips beneath the text so the upload is visible in the transcript.
      function appendUserTurn(text, attachments) {
        const wrap = appendBubble('user', text || '');
        if (attachments && attachments.length) {
          const bubble = wrap.querySelector('div');
          if (bubble) {
            const strip = document.createElement('div');
            strip.style.cssText = 'margin-top:0.35rem; display:flex; flex-wrap:wrap; gap:0.3rem;';
            attachments.forEach((att) => {
              const tag = document.createElement('span');
              tag.style.cssText = 'display:inline-flex; align-items:center; gap:0.25rem; padding:0.1rem 0.45rem; background:rgba(255,255,255,0.2); border-radius:10px; font-size:0.75rem;';
              const isImg = (att.content_type || '').startsWith('image/');
              tag.textContent = (isImg ? '🖼 ' : '📄 ') + (att.file_name || 'document');
              strip.appendChild(tag);
            });
            bubble.appendChild(strip);
          }
        }
      }

      async function sendMessage(msg) {
        const text = (msg || '').trim();
        const attachments = pendingAttachments.slice();
        // A turn needs either text or at least one attached document.
        if (!text && !attachments.length) return;
        appendUserTurn(text, attachments);
        inputEl.value = '';
        pendingAttachments = [];
        renderPendingAttachments();
        sendBtn.disabled = true;
        const typingEl = showTypingIndicator();
        try {
          const r = await fetch(API + '/ai-accountant/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              message: text,
              session_id: sessionId,
              attachment_ids: attachments.map((a) => a.id),
            }),
          });
          const data = await readJsonSafe(r);
          hideTypingIndicator(typingEl);
          if (!r.ok || data._nonJson) throw new Error((data && data.detail) || 'Chat failed');
          sessionId = data.session_id;
          if (data.text) appendBubble('assistant', data.text);
          for (const proposal of (data.proposals || [])) {
            appendProposalCard(proposal);
          }
          if (data.intake) appendIntakeCard(data.intake);
          statusEl.textContent = tf('aiChatStatusCounter', { turns: data.turns, calls: (data.tool_calls || []).length });
          loadSessions(sessionSearchEl ? sessionSearchEl.value.trim() : '');
        } catch (e) {
          hideTypingIndicator(typingEl);
          appendBubble('assistant', '[error] ' + e.message);
          statusEl.textContent = '';
        } finally {
          sendBtn.disabled = false;
          inputEl.focus();
        }
      }

      sendBtn.addEventListener('click', () => sendMessage(inputEl.value));
      inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendMessage(inputEl.value);
        }
      });
      if (attachBtn && fileInput) {
        attachBtn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', async () => {
          const files = Array.from(fileInput.files || []);
          for (const f of files) await handleAttachFile(f);
          fileInput.value = '';  // allow re-selecting the same files
        });
      }
      // ─── Drag & drop: upload the File bytes, never paste the path ───
      (function wireDropZone() {
        const panel = messagesEl ? messagesEl.closest('.chat-panel') : null;
        const overlay = document.getElementById('ai-acct-dropzone');
        const card = document.querySelector('.card[data-page="ai-accountant"]');
        if (!panel || !card) return;
        let dragDepth = 0;
        function showOverlay(on) {
          if (overlay) overlay.style.display = on ? 'flex' : 'none';
        }
        card.addEventListener('dragenter', (e) => {
          if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes('Files')) return;
          e.preventDefault();
          dragDepth++;
          showOverlay(true);
        });
        card.addEventListener('dragover', (e) => {
          e.preventDefault();  // REQUIRED: without this the browser opens/pastes the file path
          if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
        });
        card.addEventListener('dragleave', (e) => {
          e.preventDefault();
          dragDepth = Math.max(0, dragDepth - 1);
          if (!dragDepth) showOverlay(false);
        });
        card.addEventListener('drop', async (e) => {
          e.preventDefault();
          e.stopPropagation();
          dragDepth = 0;
          showOverlay(false);
          const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
          for (const f of files) await handleAttachFile(f);
        });
        // Anywhere else on the page a stray drop must not navigate away /
        // paste a filesystem path.
        window.addEventListener('dragover', (e) => e.preventDefault());
        window.addEventListener('drop', (e) => e.preventDefault());
        // Cmd/Ctrl-V of a copied file attaches it too.
        if (inputEl) {
          inputEl.addEventListener('paste', async (e) => {
            const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
            if (!files.length) return;
            e.preventDefault();
            for (const f of files) await handleAttachFile(f);
          });
        }
      })();
      if (newSessionBtn) {
        newSessionBtn.addEventListener('click', startNewChat);
      }
      if (quickActions) {
        quickActions.querySelectorAll('.chip').forEach((chip) => {
          chip.addEventListener('click', () => sendMessage(chip.dataset.msg || chip.textContent));
        });
      }
    })();

