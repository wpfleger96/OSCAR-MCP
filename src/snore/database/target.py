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

URL handling uses SQLAlchemy ``make_url()`` / ``URL.set(drivername=...)``
throughout — no hand-rolled string splitting.  SQLite file paths are always
derived from ``URL.database``, not from slash-counting.
"""

from __future__ import annotations

import logging
import os

from dataclasses import dataclass, field

from sqlalchemy.engine import make_url
from sqlalchemy.engine.url import URL

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

_CAPABILITY_ERROR = (
    "PostgreSQL support requires a driver that is not installed. "
    "The postgresql dialect is recognised but not yet supported — "
    "it will be enabled at the hosted milestone."
)


@dataclass(frozen=True)
class DatabaseTarget:
    """Parsed, normalised database connection descriptor.

    Attributes:
        dialect: ``"sqlite"`` or ``"postgresql"``.
        location: File path (SQLite) or ``host:port/dbname`` string (PostgreSQL).
        raw_url: The original URL or path string before normalisation.
    """

    dialect: str
    location: str
    raw_url: str
    # Internal: the canonical SQLAlchemy URL object used for resolution.
    # Excluded from equality/repr so tests can compare by dialect+location+raw_url.
    _parsed_url: URL = field(default=None, compare=False, repr=False)  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_url(cls, url: str) -> DatabaseTarget:
        """Parse a database URL or bare file path into a ``DatabaseTarget``.

        Driver qualifiers (e.g. ``sqlite+pysqlite://``) are normalised to the
        canonical dialect.  Bare file paths (no ``://``) are treated as SQLite
        paths.  Unrecognised dialects raise ``ValueError`` at parse time.

        Args:
            url: Database URL or bare file path string.

        Returns:
            Parsed ``DatabaseTarget``.

        Raises:
            ValueError: If the dialect is not recognised.
        """
        # Bare path (no scheme) → treat as SQLite file path.
        if "://" not in url:
            parsed = URL.create("sqlite+pysqlite", database=url)
            return cls(
                dialect="sqlite",
                location=url,
                raw_url=url,
                _parsed_url=parsed,
            )

        # Parse with SQLAlchemy so we get correct dialect extraction even for
        # driver-qualified forms like ``sqlite+pysqlite://...``.
        try:
            parsed = make_url(url)
        except Exception as exc:
            raise ValueError(f"Invalid database URL {url!r}: {exc}") from exc

        # Strip the driver qualifier to get the bare dialect.
        dialect = parsed.get_backend_name()  # "sqlite" from "sqlite+pysqlite"

        if dialect not in _RECOGNISED_DIALECTS:
            raise ValueError(
                f"Unrecognised database dialect {dialect!r}. "
                f"Supported: {sorted(_RECOGNISED_DIALECTS)}"
            )

        if dialect == "sqlite":
            # URL.database is the canonical path component after stripping
            # scheme and authority — covers relative, absolute, and :memory:.
            # For sqlite:///relative.db  → database = "relative.db"
            # For sqlite:////abs/path    → database = "/abs/path"
            # For sqlite:///:memory:     → database = ":memory:"
            location = parsed.database or ""
        else:
            # PostgreSQL: reconstruct host/port/dbname without credentials.
            host = parsed.host or ""
            port = f":{parsed.port}" if parsed.port else ""
            db = parsed.database or ""
            location = f"{host}{port}/{db}" if db else f"{host}{port}"

        return cls(
            dialect=dialect,
            location=location,
            raw_url=url,
            _parsed_url=parsed,
        )

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

        Uses ``URL.set(drivername=...)`` to insert the correct driver without
        touching any other URL component.

        Returns:
            SQLAlchemy URL string usable with ``create_engine()``.

        Raises:
            RuntimeError: If the dialect is recognised but its sync driver is
                not installed in this milestone.  No credentials appear in the
                error message.
        """
        if self.dialect not in _SYNC_RUNTIME_DRIVERS:
            raise RuntimeError(_CAPABILITY_ERROR)
        driver = _SYNC_RUNTIME_DRIVERS[self.dialect]
        resolved = self._parsed_url.set(drivername=f"{self.dialect}+{driver}")
        return str(resolved)

    def resolve_migration_url(self) -> str:
        """Return the Alembic migration URL for this target.

        Same capability-gating as ``resolve_sync_url()``.
        """
        if self.dialect not in _MIGRATION_DRIVERS:
            raise RuntimeError(_CAPABILITY_ERROR)
        driver = _MIGRATION_DRIVERS[self.dialect]
        resolved = self._parsed_url.set(drivername=f"{self.dialect}+{driver}")
        return str(resolved)

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
