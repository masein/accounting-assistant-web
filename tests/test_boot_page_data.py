"""Boot fetches the shell; each page's data loads when that page opens.

Sign-in used to preload every page's lists — the ledger, entities, invoices,
recurring rules, the dashboard (twice), budgets, inventory items, the chat's
sessions, the Settings panels — for every role on every page: about 40
requests a load against a limit of 120 a minute, and a row of 403s for roles
that can't open those pages. These tests pin the split: what may run at the
top level of a script, that every page's loaders are reached from
loadPageData, that page loaders skip a page that isn't shown, and that the
first page loads once the role is known. tests_e2e/test_boot_requests.py
counts the real requests in a browser.
"""
from __future__ import annotations

import re
from pathlib import Path

JS = Path("app/static/js")


def _js(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def _all_js() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(JS.glob("*.js"))}


def _function(src: str, name: str) -> str:
    """The body of ``function name(`` (4-space indented, like every file here)."""
    m = re.search(r"^    (?:async )?function " + re.escape(name) + r"\(", src, re.M)
    assert m, name
    end = re.search(r"^    }\n", src[m.start():], re.M)
    return src[m.start():m.start() + end.end()]


# What may run at the top level of a script, at load: the shell and local
# form setup — nothing that fetches a page's data.
SHELL_CALLS = {
    "setInvoiceDateDefaults", "updateJalaliHint", "syncManagerFilterLabels",   # local form defaults
    "loadReportingCurrency", "renderAttachments",                               # currency label; local list
    "loadReportingLocale", "loadDisplayCalendar",                               # shell state every page reads
}


def test_boot_runs_only_shell_calls():
    calls = set()
    for name, src in _all_js().items():
        calls |= set(re.findall(r"^    ([A-Za-z_]\w*)\([^()]*\);\s*$", src, re.M))
        calls |= set(re.findall(r"^    (?:const|let) \w+ = ([a-z]\w*)\(\);\s*$", src, re.M))
        calls |= set(re.findall(r"^    ([a-z]\w*)\(\)\.then\(", src, re.M))
    assert calls == SHELL_CALLS | {"loadCurrentUser", "loadFxMetadata"}, calls ^ (SHELL_CALLS | {"loadCurrentUser", "loadFxMetadata"})
    boot = _js("10-forms-fx-bank.js")
    block = boot[boot.index("const userReady = loadCurrentUser();"):boot.index("// ─── FX settings panel")]
    for loader in ("loadOwnerDashboard", "loadLedger", "loadEntities", "loadInvoices", "loadRecurringRules",
                   "loadBudgets", "loadEntityOptions", "loadManagerInventoryItems", "loadChatProviderShape"):
        assert loader + "(" not in block, loader


# loader → the page(s) whose DOM it fills; it skips when none is shown and
# loadPageData calls it when one opens.
PAGE_LOADERS = {
    "loadOwnerDashboard": ("dashboard",), "loadBudgets": ("dashboard",), "loadLedger": ("ledger",),
    "loadEntities": ("entities",), "loadInvoices": ("invoices",), "loadRecurringRules": ("recurring",),
    "loadManagerInventoryItems": ("inventory",),
}


def _page_data_lines() -> dict[str, str]:
    body = _function(_js("12-ops.js"), "loadPageData")
    return dict(re.findall(r"if \(page === '([a-z-]+)'\) \{ (.*) \}", body))


def test_every_page_loader_is_reached_from_load_page_data_and_skips_hidden_pages():
    lines = _page_data_lines()
    src = _all_js()
    for loader, pages in PAGE_LOADERS.items():
        file = next(n for n, s in src.items() if re.search(r"^    async function " + loader + r"\(", s, re.M))
        body = _function(src[file], loader)
        first = body.split("\n")[1]
        want = "if (!onPage(" + ", ".join(f"'{p}'" for p in pages) + ")) return;"
        assert first.strip().startswith(want), (loader, first)
        for p in pages:
            assert loader + "(" in lines[p], (loader, p, lines.get(p))
    # the pages that relied on the preload have their own entries now
    assert "loadLedger()" in lines["ledger"] and "loadEntityOptions()" in lines["transactions"]
    assert "aiChatInit" in lines["ai-accountant"] and lines["settings"] == "loadSettingsPage();"
    # each page appears once (recurring used to be listed twice)
    body = _function(_js("12-ops.js"), "loadPageData")
    assert len(re.findall(r"if \(page === '", body)) == len(lines)


def test_active_page_is_what_show_page_marked():
    ui = _js("03-ui.js")
    fn = _function(ui, "activePage")
    assert ".card[data-page].active-page" in fn
    assert "function onPage(...pages) { return pages.includes(activePage()); }" in ui


