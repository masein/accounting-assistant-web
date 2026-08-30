"""Structural guards for the personal-mode frontend.

There is no JS test runner in this project, so these assert the invariants the
split classic-script frontend depends on by parsing the source — the same
approach as test_translation_parity / test_jalali_converter.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
INDEX = STATIC / "index.html"
CORE_JS = STATIC / "js" / "01-core.js"
I18N_JS = STATIC / "js" / "02-i18n.js"
BOOT_FORMS_JS = STATIC / "js" / "10-forms-fx-bank.js"
ADMIN_JS = STATIC / "js" / "04-admin-settings.js"

CHIP_RE = re.compile(r'<button[^>]*class="chip (chip-business|chip-personal)"[^>]*>', re.S)


def _chips(kind: str) -> list[str]:
    html = INDEX.read_text(encoding="utf-8")
    block = html.split('id="ai-acct-quick-actions"', 1)[1].split("</div>", 1)[0]
    return [tag for tag in re.findall(r"<button[^>]*>", block) if f'chip {kind}"' in tag]


# ---------------------------------------------------------------------------
# AI chat quick-action chips
# ---------------------------------------------------------------------------
def test_both_chip_sets_exist():
    assert len(_chips("chip-business")) >= 5
    assert len(_chips("chip-personal")) >= 5


def test_personal_chips_send_their_localized_label():
    """The click handler sends `chip.dataset.msg || chip.textContent`. Personal
    chips must NOT set data-msg: sending the localized label is what makes the
    assistant answer a Persian user in Persian."""
    for tag in _chips("chip-personal"):
        assert "data-msg=" not in tag, f"personal chip must not pin an English message: {tag}"
        assert "data-i18n=" in tag


def test_personal_chips_are_hidden_by_default():
    """Business chips are the cold-start default; applyRoleAccess() reveals the
    personal set only once /auth/me says role=personal."""
    for tag in _chips("chip-personal"):
        assert "display:none" in tag.replace(" ", "")


def test_chip_click_handler_falls_back_to_text_content():
    js = (STATIC / "js" / "15-ai-chat.js").read_text(encoding="utf-8")
    assert "chip.dataset.msg || chip.textContent" in js


def test_apply_role_access_toggles_both_chip_sets():
    js = CORE_JS.read_text(encoding="utf-8")
    body = js.split("function applyRoleAccess()", 1)[1].split("\n    }", 1)[0]
    assert "chip-business" in body and "chip-personal" in body
    assert "'personal'" in body


@pytest.mark.parametrize("key", [
    "aiChipPdSpentMonth", "aiChipPdByCategory", "aiChipPdBalance",
    "aiChipPdBiggest", "aiChipPdIncome", "aiChipPdRecent",
])
def test_personal_chip_labels_exist_in_every_language_pack(key):
    """Parity across en/fa/es/ar (test_translation_parity guards the whole set;
    this pins the chip keys specifically, since a missing one renders blank)."""
    js = I18N_JS.read_text(encoding="utf-8")
    assert js.count(f"{key}:") == 4, f"{key} must be defined in all four packs"


def test_every_chip_i18n_key_is_defined():
    html = INDEX.read_text(encoding="utf-8")
    block = html.split('id="ai-acct-quick-actions"', 1)[1].split("</div>", 1)[0]
    keys = re.findall(r'data-i18n="([A-Za-z0-9_]+)"', block)
    js = I18N_JS.read_text(encoding="utf-8")
    missing = [k for k in keys if f"{k}:" not in js]
    assert not missing, f"chip labels with no translation entry: {missing}"


# ---------------------------------------------------------------------------
# Boot: owner-only loaders must not fire for every role
# ---------------------------------------------------------------------------
OWNER_ONLY_LOADERS = ["loadUsers", "loadAIConfig", "loadAnthropicConfig"]


@pytest.mark.parametrize("loader", OWNER_ONLY_LOADERS)
def test_owner_only_loaders_are_not_called_eagerly_at_boot(loader):
    """currentRole is unknown until /auth/me resolves, so calling these during
    the synchronous bootstrap 403s for every non-owner role."""
    js = BOOT_FORMS_JS.read_text(encoding="utf-8")
    for line in js.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        assert stripped != f"{loader}();", f"{loader}() is called unconditionally at boot"


@pytest.mark.parametrize("loader", OWNER_ONLY_LOADERS)
def test_owner_only_loaders_run_from_the_owner_branch(loader):
    js = ADMIN_JS.read_text(encoding="utf-8")
    branch = js.split("if (currentRole === 'owner') {", 1)[1].split("}", 1)[0]
    assert f"{loader}()" in branch


# ---------------------------------------------------------------------------
# Role wiring stays in sync with the backend
# ---------------------------------------------------------------------------
def test_personal_role_is_wired_into_the_nav_tables():
    js = CORE_JS.read_text(encoding="utf-8")
    assert "'personal'" in js.split("ROLE_ORDER", 1)[1].split("\n", 1)[0]
    assert "'personal-dashboard'" in js  # PAGE_ROLES + validPages entry
    assert "personal: 'ai-accountant'" in js  # ROLE_HOME: chat-first landing


def test_frontend_roles_match_backend_roles():
    from app.core.permissions import ALL_ROLES

    js = CORE_JS.read_text(encoding="utf-8")
    order_line = js.split("const ROLE_ORDER = ", 1)[1].split(";", 1)[0]
    frontend_roles = set(re.findall(r"'([a-z]+)'", order_line))
    assert frontend_roles == set(ALL_ROLES)


# ---------------------------------------------------------------------------
# SME-only controls on the statement page
# ---------------------------------------------------------------------------
def test_reconcile_and_approve_all_are_marked_sme_only():
    """Reconciling against existing bookkeeping is meaningless for a personal
    tenant — every statement row is new spending, not a match candidate."""
    html = INDEX.read_text(encoding="utf-8")
    for button_id in ("bs-reconcile-btn", "bs-approve-all-btn"):
        tag = re.search(rf'<button[^>]*id="{button_id}"[^>]*>', html)
        assert tag, button_id
        assert "sme-only" in tag.group(0), f"{button_id} should be hidden from personal users"


def test_post_all_is_not_sme_only():
    """Posting the suggested rows is the personal user's whole workflow."""
    html = INDEX.read_text(encoding="utf-8")
    tag = re.search(r'<button[^>]*id="bs-post-all-btn"[^>]*>', html)
    assert tag and "sme-only" not in tag.group(0)


def test_apply_role_access_hides_sme_only_for_personal():
    js = CORE_JS.read_text(encoding="utf-8")
    body = js.split("function applyRoleAccess()", 1)[1].split("\n    }", 1)[0]
    assert "sme-only" in body
    assert "personalMode ? 'none' : ''" in body
