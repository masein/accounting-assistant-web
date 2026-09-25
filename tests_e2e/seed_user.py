"""Create the users the browser suite logs in as — for a THROWAWAY database
only (CI's postgres service, a scratch DB). Reads E2E_USERNAME / E2E_PASSWORD
from the environment; refuses to run with APP_ENV=prod.

* the owner every page smoke test uses;
* an accountant for the two-factor flow (E2E_TFA_USERNAME, same password),
  so turning 2FA on and off never touches the owner, and seeding it here
  costs none of the owner's API rate-limit budget."""
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
    import uuid

    password = os.environ["E2E_PASSWORD"]
    wanted = [
        (os.environ.get("E2E_USERNAME", "e2e_owner"), "owner"),
        (os.environ.get("E2E_TFA_USERNAME", "e2e_tfa"), "accountant"),
    ]
    db = SessionLocal()
    try:
        with tenant_bypass():
            for username, role in wanted:
                user = db.execute(select(User).where(User.username == username)).scalars().first()
                ph, salt = hash_password(password)
                if user is None:
                    user = User(username=username, is_admin=(role == "owner"), role=role, is_active=True)
                    db.add(user)
                user.company_id = uuid.UUID(DEFAULT_COMPANY_ID)
                user.password_hash, user.password_salt = ph, salt
                user.last_seen_release = CURRENT_RELEASE   # no "what's new" tour over the pages
                # a rerun on the same scratch DB starts without 2FA
                user.totp_secret = user.totp_pending_secret = user.totp_recovery = None
                user.totp_enabled_at = None
                user.totp_last_step = None
                db.commit()
                print(f"e2e user {username} ({role}) ready")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
