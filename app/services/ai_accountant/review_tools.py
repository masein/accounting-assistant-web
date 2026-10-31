"""«دیگه مرور هفتگی نفرست»: the app's own messages switched off, or on, from the chat (scenario AL14).

The weekly and monthly reviews, their suggestions and the nudge are each a
switch of the person asking, never of the company: a confirm card says which,
and confirming sets it (``users.review_prefs``).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .base import BaseTool, ToolContext
from .time_tools import _register

_NAMES = {"weekly": "the weekly review", "monthly": "the monthly review", "suggestions": "the reviews' suggestions",
          "nudge": "the nudge when nothing is recorded for a few days"}


_CARD = {
    "en": {"on": "On: ", "off": "Off: ", "weekly": "weekly review", "monthly": "monthly review",
           "suggestions": "suggestions", "nudge": "nudge after quiet days"},
    "fa": {"on": "فعال: ", "off": "غیرفعال: ", "weekly": "مرور هفتگی", "monthly": "مرور ماهانه",
           "suggestions": "پیشنهادها", "nudge": "یادآوری پس از چند روز بدون ثبت"},
    "es": {"on": "Activado: ", "off": "Desactivado: ", "weekly": "resumen semanal", "monthly": "resumen mensual",
           "suggestions": "sugerencias", "nudge": "aviso tras días sin registros"},
    "ar": {"on": "تشغيل: ", "off": "إيقاف: ", "weekly": "المراجعة الأسبوعية", "monthly": "المراجعة الشهرية",
           "suggestions": "الاقتراحات", "nudge": "التذكير بعد أيام بلا تسجيل"},
}


class ProposeReviewPrefsInput(BaseModel):
    kinds: list[Literal["weekly", "monthly", "suggestions", "nudge"]] = Field(
        ..., min_length=1, description="Which of the app's own messages: weekly, monthly, suggestions, nudge (all four for 'all of them').")
    on: bool = Field(..., description="true to get them again, false to stop them.")


class ProposeReviewPrefs(BaseTool):
    name = "propose_review_prefs"
    category = "proposal"
    description = (
        "Stop, or start again, the app's own messages for the person asking: the weekly review, the monthly review, "
        "the reviews' suggestions, the nudge after a few quiet days. Use for «دیگه مرور هفتگی نفرست», 'stop the "
        "weekly report', 'turn the monthly review back on', «یادآوری ثبت نکن». Only this person's; returns a confirm card."
    )
    InputSchema = ProposeReviewPrefsInput

    async def run(self, ctx: ToolContext, args: ProposeReviewPrefsInput) -> dict[str, Any]:
        from app.services.ai_accountant.orchestrator import _message_language
        kinds = list(dict.fromkeys(args.kinds))
        lang = _message_language(getattr(ctx, "user_message", None) or "", "en")
        lang = lang if lang in _CARD else "en"
        sep = "، " if lang in ("fa", "ar") else ", "
        # the card's title in the asker's words: «خاموش: مرور هفتگی», not "Off: weekly"
        payload = {"kinds": kinds, "on": args.on,
                   "description": _CARD[lang]["on" if args.on else "off"] + sep.join(_CARD[lang][k] for k in kinds)}
        token = _register(ctx, self.name, payload)
        verb = "Start again" if args.on else "Stop"
        return {"confirmation_token": str(token), "status": "pending", "preview": payload,
                "summary": f"{verb} {', '.join(_NAMES[k] for k in kinds)} — for you only."}


def execute_review_prefs(db, proposal, *, actor_user_id: str, actor_username: str | None,
                         ip_address: str | None) -> tuple[None, str]:
    import json
    import uuid

    from app.models.audit_log import AuditLog
    from app.models.user import User
    from app.services.proactive_reviews import set_switch
    p = dict(proposal.tool_input or {})
    user = db.get(User, uuid.UUID(str(actor_user_id)))
    if user is None:
        raise ValueError("No such user.")
    for kind in p.get("kinds") or []:
        set_switch(user, kind, bool(p.get("on")))
    audit = AuditLog(action="update", entity_type="user", entity_id=str(user.id), user_id=actor_user_id,
                     username=actor_username, ip_address=ip_address, actor_source="ai-assistant",
                     session_id=proposal.session_id, tool_name=proposal.tool_name,
                     confirmation_token=proposal.confirmation_token, user_message=proposal.user_message,
                     detail=json.dumps({"review_prefs": user.review_prefs or ""}))
    db.add(audit)
    db.flush()
    return None, str(audit.id)


def register_review_tools(registry) -> None:
    registry.register(ProposeReviewPrefs())
