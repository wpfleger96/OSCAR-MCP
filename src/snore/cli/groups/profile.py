"""Profile management CLI commands."""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, Any

import click

from snore.cli.decorators import db_option, db_session
from snore.cli.display import console, print_error, print_success

if TYPE_CHECKING:
    from snore.database.models import User


def _resolve_user_id(email_or_id: str | None, db_session_ctx: object) -> None:
    """Helper note: user resolution is async — see commands below."""
    pass


@click.group()
def profile() -> None:
    """Profile management commands."""
    pass


@profile.command("list")
@click.option(
    "--user",
    "user_email",
    default=None,
    help="User email (defaults to sole user in local mode)",
)
@db_option
def profile_list(user_email: str | None, db: str | None) -> None:
    """List profiles for a user."""

    async def _run() -> None:
        async with db_session(db) as session:
            from sqlalchemy import select  # noqa: PLC0415

            from snore.database.models import Profile  # noqa: PLC0415

            user = await _resolve_user(session, user_email)
            if user is None:
                return

            stmt = (
                select(Profile)
                .where(Profile.user_id == user.id, Profile.deleting_at.is_(None))
                .order_by(Profile.id)
            )
            profiles = list((await session.execute(stmt)).scalars().all())
            if not profiles:
                console.print("No profiles found.")
                return
            default_id = user.default_profile_id
            for p in profiles:
                marker = " [default]" if p.id == default_id else ""
                deleting = " [DELETING]" if p.deleting_at else ""
                console.print(f"  [{p.id}] {p.name}{marker}{deleting}")

    asyncio.run(_run())


@profile.command("create")
@click.argument("name")
@click.option("--user", "user_email", default=None, help="User email")
@db_option
def profile_create(name: str, user_email: str | None, db: str | None) -> None:
    """Create a new profile for a user."""

    async def _run() -> None:
        async with db_session(db) as session:
            from snore.database.models import Profile  # noqa: PLC0415

            user = await _resolve_user(session, user_email)
            if user is None:
                return

            p = Profile(user_id=user.id, name=name)
            session.add(p)
            await session.flush()

            if user.default_profile_id is None:
                user.default_profile_id = p.id

        print_success(f"Created profile '{name}' (id={p.id})")

    asyncio.run(_run())


@profile.command("rename")
@click.argument("profile_id", type=int)
@click.argument("new_name")
@click.option("--user", "user_email", default=None, help="User email")
@db_option
def profile_rename(
    profile_id: int, new_name: str, user_email: str | None, db: str | None
) -> None:
    """Rename a profile."""

    async def _run() -> None:
        async with db_session(db) as session:
            from sqlalchemy import select  # noqa: PLC0415

            from snore.database.models import Profile  # noqa: PLC0415

            user = await _resolve_user(session, user_email)
            if user is None:
                return

            p = (
                (
                    await session.execute(
                        select(Profile).where(
                            Profile.id == profile_id,
                            Profile.user_id == user.id,
                            Profile.deleting_at.is_(None),
                        )
                    )
                )
                .scalars()
                .first()
            )
            if p is None:
                print_error(f"Profile {profile_id} not found")
                return
            p.name = new_name

        print_success(f"Renamed profile {profile_id} to '{new_name}'")

    asyncio.run(_run())


@profile.command("set-default")
@click.argument("profile_id", type=int)
@click.option("--user", "user_email", default=None, help="User email")
@db_option
def profile_set_default(
    profile_id: int, user_email: str | None, db: str | None
) -> None:
    """Set a profile as the default for a user."""

    async def _run() -> None:
        async with db_session(db) as session:
            from sqlalchemy import select  # noqa: PLC0415

            from snore.database.models import Profile  # noqa: PLC0415

            user = await _resolve_user(session, user_email)
            if user is None:
                return

            p = (
                (
                    await session.execute(
                        select(Profile).where(
                            Profile.id == profile_id,
                            Profile.user_id == user.id,
                            Profile.deleting_at.is_(None),
                        )
                    )
                )
                .scalars()
                .first()
            )
            if p is None:
                print_error(f"Profile {profile_id} not found")
                return
            user.default_profile_id = p.id

        print_success(f"Set profile {profile_id} as default")

    asyncio.run(_run())


@profile.command("delete")
@click.argument("profile_id", type=int)
@click.option("--user", "user_email", default=None, help="User email")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
@db_option
def profile_delete(
    profile_id: int, user_email: str | None, yes: bool, db: str | None
) -> None:
    """Delete a profile (offline operation — API server must not be running).

    This command requires the exclusive writer lease.  The API server holds
    the shared lease for its entire lifetime, so this command will fail while
    the server is running.  Stop the server first, then run this command.
    """
    if not yes:
        click.confirm(
            f"Delete profile {profile_id}? This will remove all associated data. "
            "Ensure the API server is not running.",
            abort=True,
        )

    async def _get_user_id() -> int | None:
        async with db_session(db) as session:
            user = await _resolve_user(session, user_email)
            return user.id if user else None

    user_id = asyncio.run(_get_user_id())
    if user_id is None:
        return

    from snore.services.profile_service import (  # noqa: PLC0415
        DeletionSaga,
        ProfileLastError,
        ProfileNotFoundError,
    )
    from snore.services.writer_lease import WriterLeaseError  # noqa: PLC0415

    saga = DeletionSaga()
    try:
        saga.delete_profile(profile_id, user_id)
        print_success(f"Profile {profile_id} deleted successfully")
    except WriterLeaseError as e:
        print_error(str(e))
    except ProfileLastError as e:
        print_error(str(e))
    except ProfileNotFoundError as e:
        print_error(str(e))


async def _resolve_user(session: Any, user_email: str | None) -> User | None:
    """Resolve a user by email or fall back to the sole user in the DB.

    Returns None and prints an error if resolution is ambiguous.
    """
    from typing import cast  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from snore.database.models import User as _User  # noqa: PLC0415

    if user_email is not None:
        canonical = user_email.strip().lower()
        u = cast(
            "_User | None",
            (
                (
                    await session.execute(
                        select(_User).where(_User.canonical_email == canonical)
                    )
                )
                .scalars()
                .first()
            ),
        )
        if u is None:
            print_error(f"User {canonical!r} not found")
        return u

    # No email specified: require exactly one user (fails rather than guessing).
    all_users = cast(
        "list[_User]",
        list((await session.execute(select(_User).order_by(_User.id))).scalars().all()),
    )
    if len(all_users) == 0:
        print_error("No users found. Create one with 'snore user create'.")
        return None
    if len(all_users) > 1:
        print_error(
            f"Multiple users found ({len(all_users)}). Specify one with --user <email>."
        )
        return None
    return all_users[0]
