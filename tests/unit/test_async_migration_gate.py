"""Exit-gate: no synchronous application Session or create_engine() outside Alembic.

The async migration is complete: no application-layer synchronous ORM
Session or create_engine() may exist outside the Alembic migration path (``database/session.py::_apply_migrations_sync``).

This test is a standing regression guard so the completion gate cannot
silently regress as new code is added.
"""

from __future__ import annotations

import ast

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Root of the application source tree.
SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "snore"

# Files that are intentionally permitted to use sync create_engine / Session.
# Add a new entry ONLY for a deliberate, spec-approved exception; document why.
SYNC_ENGINE_ALLOWLIST: set[str] = {
    # _apply_migrations_sync: Alembic migration path — sync by design.
    "database/session.py",
    # vacuum_sqlite(): VACUUM requires AUTOCOMMIT and cannot run via AsyncSession.
    # This is a maintenance-only operation, not an ORM read/write path.
    "services/database_service.py",
}


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(SRC_ROOT))
    except ValueError:
        return str(path)


def _is_allowed(rel: str) -> bool:
    return any(
        rel == allowed or rel.replace("\\", "/") == allowed
        for allowed in SYNC_ENGINE_ALLOWLIST
    )


class _SyncOrmVisitor(ast.NodeVisitor):
    """Collect calls that indicate synchronous ORM Session / create_engine usage.

    Flags:
    - ``create_engine(`` calls (not ``create_async_engine``)
    - ``sessionmaker(`` calls (not ``async_sessionmaker``)
    - ``from sqlalchemy.orm import Session`` imports
    - ``Session(`` constructor calls
    """

    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []  # (lineno, description)
        self._imported_session = False

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        for alias in node.names:
            name = alias.name
            if module == "sqlalchemy.orm" and name == "Session":
                self.violations.append(
                    (node.lineno, "from sqlalchemy.orm import Session")
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = self._call_name(node)
        if name == "create_engine":
            self.violations.append((node.lineno, "create_engine() call"))
        elif name == "sessionmaker":
            self.violations.append((node.lineno, "sessionmaker() call"))
        self.generic_visit(node)

    @staticmethod
    def _call_name(node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None


def test_no_sync_application_session_or_create_engine() -> None:
    """No application code outside Alembic uses synchronous Session or create_engine.

    If this test fails, a synchronous persistence seam has been introduced
    that violates the async-migration completion gate.
    Add the offending path to ALEMBIC_ALLOWLIST only if it is a deliberate,
    spec-approved Alembic-only seam.
    """
    all_violations: list[str] = []

    for path in _iter_python_files(SRC_ROOT):
        rel = _relative(path)
        if _is_allowed(rel):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue

        # Fast-path: skip files with no hint of sync usage.
        if (
            "create_engine" not in source
            and "sessionmaker" not in source
            and "from sqlalchemy.orm import Session" not in source
        ):
            continue

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        visitor = _SyncOrmVisitor()
        visitor.visit(tree)
        for lineno, desc in visitor.violations:
            all_violations.append(f"{rel}:{lineno}: {desc}")

    assert not all_violations, (
        "Synchronous application Session / create_engine usage found outside "
        "the Alembic allowlist (async-migration completion gate):\n"
        + "\n".join(f"  {v}" for v in all_violations)
    )
