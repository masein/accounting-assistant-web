from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import (
    create_session_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])
SUPPORTED_LANGUAGES = {"en", "fa", "es", "ar"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class PreferencesPatchRequest(BaseModel):
    language: str = Field(min_length=2, max_length=8)


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    username = payload.username.strip()
    user = db.execute(select(User).where(User.username == username)).scalars().first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash, user.password_salt):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_session_token(user_id=str(user.id), username=user.username, is_admin=user.is_admin)
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env.lower() == "prod",
        max_age=int(settings.auth_session_hours * 3600),
        path="/",
    )
    return {
        "ok": True,
        "user": {
            "id": str(user.id),
            "username": user.username,
            "is_admin": user.is_admin,
            "preferred_language": user.preferred_language or "en",
        },
    }


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(key=settings.auth_cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(current=Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    user_row = db.get(User, current.user_id)
    user_language = user_row.preferred_language if user_row and user_row.preferred_language else "en"
    return {
        "authenticated": True,
        "user": {
            "id": current.user_id,
            "username": current.username,
            "is_admin": current.is_admin,
            "preferred_language": user_language,
        },
    }


@router.post("/change-password")
def change_password(payload: PasswordChangeRequest, db: Session = Depends(get_db), current=Depends(get_current_user)) -> dict:
    user = db.get(User, current.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    h, s = hash_password(payload.password)
    user.password_hash = h
    user.password_salt = s
    db.commit()
    return {"ok": True}


@router.patch("/preferences")
def update_preferences(payload: PreferencesPatchRequest, db: Session = Depends(get_db), current=Depends(get_current_user)) -> dict:
    user = db.get(User, current.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    lang = (payload.language or "").strip().lower()
    if lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language")
    user.preferred_language = lang
    db.commit()
    return {"ok": True, "language": user.preferred_language}


@router.get("/admin-check")
def admin_check(_=Depends(require_admin)) -> dict:
    return {"ok": True}
