"""Profile management API routes.

Supports: list, create, rename, set-default.
DELETION IS CLI-ONLY (requires exclusive writer lease; the running API always
holds it shared — do not add a DELETE route here, ever).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.deps import get_db
from snore.services.profile_service import ProfileNotFoundError, ProfileService

router = APIRouter()

DbDep = Annotated[AsyncSession, Depends(get_db)]


class ProfileResponse(BaseModel):
    id: int
    name: str
    user_id: int

    model_config = {"from_attributes": True}


class CreateProfileRequest(BaseModel):
    name: str


class RenameProfileRequest(BaseModel):
    name: str | None = None
    default: bool | None = None


@router.get("/", response_model=list[ProfileResponse])
async def list_profiles(request: object, db: DbDep) -> list[ProfileResponse]:
    """List all live profiles for the current user."""

    # Get user_id from request state (set by AuthMiddleware in Phase 2).
    # For Phase 1, fall back to a placeholder so the route is wirable.
    actor = getattr(request, "state", None)
    if actor is not None:
        actor = getattr(actor, "actor", None)
    if actor is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    svc = ProfileService(db)
    profiles = await svc.list_profiles(actor.user_id)
    return [ProfileResponse.model_validate(p) for p in profiles]


@router.post("/", response_model=ProfileResponse, status_code=201)
async def create_profile(
    body: CreateProfileRequest, request: object, db: DbDep
) -> ProfileResponse:
    actor = getattr(getattr(request, "state", None), "actor", None)
    if actor is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not actor.can_write:
        raise HTTPException(status_code=403, detail="Write access required")

    svc = ProfileService(db)
    try:
        profile = await svc.create_profile(actor.user_id, body.name)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail=f"A profile named '{body.name}' already exists"
        ) from exc
    return ProfileResponse.model_validate(profile)


@router.patch("/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: int, body: RenameProfileRequest, request: object, db: DbDep
) -> ProfileResponse:
    actor = getattr(getattr(request, "state", None), "actor", None)
    if actor is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not actor.can_write:
        raise HTTPException(status_code=403, detail="Write access required")

    svc = ProfileService(db)
    try:
        if body.name is not None:
            profile = await svc.rename_profile(actor.user_id, profile_id, body.name)
        elif body.default is True:
            profile = await svc.set_default_profile(actor.user_id, profile_id)
        else:
            raise HTTPException(
                status_code=422, detail="Provide 'name' or 'default: true'"
            )
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Profile not found") from exc
    except IntegrityError as err:
        raise HTTPException(
            status_code=409,
            detail=f"A profile named '{body.name}' already exists",
        ) from err
    return ProfileResponse.model_validate(profile)
