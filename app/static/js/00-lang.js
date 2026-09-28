// The saved UI language's strings, before the app scripts (roadmap 2026-09 §2.6).
// Each language lives in its own file (js/i18n/<lang>.js) so nobody downloads the
// three they don't use. This runs first and, for a saved non-English language,
// writes that pack's <script> in place — a parser-inserted script, so it runs
// before 01-core.js / 02-i18n.js and the first paint is already in the right
// language. The versioned URLs come from this tag's data-pack-* attributes.
(function () {
  var me = document.currentScript;
  var lang = null;
  try { lang = localStorage.getItem('aa_ui_language'); } catch (_) { /* storage blocked */ }
  if (!me || !lang || lang === 'en' || !/^[a-z]{2}$/.test(lang)) return;
  var url = me.getAttribute('data-pack-' + lang);
  if (url && /^\/static\/js\/i18n\/[a-z]{2}\.js(\?v=[0-9a-f]+)?$/.test(url)) {
    document.write('<script src="' + url + '"><\/script>');
  }
})();
