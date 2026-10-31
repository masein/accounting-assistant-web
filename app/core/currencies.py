"""The currencies a person's books may count in (scenario Z3), named in the
four languages the app speaks. A person in Muscat keeps their money in Omani
rials, one in Dubai in dirhams; personal books started in Iranian rials for
everyone but UK English (app/api/auth.py signup)."""
from __future__ import annotations

# code → (English, Persian, Spanish, Arabic)
NAMES: dict[str, tuple[str, str, str, str]] = {
    "IRR": ("Iranian rial", "ریال ایران", "Rial iraní", "ريال إيراني"),
    "OMR": ("Omani rial", "ریال عمان", "Rial omaní", "ريال عماني"),
    "AED": ("UAE dirham", "درهم امارات", "Dírham de los EAU", "درهم إماراتي"),
    "SAR": ("Saudi riyal", "ریال عربستان", "Riyal saudí", "ريال سعودي"),
    "QAR": ("Qatari riyal", "ریال قطر", "Riyal catarí", "ريال قطري"),
    "KWD": ("Kuwaiti dinar", "دینار کویت", "Dinar kuwaití", "دينار كويتي"),
    "BHD": ("Bahraini dinar", "دینار بحرین", "Dinar bareiní", "دينار بحريني"),
    "IQD": ("Iraqi dinar", "دینار عراق", "Dinar iraquí", "دينار عراقي"),
    "TRY": ("Turkish lira", "لیر ترکیه", "Lira turca", "ليرة تركية"),
    "USD": ("US dollar", "دلار آمریکا", "Dólar estadounidense", "دولار أمريكي"),
    "EUR": ("Euro", "یورو", "Euro", "يورو"),
    "GBP": ("Pound sterling", "پوند انگلیس", "Libra esterlina", "جنيه إسترليني"),
    "CAD": ("Canadian dollar", "دلار کانادا", "Dólar canadiense", "دولار كندي"),
    "AUD": ("Australian dollar", "دلار استرالیا", "Dólar australiano", "دولار أسترالي"),
}
_LANG = {"en": 0, "fa": 1, "es": 2, "ar": 3}


def name(code: str | None, lang: str = "en") -> str:
    """«ریال عمان», "Omani rial"; a code not listed is itself."""
    c = (code or "").strip().upper()
    row = NAMES.get(c)
    return row[_LANG.get(lang, 0)] if row else c


def after_amount(code: str | None, lang: str = "en") -> str:
    """The currency after an amount in a sentence: in Persian «ریال» for the
    rial and the others by name («۹۰۰ دلار آمریکا»; «۹۰۰ USD» ended a Persian
    sentence in a code: Persian copy review, 2026-10-31); the code elsewhere."""
    c = (code or "").strip().upper()
    if lang != "fa":
        return code or ""
    return "ریال" if c == "IRR" else name(c, "fa")


def options(lang: str = "en") -> list[dict[str, str]]:
    return [{"code": c, "name": name(c, lang)} for c in NAMES]
