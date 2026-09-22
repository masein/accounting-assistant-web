"""What's-new registry: one entry per release, shown as a short tour to each
user the first time they log in after the update.

How it works
------------
* ``CURRENT_RELEASE`` is bumped with every deploy that ships user-visible
  change, and a ``Release`` describing the highlights is appended to
  ``RELEASES`` (newest last). Versions are dates, ``YYYY.MM.DD``, optionally
  with a ``.N`` suffix for a second release on the same day.
* ``users.last_seen_release`` remembers what each user has already been
  shown. ``NULL`` means "existing user from before this feature" → they see
  the current release once. A brand-new user is created with the current
  release so their first login isn't a changelog.
* ``whats_new_for(role, last_seen)`` returns the releases newer than what
  the user saw, with highlights filtered to the pages that role can open.

Copy lives here (not in the JS packs) so a release note can never be out of
step with the code that shipped it; the four UI languages are mandatory and
tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LANGUAGES = ("en", "fa", "es", "ar")

CURRENT_RELEASE = "2026.09.22"


@dataclass(frozen=True)
class Highlight:
    key: str
    title: dict[str, str]
    body: dict[str, str]
    page: str | None = None                          # SPA page "Show me" opens
    page_by_role: dict[str, str] = field(default_factory=dict)
    roles: tuple[str, ...] | None = None              # None = everyone

    def for_role(self, role: str | None) -> dict[str, Any] | None:
        r = (role or "owner").lower()
        if self.roles is not None and r not in self.roles:
            return None
        return {
            "key": self.key,
            "title": dict(self.title),
            "body": dict(self.body),
            "page": self.page_by_role.get(r, self.page),
        }


@dataclass(frozen=True)
class Release:
    version: str
    date: str
    highlights: tuple[Highlight, ...]


_BOOKS = ("owner", "cfo", "accountant", "personal")
_SME_BOOKS = ("owner", "cfo", "accountant")

RELEASES: tuple[Release, ...] = (
    Release(
        version="2026.09.22",
        date="2026-09-22",
        highlights=(
            Highlight(
                key="chat-statement",
                page="ai-accountant",
                roles=_BOOKS,
                title={
                    "en": "Drop a bank statement into the chat",
                    "fa": "صورتحساب بانکی را در گفتگو رها کنید",
                    "es": "Suelta un extracto bancario en el chat",
                    "ar": "أسقط كشف الحساب البنكي في المحادثة",
                },
                body={
                    "en": "Attach the bank's PDF, image, CSV or Excel. The assistant reads every row, checks it against your books and takes you through the differences one at a time — nothing is posted without your Confirm.",
                    "fa": "PDF، عکس، CSV یا اکسل بانک را پیوست کنید. دستیار همهٔ ردیف‌ها را می‌خواند، با دفاتر مقایسه می‌کند و اختلاف‌ها را یکی‌یکی با شما جلو می‌برد — بدون تأیید شما چیزی ثبت نمی‌شود.",
                    "es": "Adjunta el PDF, imagen, CSV o Excel del banco. El asistente lee cada fila, la compara con tus libros y te guía por las diferencias una a una; nada se registra sin tu confirmación.",
                    "ar": "أرفق ملف البنك بصيغة PDF أو صورة أو CSV أو Excel. يقرأ المساعد كل صف ويقارنه بدفاترك ويأخذك عبر الفروق واحدًا تلو الآخر — لا يُسجَّل شيء دون تأكيدك.",
                },
            ),
            Highlight(
                key="statement-review",
                page="bank-statements",
                roles=_BOOKS,
                title={
                    "en": "“Check against books” on every statement",
                    "fa": "«بررسی با دفاتر» روی هر صورتحساب",
                    "es": "«Comparar con los libros» en cada extracto",
                    "ar": "«مقارنة مع الدفاتر» على كل كشف",
                },
                body={
                    "en": "One button lists what the bank shows and the books don't, entries the bank never saw, amount mismatches, duplicates and the closing-balance gap — each with a one-click fix.",
                    "fa": "یک دکمه نشان می‌دهد چه چیزی در بانک هست و در دفاتر نیست، چه سندی را بانک ندیده، کجا مبلغ فرق دارد، تکراری‌ها و اختلاف مانده پایانی — هرکدام با یک کلیک قابل رفع.",
                    "es": "Un botón enumera lo que muestra el banco y no los libros, asientos que el banco no vio, importes distintos, duplicados y la diferencia de saldo final, cada uno con su arreglo en un clic.",
                    "ar": "زر واحد يعرض ما يظهره البنك ولا تظهره الدفاتر، والقيود التي لم يرها البنك، واختلافات المبالغ، والتكرارات، وفرق الرصيد الختامي — مع إصلاح بنقرة واحدة لكل منها.",
                },
            ),
            Highlight(
                key="entity-statement",
                page="entities",
                roles=_SME_BOOKS,
                title={
                    "en": "Entity transactions read like a statement of account",
                    "fa": "تراکنش‌های طرف حساب مثل صورتحساب",
                    "es": "Las transacciones por entidad se leen como un estado de cuenta",
                    "ar": "معاملات الطرف تُقرأ ككشف حساب",
                },
                body={
                    "en": "“View transactions” now shows Debtor, Creditor and Remaining per row, and an “Add transaction” button records a new entry with the entity already linked.",
                    "fa": "«مشاهده تراکنش‌ها» حالا ستون‌های بدهکار، بستانکار و مانده دارد و دکمهٔ «ثبت تراکنش» سند جدید را با همان طرف حساب پیوندشده ثبت می‌کند.",
                    "es": "«Ver transacciones» muestra ahora Debe, Haber y Saldo por fila, y «Añadir transacción» registra un asiento con la entidad ya vinculada.",
                    "ar": "«عرض المعاملات» يعرض الآن مدين ودائن والرصيد لكل صف، وزر «إضافة معاملة» يسجل قيدًا جديدًا مع ربط الطرف مسبقًا.",
                },
            ),
            Highlight(
                key="undo-removes",
                page="ai-accountant",
                roles=_BOOKS,
                title={
                    "en": "Undo now removes the entry",
                    "fa": "واگرد حالا سند را حذف می‌کند",
                    "es": "Deshacer ahora elimina el asiento",
                    "ar": "التراجع يحذف القيد الآن",
                },
                body={
                    "en": "Confirm, then Undo within two minutes: the entry disappears from the books instead of leaving a reversal pair. “Reverse entry” after that still posts a compensating entry.",
                    "fa": "تأیید و سپس واگرد تا دو دقیقه: سند از دفاتر حذف می‌شود، نه اینکه سند برگشتی کنارش بماند. «برگرداندن سند» بعد از آن همچنان سند معکوس ثبت می‌کند.",
                    "es": "Confirma y deshaz en dos minutos: el asiento desaparece de los libros en vez de dejar un par de reversión. «Revertir asiento» después sigue registrando un asiento compensatorio.",
                    "ar": "أكِّد ثم تراجع خلال دقيقتين: يختفي القيد من الدفاتر بدلًا من ترك قيد عكسي. «عكس القيد» بعد ذلك ما زال يسجل قيدًا تعويضيًا.",
                },
            ),
            Highlight(
                key="insights",
                page="dashboard",
                page_by_role={"personal": "personal-dashboard"},
                roles=_BOOKS,
                title={
                    "en": "The assistant tells you what changed",
                    "fa": "دستیار می‌گوید چه چیزی تغییر کرده",
                    "es": "El asistente te dice qué ha cambiado",
                    "ar": "المساعد يخبرك بما تغيّر",
                },
                body={
                    "en": "A new “What changed” panel, the bell and a short briefing when you open the chat point out payroll moves, new employees, expense spikes, unusual payments, a statement that is due for upload — before you ask.",
                    "fa": "پنل جدید «چه چیزی تغییر کرده»، زنگ اعلان‌ها و یک خلاصهٔ کوتاه در ابتدای گفتگو، تغییر حقوق و دستمزد، کارمند جدید، جهش هزینه، پرداخت غیرمعمول و موعد بارگذاری صورتحساب را پیش از اینکه بپرسید نشان می‌دهند.",
                    "es": "Un nuevo panel «Qué ha cambiado», la campana y un breve resumen al abrir el chat señalan cambios de nómina, nuevos empleados, picos de gasto, pagos inusuales o un extracto pendiente, antes de que preguntes.",
                    "ar": "لوحة «ما الذي تغيّر» الجديدة والجرس وملخص قصير عند فتح المحادثة تشير إلى تغيّرات الرواتب والموظفين الجدد وقفزات المصروفات والدفعات غير المعتادة وكشف حساب مستحق الرفع — قبل أن تسأل.",
                },
            ),
            Highlight(
                key="whats-new",
                page="settings",
                title={
                    "en": "This tour, after every update",
                    "fa": "همین راهنما، بعد از هر به‌روزرسانی",
                    "es": "Este recorrido, tras cada actualización",
                    "ar": "هذه الجولة بعد كل تحديث",
                },
                body={
                    "en": "The first time you log in after an update you'll get a short walkthrough of what's new. You can reopen it any time from Settings.",
                    "fa": "اولین بار که پس از هر به‌روزرسانی وارد می‌شوید، خلاصهٔ کوتاهی از تغییرات می‌بینید. از تنظیمات هر زمان می‌توانید دوباره آن را باز کنید.",
                    "es": "La primera vez que inicies sesión tras una actualización verás un breve recorrido por las novedades. Puedes reabrirlo cuando quieras desde Ajustes.",
                    "ar": "في أول تسجيل دخول بعد أي تحديث ستحصل على جولة قصيرة بالجديد. يمكنك إعادة فتحها في أي وقت من الإعدادات.",
                },
            ),
        ),
    ),
)


def version_tuple(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    out = []
    for part in str(version).split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def whats_new_for(role: str | None, last_seen: str | None, *, include_all: bool = False) -> dict[str, Any]:
    """Releases the user hasn't seen, highlights filtered to their role.

    ``last_seen=None`` (a user from before this feature) sees only the
    current release, not the whole history. ``include_all`` returns every
    release regardless (the Settings "What's new" button).
    """
    seen_t = version_tuple(last_seen)
    releases = []
    for rel in sorted(RELEASES, key=lambda r: version_tuple(r.version), reverse=True):
        if not include_all:
            if last_seen is None and rel.version != CURRENT_RELEASE:
                continue
            if last_seen is not None and version_tuple(rel.version) <= seen_t:
                continue
        items = [h for h in (hl.for_role(role) for hl in rel.highlights) if h]
        if items:
            releases.append({"version": rel.version, "date": rel.date, "highlights": items})
    return {
        "current_release": CURRENT_RELEASE,
        "last_seen_release": last_seen,
        "seen": not releases if not include_all else (version_tuple(last_seen) >= version_tuple(CURRENT_RELEASE)),
        "releases": releases,
    }
