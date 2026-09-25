"""Create the owner the browser smoke suite logs in as — for a THROWAWAY
database only (CI's postgres service, a scratch DB). Reads E2E_USERNAME /
E2E_PASSWORD from the environment; refuses to run with APP_ENV=prod."""
from __future__ import annotations

import os
import sys


def main() -> int:
    from sqlalchemy import select

    from app.core.auth import hash_password
    from app.core.config import settings
    from app.core.release_notes import CURRENT_RELEASE
    from app.db.seed import DEFAULT_COMPANY_ID
    from app.db.session import SessionLocal
    from app.db.tenant import tenant_bypass
    from app.models.user import User

    if settings.app_env == "prod":
        print("refusing to seed an e2e user in production", file=sys.stderr)
        return 2
    username = os.environ.get("E2E_USERNAME", "e2e_owner")
    password = os.environ["E2E_PASSWORD"]
    db = SessionLocal()
    try:
        with tenant_bypass():
            user = db.execute(select(User).where(User.username == username)).scalars().first()
            ph, salt = hash_password(password)
            if user is None:
                user = User(username=username, is_admin=True, role="owner", is_active=True)
                db.add(user)
            import uuid
            user.company_id = uuid.UUID(DEFAULT_COMPANY_ID)
            user.password_hash, user.password_salt = ph, salt
            user.last_seen_release = CURRENT_RELEASE   # no "what's new" tour over the pages
            db.commit()
    finally:
        db.close()
    print(f"e2e user {username} ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
