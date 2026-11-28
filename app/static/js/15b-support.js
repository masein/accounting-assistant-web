    // ═══════ Support: a chat with the support desk (scenarios DA1–DA8) ═══════
    // The desk is Claude in the owner's Nightshift support desk, on the owner's Mac: it fetches the waiting
    // messages, answers, and the night shift fixes what it reports. The server only keeps the conversation.
    // Each message carries what the page knew: the page, the app's version, the language and the last failed
    // requests with their ids (window.__recentErrors, 01-core.js), so the desk finds them in the server's log.
    (function supportDesk() {
      const btn = document.getElementById('support-btn');
      const panel = document.getElementById('support-panel');
      if (!btn || !panel) return;
      const list = document.getElementById('support-list');
      const form = document.getElementById('support-form');
      const input = document.getElementById('support-input');
      const send = document.getElementById('support-send');
      const fileInput = document.getElementById('support-file');
      const files = document.getElementById('support-files');
      const statusEl = document.getElementById('support-status');
      const badge = document.getElementById('support-badge');
      let isOpen = false, after = null, attached = [], timer = null, sending = false;

      function appVersion() {
        const s = document.querySelector('script[src*="01-core.js"]');
        const m = s && /[?&]v=([^&]+)/.exec(s.getAttribute('src') || '');
        return m ? m[1] : '';
      }
      function context() {
        const page = document.querySelector('[data-page].active-page');
        return {
          page: (page && page.dataset.page) || (location.hash || '').replace('#', ''),
          app_version: appVersion(),
          language: typeof currentLanguage === 'string' ? currentLanguage : '',
          user_agent: navigator.userAgent,
          screen: `${window.innerWidth}x${window.innerHeight}`,
          platform: 'web',
          errors: (window.__recentErrors || []).slice(-10),
        };
      }
      function setBadge(n) {
        badge.style.display = n ? 'block' : 'none';
        badge.textContent = n > 9 ? '9+' : String(n || '');
      }
      function setOnline(online) {
        statusEl.textContent = t(online ? 'supportOnline' : 'supportOffline');
        statusEl.classList.toggle('is-online', !!online);
      }
      function when(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
        const day = typeof formatDisplayDate === 'function' ? formatDisplayDate(iso.slice(0, 10)) : iso.slice(0, 10);
        return typeof localDigits === 'function' ? localDigits(`${day} ${hm}`) : `${day} ${hm}`;
      }
      function intro() {
        if (list.querySelector('.support-msg')) return;
        list.innerHTML = '';
        const p = document.createElement('p');
        p.className = 'support-intro';
        p.textContent = t('supportIntro');
        list.appendChild(p);
      }
      function bubble(m) {
        const row = document.createElement('div');
        row.className = 'support-msg ' + (m.author === 'desk' ? 'from-desk' : 'from-me');
        row.dataset.id = m.id;
        const who = document.createElement('div');
        who.className = 'support-who';
        who.textContent = m.author === 'desk' ? t('supportTitle') : t('supportYou');
        const body = document.createElement('div');
        body.className = 'support-bubble';
        body.dir = 'auto';
        body.textContent = m.text || '';
        if ((m.attachments || []).length) {
          const a = document.createElement('div');
          a.className = 'support-att';
          a.textContent = '📎 ' + tf('supportAttached', { n: localDigits(String(m.attachments.length)) });
          body.appendChild(a);
        }
        const at = document.createElement('div');
        at.className = 'support-when';
        at.textContent = when(m.at);
        row.append(who, body, at);
        return row;
      }
      async function load(full) {
        const res = await fetch(API + '/support' + (!full && after ? '?after=' + encodeURIComponent(after) : ''))
          .catch(() => null);
        if (!res || !res.ok) return;
        const data = await res.json();
        setOnline(data.desk && data.desk.online);
        if (full) list.innerHTML = '';
        const msgs = data.messages || [];
        if (msgs.length) {
          const intro = list.querySelector('.support-intro');
          if (intro) intro.remove();
        }
        msgs.forEach((m) => {
          if (!list.querySelector(`.support-msg[data-id="${CSS.escape(m.id)}"]`)) list.appendChild(bubble(m));
          after = m.at || after;
        });
        if (!list.querySelector('.support-msg')) intro();
        if (msgs.length) list.scrollTop = list.scrollHeight;
        if (isOpen && data.unread) {
          await fetch(API + '/support/seen', { method: 'POST' }).catch(() => {});
          setBadge(0);
          if (typeof notifyRefresh === 'function') notifyRefresh();
        } else {
          setBadge(isOpen ? 0 : data.unread);
        }
      }
      function drawFiles() {
        files.innerHTML = '';
        attached.forEach((f, i) => {
          const chip = document.createElement('span');
          chip.className = 'chip';
          chip.textContent = '📎 ' + f.file_name;
          const x = document.createElement('button');
          x.type = 'button';
          x.className = 'support-file-x';
          x.textContent = '×';
          x.setAttribute('aria-label', t('btnDelete'));
          x.addEventListener('click', () => { attached.splice(i, 1); drawFiles(); });
          chip.appendChild(x);
          files.appendChild(chip);
        });
      }
      function schedule() {
        clearInterval(timer);
        timer = setInterval(() => load(false), isOpen ? 5000 : 60000);
      }
      function openPanel() {
        isOpen = true;
        panel.style.display = 'flex';
        btn.setAttribute('aria-expanded', 'true');
        after = null;
        load(true);
        schedule();
        setTimeout(() => input.focus(), 0);
      }
      function closePanel() {
        isOpen = false;
        panel.style.display = 'none';
        btn.setAttribute('aria-expanded', 'false');
        schedule();
      }
      window.openSupport = openPanel;

      btn.addEventListener('click', () => (isOpen ? closePanel() : openPanel()));
      document.getElementById('support-close').addEventListener('click', closePanel);
      document.getElementById('support-attach').addEventListener('click', () => fileInput.click());
      fileInput.addEventListener('change', async () => {
        const f = fileInput.files && fileInput.files[0];
        fileInput.value = '';
        if (!f) return;
        if (f.size > 20 * 1024 * 1024) { showAlert(t('supportTooLarge'), true); return; }
        const fd = new FormData();
        fd.append('file', f, f.name);
        const res = await fetch(API + '/support/attachments', { method: 'POST', body: fd }).catch(() => null);
        const body = res ? await readJsonSafe(res) : {};
        if (!res || !res.ok) { showAlert((body && typeof body.detail === 'string' && body.detail) || t('supportFailed'), true); return; }
        attached.push(body);
        drawFiles();
      });
      // a screenshot pasted into the box is attached the same way
      input.addEventListener('paste', (e) => {
        const item = Array.from((e.clipboardData && e.clipboardData.items) || []).find((i) => i.type.startsWith('image/'));
        if (!item) return;
        e.preventDefault();
        const f = item.getAsFile();
        if (!f) return;
        const dt = new DataTransfer();
        dt.items.add(new File([f], 'screenshot.png', { type: f.type }));
        fileInput.files = dt.files;
        fileInput.dispatchEvent(new Event('change'));
      });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
      });
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = input.value.trim();
        if ((!text && !attached.length) || sending) return;
        sending = true;
        send.disabled = true;
        try {
          const res = await fetch(API + '/support/messages', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, attachment_ids: attached.map((a) => a.id), context: context() }),
          });
          const body = await readJsonSafe(res);
          if (!res.ok) { showAlert((body && typeof body.detail === 'string' && body.detail) || t('supportFailed'), true); return; }
          input.value = '';
          attached = [];
          drawFiles();
          await load(false);
        } catch (_) {
          showAlert(t('supportFailed'), true);
        } finally {
          sending = false;
          send.disabled = false;
          input.focus();
        }
      });
      // the badge: once now, then every minute (every five seconds while the panel is open)
      load(false);
      schedule();
    })();
