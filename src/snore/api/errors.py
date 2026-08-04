from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from snore.exceptions import NotFoundError

__all__ = [
    "NotFoundError",
    "auth_validation_error_handler",
    "not_found_handler",
    "server_error_handler",
]

_AUTH_PATH_PREFIX = "/api/v1/auth"


class ErrorResponse(BaseModel):
    error: str
    message: str


async def auth_validation_error_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Return 422 errors on auth routes without echoing submitted credential values.

    Pydantic's default ``RequestValidationError`` serialization includes an
    ``input`` field containing the rejected value verbatim.  On auth endpoints
    this exposes tokens and passwords in error bodies, contradicting the
    credential-hygiene invariant.  This handler strips ``input`` from each
    error and applies ``Cache-Control: no-store``.

    Non-auth paths fall through to FastAPI's default handler.
    """
    if not request.url.path.startswith(_AUTH_PATH_PREFIX):
        if isinstance(exc, RequestValidationError):
            return await request_validation_exception_handler(request, exc)
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    sanitized = []
    if isinstance(exc, RequestValidationError):
        sanitized = [
            {k: v for k, v in error.items() if k != "input"} for error in exc.errors()
        ]
    return JSONResponse(
        status_code=422,
        content={"detail": sanitized},
        headers={"Cache-Control": "no-store"},
    )


async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(error="not_found", message=str(exc)).model_dump(),
    )


async def server_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="server_error", message="Internal server error"
        ).model_dump(),
    )
