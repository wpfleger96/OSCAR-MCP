"""ImportService — orchestrates the CPAP data import pipeline."""

from __future__ import annotations

import logging
import shutil
import tempfile

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from snore.database.importers import SessionImporter
from snore.database.session import session_scope
from snore.parsers.register_all import register_all_parsers
from snore.parsers.registry import parser_registry
from snore.services.schemas import ImportResult, ImportSource, ImportSourceResult

logger = logging.getLogger(__name__)

__all__ = ["ImportService"]


def _safe_relative_path(filename: str) -> str | None:
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

    def import_sources(
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
    ) -> ImportResult:
        """Run the import pipeline for the selected sources.

        Backup (optional) → parse → import (or dry-run count). Returns aggregate
        counts and per-source breakdown.
        """

        def emit(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)

        source_results: list[ImportSourceResult] = []
        total_imported = 0
        total_skipped = 0
        total_failed = 0

        if not dry_run:
            with session_scope() as db_session:
                orphaned = SessionImporter.cleanup_orphaned_records(db_session)
                if orphaned > 0:
                    emit(f"Cleaned up {orphaned} orphaned records from database")

        parser_map = {p.parser_id: p for p in parser_registry.list_parsers()}

        for source in sources:
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

            # Parse sessions
            emit("Parsing sessions...")
            sessions = list(
                parser.parse_sessions(
                    parse_root,
                    date_from=date_from,
                    date_to=date_to,
                    limit=limit,
                    sort_by=sort_by,
                    parallel=parallel,
                )
            )
            emit(f"Found {len(sessions)} sessions")

            if dry_run:
                # Count what would be imported without writing to DB
                source_results.append(
                    ImportSourceResult(
                        source=source,
                        imported=len(sessions),
                        skipped=0,
                        failed=0,
                        warnings=warnings,
                    )
                )
                total_imported += len(sessions)
                continue

            # Import
            importer = SessionImporter()
            total_batches = (len(sessions) + batch_size - 1) // batch_size
            emit(f"Importing {len(sessions)} sessions in {total_batches} batch(es)...")
            imported, skipped, failed = importer.import_sessions_batch(
                sessions,
                force=force,
                batch_size=batch_size,
                progress_callback=progress_callback,
            )

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

    def import_from_upload(
        self,
        files: list[tuple[str, BinaryIO]],
        *,
        force: bool = False,
        batch_size: int = 50,
        progress_callback: Callable[[str], None] | None = None,
    ) -> ImportResult:
        """Import from uploaded file streams, preserving relative paths.

        Writes files to a temp directory preserving relative paths, then detects
        and imports. Backup is disabled (there is no SD card to protect).
        """

        def emit(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tmp_root = tmp_path.resolve()
            emit(f"Writing {len(files)} files...")
            for filename, fileobj in files:
                rel = _safe_relative_path(filename) or "unknown"
                dest = tmp_path / rel
                if not dest.resolve().is_relative_to(tmp_root):
                    logger.warning("Skipping file with unsafe path: %r", filename)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                fileobj.seek(0)
                with open(dest, "wb") as out:
                    shutil.copyfileobj(fileobj, out, 1024 * 1024)

            emit("Detecting data sources...")
            sources = self.detect_sources(tmp_path)
            emit(f"Detected {len(sources)} source(s)")
            return self.import_sources(
                sources,
                force=force,
                batch_size=batch_size,
                backup=False,
                progress_callback=progress_callback,
            )
