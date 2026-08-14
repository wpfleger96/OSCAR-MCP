"""Seed fake Google OAuth bindings for local dev testing of /admin/mcp reset flows.

Dev-only. Creates two rows that make the /admin/mcp binding-reset page exercisable
under ``just dev-auth`` where no real Google OAuth is configured:

1. **Admin binding** — attaches a fake Google identity to the ``dev@localhost``
   admin account, so the "Reset my binding" flow has something to reset.

2. **Password-less member** — creates ``nopass@localhost`` with only a Google
   identity and no password hash, exercising the disabled-Reset / skipped path
   for accounts that would lose all auth if their binding were reset.

Idempotent: safe to run repeatedly and after ``just reset`` + re-seed cycles.

Usage::

    uv run python scripts/seed_dev_google_bindings.py [DB_PATH]

``DB_PATH`` defaults to the standard dev database location.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from snore.constants import DEFAULT_DATABASE_PATH
from snore.database import models

DB = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATABASE_PATH


async def main() -> int:
    engine = create_async_engine(f"sqlite+aiosqlite:///{DB}")
    async with async_sessionmaker(engine)() as db:
        # --- Admin binding ---
        admin = (
            (
                await db.execute(
                    select(models.User).where(
                        models.User.canonical_email == "dev@localhost"
                    )
                )
            )
            .scalars()
            .first()
        )
        if admin is None:
            raise SystemExit(
                "dev@localhost not found — sign up via just dev-auth first"
            )

        admin_subject = f"fake-sub-admin-{admin.id}"
        existing_admin_identity = (
            (
                await db.execute(
                    select(models.AuthIdentity).where(
                        models.AuthIdentity.provider == "google",
                        models.AuthIdentity.subject == admin_subject,
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing_admin_identity is None:
            db.add(
                models.AuthIdentity(
                    user_id=admin.id,
                    provider="google",
                    subject=admin_subject,
                    email=admin.canonical_email,
                )
            )
            admin_status = "seeded"
        else:
            admin_status = "already present"

        # --- Password-less nopass@localhost member ---
        nopass = (
            (
                await db.execute(
                    select(models.User).where(
                        models.User.canonical_email == "nopass@localhost"
                    )
                )
            )
            .scalars()
            .first()
        )
        if nopass is None:
            nopass = models.User(
                canonical_email="nopass@localhost",
                display_name="No Password",
                role="member",
            )
            db.add(nopass)
            await db.flush()
            nopass_status = "seeded"
        else:
            nopass_status = "already present"

        # Capture id before commit — accessing expired ORM attrs after commit
        # raises MissingGreenlet under aiosqlite.
        admin_id = admin.id
        nopass_id = nopass.id

        existing_nopass_identity = (
            (
                await db.execute(
                    select(models.AuthIdentity).where(
                        models.AuthIdentity.provider == "google",
                        models.AuthIdentity.subject == "fake-sub-nopass",
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing_nopass_identity is None:
            db.add(
                models.AuthIdentity(
                    user_id=nopass_id,
                    provider="google",
                    subject="fake-sub-nopass",
                    email="nopass@localhost",
                )
            )
            nopass_identity_status = "seeded"
        else:
            nopass_identity_status = "already present"

        await db.commit()

    await engine.dispose()

    print(
        f"admin id={admin_id} google binding: {admin_status}\n"
        f"nopass@localhost id={nopass_id}: {nopass_status}\n"
        f"nopass@localhost google binding: {nopass_identity_status}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
