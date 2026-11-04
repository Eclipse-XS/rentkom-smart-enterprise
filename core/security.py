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


def get_optional_internal_or_admin_auth(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> Optional[AuthenticatedActor]:
    token = x_internal_token or x_admin_token or _extract_bearer(authorization)
    if not token:
        return None

    admin_key = get_effective_admin_key()
    internal_key = get_effective_internal_key()

    if admin_key and constant_time_equals(token, admin_key):
        return AuthenticatedActor(actor_id="admin_user", role="admin")
    if internal_key and constant_time_equals(token, internal_key):
        return AuthenticatedActor(actor_id="internal_service", role="internal_service")

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: invalid service or administrative credentials."
    )


import time
from collections import defaultdict
from threading import Lock
from fastapi import Request


def get_client_ip(request: Request) -> str:
    """
    Safely resolves client IP address:
    - Never unconditionally trusts client-supplied X-Forwarded-For headers.
    - Only inspects X-Forwarded-For if the direct peer (request.client.host) is explicitly
      listed in settings.TRUSTED_PROXIES.
    - Otherwise defaults to the verified socket peer address (request.client.host).
    """
    direct_ip = request.client.host if request.client else "unknown"
    trusted_proxies = getattr(settings, "TRUSTED_PROXIES", [])

    if direct_ip in trusted_proxies:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            forwarded_ip = forwarded.split(",")[0].strip()
            if forwarded_ip:
                return forwarded_ip

    return direct_ip


class InMemoryRateLimiter:
    """
    Thread-safe sliding window in-memory rate limiter for public API and RAG protection.

    NOTE ON PRODUCTION LIMITATIONS:
    This rate limiter maintains state in memory per Python process. In a multi-worker
    production deployment (e.g. Uvicorn with multiple workers or Kubernetes replicas behind
    a load balancer), rate limits are tracked per process rather than cluster-wide.
    For horizontal scalability across multiple processes or containers in high-scale
    production, replace this with a centralized Redis-backed token bucket or leaky bucket limiter.
    For single-instance, local portfolio, and evaluation environments, this in-memory implementation
    prevents single-node thread/CPU exhaustion without external service dependencies.
    """

    def __init__(self, requests_per_minute: int = 60, window_seconds: float = 60.0):
        self.rpm = requests_per_minute
        self.window = window_seconds
        self._history = defaultdict(list)
        self._lock = Lock()

    def acquire(self, client_ip: str) -> tuple[bool, int]:
        """
        Attempts to acquire a slot in the rate limit window for client_ip.
        Returns (is_allowed, retry_after_seconds).
        """
        now = time.time()
        with self._lock:
            cutoff = now - self.window
            timestamps = [t for t in self._history[client_ip] if t > cutoff]
            if len(timestamps) >= self.rpm:
                self._history[client_ip] = timestamps
                retry_after = max(1, int(timestamps[0] + self.window - now + 1))
                return False, retry_after
            timestamps.append(now)
            self._history[client_ip] = timestamps
            return True, 0

    def check(self, client_ip: str) -> bool:
        """Backwards-compatible boolean check."""
        allowed, _ = self.acquire(client_ip)
        return allowed

    def reset(self):
        with self._lock:
            self._history.clear()


public_rate_limiter = InMemoryRateLimiter(
    requests_per_minute=getattr(settings, "RATE_LIMIT_REQUESTS_PER_MINUTE", 60),
    window_seconds=60.0,
)

rag_rate_limiter = InMemoryRateLimiter(
    requests_per_minute=getattr(settings, "RATE_LIMIT_RAG_REQUESTS_PER_MINUTE", 10),
    window_seconds=60.0,
)


async def check_public_rate_limit(request: Request):
    """FastAPI dependency to throttle anonymous abuse on public ingress endpoints."""
    if not getattr(settings, "RATE_LIMIT_ENABLED", True):
        return

    client_ip = get_client_ip(request)
    allowed, retry_after = public_rate_limiter.acquire(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too Many Requests. Rate limit exceeded. Please try again later.",
            headers={"Retry-After": str(retry_after or 60)}
        )


async def check_rag_rate_limit(request: Request):
    """
    FastAPI dependency: strict rate limiting for computationally expensive RAG LLM queries.
    Prevents denial-of-service / resource exhaustion on local inference runtime (Ollama/Qdrant).
    """
    if not getattr(settings, "RATE_LIMIT_ENABLED", True):
        return

    client_ip = get_client_ip(request)
    allowed, retry_after = rag_rate_limiter.acquire(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too Many Requests. RAG rate limit exceeded for expensive LLM inference. Please try again later.",
            headers={"Retry-After": str(retry_after or 60)}
        )
