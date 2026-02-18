from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from snore.exceptions import NotFoundError

__all__ = ["NotFoundError", "not_found_handler", "server_error_handler"]


class ErrorResponse(BaseModel):
    error: str
    message: str


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
