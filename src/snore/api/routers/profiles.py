"""Profile management API routes.

Supports: list, create, rename, set-default.
DELETION IS CLI-ONLY (requires exclusive writer lease; the running API always
holds it shared — do not add a DELETE route here, ever).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.deps import get_db
from snore.api.guards import RequireAuth, RequireWritable
from snore.database import models
from snore.services.profile_service import ProfileNotFoundError, ProfileService

router = APIRouter()

DbDep = Annotated[AsyncSession, Depends(get_db)]


class ProfileResponse(BaseModel):
    id: int
    name: str
    user_id: int
    created_at: datetime
    is_default: bool

    model_config = {"from_attributes": True}


class CreateProfileRequest(BaseModel):
    name: str


class RenameProfileRequest(BaseModel):
    name: str | None = None
    default: bool | None = None


async def _get_default_profile_id(db: AsyncSession, user_id: int) -> int | None:
    """Return the user's default_profile_id. User always exists per ActorContextFactory."""
    user = await db.get(models.User, user_id)
    assert user is not None  # noqa: S101 — ActorContextFactory guarantees existence
    return user.default_profile_id


@router.get("/", response_model=list[ProfileResponse])
async def list_profiles(actor: RequireAuth, db: DbDep) -> list[ProfileResponse]:
    """List all live profiles for the current user."""
    svc = ProfileService(db)
    profiles = await svc.list_profiles(actor.user_id)
    default_id = await _get_default_profile_id(db, actor.user_id)
    return [
        ProfileResponse(
            id=p.id,
            name=p.name,
            user_id=p.user_id,
            created_at=p.created_at,
            is_default=(p.id == default_id),
        )
        for p in profiles
    ]


@router.post("/", response_model=ProfileResponse, status_code=201)
async def create_profile(
    body: CreateProfileRequest, actor: RequireWritable, db: DbDep
) -> ProfileResponse:
    svc = ProfileService(db)
    try:
        profile = await svc.create_profile(actor.user_id, body.name)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail=f"A profile named '{body.name}' already exists"
        ) from exc
    default_id = await _get_default_profile_id(db, actor.user_id)
    return ProfileResponse(
        id=profile.id,
        name=profile.name,
        user_id=profile.user_id,
        created_at=profile.created_at,
        is_default=(profile.id == default_id),
    )


@router.patch("/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: int, body: RenameProfileRequest, actor: RequireWritable, db: DbDep
) -> ProfileResponse:
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
    default_id = await _get_default_profile_id(db, actor.user_id)
    return ProfileResponse(
        id=profile.id,
        name=profile.name,
        user_id=profile.user_id,
        created_at=profile.created_at,
        is_default=(profile.id == default_id),
    )
