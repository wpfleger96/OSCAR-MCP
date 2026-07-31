"""DatabaseTarget: parsed, capability-gated database connection descriptor.

Precedence chain (highest first):
    --db CLI flag  >  SNORE_DATABASE_URL  >  SNORE_DB_PATH  >  default SQLite path

Resolution is capability-gated per operation:

- PR-1 (sync): SQLite only.  ``sqlite → pysqlite`` for runtime and migration.
- PostgreSQL targets are *recognised* at parse time but capability-gated at
  resolution: a sanitised error is raised; no credentials appear in any
  message.
- The postgresql → (asyncpg, psycopg) mapping is declared here but not
  installed until the hosted milestone.
"""

from __future__ import annotations

import logging
import os

from dataclasses import dataclass
from urllib.parse import urlparse

from snore.constants import DEFAULT_DATABASE_PATH

logger = logging.getLogger(__name__)

# Dialect → sync-runtime driver (installed in PR-1)
_SYNC_RUNTIME_DRIVERS: dict[str, str] = {
    "sqlite": "pysqlite",
}

# Dialect → migration driver (installed in PR-1)
_MIGRATION_DRIVERS: dict[str, str] = {
    "sqlite": "pysqlite",
}

# Declared for future milestones — NOT installed.
_FUTURE_ASYNC_DRIVERS: dict[str, str] = {
    "sqlite": "aiosqlite",  # added in PR-2
    "postgresql": "asyncpg",  # hosted milestone
}

_FUTURE_MIGRATION_DRIVERS: dict[str, str] = {
    "postgresql": "psycopg",  # hosted milestone
}

_RECOGNISED_DIALECTS = frozenset({"sqlite", "postgresql"})


@dataclass(frozen=True)
class DatabaseTarget:
    """Parsed, normalised database connection descriptor.

    Attributes:
        dialect: ``"sqlite"`` or ``"postgresql"``.
        location: File path (SQLite) or host:port/dbname string (PostgreSQL).
        raw_url: The original URL or path string before normalisation.
    """

    dialect: str
    location: str
    raw_url: str

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_url(cls, url: str) -> DatabaseTarget:
        """Parse a database URL or bare file path into a ``DatabaseTarget``.

        Driver qualifiers (e.g. ``sqlite+pysqlite://``) are stripped and
        normalised to the canonical dialect.  Unrecognised dialects raise
        ``ValueError`` at parse time.

        Args:
            url: Database URL or bare file path string.

        Returns:
            Parsed ``DatabaseTarget``.

        Raises:
            ValueError: If the dialect is not recognised.
        """
        # Bare path (no scheme) → sqlite URL
        if "://" not in url:
            return cls(dialect="sqlite", location=url, raw_url=url)

        # Strip driver qualifier: "sqlite+pysqlite://..." → "sqlite://..."
        if "+" in url.split("://")[0]:
            scheme_part, rest = url.split("://", 1)
            dialect = scheme_part.split("+")[0].lower()
            url = f"{dialect}://{rest}"
        else:
            dialect = urlparse(url).scheme.lower()

        if dialect not in _RECOGNISED_DIALECTS:
            raise ValueError(
                f"Unrecognised database dialect {dialect!r}. "
                f"Supported: {sorted(_RECOGNISED_DIALECTS)}"
            )

        # Extract location from URL
        parsed = urlparse(url)
        if dialect == "sqlite":
            # sqlite:///path/to/db → location = "path/to/db" (or absolute path)
            location = parsed.path.lstrip("/") if parsed.path else ""
            if parsed.netloc:
                location = (
                    f"/{parsed.netloc}{parsed.path}"
                    if parsed.path
                    else f"/{parsed.netloc}"
                )
            # Handle absolute path: sqlite:////abs/path → path = //abs/path
            raw_path = url.split("sqlite://", 1)[1]
            location = raw_path
        else:
            location = f"{parsed.netloc}{parsed.path}"

        return cls(dialect=dialect, location=location, raw_url=url)

    @classmethod
    def from_env_and_flags(
        cls,
        *,
        db_flag: str | None = None,
        warn_ignored: bool = True,
    ) -> DatabaseTarget:
        """Resolve a ``DatabaseTarget`` from the precedence chain.

        Chain (highest first):
            ``db_flag``  >  ``SNORE_DATABASE_URL``  >  ``SNORE_DB_PATH``  >  default

        Args:
            db_flag: Value of the ``--db`` CLI flag, or ``None``.
            warn_ignored: If ``True``, log warnings for lower-precedence inputs
                          that are overridden by a higher-precedence source.

        Returns:
            Resolved ``DatabaseTarget``.
        """
        database_url = os.environ.get("SNORE_DATABASE_URL")
        db_path = os.environ.get("SNORE_DB_PATH")

        if db_flag is not None:
            if warn_ignored:
                if database_url:
                    logger.warning(
                        "SNORE_DATABASE_URL is set but ignored because --db was provided"
                    )
                if db_path:
                    logger.warning(
                        "SNORE_DB_PATH is set but ignored because --db was provided"
                    )
            return cls.from_url(db_flag)

        if database_url is not None:
            if warn_ignored and db_path:
                logger.warning(
                    "SNORE_DB_PATH is set but ignored because SNORE_DATABASE_URL was provided"
                )
            return cls.from_url(database_url)

        if db_path is not None:
            return cls.from_url(db_path)

        return cls.from_url(DEFAULT_DATABASE_PATH)

    # ------------------------------------------------------------------
    # Resolution (capability-gated)
    # ------------------------------------------------------------------

    def resolve_sync_url(self) -> str:
        """Return the sync SQLAlchemy URL for this target.

        Returns:
            SQLAlchemy URL string usable with ``create_engine()``.

        Raises:
            RuntimeError: If the dialect is recognised but its sync driver is
                not installed in this milestone.  No credentials appear in the
                error message.
        """
        if self.dialect not in _SYNC_RUNTIME_DRIVERS:
            raise RuntimeError(
                "PostgreSQL support requires a driver that is not installed. "
                "The postgresql dialect is recognised but not yet supported — "
                "it will be enabled at the hosted milestone."
            )
        driver = _SYNC_RUNTIME_DRIVERS[self.dialect]
        if self.dialect == "sqlite":
            return f"sqlite+{driver}:///{self.location}"
        return f"{self.dialect}+{driver}://{self.location}"

    def resolve_migration_url(self) -> str:
        """Return the Alembic migration URL for this target.

        Same capability-gating as ``resolve_sync_url()``.
        """
        if self.dialect not in _MIGRATION_DRIVERS:
            raise RuntimeError(
                "PostgreSQL support requires a driver that is not installed. "
                "The postgresql dialect is recognised but not yet supported — "
                "it will be enabled at the hosted milestone."
            )
        driver = _MIGRATION_DRIVERS[self.dialect]
        if self.dialect == "sqlite":
            return f"sqlite+{driver}:///{self.location}"
        return f"{self.dialect}+{driver}://{self.location}"

    @property
    def is_sqlite(self) -> bool:
        """True if this target is a SQLite database."""
        return self.dialect == "sqlite"

    @property
    def sqlite_path(self) -> str:
        """File path for SQLite targets.

        Raises:
            ValueError: If this is not a SQLite target.
        """
        if not self.is_sqlite:
            raise ValueError(
                f"sqlite_path is only valid for sqlite targets, got {self.dialect!r}"
            )
        return self.location
