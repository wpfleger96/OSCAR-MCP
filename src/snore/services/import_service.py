"""ImportService — orchestrates the CPAP data import pipeline."""

from __future__ import annotations

import logging

from collections.abc import Callable
from pathlib import Path, PurePosixPath

from sqlalchemy.ext.asyncio import AsyncSession

from snore.auth.factory import resolve_local_profile_id
from snore.database.importers import SessionImporter
from snore.database.session import session_scope
from snore.database.txn import run_txn
from snore.parsers.register_all import register_all_parsers
from snore.parsers.registry import parser_registry
from snore.parsers.unified import UnifiedSession
from snore.services.schemas import ImportResult, ImportSource, ImportSourceResult

logger = logging.getLogger(__name__)

__all__ = ["ImportService", "safe_relative_path"]


def safe_relative_path(filename: str) -> str | None:
    """Sanitize a multipart filename, preserving relative directory structure."""
    parts = PurePosixPath(filename.replace("\\", "/")).parts
    safe = [
        clean
        for p in parts
        if (clean := p.replace("\x00", ""))
        and clean not in (".", "..")
        and not clean.startswith("/")
        and not (len(clean) == 2 and clean[1] == ":")
    ]
    return "/".join(safe) or None


class ImportService:
    """Orchestrates the CPAP data import pipeline."""

    def detect_sources(self, path: Path) -> list[ImportSource]:
        """Detect all importable data sources under path."""
        register_all_parsers()
        results = parser_registry.detect_all_parsers(path)

        sources: list[ImportSource] = []
        for parser, detection in results:
            meta = detection.metadata or {}
            all_roots = meta.get("all_roots", [])

            if not all_roots:
                sources.append(
                    ImportSource(
                        parser_name=parser.parser_id,
                        device_serial=meta.get("device_serial"),
                        profile_name=meta.get("profile_name"),
                        structure_type=meta.get("structure_type"),
                        root_path=str(meta.get("data_root") or path),
                        data_root=meta.get("data_root"),
                    )
                )
            else:
                root_metadata = meta.get("root_metadata", {})
                for root_path in all_roots:
                    root_info = root_metadata.get(root_path, {})
                    sources.append(
                        ImportSource(
                            parser_name=parser.parser_id,
                            device_serial=root_info.get(
                                "device_serial", meta.get("device_serial")
                            ),
                            profile_name=root_info.get(
                                "profile_name", meta.get("profile_name")
                            ),
                            structure_type=root_info.get(
                                "structure_type", meta.get("structure_type")
                            ),
                            root_path=root_path,
                            data_root=root_info.get("data_root", meta.get("data_root")),
                        )
                    )

        return sources

    async def import_sources(
        self,
        sources: list[ImportSource],
        *,
        force: bool = False,
        batch_size: int = 50,
        backup: bool = True,
        backup_root: Path | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        parallel: bool = True,
        dry_run: bool = False,
        progress_callback: Callable[[str], None] | None = None,
        cancel_predicate: Callable[[], bool] | None = None,
        user_ref: str | None = None,
        profile_ref: str | None = None,
        profile_id: int | None = None,
    ) -> ImportResult:
        """Run the import pipeline for the selected sources.

        Backup (optional) → parse → import (or dry-run count). Returns aggregate
        counts and per-source breakdown.

        Args:
            cancel_predicate: Optional callable that returns True when the caller
                has requested cancellation.  Checked between sources and at each
                batch boundary inside ``SessionImporter``.
            user_ref:    Value of --user / SNORE_USER for profile resolution.
            profile_ref: Value of --profile / SNORE_PROFILE for profile resolution.
            profile_id:  Pre-resolved profile ID (skips user_ref/profile_ref resolution
                         when provided — used by the import worker to consume its
                         snapshotted job.target_profile_id).
        """
        return await self._import_sources_async(
            sources,
            force=force,
            batch_size=batch_size,
            backup=backup,
            backup_root=backup_root,
            sort_by=sort_by,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
            parallel=parallel,
            dry_run=dry_run,
            progress_callback=progress_callback,
            cancel_predicate=cancel_predicate,
            user_ref=user_ref,
            profile_ref=profile_ref,
            profile_id=profile_id,
        )

    async def _import_sources_async(
        self,
        sources: list[ImportSource],
        *,
        force: bool = False,
        batch_size: int = 50,
        backup: bool = True,
        backup_root: Path | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        parallel: bool = True,
        dry_run: bool = False,
        progress_callback: Callable[[str], None] | None = None,
        cancel_predicate: Callable[[], bool] | None = None,
        user_ref: str | None = None,
        profile_ref: str | None = None,
        profile_id: int | None = None,
    ) -> ImportResult:

        def emit(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)

        def is_cancelled() -> bool:
            return cancel_predicate is not None and cancel_predicate()

        source_results: list[ImportSourceResult] = []
        total_imported = 0
        total_skipped = 0
        total_failed = 0

        # Resolve the active profile once — all devices created during this import
        # are owned by this profile.  In local mode this auto-provisions the first
        # admin user + profile on first run.
        #
        # If profile_id is already resolved (e.g. from a snapshotted job), skip
        # user_ref/profile_ref resolution entirely.
        if profile_id is None:
            async with session_scope() as _profile_db:
                if user_ref is not None or profile_ref is not None:
                    from snore.auth.factory import (
                        resolve_cli_profile_id,  # noqa: PLC0415
                    )

                    profile_id = await resolve_cli_profile_id(
                        _profile_db, user_ref, profile_ref
                    )
                else:
                    profile_id = await resolve_local_profile_id(_profile_db)

        if not dry_run:
            async with session_scope() as db_session:
                orphaned = await SessionImporter.cleanup_orphaned_records(db_session)
                if orphaned > 0:
                    emit(f"Cleaned up {orphaned} orphaned records from database")

        parser_map = {p.parser_id: p for p in parser_registry.list_parsers()}

        for source in sources:
            if is_cancelled():
                break

            parser = parser_map.get(source.parser_name)
            if parser is None:
                logger.warning(
                    "Parser %r not found — skipping source", source.parser_name
                )
                source_results.append(
                    ImportSourceResult(
                        source=source,
                        imported=0,
                        skipped=0,
                        failed=0,
                        warnings=[f"Parser {source.parser_name!r} not found"],
                    )
                )
                continue

            parse_root = Path(source.root_path)
            warnings: list[str] = []

            # Backup raw files before parsing
            if backup and not dry_run and parser.supports_raw_backup:
                device_serial = source.device_serial or ""
                if device_serial:
                    from snore.services.backup_service import BackupService

                    backup_svc = BackupService(backup_root)
                    try:
                        emit("Backing up raw files...")
                        backup_result = backup_svc.backup_via_parser(
                            parser,
                            parse_root,
                            device_serial,
                            progress_callback=emit,
                        )
                        if backup_result.was_skipped:
                            emit(f"Backup skipped: {backup_result.skipped_reason}")
                        else:
                            emit(f"Backed up to {backup_result.backup_root}")
                            parse_root = backup_result.backup_root
                    except Exception as exc:
                        raise RuntimeError(
                            f"Backup failed: {exc}\nUse backup=False to skip backup."
                        ) from exc
                else:
                    warnings.append("No device serial — backup skipped")
                    emit("No device serial found — skipping backup")

            if is_cancelled():
                break

            # Parse sessions lazily — no full-batch prefetch.
            # parse_sessions() returns a generator; import_sessions_batch consumes
            # it in bounded batch_size chunks so memory is bounded per-batch.
            session_iter = parser.parse_sessions(
                parse_root,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
                sort_by=sort_by,
                parallel=parallel,
                progress_callback=emit,
            )
            emit("Detected sessions — starting import")

            if dry_run:
                # Count what would be imported without writing to DB.
                # Consume the iterator to get the count (dry_run materializes anyway).
                sessions_list = list(session_iter)
                source_results.append(
                    ImportSourceResult(
                        source=source,
                        imported=len(sessions_list),
                        skipped=0,
                        failed=0,
                        warnings=warnings,
                    )
                )
                total_imported += len(sessions_list)
                continue

            # Import — ImportService opens ONE scope per bounded batch chunk.
            # Each chunk is committed separately; a failed chunk does not poison
            # subsequent chunks.
            importer = SessionImporter()
            emit("Importing sessions...")
            imported = 0
            skipped = 0
            failed = 0
            session_iter_internal = iter(session_iter)
            import itertools as _itertools  # noqa: PLC0415

            for _chunk_num in _itertools.count(1):
                if cancel_predicate is not None and cancel_predicate():
                    break
                chunk = list(_itertools.islice(session_iter_internal, batch_size))
                if not chunk:
                    break

                # run_txn opens a fresh session per attempt and retries on
                # SQLite contention.  UNIQUE(device_id, device_session_id)
                # makes this idempotent: a replay of the same chunk produces
                # the same rows and skips duplicates.
                async def _import_chunk(
                    db: AsyncSession,
                    *,
                    _chunk: list[UnifiedSession] = chunk,
                    _profile_id: int | None = profile_id,
                    _importer: SessionImporter = importer,
                ) -> tuple[int, int, int]:
                    return await _importer.import_sessions_batch(
                        iter(_chunk),
                        force=force,
                        batch_size=batch_size,
                        progress_callback=progress_callback,
                        cancel_predicate=cancel_predicate,
                        db=db,
                        profile_id=_profile_id,
                    )

                ci, cs, cf = await run_txn(_import_chunk)
                imported += ci
                skipped += cs
                failed += cf

            total_imported += imported
            total_skipped += skipped
            total_failed += failed

            source_results.append(
                ImportSourceResult(
                    source=source,
                    imported=imported,
                    skipped=skipped,
                    failed=failed,
                    warnings=warnings,
                )
            )

        return ImportResult(
            total_imported=total_imported,
            total_skipped=total_skipped,
            total_failed=total_failed,
            sources=source_results,
            warnings=[],
        )
