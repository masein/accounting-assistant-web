"""One invoice → a سامانه مودیان packet, plus the problems that block it.

Field codes follow the tax organisation's "دستورالعمل صدور صورتحساب
الکترونیکی" JSON (header / body / payments), as implemented by the public
SDKs. Amounts are whole rials. Values chosen here:

* inty  1 = نوع اول (buyer identified: national id / economic code), 2 = نوع دوم
* inp   1 = الگوی فروش          ins 1 = اصلی (original)
* tob   1 = حقیقی (10-digit national code), 2 = حقوقی (11-digit national id)
* setm  1 = نقد (paid in full), 2 = نسیه (nothing paid), 3 = نقد و نسیه
* indatim = the issue date at 12:00 Tehran time in epoch milliseconds, so the
  calendar day inside the 22-char tax number matches however it is read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entity import Entity
from app.models.invoice import Invoice
from app.services.moadian.settings import get_settings
from app.services.moadian.taxid import generate_taxid, valid_memory_id

TEHRAN = timezone(timedelta(hours=3, minutes=30))
SENDABLE = ("issued", "partially_paid", "paid")


@dataclass
class Built:
    packet: dict | None
    problems: list[str] = field(default_factory=list)   # block the export
    warnings: list[str] = field(default_factory=list)   # informational

    @property
    def ready(self) -> bool:
        return not self.problems


def _digits(v: str | None) -> str:
    return "".join(ch for ch in (v or "") if ch.isdigit())


def indatim_ms(d: date) -> int:
    return int(datetime.combine(d, time(12, 0), tzinfo=TEHRAN).timestamp() * 1000)


def _company_ids(db: Session) -> tuple[str, str]:
    from app.models.company_profile import CompanyProfile
    prof = db.execute(select(CompanyProfile)).scalars().first()
    econ = _digits(prof.economic_code) if prof else ""
    nid = _digits(prof.national_id) if prof else ""
    return econ, nid


def _buyer(ent: Entity | None) -> tuple[int | None, str | None, str | None, str | None]:
    """(tob, bid, tinb, bpc) from the customer record."""
    if ent is None:
        return None, None, None, None
    nid = _digits(ent.national_id)
    econ = _digits(ent.economic_code)
    tob = 1 if len(nid) == 10 else 2 if len(nid) == 11 else (2 if len(econ) == 11 else 1 if len(econ) == 14 else None)
    return tob, (nid or None), (econ or None), (_digits(ent.postal_code) or None)


def build(db: Session, inv: Invoice, *, serial: int | None = None) -> Built:
    from app.api.invoices import _invoice_totals

    conf = get_settings(db)
    b = Built(packet=None)
    if inv.kind != "sales":
        b.problems.append("Only sales invoices go to سامانه مودیان.")
    if inv.status not in SENDABLE:
        b.problems.append(f"A {inv.status} invoice can't be sent; issue it first." if inv.status == "draft"
                          else f"A {inv.status} invoice can't be sent.")
    if (inv.currency or "").upper() != "IRR":
        b.problems.append(f"Only rial invoices are supported in this export (this one is {inv.currency}).")
    if not valid_memory_id(conf["memory_id"]):
        b.problems.append("Enter the company's tax memory id (شناسه یکتای حافظه مالیاتی) in the مودیان settings.")
    econ, nid = _company_ids(db)
    tins = econ or nid
    if not tins:
        b.problems.append("Add the company's economic code (شماره اقتصادی) or national id to the company profile.")

    party = db.get(Entity, inv.entity_id) if inv.entity_id else None
    tob, bid, tinb, bpc = _buyer(party)
    inty = 1 if (bid or tinb) else 2
    if inty == 2:
        b.warnings.append("The customer has no national id or economic code, so this goes as نوع دوم (type 2).")
    elif tob is None:
        b.problems.append("The customer's national id must be 10 digits (person) or 11 digits (company).")
    if inty == 1 and bpc and len(bpc) != 10:
        b.problems.append("The customer's postal code must be 10 digits.")
    if inty == 1 and not bpc:
        b.warnings.append("The customer has no postal code.")

    default_sstid = (conf.get("default_sstid") or "").strip()
    default_mu = (conf.get("default_mu") or "").strip()
    lines = list(inv.items or [])
    body: list[dict] = []
    if not lines:
        lines_src = [(inv.description or f"Invoice {inv.number}", 1.0, int(inv.amount or 0), int(inv.amount or 0),
                      0.0, False, None, None)]
    else:
        lines_src = [(it.product_name, float(it.quantity or 0), int(it.unit_price or 0), int(it.line_total or 0),
                      float(it.tax_rate or 0), bool(it.taxable), it.sstid, it.mu) for it in lines]
    for n, (title, am, fee, line_total, rate, taxable, sstid, mu) in enumerate(lines_src, start=1):
        sid = (sstid or default_sstid or "").strip()
        unit = (mu or default_mu or "").strip()
        if not (sid.isdigit() and len(sid) == 13):
            b.problems.append(f"Line {n} ({title}): no 13-digit goods/service id (شناسه کالا/خدمت) and no default set.")
        if not unit:
            b.problems.append(f"Line {n} ({title}): no measurement-unit code and no default set.")
        prdis = int(round(am * fee))
        if prdis != line_total:
            b.problems.append(f"Line {n} ({title}): quantity × unit price ({prdis:,}) differs from the line total ({line_total:,}).")
        vra = rate if taxable else 0.0
        vam = int(round(line_total * vra / 100.0)) if vra > 0 else 0
        body.append({
            "sstid": sid, "sstt": (title or "")[:400], "am": am, "mu": unit, "fee": fee,
            "prdis": line_total, "dis": 0, "adis": line_total, "vra": vra, "vam": vam,
            "odam": 0, "olam": 0, "tsstam": line_total + vam,
        })

    tprdis = sum(x["prdis"] for x in body)
    tvam = sum(x["vam"] for x in body)
    tbill = tprdis + tvam
    if tbill != int(inv.amount or 0):
        b.problems.append(f"Line totals ({tbill:,}) do not add up to the invoice amount ({int(inv.amount or 0):,}).")
    paid, credited, _balance = _invoice_totals(db, inv)
    if credited:
        b.warnings.append("This invoice has credit notes; send them as return invoices (برگشت از فروش) separately.")
    cash = min(paid, tbill)
    setm = 1 if cash >= tbill else 2 if cash <= 0 else 3

    header = {
        "taxid": None, "indatim": indatim_ms(inv.issue_date), "indati2m": indatim_ms(inv.issue_date),
        "inty": inty, "inno": None, "irtaxid": None, "inp": 1, "ins": 1, "tins": tins,
        "tob": tob if inty == 1 else None, "bid": bid if inty == 1 else None,
        "tinb": tinb if inty == 1 else None, "bpc": bpc if inty == 1 else None,
        "tprdis": tprdis, "tdis": 0, "tadis": tprdis, "tvam": tvam, "todam": 0, "tbill": tbill,
        "setm": setm, "cap": cash, "insp": tbill - cash,
    }
    if serial is not None and valid_memory_id(conf["memory_id"]):
        header["taxid"] = generate_taxid(conf["memory_id"], inv.issue_date, serial)
        header["inno"] = f"{int(serial):010X}"
    b.packet = {"header": header, "body": body, "payments": [],
                "_meta": {"invoice_id": str(inv.id), "number": inv.number}}
    return b


def next_serial(db: Session) -> int:
    top = db.execute(select(func.max(Invoice.moadian_serial))).scalar()
    return int(top or 0) + 1


def deadline(db: Session, inv: Invoice) -> date:
    return inv.issue_date + timedelta(days=int(get_settings(db)["deadline_days"]))
