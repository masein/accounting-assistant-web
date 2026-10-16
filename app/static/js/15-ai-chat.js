
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
      const titleEl = document.getElementById('ai-acct-title');
      const scrollBtn = document.getElementById('ai-acct-scroll-bottom');
      if (!messagesEl || !sendBtn) return;

      const _lang = () => (typeof currentLanguage !== 'undefined' && currentLanguage) || 'en';
      function _fmtTime(iso) {
        try {
          const d = iso ? new Date(iso) : new Date();
          if (Number.isNaN(d.getTime())) return '';
          // Arabic in Latin digits, like every figure (#336); Persian in its own
          const loc = { fa: 'fa-IR', ar: 'ar-u-nu-latn', es: 'es-ES' }[_lang()] || 'en-GB';
          return d.toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit' });
        } catch (_) { return ''; }
      }
      // Keep the view pinned to the newest message unless the reader has
      // scrolled up to re-read something; then offer a jump-back button.
      function _nearBottom() { return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 120; }
      function scrollToBottom(force) {
        if (force || _nearBottom()) messagesEl.scrollTop = messagesEl.scrollHeight;
        if (scrollBtn) scrollBtn.hidden = _nearBottom();
      }
      messagesEl.addEventListener('scroll', () => { if (scrollBtn) scrollBtn.hidden = _nearBottom(); });
      if (scrollBtn) scrollBtn.addEventListener('click', () => scrollToBottom(true));
      function setChatTitle(text) { if (titleEl) titleEl.textContent = text || ''; }
      function renderEmptyState() {
        if (messagesEl.children.length) return;
        const box = document.createElement('div');
        box.className = 'ai-empty';
        box.innerHTML = '<div class="ai-empty-icon"><svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>'
          + '<h3>' + escapeHtml(t('aiChatEmptyTitle')) + '</h3><p>' + escapeHtml(t('aiChatEmptyBody')) + '</p>';
        const list = document.createElement('div');
        list.className = 'ai-examples';
        ['aiChatExample1', 'aiChatExample2', 'aiChatExample3'].forEach((k) => {
          const b = document.createElement('button');
          b.type = 'button'; b.className = 'ai-example'; b.textContent = t(k);
          b.addEventListener('click', () => sendMessage(b.textContent));
          list.appendChild(b);
        });
        box.appendChild(list);
        messagesEl.appendChild(box);
      }
      function clearEmptyState() {
        const e = messagesEl.querySelector('.ai-empty');
        if (e) e.remove();
      }

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
      // What the assistant learned from corrections (roadmap §5.4).
      async function aiLoadPreferences(open) {
        const box = document.getElementById('ai-prefs');
        const list = document.getElementById('ai-prefs-list');
        if (!box || !list) return;
        let rows = [];
        try {
          const res = await fetch(API + '/ai-accountant/preferences');
          if (!res.ok) { box.hidden = true; return; }
          rows = await res.json();
        } catch (_) { box.hidden = true; return; }
        box.hidden = false;
        document.getElementById('ai-prefs-count').textContent = rows.length ? ' (' + rows.length + ')' : '';
        if (open && rows.length) box.open = true;
        if (!rows.length) { list.innerHTML = '<p class="ai-prefs-empty">' + escapeHtml(t('aiPrefsEmpty')) + '</p>'; return; }
        list.innerHTML = rows.map(r => {
          const target = [r.account_code ? (r.account_code + (r.account_name ? ' ' + r.account_name : '')) : '',
                          r.entity_name || ''].filter(Boolean).join(' · ');
          return `<div class="ai-pref" data-id="${escapeHtml(r.id)}">
            <div class="ai-pref-text"><bdi dir="auto">${escapeHtml(r.label)}</bdi> → <bdi dir="auto">${escapeHtml(target)}</bdi>
              <span class="ai-pref-meta">${escapeHtml(tf('aiPrefsMeta', { source: t('aiPrefsSource_' + r.source), n: r.times_used }))}</span></div>
            <button type="button" class="btn btn-secondary btn-sm ai-pref-forget" aria-label="${escapeHtml(t('aiPrefsForget'))}" title="${escapeHtml(t('aiPrefsForget'))}">✕</button>
          </div>`;
        }).join('');
        list.querySelectorAll('.ai-pref-forget').forEach(btn => btn.addEventListener('click', async () => {
          const row = btn.closest('.ai-pref');
          if (!(await uiConfirm({ message: t('aiPrefsForgetConfirm'), confirmLabel: t('aiPrefsForget'), danger: true }))) return;
          const res = await fetch(API + '/ai-accountant/preferences/' + encodeURIComponent(row.dataset.id), { method: 'DELETE' });
          if (res.ok) aiLoadPreferences(true);
        }));
      }
      window.aiLoadPreferences = aiLoadPreferences;

      // Two-person approval (§5.6): what waits for someone who can approve.
      // Only approvers get the list (403 for everyone else → panel hidden).
      async function aiLoadApprovals() {
        const box = document.getElementById('ai-approvals');
        const list = document.getElementById('ai-approvals-list');
        if (!box || !list) return;
        let items = [];
        try {
          const res = await fetch(API + '/ai-accountant/approvals');
          if (!res.ok) { box.hidden = true; return; }
          items = (await res.json()).items || [];
        } catch (_) { box.hidden = true; return; }
        box.hidden = !items.length;
        document.getElementById('ai-approvals-count').textContent = items.length ? ' (' + items.length + ')' : '';
        list.innerHTML = items.map(it => `<div class="ai-pref ai-approval" data-token="${escapeHtml(it.confirmation_token)}">
            <div class="ai-pref-text"><bdi dir="auto">${escapeHtml(it.summary || it.tool_name)}</bdi>
              <span class="ai-pref-meta">${escapeHtml(tf('aiApprovalMeta', { who: it.requested_by }))}${it.amount != null ? ' · ' + escapeHtml(formatAmount(it.amount)) + ' ' + escapeHtml(currencyUnit()) : ''}</span>
              ${it.mine ? `<span class="ai-pref-meta">${escapeHtml(t('aiApprovalMine'))}</span>` : `<span class="ai-approval-actions">
                <button type="button" class="btn btn-primary btn-sm ai-approve">${escapeHtml(t('aiApprove'))}</button>
                <button type="button" class="btn btn-secondary btn-sm ai-reject">${escapeHtml(t('aiReject'))}</button></span>`}
            </div></div>`).join('');
        list.querySelectorAll('.ai-approve').forEach(btn => btn.addEventListener('click', async () => {
          const token = btn.closest('.ai-approval').dataset.token;
          if (!(await uiConfirm({ message: t('aiApproveConfirm'), confirmLabel: t('aiApprove') }))) return;
          const res = await fetch(API + '/ai-accountant/approvals/' + encodeURIComponent(token) + '/approve', { method: 'POST' });
          const d = await res.json().catch(() => ({}));
          if (!res.ok) { showAlert(typeof d.detail === 'string' ? d.detail : t('aiApprovalFailed'), true); }
          else { showAlert(t('aiApproved')); }
          aiLoadApprovals();
          if (typeof notifyRefresh === 'function') notifyRefresh();
        }));
        list.querySelectorAll('.ai-reject').forEach(btn => btn.addEventListener('click', async () => {
          const token = btn.closest('.ai-approval').dataset.token;
          const note = await uiPrompt({ title: t('aiReject'), message: t('aiRejectPrompt'), confirmLabel: t('aiReject') });
          if (note === null) return;
          const res = await fetch(API + '/ai-accountant/approvals/' + encodeURIComponent(token) + '/reject', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }) });
          if (!res.ok) { const d = await res.json().catch(() => ({})); showAlert(typeof d.detail === 'string' ? d.detail : t('aiApprovalFailed'), true); }
          aiLoadApprovals();
          if (typeof notifyRefresh === 'function') notifyRefresh();
        }));
      }
      window.aiLoadApprovals = aiLoadApprovals;
      // Drawn by script, so redrawn in a new language (02-i18n.js applyLanguage).
      window.aiRelocalize = () => {
        if (sessionListEl && Array.isArray(_sessionsCache)) renderSessionList(sessionSearchEl ? sessionSearchEl.value.trim() : '');
        // a new, empty chat: its title and welcome were drawn in the old language
        if (messagesEl && messagesEl.querySelector('.ai-empty') && !messagesEl.querySelector('.msg-row')) {
          if (!sessionId) setChatTitle(t('chatUntitled'));
          clearEmptyState();
          renderEmptyState();
        }
        const box = document.getElementById('ai-prefs');
        if (box && !box.hidden) aiLoadPreferences(false);
        const appr = document.getElementById('ai-approvals');
        if (appr && !appr.hidden) aiLoadApprovals();
      };

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
          sessionListEl.innerHTML = '<div class="sess-empty">' + escapeHtml(t('chatSessionsEmpty')) + '</div>';
          return;
        }
        _sessionsCache.forEach((sess) => {
          const item = document.createElement('div');
          const active = sess.id === sessionId;
          item.className = 'sess-item' + (active ? ' active' : '');
          item.setAttribute('role', 'listitem');
          if (active) setChatTitle(sess.title || t('chatUntitled'));
          const row = document.createElement('div');
          row.className = 'sess-row';
          const title = document.createElement('span');
          title.className = 'sess-title';
          title.innerHTML = _highlight(sess.title || t('chatUntitled'), q);
          row.appendChild(title);
          const ren = document.createElement('button');
          ren.type = 'button'; ren.className = 'sess-act'; ren.textContent = '✎'; ren.title = t('chatRename');
          ren.setAttribute('aria-label', t('chatRename'));
          ren.addEventListener('click', (e) => { e.stopPropagation(); startInlineRename(item, title, sess); });
          row.appendChild(ren);
          const del = document.createElement('button');
          del.type = 'button'; del.className = 'sess-act'; del.textContent = '🗑'; del.title = t('chatDelete');
          del.setAttribute('aria-label', t('chatDelete'));
          del.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (!(await uiConfirm({ title: t('chatDelete'), message: t('chatDeleteConfirm'), confirmLabel: t('chatDelete'), danger: true }))) return;
            await fetch(API + '/ai-accountant/sessions/' + encodeURIComponent(sess.id), { method: 'DELETE' });
            if (sess.id === sessionId) { sessionId = null; messagesEl.innerHTML = ''; setChatTitle(''); renderEmptyState(); }
            loadSessions(sessionSearchEl ? sessionSearchEl.value.trim() : '');
          });
          row.appendChild(del);
          item.appendChild(row);
          const meta = document.createElement('div');
          meta.className = 'sess-meta';
          meta.textContent = _relTime(sess.updated_at);
          item.appendChild(meta);
          if (sess.match_snippet) {
            const snip = document.createElement('div');
            snip.className = 'sess-snip';
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
        input.className = 'sess-rename';
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
          if (m.role === 'user') {
            // what the user said and sent; "text" may also carry a document's OCR, written for the model
            const said = typeof c.said === 'string' ? c.said
              : (typeof c.text === 'string' ? c.text : (typeof c.content === 'string' ? c.content : ''));  // legacy shape
            const files = (Array.isArray(c.files) ? c.files : []).map((f) => ({ file_name: f.name, content_type: f.content_type }));
            const quote = c.reply_to && typeof c.reply_to === 'object' ? c.reply_to : null;
            if (said.trim() || files.length) appendUserTurn(said, files, m.created_at, false, quote);
            return;
          }
          // the cards that came with this answer, each as it stands now (scenarios AE1–AE2)
          const cards = Array.isArray(m.cards) ? m.cards : [];
          const text = (typeof c.text === 'string' && c.text.trim())
            ? c.text
            : (typeof c.content === 'string' ? c.content : null);  // legacy shape
          if (!text) return;  // skip tool turns / empty tool-call turns
          if (m.role === 'assistant') appendBubble('assistant', text, { at: m.created_at, animate: false });
          cards.forEach((card) => appendProposalCard(card, { stored: true }));
        });
        renderEmptyState();
        scrollToBottom(true);
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
          const sess = _sessionsCache.find((x) => x.id === id);
          setChatTitle(sess ? (sess.title || t('chatUntitled')) : '');
          renderSessionList(sessionSearchEl ? sessionSearchEl.value.trim() : '');
        } catch (_) { /* keep current view */ }
      }
      // The assistant speaks first: once a day, when a chat is opened, ask
      // the server for a briefing of the proactive insights. Deterministic —
      // no model call — and persisted in the session, so it reads like any
      // other assistant message.
      async function maybeBriefing() {
        const key = 'aa_ai_briefing_' + localIsoDate(new Date());
        try { if (localStorage.getItem(key)) return; } catch (_) { /* storage blocked */ }
        try {
          const r = await fetch(API + '/ai-accountant/briefing', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId }),
          });
          const data = await readJsonSafe(r);
          if (!r.ok || !data || !data.text) return;
          sessionId = data.session_id || sessionId;
          appendBubble('assistant', data.text);
          try { localStorage.setItem(key, '1'); } catch (_) { /* ignore */ }
          loadSessions('');
        } catch (_) { /* briefing is best effort */ }
      }
      // Dashboard "Ask the AI" buttons (and the migration page) land here:
      // the chat is opened — its latest session restored — before sending.
      window.aiChatAsk = async (text) => {
        if (typeof showPage === 'function') showPage('ai-accountant');
        await window.aiChatInit();
        sendMessage(text);
      };

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
        setChatTitle(t('chatUntitled'));
        renderEmptyState();
        statusEl.textContent = t('aiChatNewStarted');
        loadSessions('');
        maybeBriefing();
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
      // Once, when the chat is first opened (loadPageData, aiChatAsk): it used
      // to run at every sign-in, on every page, for roles without the chat.
      let _chatReady = null;
      window.aiChatInit = () => {
        if (!_chatReady) {
          _chatReady = (async function restoreLatest() {
            await loadSessions('');
            aiLoadPreferences(false);
            aiLoadApprovals();
            if (_sessionsCache.length) await openSession(_sessionsCache[0].id);
            else { setChatTitle(t('chatUntitled')); renderEmptyState(); }
            maybeBriefing();
          })();
        }
        return _chatReady;
      };
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
        if (bytes < 1024) return localDigits(bytes) + ' B';
        if (bytes < 1024 * 1024) return localDigits(Math.round(bytes / 1024)) + ' KB';
        return localDigits((bytes / (1024 * 1024)).toFixed(1)) + ' MB';
      }

      function renderPendingAttachments() {
        if (!attachmentsEl) return;
        attachmentsEl.innerHTML = '';
        if (!pendingAttachments.length) { attachmentsEl.style.display = 'none'; return; }
        attachmentsEl.style.display = 'flex';
        pendingAttachments.forEach((att) => {
          const chip = document.createElement('span');
          chip.className = 'ai-attachment';
          const isImg = (att.content_type || '').startsWith('image/');
          const isSheet = /csv|excel|spreadsheet|tab-separated/.test(att.content_type || '');
          const name = document.createElement('span');
          name.className = 'name';
          const size = _fmtSize(att.size_bytes);
          name.textContent = (isImg ? '🖼 ' : (isSheet ? '📊 ' : '📄 ')) + (att.file_name || t('msgDocumentDefault')) + (size ? ' · ' + size : '');
          chip.appendChild(name);
          const rm = document.createElement('button');
          rm.type = 'button';
          rm.textContent = '✕';
          rm.title = t('aiChatRemoveAttachment');
          rm.setAttribute('aria-label', t('aiChatRemoveAttachment'));
          rm.addEventListener('click', () => {
            pendingAttachments = pendingAttachments.filter((a) => a.id !== att.id);
            renderPendingAttachments();
          });
          chip.appendChild(rm);
          attachmentsEl.appendChild(chip);
        });
      }

      // A phone photo is 3–8 MB; the reader needs far less. Scale big photos
      // to at most 2000 px as JPEG before upload (keeps within the limit and
      // off the user's data plan). Anything that can't be decoded goes as is.
      async function shrinkImage(file) {
        if (!/^image\/(jpeg|png|webp)$/.test(file.type || '') || file.size <= 1500000
            || typeof createImageBitmap !== 'function') return file;
        try {
          const bmp = await createImageBitmap(file);
          const scale = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
          const canvas = document.createElement('canvas');
          canvas.width = Math.round(bmp.width * scale);
          canvas.height = Math.round(bmp.height * scale);
          canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
          const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
          if (!blob || blob.size >= file.size) return file;
          return new File([blob], (file.name || 'photo').replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' });
        } catch (_) {
          return file;
        }
      }

      async function handleAttachFile(file) {
        if (!file) return;
        if (!_attachTypeOk(file)) {
          showAlert(t('aiChatAttachBadType'), true);
          return;
        }
        file = await shrinkImage(file);
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

        clearEmptyState();
        const wrap = document.createElement('div');
        wrap.className = 'typing-row';
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
        scrollToBottom(true);

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

      // Minimal markdown → HTML. Escapes first (XSS-safe), then handles the
      // subset LLMs emit in chat: # / ## / ### headings, **bold**, *italic*,
      // `inline code`, ``` fenced code, bulleted (- / * / •) and numbered
      // (1. 2. 3.) lists, | pipe | tables |, --- rules and paragraphs. Output
      // uses classes (styled under .md) and every block is direction-neutral
      // so Persian and English lines each lay out their own way.
      const _NUMERIC_CELL = /^[\s\d.,٬٫،%()+\-−۰-۹٠-٩]*[\d۰-۹٠-٩][\s\d.,٬٫،%()+\-−۰-۹٠-٩]*$/;
      function _renderChatMarkdown(text) {
        const esc = String(text || '').replace(/[&<>"']/g, c => ({
          '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
        }[c]));
        const lines = esc.split(/\r?\n/);
        const out = [];
        let listType = null;  // 'ul' | 'ol' | null
        let para = [];
        let inCode = false;
        let table = null;     // array of row arrays while inside a pipe table
        const flushPara = () => { if (para.length) { out.push('<p>' + para.join('<br>') + '</p>'); para = []; } };
        const closeList = () => { if (listType) { out.push(`</${listType}>`); listType = null; } };
        const flushTable = () => {
          if (!table) return;
          const [head, ...body] = table;
          const cell = (c, tag) => `<${tag}${_NUMERIC_CELL.test(c.replace(/&[a-z#0-9]+;/g, '')) && c.trim() ? ' class="num"' : ''}>${inline(c.trim())}</${tag}>`;
          let html = '<table><thead><tr>' + head.map(c => cell(c, 'th')).join('') + '</tr></thead>';
          if (body.length) html += '<tbody>' + body.map(r => '<tr>' + r.map(c => cell(c, 'td')).join('') + '</tr>').join('') + '</tbody>';
          out.push(html + '</table>');
          table = null;
        };
        const inline = (s) =>
          s
            .replace(/`([^`]+?)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>')
            .replace(/__([^_]+?)__/g, '<strong>$1</strong>')
            .replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>')
            .replace(/(^|[^_])_([^_\n]+?)_(?!_)/g, '$1<em>$2</em>');
        const splitRow = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|');
        for (let i = 0; i < lines.length; i++) {
          const line = lines[i].trimEnd();
          if (/^```/.test(line.trim())) {
            flushPara(); closeList(); flushTable();
            if (inCode) { out.push('</code></pre>'); inCode = false; }
            else { out.push('<pre><code>'); inCode = true; }
            continue;
          }
          if (inCode) { out.push(line); continue; }
          if (!line.trim()) { flushPara(); closeList(); flushTable(); continue; }
          let m;
          if (/^\s*\|.*\|\s*$/.test(line)) {
            flushPara(); closeList();
            const cells = splitRow(line);
            if (cells.every(c => /^\s*:?-{2,}:?\s*$/.test(c))) continue;  // header separator
            (table = table || []).push(cells);
            continue;
          }
          flushTable();
          if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flushPara(); closeList(); out.push('<hr>'); continue; }
          if ((m = /^(#{1,3})\s+(.*)$/.exec(line))) {
            flushPara(); closeList();
            const level = m[1].length + 2;  // # → h3, ## → h4, ### → h5
            out.push(`<h${level}>${inline(m[2])}</h${level}>`);
            continue;
          }
          if ((m = /^\s*[-*•]\s+(.*)$/.exec(line))) {
            flushPara();
            if (listType !== 'ul') { closeList(); out.push('<ul>'); listType = 'ul'; }
            out.push(`<li>${inline(m[1])}</li>`);
            continue;
          }
          if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) {
            flushPara();
            if (listType !== 'ol') { closeList(); out.push('<ol>'); listType = 'ol'; }
            out.push(`<li>${inline(m[1])}</li>`);
            continue;
          }
          closeList();
          para.push(inline(line));
        }
        if (inCode) out.push('</code></pre>');
        flushPara(); closeList(); flushTable();
        return out.join('');
      }

      // ─── Reply, copy, share on a message (scenarios AC1–AC4) ───
      // replyTo: the message the next one answers, {role, text, token?}, shown above the composer
      let replyTo = null;
      const QUOTE_CHARS = 400;               // the server keeps as much
      const _ACTION_ICONS = {
        reply: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="flip-rtl"><path d="M9 14L4 9l5-5"/><path d="M4 9h10a7 7 0 0 1 7 7v4"/></svg>',
        copy: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="1.5"/><path d="M15 9V5.5A1.5 1.5 0 0 0 13.5 4h-8A1.5 1.5 0 0 0 4 5.5v8A1.5 1.5 0 0 0 5.5 15H9"/></svg>',
        share: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7"/><path d="M16 6l-4-4-4 4"/><path d="M12 2v13"/></svg>',
      };
      function _quoteOf(role, text, token) {
        const words = String(text || '').replace(/\s+/g, ' ').trim().slice(0, QUOTE_CHARS);
        if (!words && !token) return null;
        return { role: role === 'user' ? 'user' : 'assistant', text: words, token: token || null };
      }
      async function _copyText(text) {
        try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* older browsers, an http page */ }
        const box = document.createElement('textarea');
        box.value = text;
        box.setAttribute('readonly', '');
        box.style.cssText = 'position:fixed;inset-inline-start:-9999px;top:0;';
        document.body.appendChild(box);
        box.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
        box.remove();
        return ok;
      }
      // the browser's own share sheet where it has one (phones, Safari, Edge); else Telegram, WhatsApp, e-mail
      function _shareText(text, anchor) {
        if (navigator.share) { navigator.share({ text }).catch(() => {}); return; }
        document.querySelectorAll('.msg-share-menu').forEach((m) => m.remove());
        const menu = document.createElement('div');
        menu.className = 'msg-share-menu';
        menu.setAttribute('role', 'menu');
        const q = encodeURIComponent(text);
        [['aiChatShareTelegram', 'https://t.me/share/url?url=' + q],
         ['aiChatShareWhatsApp', 'https://wa.me/?text=' + q],
         ['aiChatShareEmail', 'mailto:?body=' + q]].forEach(([key, href]) => {
          const a = document.createElement('a');
          a.href = href;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          a.setAttribute('role', 'menuitem');
          a.textContent = t(key);
          a.addEventListener('click', () => menu.remove());
          menu.appendChild(a);
        });
        anchor.parentNode.appendChild(menu);
        const away = (ev) => { if (!menu.contains(ev.target) && ev.target !== anchor) { menu.remove(); document.removeEventListener('click', away, true); } };
        setTimeout(() => document.addEventListener('click', away, true), 0);
      }
      // Reply, Copy, Share beside a message's meta line: shown on hover or focus, always on a touch screen
      function addMessageActions(host, getText, getQuote) {
        const bar = document.createElement('span');
        bar.className = 'msg-actions';
        const button = (key, icon, onClick) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'msg-action';
          b.title = t(key);
          b.setAttribute('aria-label', t(key));
          b.innerHTML = icon;
          b.addEventListener('click', onClick);
          bar.appendChild(b);
          return b;
        };
        button('aiChatReply', _ACTION_ICONS.reply, () => setReply(getQuote()));
        button('aiChatCopy', _ACTION_ICONS.copy, async () => {
          if (!(await _copyText(getText()))) return;
          bar.dataset.note = t('aiChatCopied');
          clearTimeout(bar._note);
          bar._note = setTimeout(() => { delete bar.dataset.note; }, 1600);
        });
        button('aiChatShare', _ACTION_ICONS.share, (ev) => _shareText(getText(), ev.currentTarget));
        host.appendChild(bar);
        return bar;
      }
      // Swipe a message left on a touch screen to reply (scenario AC11): it follows the finger, the reply
      // arrow comes up beside it (filled past the line, with a tick where the phone can), and it springs back.
      // [moving] is what slides; [host] holds the arrow. Vertical scrolling stays the browser's (touch-action).
      const SWIPE_LINE = 56, SWIPE_MOST = 84;
      function addSwipeReply(moving, host, getQuote) {
        const arrow = document.createElement('span');
        arrow.className = 'msg-swipe-arrow';
        arrow.setAttribute('aria-hidden', 'true');
        arrow.innerHTML = _ACTION_ICONS.reply.replace(' class="flip-rtl"', '');
        host.classList.add('swipeable');
        host.appendChild(arrow);
        moving.classList.add('swipe-move');
        let x0 = null, y0 = 0, dx = 0, armed = false;
        const show = () => {
          moving.style.transform = dx ? 'translateX(' + dx + 'px)' : '';
          host.style.setProperty('--pull', String(Math.min(1, -dx / SWIPE_LINE)));
          arrow.classList.toggle('armed', armed);
        };
        moving.addEventListener('pointerdown', (e) => {
          if (e.pointerType !== 'touch') return;
          x0 = e.clientX; y0 = e.clientY; dx = 0; armed = false;
        });
        moving.addEventListener('pointermove', (e) => {
          if (x0 === null) return;
          let d = Math.min(0, e.clientX - x0);
          if (Math.abs(e.clientY - y0) > Math.abs(d) && !dx) return;      // a scroll, not a swipe
          if (-d > SWIPE_LINE) d = -(SWIPE_LINE + (-d - SWIPE_LINE) * 0.35);
          dx = Math.max(d, -SWIPE_MOST);
          host.classList.add('swiping');
          const past = -dx >= SWIPE_LINE;
          if (past && !armed && navigator.vibrate) { try { navigator.vibrate(8); } catch (_) { /* not allowed here */ } }
          armed = past;
          show();
        });
        const end = (cancelled) => {
          if (x0 === null) return;
          const reply = armed && !cancelled;
          x0 = null; dx = 0; armed = false;
          host.classList.remove('swiping');
          show();
          if (reply) setReply(getQuote());
        };
        moving.addEventListener('pointerup', () => end(false));
        moving.addEventListener('pointercancel', () => end(true));
      }

      // above the composer while a reply is written: what it answers, and ✕ to answer nothing
      const replyStrip = document.createElement('div');
      replyStrip.className = 'ai-reply-strip';
      replyStrip.hidden = true;
      if (attachmentsEl && attachmentsEl.parentNode) attachmentsEl.parentNode.insertBefore(replyStrip, attachmentsEl);
      function renderReplyStrip() {
        replyStrip.innerHTML = '';
        replyStrip.hidden = !replyTo;
        if (!replyTo) return;
        replyStrip.appendChild(_quoteBlock(replyTo, t(replyTo.role === 'user' ? 'aiChatReplyingToYou' : 'aiChatReplyingToAssistant')));
        const drop = document.createElement('button');
        drop.type = 'button';
        drop.className = 'ai-icon-btn ai-reply-drop';
        drop.title = t('aiChatReplyDrop');
        drop.setAttribute('aria-label', t('aiChatReplyDrop'));
        drop.textContent = '✕';
        drop.addEventListener('click', () => setReply(null));
        replyStrip.appendChild(drop);
      }
      function setReply(quote) {
        replyTo = quote || null;
        renderReplyStrip();
        if (replyTo) inputEl.focus();
      }
      // a quoted message: whose it was, its first words, in the direction of its words. Its own dir
      // also keeps the bubble's dir="auto" on the user's words, not on an English label above them.
      function _quoteBlock(quote, label) {
        const box = document.createElement('div');
        box.className = 'msg-quote';
        box.setAttribute('dir', /^[^A-Za-z\u0590-\u08FF]*[\u0590-\u08FF]/.test(quote.text || '') ? 'rtl' : 'ltr');
        const who = document.createElement('div');
        who.className = 'msg-quote-who';
        who.textContent = label || t(quote.role === 'user' ? 'aiChatYou' : 'aiChatAssistant');
        const words = document.createElement('div');
        words.className = 'msg-quote-text';
        words.textContent = quote.text || '';
        box.appendChild(who);
        box.appendChild(words);
        return box;
      }

      // ─── A person's voucher on its card (scenarios AD1–AD5), as the phone draws it ───
      const _RTL_WORDS = /[\u0590-\u08FF]/;
      function _voucherWhat(card) {
        const date = card.date && card.date.display;
        // beside Persian digits a middle dot reads as a zero («۲۴۰ مهر»): a comma there
        const sep = date && _RTL_WORDS.test(date) ? '، ' : ' · ';
        return [card.title, date].filter(Boolean).join(sep);
      }
      // a person's lines To and From; a company's Dr and Cr («بد», «بس») (scenario AE3)
      function _voucherSide(line) {
        const out = Number(line.credit) > 0 && !(Number(line.debit) > 0);
        const personal = currentRole === 'personal';
        return { side: t(out ? (personal ? 'aiCardFrom' : 'aiCardCr') : (personal ? 'aiCardTo' : 'aiCardDr')),
                 value: out ? line.credit : line.debit };
      }
      // a card that posts to the books is drawn as the card itself; one that posts nothing keeps its sentence
      function _voucherOf(proposal) {
        return proposal.card && Array.isArray(proposal.card.lines) && proposal.card.lines.length ? proposal.card : null;
      }
      function _voucherBody(card) {
        const box = document.createElement('div');
        box.className = 'ai-voucher';
        const ccy = card.amount && card.amount.currency;
        if (card.amount) {
          const amt = document.createElement('div');
          amt.className = 'ai-voucher-amount';
          amt.setAttribute('dir', 'ltr');
          amt.textContent = formatAmount(card.amount.value, ccy);
          const unit = document.createElement('span');
          unit.className = 'ai-voucher-ccy';
          unit.textContent = ccy || '';
          amt.appendChild(unit);
          box.appendChild(amt);
        }
        const what = document.createElement('div');
        what.className = 'ai-voucher-what';
        what.setAttribute('dir', 'auto');
        if (card.title) what.appendChild(document.createTextNode(card.title));
        if (card.date && card.date.display) {
          const date = card.date.display;
          if (card.title) what.appendChild(document.createTextNode(_RTL_WORDS.test(date) ? '، ' : ' · '));
          const bdi = document.createElement('bdi');        // the date in its own direction, in one piece
          bdi.textContent = date;
          what.appendChild(bdi);
        }
        box.appendChild(what);
        const lines = document.createElement('div');
        lines.className = 'ai-voucher-lines';
        card.lines.forEach((line) => {
          const row = document.createElement('div');
          row.className = 'ai-voucher-line';
          const { side, value } = _voucherSide(line);
          const code = document.createElement('span');
          code.className = 'ai-voucher-code';
          code.textContent = localCode(line.account);
          const name = document.createElement('span');
          name.className = 'ai-voucher-name';
          name.setAttribute('dir', 'auto');
          name.textContent = line.name || line.account || '';
          const amount = document.createElement('span');
          amount.className = 'ai-voucher-side';
          amount.textContent = side + ' ' + formatAmount(value, ccy);
          row.appendChild(code);
          row.appendChild(name);
          row.appendChild(amount);
          lines.appendChild(row);
        });
        box.appendChild(lines);
        return box;
      }
      function _voucherText(card) {
        const ccy = card.amount && card.amount.currency;
        return [_voucherWhat(card),
                card.amount ? formatAmount(card.amount.value, ccy) + ' ' + (ccy || '') : '',
                ...card.lines.map((line) => {
                  const { side, value } = _voucherSide(line);
                  return localCode(line.account) + ' ' + (line.name || '') + '  ' + side + ' ' + formatAmount(value, ccy);
                })].filter((x) => String(x).trim()).join('\n');
      }

      // One message row: meta line (who + when) and a direction-neutral bubble.
      // role: 'user' | 'assistant' | 'system' (errors). opts.at = ISO time; opts.quote = what it answers.
      function appendBubble(role, text, opts) {
        opts = opts || {};
        clearEmptyState();
        const wrap = document.createElement('div');
        const kind = role === 'user' ? 'user' : (role === 'system' ? 'system' : 'assistant');
        wrap.className = 'msg-row ' + kind + (opts.animate === false ? '' : ' message-in');
        const meta = document.createElement('div');
        meta.className = 'msg-meta';
        const who = document.createElement('span');
        who.textContent = kind === 'user' ? t('aiChatYou') : t('aiChatAssistant');
        meta.appendChild(who);
        const when = _fmtTime(opts.at);
        if (when) { const tm = document.createElement('span'); tm.textContent = when; meta.appendChild(tm); }
        const bubble = document.createElement('div');
        bubble.className = 'msg' + (kind === 'assistant' ? ' md' : '');
        bubble.setAttribute('dir', 'auto');
        if (kind === 'assistant') bubble.innerHTML = _renderChatMarkdown(text);
        else bubble.textContent = text;
        if (opts.quote && opts.quote.text) bubble.insertBefore(_quoteBlock(opts.quote), bubble.firstChild);
        if (kind !== 'system') {
          // the words as they read (Markdown drawn, not its marks); a quote above them is not copied
          const words = () => kind === 'assistant' ? (bubble.innerText || bubble.textContent || '').trim() : String(text || '');
          addMessageActions(meta, words, () => _quoteOf(kind, words()));
          addSwipeReply(bubble, wrap, () => _quoteOf(kind, words()));
        }
        const body = document.createElement('div');
        body.className = 'msg-body';
        body.appendChild(bubble);
        wrap.appendChild(meta);
        wrap.appendChild(body);
        messagesEl.appendChild(wrap);
        scrollToBottom(kind === 'user');
        return wrap;
      }

      // ─── Bank statement card: imported + checked against the books ───
      function appendStatementCard(card, intake, fmt) {
        const c = intake.counts || {};
        // no bank found in the file or the message: no "— Unknown" after the title
        const bank = intake.bank_name && intake.bank_name !== 'Unknown' ? ' — ' + intake.bank_name : '';
        let html = '<div style="font-weight:600;margin-bottom:0.3rem;">' + escapeHtml(t('chatStmtTitle') + bank) + '</div>';
        if (intake.status === 'duplicate') {
          html += '<div>' + escapeHtml(t('chatStmtDuplicateFile')) + '</div>';
        } else if (intake.status === 'needs_password') {
          html += '<div>' + escapeHtml(t('chatStmtNeedsPassword')) + '</div>';
        } else if (intake.status === 'failed' || intake.status === 'needs_mapping') {
          html += '<div style="color:var(--danger,#dc3545);">' + escapeHtml(intake.error || t('chatStmtNeedsMapping')) + '</div>';
        } else {
          html += '<div>' + fmt(intake.total_rows || 0) + ' ' + escapeHtml(t('chatStmtRows'))
            + ((intake.from_date && intake.to_date) ? ' · ' + escapeHtml(formatDateDual(intake.from_date)) + ' – ' + escapeHtml(formatDateDual(intake.to_date)) : '')
            + '</div>';
          const bits = [];
          if (c.matched) bits.push('<span><strong>' + fmt(c.matched) + '</strong> ' + escapeHtml(t('chatStmtOnFile')) + '</span>');
          if (c.unrecorded) bits.push('<span style="color:#b45309;"><strong>' + fmt(c.unrecorded) + '</strong> ' + escapeHtml(t('chatStmtNew')) + '</span>');
          if (c.needs_confirmation) bits.push('<span><strong>' + fmt(c.needs_confirmation) + '</strong> ' + escapeHtml(t('chatStmtConfirm')) + '</span>');
          if (c.amount_mismatch) bits.push('<span style="color:var(--danger,#dc3545);"><strong>' + fmt(c.amount_mismatch) + '</strong> ' + escapeHtml(t('chatStmtMismatch')) + '</span>');
          if (c.missing_in_bank) bits.push('<span style="color:var(--danger,#dc3545);"><strong>' + fmt(c.missing_in_bank) + '</strong> ' + escapeHtml(t('chatStmtMissing')) + '</span>');
          if (c.duplicates) bits.push('<span style="color:var(--text-muted);"><strong>' + fmt(c.duplicates) + '</strong> ' + escapeHtml(t('chatStmtDupes')) + '</span>');
          if (bits.length) html += '<div style="display:flex;gap:0.8rem;flex-wrap:wrap;margin-top:0.3rem;">' + bits.join('') + '</div>';
          const b = intake.balance;
          if (b && b.gap) {
            html += '<div style="margin-top:0.3rem;font-weight:600;color:' + (b.explained ? '#b45309' : 'var(--danger,#dc3545)') + ';">'
              + escapeHtml(tf('chatStmtGap', { gap: formatAmount(Math.abs(b.gap), intake.currency), ccy: intake.currency || '' }))
              + (b.explained ? ' · ' + escapeHtml(t('chatStmtGapExplained')) : '') + '</div>';
          }
          if (intake.clean) html += '<div style="margin-top:0.3rem;color:var(--success,#059669);font-weight:600;">' + escapeHtml(t('chatStmtClean')) + '</div>';
        }
        card.innerHTML = html;
        if (intake.status === 'needs_password') {
          const go = document.createElement('button');
          go.type = 'button';
          go.className = 'btn btn-secondary btn-sm';
          go.style.marginTop = '0.5rem';
          go.textContent = t('chatStmtOpenUpload');
          go.addEventListener('click', () => showPage('bank-statements'));
          card.appendChild(go);
          return;
        }
        if (!intake.statement_id) return;
        const btnRow = document.createElement('div');
        btnRow.style.cssText = 'margin-top:0.5rem;display:flex;gap:0.5rem;flex-wrap:wrap;';
        if (intake.status === 'imported' && !intake.clean) {
          const fixBtn = document.createElement('button');
          fixBtn.type = 'button';
          fixBtn.className = 'btn btn-primary btn-sm';
          fixBtn.textContent = t('chatStmtReviewBtn');
          fixBtn.addEventListener('click', () => sendMessage(tf('chatStmtReviewMsg', { id: intake.statement_id })));
          btnRow.appendChild(fixBtn);
        }
        const openBtn = document.createElement('button');
        openBtn.type = 'button';
        openBtn.className = 'btn btn-secondary btn-sm';
        openBtn.textContent = t('chatStmtOpenBtn');
        openBtn.addEventListener('click', () => {
          if (typeof openStatementFromChat === 'function') openStatementFromChat(intake.statement_id, { review: true });
        });
        btnRow.appendChild(openBtn);
        card.appendChild(btnRow);
      }

      // ─── Smart-intake cards (spreadsheet drops) ───
      function appendIntakeCard(intake) {
        clearEmptyState();
        const card = document.createElement('div');
        card.className = 'ai-card message-in';
        card.setAttribute('dir', 'auto');
        card.style.cssText = 'background:#f0fdfa; font-size:0.88rem;';
        const fmt = (n) => (typeof n === 'number' ? formatNum(n) : n);
        let html = '';
        if (intake.kind === 'bank_statement') {
          appendStatementCard(card, intake, fmt);
          messagesEl.appendChild(card);
          messagesEl.scrollTop = messagesEl.scrollHeight;
          return;
        }
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
              if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : ((data.detail || {}).message || t('msgFailed')));
              const r = data.result || {};
              const chart = r.chart || {};
              const created = ['group', 'kol', 'moein'].reduce((a, k) => a + ((chart[k] || {}).created || 0), 0);
              const ents = r.entities || {};
              appendBubble('assistant', t('chatIntakeApplied') + ' — ' + created + ' ' + t('migrationAccountsCreated')
                + ', ' + ((ents.banks_created || 0) + (ents.counterparties_created || 0)) + ' ' + t('migrationEntitiesCreated')
                + '. ' + t((r.opening_journal || {}).kept ? 'migrationOpeningKept' : 'migrationJournalPosted')
                + ': ' + formatDisplayDate((r.opening_journal || {}).opening_date || ''));
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
              if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : t('msgFailed'));
              appendBubble('assistant', t('chatIntakeApplied') + ' — ' + (data.imported || 0) + ' ' + t('chatIntakeVouchers')
                + ((data.errors || []).length ? ' · ' + data.errors.slice(0, 3).join(' | ') : ''));
            }
            confirmBtn.textContent = '✓';
          } catch (e) {
            appendBubble('system', '⚠ ' + e.message);
            confirmBtn.disabled = false;
          }
        });
        btnRow.appendChild(confirmBtn);
        card.appendChild(btnRow);
        messagesEl.appendChild(card);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      // opts.stored: a card from a conversation opened again, drawn as it stands now (scenarios AE1–AE2)
      function appendProposalCard(proposal, opts) {
        opts = opts || {};
        clearEmptyState();
        const card = document.createElement('div');
        card.className = 'ai-card message-in';
        card.setAttribute('dir', 'auto');
        card.dataset.token = proposal.confirmation_token;
        if (proposal.replaces) {
          // the accountant corrected an earlier card: that one can no longer be confirmed
          const old = messagesEl.querySelector('.ai-card[data-token="' + CSS.escape(proposal.replaces) + '"]');
          if (old && !old.classList.contains('is-cancelled')) {
            old.classList.add('is-cancelled');
            old.querySelectorAll('.ai-card-actions button').forEach((b) => { b.disabled = true; });
            const note = document.createElement('div');
            note.style.cssText = 'font-size:0.8rem;color:var(--text-muted);margin-top:0.4rem;';
            note.textContent = t('aiChatReplaced');
            old.appendChild(note);
          }
        }

        const title = document.createElement('div');
        title.className = 'ai-card-head';
        title.innerHTML = '<span class="ai-card-icon">✓</span>';
        const titleText = document.createElement('span');
        titleText.textContent = t('aiChatProposedAction');
        title.appendChild(titleText);
        // copied and shared as it reads; a reply names the card, so a correction replaces this one
        const what = (proposal.preview && proposal.preview.description) || String(proposal.summary || '').split('\n')[0];
        const voucherCard = _voucherOf(proposal);
        addMessageActions(title, () => voucherCard ? _voucherText(voucherCard) : String(proposal.summary || '').trim(),
                          () => _quoteOf('assistant', what, proposal.confirmation_token));
        const host = document.createElement('div');        // holds the swipe's arrow, which stays as the card slides
        host.className = 'ai-card-wrap';
        host.appendChild(card);
        addSwipeReply(card, host, () => _quoteOf('assistant', what, proposal.confirmation_token));
        card.appendChild(title);

        // a voucher as the card itself (scenarios AD1–AD4, AE3); a card that posts nothing as its sentence
        if (voucherCard) card.appendChild(_voucherBody(voucherCard));
        else {
          const summary = document.createElement('pre');
          summary.className = 'ai-card-summary';
          summary.setAttribute('dir', 'auto');
          summary.textContent = proposal.summary || '';
          card.appendChild(summary);
        }

        // New entities to be created on Confirm (localized, alongside the entry).
        if (Array.isArray(proposal.new_entities) && proposal.new_entities.length) {
          const box = document.createElement('div');
          box.className = 'ai-card-note';
          proposal.new_entities.forEach(ne => {
            const line = document.createElement('div');
            const typeLabel = t('entType_' + ne.type) || ne.type;
            if (ne.type === 'bank' && ne.account_code) {
              const verb = ne.account_existing ? t('aiWillUseAccount') : t('aiWillCreateAccount');
              line.textContent = '➕ ' + tf('aiWillCreateBank', { name: ne.name, verb, code: localCode(ne.account_code) });
            } else {
              line.textContent = '➕ ' + tf('aiWillCreateEntity', { type: typeLabel, name: ne.name });
            }
            box.appendChild(line);
          });
          card.appendChild(box);
        }

        if (proposal.needs_approval) {
          const note = document.createElement('div');
          note.className = 'ai-card-note ai-card-approval';
          note.textContent = '⚖ ' + tf('aiNeedsApproval', { limit: formatAmount(proposal.approval_threshold || 0) + ' ' + currencyUnit() });
          card.appendChild(note);
        }

        const buttons = document.createElement('div');
        buttons.className = 'ai-card-actions';

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
          card.classList.add('is-cancelled');
          confirmBtn.disabled = true;
          cancelBtn.disabled = true;
          // the server forgets it too, so nothing can confirm it later
          fetch(API + '/ai-accountant/proposals/' + encodeURIComponent(proposal.confirmation_token) + '/cancel',
                { method: 'POST' }).catch(() => {});
          const cancelled = document.createElement('div');
          cancelled.style.cssText = 'font-size:0.8rem;color:var(--text-muted);margin-top:0.4rem;';
          cancelled.textContent = t('aiChatCancelled');
          card.appendChild(cancelled);
        });

        buttons.appendChild(confirmBtn);
        buttons.appendChild(cancelBtn);
        card.appendChild(buttons);
        if (opts.stored) _showStoredState(card, proposal);
        messagesEl.appendChild(host);
        scrollToBottom(true);
      }

      // A card from a conversation opened again, as it stands now (scenario AE2): still pending, its
      // buttons work; posted, its reference and date; otherwise what became of it, and no buttons.
      function _showStoredState(card, proposal) {
        const state = proposal.state || 'pending';
        if (state === 'pending' && proposal.approval_status !== 'requested') return;
        const buttons = card.querySelector('.ai-card-actions');
        const note = document.createElement('div');
        // settled: no longer "please confirm" above it
        const head = card.querySelector('.ai-card-head > span:not(.ai-card-icon)');
        if (head && state !== 'pending') head.textContent = t('aiCardEntry');
        if (state === 'executed') {
          if (buttons) buttons.remove();
          const posted = proposal.posted || {};
          card.classList.add('is-posted');
          note.className = 'ai-card-posted';
          // each part in its own direction («JE-۰۰۴۲» beside a Persian date jumbled), a comma between
          // them on a right-to-left page, where a middle dot reads as a zero
          const sep = document.documentElement.dir === 'rtl' ? '، ' : ' · ';
          note.appendChild(document.createTextNode(t(posted.undone ? 'aiCardUndone' : 'aiCardPosted')));
          [posted.voucher ? localCode(posted.voucher) : '', posted.date && posted.date.display].filter(Boolean)
            .forEach((part) => {
              note.appendChild(document.createTextNode(sep));
              const b = document.createElement('bdi');
              b.textContent = part;
              note.appendChild(b);
            });
        } else if (state === 'pending') {                    // sent for approval: someone else decides
          if (buttons) buttons.querySelectorAll('button').forEach((b) => { b.disabled = true; });
          note.className = 'ai-card-note ai-card-approval';
          note.textContent = '⏳ ' + t('aiSentForApproval');
        } else {
          if (buttons) buttons.remove();
          card.classList.add('is-cancelled');
          note.className = 'ai-card-gone';
          note.textContent = t({ replaced: 'aiChatReplaced', expired: 'aiCardExpired', rejected: 'aiCardRejected' }[state]
                               || 'aiChatCancelled');
        }
        card.appendChild(note);
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
            throw new Error(aborter.signal.aborted ? t('aiConfirmTimeout') : (netErr.message || t('msgNetworkError')));
          } finally {
            clearTimeout(killer);
          }
          const data = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(data.detail || tf('msgExecuteFailed', { status: r.status }));
          if (confirmBtn) confirmBtn.textContent = confirmLabel;
          if (r.status === 202 && data.status === 'awaiting_approval') {
            // above the approval limit: not recorded yet, waiting for someone else
            const waitNote = document.createElement('div');
            waitNote.className = 'ai-card-note ai-card-approval';
            waitNote.textContent = '⏳ ' + t(data.approvers ? 'aiSentForApproval' : 'aiSentNoApprover');
            cardEl.appendChild(waitNote);
            if (typeof aiLoadApprovals === 'function') aiLoadApprovals();
            return;
          }
          const receipt = document.createElement('div');
          receipt.style.cssText = 'margin-top:0.5rem; padding:0.4rem 0.6rem; background:#f0fdf4; border:1px solid #86efac; border-radius:6px; font-size:0.82rem;';
          receipt.innerHTML = '<strong>' + escapeHtml(t('aiUndoRecorded')) + '</strong> ' +
            (data.transaction_id ? `Transaction <code>${escapeHtml(data.transaction_id.slice(0,8))}…</code>` : '') +
            (data.idempotent ? ' <em>' + escapeHtml(t('aiUndoAlreadyCommitted')) + '</em>' : '');
          cardEl.appendChild(receipt);
          if (data.tool_name === 'propose_remember_preference' && typeof aiLoadPreferences === 'function') {
            aiLoadPreferences(true);
          }

          // Undo / Reverse act on a posted entry; a created party or a saved
          // preference has none (their undo would only fail).
          if (!data.idempotent && data.transaction_id) {
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
          err.textContent = tf('errorWithMessage', { message: e.message });
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
          // Quick undo removes the entry outright (mode "deleted"); the
          // persistent reverse — or an undo inside a closed period — posts a
          // compensating entry instead and says so.
          const deleted = data.mode === 'deleted';
          btn.textContent = deleted ? t('aiUndoDeleted') : t('aiUndoReversed');
          const note = document.createElement('div');
          note.style.cssText = 'margin-top:0.3rem; color:var(--text-muted); font-size:0.78rem;';
          note.textContent = deleted
            ? t('aiUndoDeletedNote')
            : tf('aiUndoReversalNote', { id: (data.reversal_transaction_id || '').slice(0, 8) });
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
      function appendUserTurn(text, attachments, at, animate, quote) {
        const wrap = appendBubble('user', text || '', { at: at || new Date().toISOString(), animate, quote });
        if (attachments && attachments.length) {
          const bubble = wrap.querySelector('.msg');
          if (bubble) {
            const strip = document.createElement('div');
            strip.className = 'msg-attach';
            attachments.forEach((att) => {
              const tag = document.createElement('span');
              const isImg = (att.content_type || '').startsWith('image/');
              tag.textContent = (isImg ? '🖼 ' : '📄 ') + (att.file_name || t('msgDocumentDefault'));
              strip.appendChild(tag);
            });
            bubble.appendChild(strip);
          }
        }
      }

      // tapping one sends it as a typed answer would be; the row goes once one is picked
      function appendChoices(options) {
        const row = document.createElement('div');
        row.className = 'ai-choices';
        options.slice(0, 4).forEach((o) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'chip';
          b.textContent = String(o);
          b.addEventListener('click', () => { row.remove(); sendMessage(String(o)); });
          row.appendChild(b);
        });
        messagesEl.appendChild(row);
        scrollToBottom(false);
      }

      async function sendMessage(msg) {
        const text = (msg || '').trim();
        const attachments = pendingAttachments.slice();
        // A turn needs either text or at least one attached document.
        if (!text && !attachments.length) return;
        const quote = replyTo;
        setReply(null);
        appendUserTurn(text, attachments, undefined, undefined, quote);
        inputEl.value = '';
        autosizeInput();
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
              reply_to: quote || null,
            }),
          });
          const data = await readJsonSafe(r);
          hideTypingIndicator(typingEl);
          if (!r.ok || data._nonJson) {
            const err = new Error((data && typeof data.detail === 'string' && data.detail) || t('aiChatFailed'));
            err.aiUnavailable = r.headers.get('X-Error-Code') === 'ai_unavailable';
            err.outOfCredit = r.headers.get('X-Error-Reason') === 'out_of_credit';
            throw err;
          }
          sessionId = data.session_id;
          if (data.text) appendBubble('assistant', data.text, { at: new Date().toISOString() });
          for (const proposal of (data.proposals || [])) {
            appendProposalCard(proposal);
          }
          // a short question's answers to tap (ask_with_choices: «count the books in Omani rials, or keep them?»)
          const ask = (data.tool_calls || []).slice().reverse()
            .find((c) => c && c.name === 'ask_with_choices' && c.result && Array.isArray(c.result.options));
          if (ask) appendChoices(ask.result.options);
          if (data.intake) appendIntakeCard(data.intake);
          statusEl.textContent = tf('aiChatStatusCounter', { turns: data.turns, calls: (data.tool_calls || []).length });
          loadSessions(sessionSearchEl ? sessionSearchEl.value.trim() : '');
        } catch (e) {
          hideTypingIndicator(typingEl);
          // the provider's own words are technical (and English): say it plainly, keep them for whoever fixes it
          appendBubble('assistant', e.outOfCredit ? t('aiOutOfCredit') + '\n\n(' + e.message + ')'
            : e.aiUnavailable ? t('aiUnavailable') + '\n\n(' + e.message + ')' : tf('aiChatError', { message: e.message }));
          statusEl.textContent = '';
        } finally {
          sendBtn.disabled = false;
          inputEl.focus();
        }
      }

      // Composer grows with the text (1–6 lines); Enter sends, Shift+Enter breaks.
      function autosizeInput() {
        inputEl.style.height = 'auto';
        inputEl.style.height = Math.min(inputEl.scrollHeight, 152) + 'px';
      }
      inputEl.addEventListener('input', autosizeInput);
      sendBtn.addEventListener('click', () => sendMessage(inputEl.value));
      inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendMessage(inputEl.value);
        }
      });
      // Camera capture (phones): the photo goes through the same attach path.
      // Voice notes (roadmap §5.7; scenarios W1–W7): record → /ai-accountant/transcribe → the
      // text lands in the input for the user to check and send. While it records a bar takes
      // the text box's place: the time, the voice's level, ✕ and ✓. Tap the microphone to record
      // hands-free (✓, Enter or the microphone to stop; ✕ or Esc to throw it away), or hold it
      // and let go; while holding, sliding toward the start side throws the recording away.
      const micBtn = document.getElementById('ai-acct-mic');
      const recBar = document.getElementById('ai-acct-rec');
      const recTime = document.getElementById('ai-acct-rec-time');
      const recLevel = document.getElementById('ai-acct-rec-level');
      const recHint = document.getElementById('ai-acct-rec-hint');
      const composer = micBtn ? micBtn.closest('.ai-composer') : null;
      const MIC_MAX_SECONDS = 120;
      const MIC_MIN_MS = 600;          // shorter is a stray touch, not words
      const HOLD_MS = 350;             // a press longer than this is hold-to-talk; shorter, a tap
      const CANCEL_PX = 90;            // slide this far toward the start side to throw it away
      let _rec = null, _recChunks = [], _recTimer = null, _recStarted = 0, _recDiscard = false;
      let _recStream = null, _recAudio = null, _recRaf = 0, _recLevels = [], _recLastBar = 0;
      let _hold = null;                // { x, id, at } while the microphone is pressed
      function _micType() {
        const types = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm'];
        return types.find(tp => window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(tp)) || '';
      }
      const _recRtl = () => !!composer && getComputedStyle(composer).direction === 'rtl';
      // «‹» is a mirrored glyph: it points to the start side in either direction
      function _recHintText() {
        if (_hold) return '‹ ' + t('aiRecSlide');
        return window.matchMedia && matchMedia('(pointer: coarse)').matches ? '' : t('aiRecEsc');
      }
      function _micTick() {
        const secs = Math.floor((Date.now() - _recStarted) / 1000);
        recTime.textContent = localDigits(Math.floor(secs / 60) + ':' + String(secs % 60).padStart(2, '0'));
        if (secs >= MIC_MAX_SECONDS) _recStop(false);
      }
      // the voice's level as bars: the newest at the end, older ones moving toward the start
      function _drawLevel(now) {
        _recRaf = requestAnimationFrame(_drawLevel);
        const cv = recLevel, dpr = window.devicePixelRatio || 1;
        const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
        if (!w || !h) return;
        if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
        if (now - _recLastBar < 70) return;
        _recLastBar = now;
        let level = 0;
        if (_recAudio) {
          const buf = _recAudio.buf;
          _recAudio.an.getByteTimeDomainData(buf);
          let sum = 0;
          for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
          level = Math.min(1, Math.sqrt(sum / buf.length) * 5);
        }
        const bar = 3 * dpr, gap = 2 * dpr, n = Math.max(4, Math.floor(w / (bar + gap)));
        _recLevels.push(level);
        while (_recLevels.length > n) _recLevels.shift();
        const ctx = cv.getContext('2d');
        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = '#dc2626';
        const rtl = _recRtl(), len = _recLevels.length;
        _recLevels.forEach((v, i) => {
          const bh = Math.max(2 * dpr, v * h);
          const slot = len - 1 - i;                     // 0 = the newest
          const x = rtl ? slot * (bar + gap) : w - (slot + 1) * (bar + gap) + gap;
          ctx.globalAlpha = 0.45 + 0.55 * (i / len);
          ctx.fillRect(x, (h - bh) / 2, bar, bh);
        });
        ctx.globalAlpha = 1;
      }
      async function _recStart() {
        if (_rec || !composer) return;
        let stream;
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (_) {
          _hold = null;
          micBtn.classList.remove('holding');
          showAlert(t('aiMicDenied'), true);
          return;
        }
        if (_rec) { stream.getTracks().forEach(tr => tr.stop()); return; }
        const type = _micType();
        _recStream = stream;
        _rec = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream);
        _recChunks = [];
        _recDiscard = false;
        _recLevels = [];
        _rec.addEventListener('dataavailable', (e) => { if (e.data && e.data.size) _recChunks.push(e.data); });
        _rec.addEventListener('stop', _recStopped);
        try {
          const AC = window.AudioContext || window.webkitAudioContext;
          const ac = new AC();
          const an = ac.createAnalyser();
          an.fftSize = 512;
          ac.createMediaStreamSource(stream).connect(an);
          _recAudio = { ac, an, buf: new Uint8Array(an.fftSize) };
        } catch (_) { _recAudio = null; }          // no meter, still a recording
        _rec.start();
        _recStarted = Date.now();
        composer.classList.add('is-recording');
        composer.classList.toggle('hands-free', !_hold);
        recBar.hidden = false;
        recBar.classList.remove('writing', 'sliding');
        recHint.classList.remove('near');
        recHint.style.transform = '';
        recHint.textContent = _recHintText();
        micBtn.classList.add('recording');
        micBtn.setAttribute('aria-pressed', 'true');
        statusEl.textContent = '';
        _micTick();
        _recTimer = setInterval(_micTick, 250);
        _recRaf = requestAnimationFrame(_drawLevel);
      }
      function _recStop(discard) {
        if (!_rec || _rec.state !== 'recording') return;
        _recDiscard = !!discard;
        _rec.stop();
      }
      function _recRelease() {             // the microphone, the meter and the timer
        clearInterval(_recTimer);
        cancelAnimationFrame(_recRaf);
        if (_recStream) _recStream.getTracks().forEach(tr => tr.stop());
        if (_recAudio) { try { _recAudio.ac.close(); } catch (_) { /* closed */ } }
        _recStream = null;
        _recAudio = null;
        _hold = null;
        micBtn.classList.remove('recording', 'holding');
        micBtn.setAttribute('aria-pressed', 'false');
      }
      function _recClose() {               // the text box back in its place
        recBar.hidden = true;
        recBar.classList.remove('writing', 'sliding');
        composer.classList.remove('is-recording', 'hands-free');
      }
      function _recStopped() {
        const took = Date.now() - _recStarted;
        const blob = new Blob(_recChunks, { type: (_rec && _rec.mimeType) || _micType() || 'audio/webm' });
        _rec = null;
        _recChunks = [];
        _recRelease();
        if (_recDiscard) { _recClose(); statusEl.textContent = t('aiRecCancelled'); inputEl.focus(); return; }
        if (!blob.size || took < MIC_MIN_MS) { _recClose(); statusEl.textContent = t('aiRecTooShort'); return; }
        _micSend(blob);
      }
      async function _micSend(blob) {
        micBtn.classList.add('busy');
        micBtn.disabled = true;
        recBar.classList.add('writing');
        recHint.style.transform = '';
        recHint.textContent = t('aiRecWriting');
        try {
          const fd = new FormData();
          fd.append('file', blob, 'voice-note');
          const res = await fetch(API + '/ai-accountant/transcribe', { method: 'POST', body: fd });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.text) {
            statusEl.textContent = '';
            // 422: nothing understood — say so in the user's language; other
            // refusals (the AI budget, the rate limit) carry their own reason
            const why = res.status !== 422 && typeof data.detail === 'string' ? data.detail : '';
            showAlert(why || t('aiMicFailed'), true);
            return;
          }
          const cur = inputEl.value.trim();
          inputEl.value = cur ? cur + ' ' + data.text : data.text;
          inputEl.dispatchEvent(new Event('input', { bubbles: true }));
          statusEl.textContent = t('aiMicDone');
        } catch (_) {
          statusEl.textContent = '';
          showAlert(t('aiMicFailed'), true);
        } finally {
          micBtn.classList.remove('busy');
          micBtn.disabled = false;
          _recClose();
          inputEl.focus();
          inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
        }
      }
      if (micBtn && recBar && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder) {
        micBtn.hidden = false;
        micBtn.addEventListener('pointerdown', (e) => {
          if (e.button !== 0 || micBtn.disabled || _rec) return;   // recording hands-free: the click stops it
          e.preventDefault();
          _hold = { x: e.clientX, id: e.pointerId, at: Date.now() };
          try { micBtn.setPointerCapture(e.pointerId); } catch (_) { /* not capturable */ }
          micBtn.classList.add('holding');
          _recStart();
        });
        micBtn.addEventListener('pointermove', (e) => {
          if (!_hold || e.pointerId !== _hold.id || !_rec) return;
          const dx = e.clientX - _hold.x;
          const d = Math.max(0, _recRtl() ? dx : -dx);          // how far toward the start side
          recBar.classList.add('sliding');
          recHint.style.transform = 'translateX(' + (_recRtl() ? d : -d) + 'px)';
          recHint.classList.toggle('near', d > CANCEL_PX * 0.6);
          if (d > CANCEL_PX) {
            if (navigator.vibrate) navigator.vibrate(30);
            _recStop(true);
          }
        });
        micBtn.addEventListener('pointerup', (e) => {
          if (!_hold || e.pointerId !== _hold.id) return;
          const held = Date.now() - _hold.at;
          _hold = null;
          micBtn.classList.remove('holding');
          recBar.classList.remove('sliding');
          recHint.style.transform = '';
          recHint.classList.remove('near');
          if (held < HOLD_MS) {                                  // a tap: hands-free, ✓ stops it
            if (_rec) composer.classList.add('hands-free');
            recHint.textContent = _recHintText();
            return;
          }
          _recStop(false);                                       // let go after holding: write it
        });
        micBtn.addEventListener('pointercancel', (e) => {
          if (_hold && e.pointerId === _hold.id) _recStop(true);
        });
        micBtn.addEventListener('click', (e) => {
          // Enter or Space on the button starts hands-free and stops it again; a press while
          // recording hands-free stops and writes
          if (e.detail === 0) { if (_rec) _recStop(false); else _recStart(); return; }
          if (_rec && !_hold && Date.now() - _recStarted > HOLD_MS) _recStop(false);
        });
        document.getElementById('ai-acct-rec-cancel').addEventListener('click', () => _recStop(true));
        document.getElementById('ai-acct-rec-done').addEventListener('click', () => _recStop(false));
        document.addEventListener('keydown', (e) => {
          if (!_rec || recBar.hidden) return;
          if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); _recStop(true); }
          else if (e.key === 'Enter' && !_hold) { e.preventDefault(); e.stopPropagation(); _recStop(false); }
        }, true);
        window.addEventListener('pagehide', () => _recStop(true));
      }

      const cameraBtn = document.getElementById('ai-acct-camera-btn');
      const cameraInput = document.getElementById('ai-acct-camera');
      if (cameraBtn && cameraInput) {
        cameraBtn.addEventListener('click', () => cameraInput.click());
        cameraInput.addEventListener('change', async () => {
          const shot = (cameraInput.files || [])[0];
          cameraInput.value = '';
          if (shot) await handleAttachFile(shot);
        });
      }

      // A photo or PDF shared to the installed app: the service worker kept
      // it in the "shared-files" cache and opened /?shared=N#ai-accountant.
      (async function pickUpSharedFiles() {
        const params = new URLSearchParams(location.search);
        if (!params.has('shared') || !('caches' in window)) return;
        history.replaceState(null, '', location.pathname + location.hash);
        try {
          const cache = await caches.open('shared-files');
          for (const req of await cache.keys()) {
            const res = await cache.match(req);
            await cache.delete(req);
            if (!res) continue;
            const blob = await res.blob();
            const name = new URL(req.url).searchParams.get('name') || 'shared';
            await handleAttachFile(new File([blob], name, { type: blob.type }));
          }
        } catch (_) { /* nothing to pick up */ }
      })();

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