def test_the_first_page_loads_once_the_role_is_known():
    boot = _js("16-boot.js")
    assert "showPage(initialPage);" in boot
    assert "userReady.then(() => loadPageData(activePage() || initialPage));" in boot
    assert boot.count("loadPageData(") == 1                     # not also straight away
    user = _function(_js("04-admin-settings.js"), "loadCurrentUser")
    assert "loadPageData(" not in user                          # boot does it, once
    assert user.count("return true;") == 1 and "return false;" in user
    # the role is set before anything role-dependent runs
    assert user.index("currentRole = (data.user.role") < user.index("loadCompanyBranding()")
    # a language this browser hasn't cached is fetched before the page draws
    assert user.index("await loadLanguagePack(lang)") < user.index("applyLanguage(lang, true)")
    # navigating to a page the role can't open loads the page it was sent to
    forms = _js("10-forms-fx-bank.js")
    handler = forms[forms.index("window.addEventListener('hashchange'"):]
    assert "loadPageData(activePage() || p);" in handler[:400]


def test_settings_panels_load_with_the_settings_page():
    admin = _js("04-admin-settings.js")
    user = _function(admin, "loadCurrentUser")
    for loader in ("loadUsers", "loadApiKeys", "loadAIUsage", "loadDigestSettings", "loadGuardrails",
                   "loadAILimits", "loadRateFeeds", "loadMessengerBots", "loadAIConfig", "loadAnthropicConfig",
                   "loadChatProviderShape", "populateEntityLinkOptions"):
        assert loader + "(" not in user, loader
    page = _function(admin, "loadSettingsPage")
    owner = page[page.index("if (currentRole === 'owner') {"):page.index("if (isSuperadmin) {")]
    for loader in ("loadUsers", "loadDigestSettings", "loadApiKeys", "loadAIUsage", "loadGuardrails"):
        assert loader + "(" in owner, loader
    platform = page[page.index("if (isSuperadmin) {"):]
    for loader in ("loadAIConfig", "loadAnthropicConfig", "loadChatProviderShape", "loadAILimits",
                   "loadRateFeeds", "loadMessengerBots"):
        assert loader + "(" in platform, loader
    assert "loadFxSettings()" in page                            # no longer at script load
    # the profile (logo, legal name) only for roles that may read settings
    assert "if (isSuperadmin || ['owner', 'cfo', 'personal'].includes(currentRole)) loadCompanyBranding();" in user


def test_the_chat_restores_its_session_when_opened_not_at_sign_in():
    chat = _js("15-ai-chat.js")
    init = chat[chat.index("window.aiChatInit = () => {"):]
    init = init[:init.index("\n      };\n") + 9]
    assert "async function restoreLatest()" in init and "return _chatReady;" in init
    assert "(async function restoreLatest() {" not in chat.replace(init, "")    # no longer an IIFE at load
    ask = chat[chat.index("window.aiChatAsk = async (text) => {"):]
    ask = ask[:ask.index("};")]
    assert ask.index("await window.aiChatInit();") < ask.index("sendMessage(text);")
    ops = _js("12-ops.js")
    mig = _function(ops, "migrationAskAI")
    assert "window.aiChatAsk(msg)" in mig and "send.click()" not in mig


def test_shared_requests_go_out_once():
    ui = _js("03-ui.js")
    meta = _function(ui, "loadFxMetadata")
    assert "if (!_fxMetaInflight) {" in meta and "return _fxMetaInflight;" in meta
    cur = _function(ui, "loadReportingCurrency")
    assert "if (_reportingCurrencyLoaded && !force && !_reportingCurrencyInflight) return Promise.resolve();" in cur
    assert "_reportingCurrencyLoaded = true;" in cur
    # saving it in Settings updates the cached value the dashboards now read
    forms = _js("10-forms-fx-bank.js")
    save = forms[forms.index("method: 'PUT',\n          headers: { 'Content-Type': 'application/json' },\n          body: JSON.stringify({ currency: value }),"):]
    assert "window.__REPORTING_CURRENCY = data.currency;" in save[:600]


def test_the_party_pickers_load_wherever_the_journal_editor_opens():
    """loadEntityOptions fills entityOptions, which the journal editor in the
    entity statement reads on any page. Guarded to the voucher/invoice pages
    (#187), the editor's party dropdowns came up empty and saving dropped the
    journal's parties."""
    vouchers = _js("06-vouchers.js")
    body = _function(vouchers, "loadEntityOptions")
    assert "onPage(" not in body                                          # loads on any page…
    boot = _js("10-forms-fx-bank.js")
    assert "loadEntityOptions();" not in boot[boot.index("const userReady"):boot.index("// ─── FX settings panel")]  # …never at boot
    lines = _page_data_lines()
    assert "loadEntityOptions()" in lines["transactions"] and "loadEntityOptions()" in lines["invoices"]
    ent = _js("08-entities-invoices.js")
    opener = _function(ent, "openEntityTransactions")
    assert "await loadEntityOptions();" in opener
    # and even if the list fails, the journal's own party stays selectable
    picker = _function(ent, "roleSelectHtml")
    assert "options.unshift({ id: currentId" in picker
    editor = _function(ent, "openEntityTransactionEditor")
    for role in ("client", "bank", "payee", "supplier"):
        assert f"roleSelectHtml('{role}', selectedEntityIdForRole(tx, '{role}'), linkedNameForRole(tx, '{role}'))" in editor

