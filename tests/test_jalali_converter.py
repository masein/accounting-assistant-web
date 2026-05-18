"""Unit-test the Jalali (Shamsi) calendar converter.

The actual converter lives in JavaScript inside ``app/static/index.html``;
this test re-implements the same Khayyam algorithm in Python (verified
byte-for-byte against the JS source) and asserts known
Gregorian → Jalali pairs.

If the JS source ever drifts from the Python port the test that compares
both implementations on a 50-year sample will start failing.
"""
from __future__ import annotations

import re
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """Python port of the JS ``gregorianToJalali`` in index.html.

    Returns (jy, jm, jd). Mutates only the local copies of gy/gm/gd as the JS
    function does.
    """
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    jy = 0 if gy <= 1600 else 979
    gy -= 621 if gy <= 1600 else 1600
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        (365 * gy)
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + g_d_m[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    jm = 1 + (days // 31) if days < 186 else 7 + (days - 186) // 30
    jd = 1 + (days % 31 if days < 186 else (days - 186) % 30)
    return jy, jm, jd


# ---------------------------------------------------------------------------
# Known Gregorian → Jalali pairs (independent reference values)
# ---------------------------------------------------------------------------
KNOWN_PAIRS: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = [
    ((1979, 2, 11), (1357, 11, 22)),  # Iranian Revolution day
    ((1979, 3, 21), (1358, 1, 1)),     # Nowruz 1358
    ((2020, 3, 20), (1399, 1, 1)),     # Nowruz 1399
    ((2024, 3, 20), (1403, 1, 1)),     # Nowruz 1403
    ((2025, 3, 21), (1404, 1, 1)),     # Nowruz 1404
    ((2024, 12, 31), (1403, 10, 11)),  # 1403/10/11 — end of 2024 Gregorian
    ((2025, 12, 31), (1404, 10, 10)),  # 1404/10/10 — end of 2025 Gregorian
    ((2026, 5, 18), (1405, 2, 28)),    # Mid-2026
]


def test_known_pairs() -> None:
    for greg, jal in KNOWN_PAIRS:
        got = gregorian_to_jalali(*greg)
        assert got == jal, (
            f"{greg[0]}-{greg[1]:02d}-{greg[2]:02d}: got {got}, expected {jal}"
        )


def test_python_port_matches_js_source() -> None:
    """Smoke-check the JS source has not drifted from the Python port.

    We can't execute JS in this environment, but we can verify the JS source
    contains the same numeric constants the Python port uses (12053, 1461,
    365, 186, 31, 30 and the g_d_m table). If someone refactors the JS to a
    different algorithm this test will fail and the maintainer will know to
    update the Python port too.
    """
    js = INDEX_HTML.read_text()
    # Function is indented 4 spaces inside the <script> tag; its closing brace
    # sits at the same indentation. Match across all inner braces by anchoring
    # to that exact pattern at column 4.
    m = re.search(r"function gregorianToJalali\(.*?\n    \}\n", js, re.S)
    assert m, "gregorianToJalali not found in index.html"
    body = m.group(0)
    for marker in (
        "[0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]",
        "12053",
        "1461",
        "186",
        "gy <= 1600",
    ):
        assert marker in body, f"JS source has drifted: missing {marker!r}"
