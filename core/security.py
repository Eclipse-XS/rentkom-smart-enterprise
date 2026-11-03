import secrets
from typing import Optional
from fastapi import Header, HTTPException, status
from core.config import settings


def constant_time_equals(val1: Optional[str], val2: Optional[str]) -> bool:
    """Timing-safe comparison of secret tokens."""
    if not val1 or not val2:
        return False
    return secrets.compare_digest(val1.encode("utf-8"), val2.encode("utf-8"))


def get_effective_admin_key() -> Optional[str]:
    if settings.ADMIN_API_KEY:
        return settings.ADMIN_API_KEY
    return None


def get_effective_internal_key() -> Optional[str]:
    if settings.INTERNAL_API_KEY:
        return settings.INTERNAL_API_KEY
    return None


def get_effective_test_secret() -> Optional[str]:
    if settings.TEST_WEBHOOK_SECRET:
        return settings.TEST_WEBHOOK_SECRET
    return None


class AuthenticatedActor:
    def __init__(self, actor_id: str, role: str):
        self.actor_id = actor_id
        self.role = role


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization.strip()


def require_admin_auth(
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> AuthenticatedActor:
    admin_key = get_effective_admin_key()
    if not admin_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server security misconfiguration: ADMIN_API_KEY is not configured."
        )

    token = x_admin_token or _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: missing administrative authorization."
        )
    if not constant_time_equals(token, admin_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: invalid administrative credentials."
        )
    return AuthenticatedActor(actor_id="admin_user", role="admin")


def require_internal_or_admin_auth(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> AuthenticatedActor:
    internal_key = get_effective_internal_key()
    admin_key = get_effective_admin_key()

    if not (internal_key or admin_key):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server security misconfiguration: INTERNAL_API_KEY / ADMIN_API_KEY not configured."
        )

    token = x_internal_token or x_admin_token or _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: missing service or administrative authorization."
        )

    if admin_key and constant_time_equals(token, admin_key):
        return AuthenticatedActor(actor_id="admin_user", role="admin")
    if internal_key and constant_time_equals(token, internal_key):
        return AuthenticatedActor(actor_id="internal_service", role="internal_service")

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: invalid service or administrative credentials."
    )
