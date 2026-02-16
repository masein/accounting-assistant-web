from __future__ import annotations

from threading import RLock
from typing import Any

from app.core.config import settings


_lock = RLock()


def _default_provider() -> str:
    p = (settings.ai_provider or "").strip().lower()
    if p in ("lmstudio", "metis", "custom"):
        return p
    if settings.ai_base_url:
        return "custom"
    return "lmstudio"


_state: dict[str, Any] = {
    "provider": _default_provider(),
    "lmstudio": {
        "base_url": (settings.lm_studio_base_url or "").strip(),
        "model": (settings.lm_studio_model or "").strip(),
        "api_key": "",
        "api_key_header": "Authorization",
        "api_key_prefix": "Bearer",
    },
    "metis": {
        "base_url": (settings.metis_base_url or "https://api.metisai.ir/openai/v1").strip(),
        "model": (settings.metis_model or "gpt-4o-mini").strip(),
        "api_key": (settings.metis_api_key or settings.ai_api_key or "").strip(),
        "api_key_header": "Authorization",
        "api_key_prefix": "Bearer",
    },
    "custom": {
        "base_url": (settings.ai_base_url or "").strip(),
        "model": (settings.ai_model or settings.lm_studio_model or "").strip(),
        "api_key": (settings.ai_api_key or "").strip(),
        "api_key_header": (settings.ai_api_key_header or "Authorization").strip(),
        "api_key_prefix": (settings.ai_api_key_prefix or "Bearer").strip(),
    },
}


def _sanitize_provider(p: str | None) -> str:
    v = (p or "").strip().lower()
    return v if v in ("lmstudio", "metis", "custom") else _state["provider"]


def get_ai_config_public() -> dict[str, Any]:
    with _lock:
        provider = _state["provider"]
        active = dict(_state.get(provider, {}))
        active["api_key"] = ""
        return {
            "provider": provider,
            "providers": ["metis", "lmstudio", "custom"],
            "active": active,
            "lmstudio": {
                "base_url": _state["lmstudio"]["base_url"],
                "model": _state["lmstudio"]["model"],
                "has_api_key": bool(_state["lmstudio"]["api_key"]),
            },
            "metis": {
                "base_url": _state["metis"]["base_url"],
                "model": _state["metis"]["model"],
                "has_api_key": bool(_state["metis"]["api_key"]),
            },
            "custom": {
                "base_url": _state["custom"]["base_url"],
                "model": _state["custom"]["model"],
                "api_key_header": _state["custom"]["api_key_header"],
                "api_key_prefix": _state["custom"]["api_key_prefix"],
                "has_api_key": bool(_state["custom"]["api_key"]),
            },
        }


def update_ai_config(
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_key_header: str | None = None,
    api_key_prefix: str | None = None,
) -> dict[str, Any]:
    with _lock:
        if provider is not None:
            _state["provider"] = _sanitize_provider(provider)
        active_provider = _state["provider"]
        target = _state[active_provider]
        if model is not None and str(model).strip():
            target["model"] = str(model).strip()
        if base_url is not None and str(base_url).strip():
            target["base_url"] = str(base_url).strip()
        if api_key is not None:
            # allow empty to keep existing; explicit "-" clears
            if str(api_key).strip() == "-":
                target["api_key"] = ""
            elif str(api_key).strip():
                target["api_key"] = str(api_key).strip()
        if api_key_header is not None and str(api_key_header).strip():
            target["api_key_header"] = str(api_key_header).strip()
        if api_key_prefix is not None and str(api_key_prefix).strip():
            target["api_key_prefix"] = str(api_key_prefix).strip()
        return get_ai_config_public()


def resolve_active_ai_backend() -> dict[str, str]:
    with _lock:
        provider = _state["provider"]
        cfg = _state[provider]
        return {
            "provider": provider,
            "base_url": str(cfg.get("base_url") or "").strip(),
            "model": str(cfg.get("model") or "").strip(),
            "api_key": str(cfg.get("api_key") or "").strip(),
            "api_key_header": str(cfg.get("api_key_header") or "Authorization").strip() or "Authorization",
            "api_key_prefix": str(cfg.get("api_key_prefix") or "").strip(),
        }

