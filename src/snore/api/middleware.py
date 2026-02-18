from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class AuthMiddleware(BaseHTTPMiddleware):
    """No-op auth middleware stub. Replace body with real JWT/session validation."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request.state.user = None
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """No-op rate limit stub. Swap in slowapi or similar when needed."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        return await call_next(request)
