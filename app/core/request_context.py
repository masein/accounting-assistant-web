"""Request-scoped current user.

Mirrors the company ContextVar in ``app/db/tenant.py``: the auth middleware sets
the authenticated ``SessionUser`` here for the duration of each request, so code
deep in the service/serialization layer can attribute audit entries, enforce
object-level ownership, and strip sensitive fields by role — without threading
the user through every call.

Like the tenant scoping, it is a no-op outside a request (CLI, tests, pre-login).
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_current_user: ContextVar[Any | None] = ContextVar("current_user", default=None)


def set_current_user(user: Any | None) -> None:
    _current_user.set(user)


def get_current_actor() -> Any | None:
    """The current ``SessionUser`` (or None). Duck-typed: has user_id, username,
    role, entity_id, is_superadmin."""
    return _current_user.get()


def clear_current_user() -> None:
    _current_user.set(None)


def current_actor_role() -> str | None:
    u = _current_user.get()
    return getattr(u, "role", None) if u is not None else None


def current_actor_entity_id() -> str | None:
    u = _current_user.get()
    return getattr(u, "entity_id", None) if u is not None else None
