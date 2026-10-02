"""Throwaway QA database only: a super-admin to create the tenants through the UI."""
import os, sys
from sqlalchemy import select
from app.core.auth import hash_password
from app.core.config import settings
from app.core.release_notes import CURRENT_RELEASE
from app.db.seed import DEFAULT_COMPANY_ID
from app.db.session import SessionLocal
from app.db.tenant import tenant_bypass
from app.models.user import User

if settings.app_env == "prod":
    sys.exit("refusing to run in production")
pw = os.environ["QA_PASSWORD"]
db = SessionLocal()
with tenant_bypass():
    u = db.execute(select(User).where(User.username == "qa_super")).scalars().first()
    if u is None:
        u = User(username="qa_super", role="owner", is_admin=True, is_superadmin=True, is_active=True)
        db.add(u)
    u.password_hash, u.password_salt = hash_password(pw)
    u.is_superadmin = True
    # like the real seed's 'admin': the platform admin belongs to the default company
    import uuid
    u.company_id = uuid.UUID(DEFAULT_COMPANY_ID)
    u.last_seen_release = CURRENT_RELEASE
    db.commit()
print("qa_super ready")
