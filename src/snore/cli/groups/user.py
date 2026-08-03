"""User management CLI commands (global operator boundary — no ActorContext)."""

from __future__ import annotations

import asyncio
import hashlib
import secrets

from datetime import UTC, datetime, timedelta

import click

from snore.cli.decorators import db_option, db_session
from snore.cli.display import console, print_error, print_success

INVITE_TTL_DAYS = 7


@click.group()
def user() -> None:
    """User and invite management (operator commands)."""
    pass


@user.command("list")
@db_option
def user_list(db: str | None) -> None:
    """List all users."""

    async def _run() -> None:
        async with db_session(db) as session:
            from sqlalchemy import select  # noqa: PLC0415

            from snore.database.models import User  # noqa: PLC0415

            users = list(
                (await session.execute(select(User).order_by(User.id))).scalars().all()
            )
            if not users:
                console.print("No users found.")
                return
            for u in users:
                status = "disabled" if u.disabled_at else "active"
                console.print(
                    f"  [{u.id}] {u.canonical_email}  role={u.role}  status={status}"
                )

    asyncio.run(_run())


@user.command("create")
@click.argument("email")
@click.option(
    "--role",
    type=click.Choice(["admin", "member"]),
    default="member",
    show_default=True,
)
@click.option("--display-name", default=None)
@db_option
def user_create(
    email: str, role: str, display_name: str | None, db: str | None
) -> None:
    """Create a user directly (no invite flow). Sets no password — user must log in via Google or an admin must set one."""

    async def _run() -> None:
        async with db_session(db) as session:
            from snore.database.models import Profile, User  # noqa: PLC0415

            canonical = email.strip().lower()
            u = User(
                canonical_email=canonical,
                display_name=display_name or canonical.split("@")[0],
                role=role,
            )
            session.add(u)
            await session.flush()

            # Create a default profile.
            p = Profile(user_id=u.id, name="Default")
            session.add(p)
            await session.flush()
            u.default_profile_id = p.id

        print_success(f"Created user {canonical} (id={u.id}) with profile {p.id}")

    asyncio.run(_run())


@user.command("disable")
@click.argument("email")
@db_option
def user_disable(email: str, db: str | None) -> None:
    """Disable a user account (they can no longer log in)."""

    async def _run() -> None:
        async with db_session(db) as session:
            from sqlalchemy import select  # noqa: PLC0415

            from snore.database.models import User  # noqa: PLC0415

            canonical = email.strip().lower()
            u = (
                (
                    await session.execute(
                        select(User).where(User.canonical_email == canonical)
                    )
                )
                .scalars()
                .first()
            )
            if u is None:
                print_error(f"User {canonical!r} not found")
                return
            u.disabled_at = datetime.now(UTC)
            u.session_version += 1  # Invalidate all existing sessions.

        print_success(f"Disabled user {canonical}")

    asyncio.run(_run())


@user.command("invite")
@click.argument("email")
@click.option(
    "--role",
    type=click.Choice(["admin", "member"]),
    default="member",
    show_default=True,
)
@click.option(
    "--ttl-days",
    type=int,
    default=INVITE_TTL_DAYS,
    show_default=True,
    help="Invite expiry in days",
)
@click.option(
    "--created-by",
    type=int,
    default=None,
    help="User ID of the inviting admin (optional)",
)
@db_option
def user_invite(
    email: str, role: str, ttl_days: int, created_by: int | None, db: str | None
) -> None:
    """Create an invite link for a new user.

    Prints the full redemption URL (copy it and send to the invitee).
    The invite token is only shown once.
    """

    async def _run() -> None:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        async with db_session(db) as session:
            from snore.database.models import Invite  # noqa: PLC0415

            canonical = email.strip().lower()
            inv = Invite(
                email=canonical,
                token_hash=token_hash,
                role=role,
                created_by=created_by,
                expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
            )
            session.add(inv)

        import os  # noqa: PLC0415

        base_url = os.environ.get(
            "SNORE_PUBLIC_BASE_URL", "http://localhost:8000"
        ).rstrip("/")
        console.print(f"\n✉  Invite URL for {canonical}:")
        console.print(f"   {base_url}/api/v1/auth/invites/{raw_token}")
        console.print(f"\n   Role: {role}  |  Expires: {ttl_days} days from now")
        console.print("\n   ⚠  This token is shown only once. Send it securely.")

    asyncio.run(_run())


@user.command("invite-revoke")
@click.argument("email")
@db_option
def invite_revoke(email: str, db: str | None) -> None:
    """Revoke all pending invites for an email address."""

    async def _run() -> None:
        async with db_session(db) as session:
            from sqlalchemy import select  # noqa: PLC0415

            from snore.database.models import Invite  # noqa: PLC0415

            canonical = email.strip().lower()
            stmt = select(Invite).where(
                Invite.email == canonical,
                Invite.redeemed_at.is_(None),
                Invite.revoked_at.is_(None),
            )
            invites = list((await session.execute(stmt)).scalars().all())
            now = datetime.now(UTC)
            for inv in invites:
                inv.revoked_at = now

        count = len(invites)
        if count:
            print_success(f"Revoked {count} invite(s) for {canonical}")
        else:
            console.print(f"No pending invites found for {canonical}")

    asyncio.run(_run())
