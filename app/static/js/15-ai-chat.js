
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
          const loc = { fa: 'fa-IR', ar: 'ar-EG', es: 'es-ES' }[_lang()] || 'en-GB';
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
            if (!window.confirm(t('chatDeleteConfirm'))) return;
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
          const text = (typeof c.text === 'string' && c.text.trim())
            ? c.text
            : (typeof c.content === 'string' ? c.content : null);  // legacy shape
          if (!text) return;  // skip tool turns / empty tool-call turns
          if (m.role === 'user') appendBubble('user', text, { at: m.created_at, animate: false });
          else if (m.role === 'assistant') appendBubble('assistant', text, { at: m.created_at, animate: false });
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
        const key = 'aa_ai_briefing_' + new Date().toISOString().slice(0, 10);
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
      // Dashboard "Ask the AI" buttons land here.
      window.aiChatAsk = (text) => {
        if (typeof showPage === 'function') showPage('ai-accountant');
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
      (async function restoreLatest() {
        await loadSessions('');
        if (_sessionsCache.length) await openSession(_sessionsCache[0].id);
        else { setChatTitle(t('chatUntitled')); renderEmptyState(); }
        maybeBriefing();
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
          chip.className = 'ai-attachment';
          const isImg = (att.content_type || '').startsWith('image/');
          const isSheet = /csv|excel|spreadsheet|tab-separated/.test(att.content_type || '');
          const name = document.createElement('span');
          name.className = 'name';
          const size = _fmtSize(att.size_bytes);
          name.textContent = (isImg ? '🖼 ' : (isSheet ? '📊 ' : '📄 ')) + (att.file_name || 'document') + (size ? ' · ' + size : '');
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

      // One message row: meta line (who + when) and a direction-neutral bubble.
      // role: 'user' | 'assistant' | 'system' (errors). opts.at = ISO time.
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
        let html = '<div style="font-weight:600;margin-bottom:0.3rem;">' + escapeHtml(t('chatStmtTitle')) + ' — ' + escapeHtml(intake.bank_name || '') + '</div>';
        if (intake.status === 'duplicate') {
          html += '<div>' + escapeHtml(t('chatStmtDuplicateFile')) + '</div>';
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
              + escapeHtml(tf('chatStmtGap', { gap: fmt(Math.abs(b.gap)), ccy: intake.currency || '' }))
              + (b.explained ? ' · ' + escapeHtml(t('chatStmtGapExplained')) : '') + '</div>';
          }
          if (intake.clean) html += '<div style="margin-top:0.3rem;color:var(--success,#059669);font-weight:600;">' + escapeHtml(t('chatStmtClean')) + '</div>';
        }
        card.innerHTML = html;
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
        const fmt = (n) => (typeof n === 'number' ? n.toLocaleString() : n);
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
            appendBubble('system', '⚠ ' + e.message);
            confirmBtn.disabled = false;
          }
        });
        btnRow.appendChild(confirmBtn);
        card.appendChild(btnRow);
        messagesEl.appendChild(card);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      function appendProposalCard(proposal) {
        clearEmptyState();
        const card = document.createElement('div');
        card.className = 'ai-card message-in';
        card.setAttribute('dir', 'auto');
        card.dataset.token = proposal.confirmation_token;

        const title = document.createElement('div');
        title.className = 'ai-card-head';
        title.innerHTML = '<span class="ai-card-icon">✓</span>';
        const titleText = document.createElement('span');
        titleText.textContent = t('aiChatProposedAction');
        title.appendChild(titleText);
        card.appendChild(title);

        const summary = document.createElement('pre');
        summary.className = 'ai-card-summary';
        summary.setAttribute('dir', 'auto');
        summary.textContent = proposal.summary || '';
        card.appendChild(summary);

        // New entities to be created on Confirm (localized, alongside the entry).
        if (Array.isArray(proposal.new_entities) && proposal.new_entities.length) {
          const box = document.createElement('div');
          box.className = 'ai-card-note';
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
          const cancelled = document.createElement('div');
          cancelled.style.cssText = 'font-size:0.8rem;color:var(--text-muted);margin-top:0.4rem;';
          cancelled.textContent = t('aiChatCancelled');
          card.appendChild(cancelled);
        });

        buttons.appendChild(confirmBtn);
        buttons.appendChild(cancelBtn);
        card.appendChild(buttons);
        messagesEl.appendChild(card);
        scrollToBottom(true);
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
      function appendUserTurn(text, attachments) {
        const wrap = appendBubble('user', text || '', { at: new Date().toISOString() });
        if (attachments && attachments.length) {
          const bubble = wrap.querySelector('.msg');
          if (bubble) {
            const strip = document.createElement('div');
            strip.className = 'msg-attach';
            attachments.forEach((att) => {
              const tag = document.createElement('span');
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
            }),
          });
          const data = await readJsonSafe(r);
          hideTypingIndicator(typingEl);
          if (!r.ok || data._nonJson) throw new Error((data && data.detail) || 'Chat failed');
          sessionId = data.session_id;
          if (data.text) appendBubble('assistant', data.text, { at: new Date().toISOString() });
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

