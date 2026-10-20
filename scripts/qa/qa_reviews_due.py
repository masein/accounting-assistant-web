"""Throwaway QA database only: run the scheduler's reviews job for the named companies on the given days
(scenarios AL1–AL16), as the daily job would on those mornings, then refresh their bells.

    docker exec aa-qa python /qa/qa_reviews_due.py "خانواده سارا=2026-10-17,2026-09-23,2026-10-29" ...

Writes what each made to /qa/out/reviews_made.json."""
import json
import sys
from datetime import date

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.db.tenant import tenant_bypass, use_company
from app.models.company import Company
from app.services.notification_service import refresh_notifications
from app.services.proactive_reviews import generate_due

if settings.app_env == "prod":
    sys.exit("refusing to run in production")
made: dict[str, dict[str, list[str]]] = {}
db = SessionLocal()
for arg in sys.argv[1:]:
    name, days = arg.split("=", 1)
    with tenant_bypass():
        company = db.execute(select(Company).where(Company.name == name)).scalars().first()
    if company is None:
        made[name] = {"error": ["no such company"]}
        continue
    with use_company(company.id):
        made[name] = {d: generate_due(db, date.fromisoformat(d)) for d in days.split(",")}
        db.commit()
        refresh_notifications(db)
        db.commit()
db.close()
json.dump(made, open("/qa/out/reviews_made.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(made, ensure_ascii=False))
