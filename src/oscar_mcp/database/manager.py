"""
Database manager for OSCAR-MCP SQLite database.

Handles database initialization, transactions, and core operations.
Auto-creates database on first use.
"""

import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Dict, Any

from oscar_mcp.models.unified import DeviceInfo

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages SQLite database for CPAP data storage."""

    DEFAULT_DB_NAME = "oscar_mcp.db"

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize database manager.

        Args:
            db_path: Path to SQLite database file. If None, uses default location.
        """
        self.db_path = db_path or self._get_default_path()
        self.db_path = Path(self.db_path)
        self._connection = None

        # Ensure database exists and is initialized
        self.ensure_database()

    def _get_default_path(self) -> Path:
        """Get default database path in user's home directory."""
        home = Path.home()
        oscar_dir = home / ".oscar-mcp"
        oscar_dir.mkdir(parents=True, exist_ok=True)
        return oscar_dir / self.DEFAULT_DB_NAME

    def ensure_database(self):
        """Ensure database exists and is properly initialized."""
        if not self.db_path.exists():
            logger.info(f"Creating new database at {self.db_path}")
            self._create_database()
        else:
            logger.debug(f"Using existing database at {self.db_path}")

        # Verify database is valid
        self._verify_database()

    def _create_database(self):
        """Create new database with schema."""
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Read schema from file
        schema_path = Path(__file__).parent / "schema.sql"
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_path}")

        with open(schema_path, "r") as f:
            schema_sql = f.read()

        # Execute schema
        with self.get_connection() as conn:
            conn.executescript(schema_sql)
            conn.commit()

        logger.info("Database created successfully")

    def _verify_database(self):
        """Verify database has required tables."""
        required_tables = ["devices", "sessions", "waveforms", "events", "statistics"]

        with self.get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing_tables = {row[0] for row in cursor.fetchall()}

        missing_tables = set(required_tables) - existing_tables
        if missing_tables:
            raise RuntimeError(
                f"Database is missing required tables: {missing_tables}. "
                f"Delete {self.db_path} and restart to recreate."
            )

    def get_connection(self) -> sqlite3.Connection:
        """
        Get database connection with optimizations enabled.

        Returns:
            SQLite connection
        """
        if self._connection is None:
            self._connection = sqlite3.connect(
                self.db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
            )
            # Enable foreign keys
            self._connection.execute("PRAGMA foreign_keys = ON")
            # Use WAL mode for better concurrency
            self._connection.execute("PRAGMA journal_mode = WAL")
            # Row factory for dict-like access
            self._connection.row_factory = sqlite3.Row

        return self._connection

    @contextmanager
    def transaction(self):
        """
        Context manager for database transactions.

        Usage:
            with db.transaction() as conn:
                conn.execute(...)
        """
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Transaction failed: {e}")
            raise

    def close(self):
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    # =========================================================================
    # Device Management
    # =========================================================================

    def upsert_device(self, device_info: DeviceInfo) -> int:
        """
        Insert or update device information.

        Args:
            device_info: DeviceInfo object

        Returns:
            Device ID
        """
        with self.transaction() as conn:
            # Try to find existing device
            cursor = conn.execute(
                "SELECT id FROM devices WHERE serial_number = ?", (device_info.serial_number,)
            )
            row = cursor.fetchone()

            if row:
                # Update existing device
                device_id = row["id"]
                conn.execute(
                    """
                    UPDATE devices
                    SET manufacturer = ?,
                        model = ?,
                        firmware_version = ?,
                        hardware_version = ?,
                        product_code = ?,
                        last_import = CURRENT_TIMESTAMP
                    WHERE id = ?
                """,
                    (
                        device_info.manufacturer,
                        device_info.model,
                        device_info.firmware_version,
                        device_info.hardware_version,
                        device_info.product_code,
                        device_id,
                    ),
                )
                logger.debug(f"Updated device {device_id}")
            else:
                # Insert new device
                cursor = conn.execute(
                    """
                    INSERT INTO devices (
                        manufacturer, model, serial_number,
                        firmware_version, hardware_version, product_code,
                        last_import
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                    (
                        device_info.manufacturer,
                        device_info.model,
                        device_info.serial_number,
                        device_info.firmware_version,
                        device_info.hardware_version,
                        device_info.product_code,
                    ),
                )
                device_id = cursor.lastrowid
                logger.info(
                    f"Inserted new device {device_id}: {device_info.manufacturer} {device_info.model}"
                )

            return device_id

    # =========================================================================
    # Session Management
    # =========================================================================

    def session_exists(self, device_id: int, device_session_id: str) -> bool:
        """
        Check if session already exists.

        Args:
            device_id: Device ID
            device_session_id: Device-specific session ID

        Returns:
            True if session exists
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM sessions WHERE device_id = ? AND device_session_id = ?",
                (device_id, device_session_id),
            )
            return cursor.fetchone() is not None

    def get_session_id(self, device_id: int, device_session_id: str) -> Optional[int]:
        """
        Get session ID by device and device_session_id.

        Args:
            device_id: Device ID
            device_session_id: Device-specific session ID

        Returns:
            Session ID or None
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM sessions WHERE device_id = ? AND device_session_id = ?",
                (device_id, device_session_id),
            )
            row = cursor.fetchone()
            return row["id"] if row else None

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """
        Get database statistics.

        Returns:
            Dict with database stats
        """
        with self.get_connection() as conn:
            stats = {}

            # Count tables
            cursor = conn.execute("SELECT COUNT(*) FROM devices")
            stats["devices"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM sessions")
            stats["sessions"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM events")
            stats["events"] = cursor.fetchone()[0]

            # Database size
            stats["size_bytes"] = self.db_path.stat().st_size
            stats["size_mb"] = stats["size_bytes"] / (1024 * 1024)

            # Date range
            cursor = conn.execute("SELECT MIN(start_time), MAX(start_time) FROM sessions")
            row = cursor.fetchone()
            stats["first_session"] = row[0]
            stats["last_session"] = row[1]

            return stats
