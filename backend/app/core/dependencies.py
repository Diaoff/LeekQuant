"""Shared FastAPI dependencies.

Centralizes request-level resolution that was previously duplicated across
``api/sim.py``, ``api/signals.py``, ``api/preferences.py`` and
``api/backtests.py`` (each re-implemented ``_extract_user_id``).

NOTE: this resolves the user identity from the ``X-User-ID`` header / query
param only. It does NOT perform authentication or authorization — that is the
scope of milestone M7. Centralizing here means M7 can later inject real auth
in exactly one place without touching every endpoint.
"""
from __future__ import annotations

from fastapi import Request
import logging
logger = logging.getLogger(__name__)

# Fallback identity used when no header/query is supplied. Kept for backward
# compatibility with the pre-auth state; M7 should reject unauthenticated
# requests instead of defaulting.
_DEFAULT_USER_ID = 1


def current_user_id(request: Request) -> int:
    """Resolve the acting user id from ``X-User-ID`` header or ``user_id`` query.

    Returns ``_DEFAULT_USER_ID`` (1) when neither is present or unparsable.
    Usable both as a plain callable (``current_user_id(request)``) and as a
    FastAPI dependency (``user_id: int = Depends(current_user_id)``).
    """
    user_id: int | None = None

    raw = request.headers.get("X-User-ID") or request.query_params.get("user_id")
    if raw:
        try:
            user_id = int(raw)
        except (ValueError, TypeError):
            logger.debug("silent except in current_user_id")
            user_id = None

    return user_id or _DEFAULT_USER_ID
