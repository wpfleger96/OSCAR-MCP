"""Immutable ActorContext — the single authorization contract for all execution paths.

Rules (enforced by the factory and tests):
- ONE factory: API middleware, CLI resolution, and import job workers all construct
  ActorContext through ActorContextFactory.make(); that is the only place profile
  ownership is validated.
- can_write is derived from role, never supplied at construction — demo + can_write=True
  is unrepresentable.
- mode carries the runtime auth configuration so services never need to check env vars.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"
    DEMO = "demo"


class AuthMode(StrEnum):
    LOCAL = "local"
    MULTIUSER = "multiuser"


@dataclass(frozen=True)
class ActorContext:
    """Immutable per-request/per-job authorization context.

    Attributes:
        user_id:    The authenticated user's database ID.
        profile_id: The active profile's database ID.
                    Validated: profile.user_id == user_id at construction.
        role:       User's role — determines can_write capability.
        mode:       Auth mode the server is running in.
    """

    user_id: int
    profile_id: int
    role: Role
    mode: AuthMode

    @property
    def can_write(self) -> bool:
        """True unless the actor is the demo role.

        Derived from role — never supplied at construction.
        demo + can_write=True is structurally unrepresentable.
        """
        return self.role is not Role.DEMO

    @property
    def is_admin(self) -> bool:
        """True if the actor has admin privileges."""
        return self.role is Role.ADMIN

    def __repr__(self) -> str:
        return (
            f"ActorContext(user_id={self.user_id}, profile_id={self.profile_id}, "
            f"role={self.role.value}, mode={self.mode.value}, can_write={self.can_write})"
        )
