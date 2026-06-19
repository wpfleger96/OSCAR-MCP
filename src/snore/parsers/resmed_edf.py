"""
ResMed EDF+ Parser

Parser for ResMed CPAP devices that output EDF+ format files.
Supports AirSense 10/11, AirCurve 10/11, and S9 series.

File Types:
- STR.edf: Device settings and configuration
- BRP.edf: Breathing waveforms (Flow Rate)
- PLD.edf: Pressure & Leak Data
- EVE.edf: Events (Apneas, Hypopneas, etc.)
- SA2.edf: Statistics and summary data
- CSL.edf: Compliance/Summary Log
"""

import json
import logging
import os
import re

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from snore.constants import (
    PARSER_MAX_SEARCH_DEPTH,
    UNIT_BPM,
    UNIT_FLOW,
    UNIT_PERCENT,
    UNIT_PRESSURE,
)
from snore.parsers.base import (
    DeviceParser,
    ParserDetectionResult,
    ParserError,
    ParserMetadata,
    RawFileManifest,
)
from snore.parsers.discovery import DataRoot, DataRootFinder
from snore.parsers.event_labels import EVENT_TYPE_MAP, FILTERED_ANNOTATIONS
from snore.parsers.formats.edf import EDFReader
from snore.parsers.unified import (
    DeviceInfo,
    RespiratoryEvent,
    TherapyMode,
    TherapySettings,
    UnifiedSession,
    WaveformData,
    WaveformType,
    extract_basic_stats,
)

logger = logging.getLogger(__name__)


class ResmedEDFParser(DeviceParser):
    """
    Parser for ResMed EDF+ data format.

    This parser handles the standard ResMed SD card structure:
    - Backup/
      - STR.edf (settings/configuration)
      - Identification.json (device info)
      - DATALOG/YYYY/
        - YYYYMMDD_HHMMSS_BRP.edf (breathing waveforms)
        - YYYYMMDD_HHMMSS_PLD.edf (pressure/leak)
        - YYYYMMDD_HHMMSS_SA2.edf (statistics)
        - YYYYMMDD_HHMMSS_EVE.edf (events)
        - YYYYMMDD_HHMMSS_CSL.edf (compliance)
    """

    FILE_TYPE_BRP = "_BRP.edf"  # Breathing waveforms
    FILE_TYPE_PLD = "_PLD.edf"  # Pressure/Leak data
    FILE_TYPE_SA2 = "_SA2.edf"  # Statistics
    FILE_TYPE_EVE = "_EVE.edf"  # Events
    FILE_TYPE_CSL = "_CSL.edf"  # Compliance

    STR_SETTINGS_MAP = {
        "Mode": "mode",
        "S.C.Press": "pressure_fixed",
        "S.A.MinPress": "pressure_min",
        "S.A.MaxPress": "pressure_max",
        "S.C.StartPress": "ramp_start_pressure",
        "S.A.StartPress": "ramp_start_pressure",
        "S.EPR.Level": "epr_level",
        "S.EPR.EPRType": "epr_mode",
        "S.RampEnable": "ramp_enabled",
        "S.RampTime": "ramp_time",
        "S.ClimateControl": "climate_control",
        "S.HumEnable": "humidity_enabled",
        "S.HumLevel": "humidity_level",
        "S.TempEnable": "tube_temp_enabled",
        "S.Temp": "tube_temp",
        "S.SmartStart": "smart_start",
        "S.ABFilter": "ab_filter",
        "S.Mask": "mask_type",
    }

    STR_SUMMARY_SIGNALS = {
        ("MaskPress.50", "Mask Pres Med"): "pressure_median",
        ("MaskPress.95", "Mask Pres 95"): "pressure_95th",
        ("MaskPress.Max", "Mask Pres Max"): "pressure_max",
        ("TgtEPAP.50", "Exp Pres Med"): "epap_median",
        ("TgtEPAP.95", "Exp Pres 95"): "epap_95th",
        ("TgtEPAP.Max", "Exp Pres Max"): "epap_max",
        ("TgtIPAP.50", "Insp Pres Med"): "ipap_median",
        ("TgtIPAP.95", "Insp Pres 95"): "ipap_95th",
        ("TgtIPAP.Max", "Insp Pres Max"): "ipap_max",
        ("Leak.50", "Leak Med"): "leak_median",
        ("Leak.95", "Leak 95"): "leak_95th",
        ("Leak.Max", "Leak Max"): "leak_max",
        ("RespRate.50", "RR Med"): "respiratory_rate_mean",
        ("TidVol.50", "Tid Vol Med"): "tidal_volume_mean",
        ("MinVent.50", "Min Vent Med"): "minute_ventilation_mean",
        ("AHI", "AHI"): "ahi",
        ("OAI", "OAI"): "oai",
        ("CAI", "CAI"): "cai",
        ("HI", "HI"): "hi",
    }

    EPR_TYPE_MAP = {0: "Off", 1: "Ramp Only", 2: "Full Time"}
    MASK_TYPE_MAP = {
        0: "Pillows",
        1: "Full Face",
        2: "Nasal",
        3: "Nasal Pillows",
        4: "Nasal",
    }
    CLIMATE_CONTROL_MAP = {1: "Manual", 2: "Auto"}
    AB_FILTER_MAP = {0: "Standard", 1: "Antibacterial"}
    MODE_MAP = {0: TherapyMode.CPAP, 1: TherapyMode.APAP, 2: TherapyMode.BIPAP}

    def __init__(self) -> None:
        """Initialize ResMed parser."""
        super().__init__()
        self._data_root: Path | None = None
        self._root_metadata: DataRoot | None = None
        self._all_roots: list[DataRoot] = []
        self._finder = DataRootFinder()

    def get_metadata(self) -> ParserMetadata:
        """Return ResMed parser metadata."""
        return ParserMetadata(
            parser_id="resmed_edf",
            parser_version="1.0.0",
            manufacturer="ResMed",
            supported_formats=["EDF+", "EDF"],
            supported_models=[
                "AirSense 10 AutoSet",
                "AirSense 10 Elite",
                "AirSense 10 CPAP",
                "AirSense 11 AutoSet",
                "AirCurve 10 S",
                "AirCurve 10 VAuto",
                "AirCurve 10 ASV",
                "AirCurve 11 VAuto",
                "S9 AutoSet",
                "S9 Elite",
                "S9 VPAP Auto",
            ],
            description="Parser for ResMed CPAP devices using EDF+ format",
            requires_libraries=["pyedflib", "numpy"],
        )

    def detect(self, path: Path) -> ParserDetectionResult:
        """
        Detect ResMed EDF+ data structure with smart path discovery.

        Searches for STR.edf + DATALOG signature in:
        - Current path
        - Parent directories (up to 5 levels)
        - Child directories (up to 3 levels)

        Supports both raw SD card format and OSCAR profile format.
        """
        path = Path(path)

        if not path.exists():
            return ParserDetectionResult(
                detected=False, message=f"Path does not exist: {path}"
            )

        roots = self._finder.find_data_roots(
            path,
            validator_func=self._is_resmed_root,
            metadata_extractor_func=self._create_data_root,
            max_levels_down=PARSER_MAX_SEARCH_DEPTH,
        )

        if not roots:
            return ParserDetectionResult(
                detected=False,
                message=f"No ResMed data found. Searched {path} and parent/child directories for STR.edf + DATALOG structure.",
            )

        self._data_root = roots[0].path
        self._root_metadata = roots[0]
        self._all_roots = roots

        metadata_dict = {
            "data_root": str(self._data_root),
            "structure_type": self._root_metadata.structure_type,
            "profile_name": self._root_metadata.profile_name,
            "device_serial": self._root_metadata.device_serial,
            "all_roots": [str(r.path) for r in roots],
            "root_metadata": {
                str(r.path): {
                    "profile_name": r.profile_name,
                    "structure_type": r.structure_type,
                    "device_serial": r.device_serial,
                }
                for r in roots
            },
        }

        if self._data_root != path:
            location_desc = f"in {'parent' if self._data_root in path.parents else 'child'} directory"
        else:
            location_desc = "at provided path"

        structure_name = self._root_metadata.structure_type.replace("_", " ")

        if len(roots) > 1:
            message = f"Found {len(roots)} ResMed data locations ({location_desc})"
        else:
            message = f"ResMed {structure_name} data detected ({location_desc})"

        return ParserDetectionResult(
            detected=True,
            confidence=self._root_metadata.confidence,
            message=message,
            metadata=metadata_dict,
        )

    def _is_resmed_root(self, path: Path) -> bool:
        """Check if path contains ResMed data signature (STR.edf + DATALOG)."""
        if not path.is_dir():
            return False
        return (path / "STR.edf").exists() and (path / "DATALOG").is_dir()

    def _create_data_root(self, path: Path) -> DataRoot:
        """Create DataRoot with metadata extracted from path structure."""
        parts = path.parts

        if "Profiles" in parts and "Backup" in parts:
            try:
                profiles_idx = parts.index("Profiles")
                profile_name = (
                    parts[profiles_idx + 1] if profiles_idx + 1 < len(parts) else None
                )
                device_str = (
                    parts[profiles_idx + 2] if profiles_idx + 2 < len(parts) else None
                )

                serial = None
                if device_str and "_" in device_str:
                    serial = device_str.split("_", 1)[1]

                return DataRoot(
                    path=path,
                    structure_type="oscar_profile",
                    profile_name=profile_name,
                    device_serial=serial,
                    confidence=0.95,
                )
            except (IndexError, ValueError):
                pass

        serial = self._extract_serial_from_identification(path)
        return DataRoot(
            path=path,
            structure_type="raw_sd",
            profile_name=None,
            device_serial=serial,
            confidence=0.90,
        )

    def _extract_serial_from_identification(self, path: Path) -> str | None:
        """Extract device serial number from Identification.json."""
        id_file = path / "Identification.json"
        if not id_file.exists():
            return None

        try:
            with open(id_file) as f:
                data = json.load(f)

            fg = data.get("FlowGenerator", {})
            profiles = fg.get("IdentificationProfiles", {})
            product = profiles.get("Product", {})
            serial = product.get("SerialNumber")
            return serial if isinstance(serial, str) else None
        except Exception:
            return None

    def get_device_info(self, path: Path) -> DeviceInfo:
        """
        Extract ResMed device information.

        Tries Identification.json first, falls back to STR.edf.
        """
        path = Path(self._data_root if self._data_root else path)

        id_file = path / "Identification.json"
        if id_file.exists():
            try:
                with open(id_file) as f:
                    data = json.load(f)

                fg = data.get("FlowGenerator", {})
                profiles = fg.get("IdentificationProfiles", {})
                product = profiles.get("Product", {})
                software = profiles.get("Software", {})

                return DeviceInfo(
                    manufacturer="ResMed",
                    model=product.get("ProductName", "Unknown"),
                    serial_number=product.get("SerialNumber", "Unknown"),
                    firmware_version=software.get("ApplicationIdentifier", None),
                    product_code=product.get("ProductCode", None),
                )

            except Exception as e:
                logger.warning(f"Failed to parse Identification.json: {e}")

        str_file = path / "STR.edf"
        if str_file.exists():
            try:
                with EDFReader(str_file) as edf:
                    header = edf.get_header()

                    recording_info = header.recording_info

                    model = "Unknown"
                    serial = "Unknown"

                    if "AirSense" in recording_info:
                        match = re.search(r"(AirSense \d+ [A-Za-z]+)", recording_info)
                        if match:
                            model = match.group(1)
                    elif "AirCurve" in recording_info:
                        match = re.search(r"(AirCurve \d+ [A-Za-z]+)", recording_info)
                        if match:
                            model = match.group(1)

                    serial_match = re.search(r"SN[:\s]+(\d+)", recording_info)
                    if serial_match:
                        serial = serial_match.group(1)

                    return DeviceInfo(
                        manufacturer="ResMed", model=model, serial_number=serial
                    )

            except Exception as e:
                logger.warning(f"Failed to parse STR.edf: {e}")

        return DeviceInfo(
            manufacturer="ResMed", model="Unknown", serial_number="Unknown"
        )

    def parse_sessions(
        self,
        path: Path,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int | None = None,
        sort_by: str | None = None,
        parallel: bool = True,
    ) -> Iterator[UnifiedSession]:
        """
        Parse all ResMed sessions from the given path.

        Yields one UnifiedSession per therapy session.

        Args:
            path: Path to data directory
            date_from: Filter sessions from this date
            date_to: Filter sessions to this date
            limit: Limit number of sessions
            sort_by: Sort order (date-asc, date-desc, or None)
            parallel: Enable parallel parsing (default: True)
        """
        path = Path(path)

        path, night_items = self._discover_session_files(path, sort_by)
        night_items = self._filter_night_items(night_items, date_from, date_to)

        device_info = self.get_device_info(path)

        str_file = path / "STR.edf"
        str_settings_cache = self._preload_str_settings(str_file)
        str_summaries_cache = self._preload_str_summaries(str_file)

        sessions_yielded = 0

        if parallel and len(night_items) > 1:
            # limit counts yielded sessions, not nights: a night can be dropped
            # by the per-session date filter or fail to parse, so truncating
            # night_items up front would under-deliver. The as_completed loop
            # below enforces the limit and cancels remaining futures instead.
            logger.debug(
                f"Parsing {len(night_items)} nights in parallel with {os.cpu_count()} workers"
            )

            with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                futures = {
                    executor.submit(
                        self._parse_single_session_bundle,
                        night_date,
                        segments,
                        device_info,
                        path,
                        str_settings_cache,
                        str_summaries_cache,
                        date_from,
                        date_to,
                    ): night_date
                    for night_date, segments in night_items
                }

                for future in as_completed(futures):
                    night_date = futures[future]

                    if limit is not None and sessions_yielded >= limit:
                        logger.debug(
                            f"Reached session limit of {limit}, cancelling remaining"
                        )
                        for remaining_future in futures:
                            if not remaining_future.done():
                                remaining_future.cancel()
                        break

                    try:
                        session = future.result()
                    except Exception as e:
                        logger.error(f"Failed to parse night {night_date}: {e}")
                        continue

                    if session is None:
                        continue

                    yield session
                    sessions_yielded += 1
        else:
            for night_date, segments in night_items:
                if limit is not None and sessions_yielded >= limit:
                    logger.debug(f"Reached session limit of {limit}, stopping")
                    break

                try:
                    session = self._parse_single_session_bundle(
                        night_date,
                        segments,
                        device_info,
                        path,
                        str_settings_cache,
                        str_summaries_cache,
                        date_from,
                        date_to,
                    )
                except Exception as e:
                    logger.error(f"Failed to parse night {night_date}: {e}")
                    continue

                if session is None:
                    continue

                yield session
                sessions_yielded += 1

    def _discover_session_files(
        self, path: Path, sort_by: str | None
    ) -> tuple[Path, list[tuple[str, dict[str, dict[str, Path]]]]]:
        """
        Resolve the data root and collect session files grouped by night.

        Returns:
            (resolved_path, night_items) where night_items is a list of
            (night_date, segments) tuples in the requested sort order
        """
        if self._all_roots:
            matching_roots = [r for r in self._all_roots if r.path == path]
            if matching_roots:
                path = matching_roots[0].path
            elif self._data_root:
                path = self._data_root
        elif self._data_root:
            path = self._data_root

        datalog_dir = path / "DATALOG"

        if not datalog_dir.exists():
            raise ParserError("DATALOG directory not found", self)

        night_groups = self._group_session_files(datalog_dir)

        total_segments = sum(len(segments) for segments in night_groups.values())
        logger.debug(
            f"Found {len(night_groups)} nights with {total_segments} total segments "
            f"(avg {total_segments / len(night_groups):.1f} segments per night)"
        )

        if sort_by == "date-asc":
            night_items = sorted(night_groups.items(), key=lambda x: x[0])
        elif sort_by == "date-desc":
            night_items = sorted(night_groups.items(), key=lambda x: x[0], reverse=True)
        else:
            night_items = list(night_groups.items())

        return path, night_items

    def _filter_night_items(
        self,
        night_items: list[tuple[str, dict[str, dict[str, Path]]]],
        date_from: str | None,
        date_to: str | None,
    ) -> list[tuple[str, dict[str, dict[str, Path]]]]:
        """Filter night items by date range based on their night-date IDs."""
        if not (date_from or date_to):
            return night_items

        filtered_items = []
        for night_date, segments in night_items:
            try:
                night_date_obj = datetime.strptime(night_date, "%Y%m%d").date()

                if date_from:
                    filter_date_from = datetime.fromisoformat(date_from).date()
                    if night_date_obj < filter_date_from:
                        logger.debug(
                            f"Skipping night {night_date}: before {filter_date_from}"
                        )
                        continue

                if date_to:
                    filter_date_to = datetime.fromisoformat(date_to).date()
                    if night_date_obj > filter_date_to:
                        logger.debug(
                            f"Skipping night {night_date}: after {filter_date_to}"
                        )
                        continue
            except (ValueError, IndexError) as e:
                logger.warning(f"Could not parse night date {night_date}: {e}")

            filtered_items.append((night_date, segments))

        return filtered_items

    def _parse_single_session_bundle(
        self,
        night_date: str,
        segments: dict[str, dict[str, Path]],
        device_info: DeviceInfo,
        base_path: Path,
        str_settings_cache: dict[date, dict[str, float]] | None,
        str_summaries_cache: dict[date, dict[str, float]] | None,
        date_from: str | None,
        date_to: str | None,
    ) -> UnifiedSession | None:
        """
        Parse one night's file bundle into a finalized session.

        Returns None when the night has no valid segments or its actual
        start date falls outside the requested date range.
        """
        session = self._parse_night_session(
            night_date,
            segments,
            device_info,
            base_path,
            str_settings_cache,
            str_summaries_cache,
        )

        if session is None:
            return None

        if date_from:
            if session.start_time.date() < datetime.fromisoformat(date_from).date():
                logger.warning(
                    f"Night {night_date} has mismatched date in ID vs file contents"
                )
                return None
        if date_to:
            if session.start_time.date() > datetime.fromisoformat(date_to).date():
                logger.warning(
                    f"Night {night_date} has mismatched date in ID vs file contents"
                )
                return None

        session.finalize_statistics()
        return session

    # ------------------------------------------------------------------
    # Raw file backup / export
    # ------------------------------------------------------------------

    @property
    def supports_raw_backup(self) -> bool:
        return True

    def backup_raw_data(
        self,
        source_root: Path,
        dest_root: Path,
        progress_callback: Callable[[str], None] | None = None,
    ) -> RawFileManifest:
        """Copy ResMed raw files from SD card to backup directory.

        Handles STR.edf versioning (moves old copy to STR_Backup/) and
        copies DATALOG EDF files preserving directory structure. Skips
        files that already exist at dest with matching size and mtime.
        """
        from snore.parsers.resmed_file_index import (
            flatten_night_files,
            group_session_files,
        )

        dest_root.mkdir(parents=True, exist_ok=True)

        device_files_copied: list[Path] = []

        # STR.edf versioning (matching OSCAR's backupSTRfiles pattern)
        src_str = source_root / "STR.edf"
        dest_str = dest_root / "STR.edf"
        if src_str.exists():
            if dest_str.exists():
                str_backup_dir = dest_root / "STR_Backup"
                str_backup_dir.mkdir(exist_ok=True)
                snapshot_name = self._str_snapshot_name(dest_str)
                snapshot_path = str_backup_dir / snapshot_name
                if not snapshot_path.exists():
                    self._safe_copy(dest_str, snapshot_path)
                    logger.debug(f"Archived STR.edf → STR_Backup/{snapshot_name}")
            self._safe_copy(src_str, dest_str)
            device_files_copied.append(dest_str)

        # Identification files (overwrite each import)
        for ident_name in ("Identification.json", "Identification.tgt"):
            src_ident = source_root / ident_name
            if src_ident.exists():
                dest_ident = dest_root / ident_name
                self._safe_copy(src_ident, dest_ident)
                device_files_copied.append(dest_ident)

        if device_files_copied:
            names = ", ".join(f.name for f in device_files_copied)
            if progress_callback:
                progress_callback(f"Copied device files ({names})")

        # DATALOG EDF files
        datalog_src = source_root / "DATALOG"
        nights_copied: dict[date, list[Path]] = {}
        total_files_copied = 0
        total_files_skipped = 0

        if datalog_src.is_dir():
            grouped = group_session_files(datalog_src)
            flat = flatten_night_files(grouped)
            total_nights = len(flat)

            if progress_callback:
                progress_callback(f"Copying {total_nights} nights of DATALOG files...")

            for night_str, src_files in sorted(flat.items()):
                night_date = datetime.strptime(night_str, "%Y%m%d").date()
                copied_files: list[Path] = []

                for src_file in src_files:
                    rel = src_file.relative_to(source_root)
                    dest_file = dest_root / rel
                    dest_file.parent.mkdir(parents=True, exist_ok=True)

                    if dest_file.exists() and self._files_match(src_file, dest_file):
                        copied_files.append(dest_file)
                        total_files_skipped += 1
                        continue

                    self._safe_copy(src_file, dest_file)
                    copied_files.append(dest_file)
                    total_files_copied += 1

                if copied_files:
                    nights_copied[night_date] = copied_files

            logger.debug(
                f"DATALOG backup: {total_files_copied} copied, "
                f"{total_files_skipped} skipped across {len(nights_copied)} nights"
            )

        return RawFileManifest(
            device_files=device_files_copied,
            nights=nights_copied,
            source_root=dest_root,
            files_copied=total_files_copied + len(device_files_copied),
            files_skipped=total_files_skipped,
        )

    def get_raw_file_manifest(
        self,
        root: Path,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> RawFileManifest:
        """Return manifest of ResMed raw files, optionally filtered by date."""
        from snore.parsers.resmed_file_index import (
            flatten_night_files,
            group_session_files,
        )

        device_files: list[Path] = []
        for name in ("STR.edf", "Identification.json", "Identification.tgt"):
            p = root / name
            if p.exists():
                device_files.append(p)

        nights: dict[date, list[Path]] = {}
        datalog = root / "DATALOG"
        if datalog.is_dir():
            grouped = group_session_files(datalog)
            flat = flatten_night_files(grouped)

            for night_str, files in sorted(flat.items()):
                night_date = datetime.strptime(night_str, "%Y%m%d").date()

                if date_from and night_date < date_from:
                    continue
                if date_to and night_date > date_to:
                    continue

                nights[night_date] = files

        return RawFileManifest(
            device_files=device_files,
            nights=nights,
            source_root=root,
        )

    @staticmethod
    def _str_snapshot_name(str_path: Path) -> str:
        """Derive STR_Backup snapshot filename from the EDF header start date."""
        try:
            with open(str_path, "rb") as f:
                header = f.read(256)
            if len(header) < 184:
                return "STR-unknown.edf"
            date_str = header[168:176].decode("ascii", errors="ignore").strip()
            time_str = header[176:184].decode("ascii", errors="ignore").strip()
            day = int(date_str[0:2])
            month = int(date_str[3:5])
            year = int(date_str[6:8])
            year = year + 2000 if year < 85 else year + 1900
            hour = int(time_str[0:2])
            minute = int(time_str[3:5])
            second = int(time_str[6:8])
            return f"STR-{year:04d}{month:02d}{day:02d}-{hour:02d}{minute:02d}{second:02d}.edf"
        except Exception:
            logger.warning(
                f"Could not read EDF header from {str_path} for snapshot naming"
            )
            return "STR-unknown.edf"

    def trim_device_summary(
        self,
        output_root: Path,
        date_from: date,
        date_to: date,
    ) -> None:
        """Trim STR.edf in the output directory to the given date range."""
        str_path = output_root / "STR.edf"
        if not str_path.exists():
            return
        self._slice_str_edf(str_path, str_path, date_from, date_to)

    @staticmethod
    def _slice_str_edf(src: Path, dest: Path, date_from: date, date_to: date) -> None:
        """Slice an STR.edf file to only include records for a date range.

        Pure binary manipulation — reads the EDF header to determine record
        layout, computes which records fall within the date range, and writes
        a new file with updated header fields and only the selected records.
        """
        from datetime import timedelta as td

        with open(src, "rb") as f:
            global_header = bytearray(f.read(256))

        if len(global_header) < 256:
            logger.warning(f"STR.edf too small to slice: {src}")
            return

        date_str = global_header[168:176].decode("ascii", errors="ignore").strip()
        day = int(date_str[0:2])
        month = int(date_str[3:5])
        year = int(date_str[6:8])
        year = year + 2000 if year < 85 else year + 1900
        str_start_date = date(year, month, day)

        num_records = int(
            global_header[236:244].decode("ascii", errors="ignore").strip()
        )
        num_signals = int(
            global_header[252:256].decode("ascii", errors="ignore").strip()
        )

        if num_records <= 0 or num_signals <= 0:
            return

        signal_header_size = num_signals * 256
        with open(src, "rb") as f:
            f.seek(256)
            signal_headers = f.read(signal_header_size)

        # samples_per_record sits at offset num_signals * 216 within signal headers
        samples_per_record = []
        spr_offset = num_signals * 216
        for i in range(num_signals):
            spr_bytes = signal_headers[spr_offset + i * 8 : spr_offset + (i + 1) * 8]
            samples_per_record.append(
                int(spr_bytes.decode("ascii", errors="ignore").strip())
            )

        record_size = sum(s * 2 for s in samples_per_record)

        from_record = max(0, (date_from - str_start_date).days)
        to_record = min(num_records - 1, (date_to - str_start_date).days)

        if from_record > to_record:
            slice_count = 0
        else:
            slice_count = to_record - from_record + 1

        new_header = bytearray(global_header)

        new_start = str_start_date + td(days=from_record)
        year_2d = new_start.year % 100
        new_date_str = f"{new_start.day:02d}.{new_start.month:02d}.{year_2d:02d}"
        new_header[168:176] = f"{new_date_str:<8}".encode("ascii")  # start date
        new_header[236:244] = f"{slice_count:<8}".encode("ascii")  # num records

        data_offset = 256 + signal_header_size + from_record * record_size
        data_length = slice_count * record_size

        if data_length > 0:
            with open(src, "rb") as f:
                f.seek(data_offset)
                data_records = f.read(data_length)
        else:
            data_records = b""

        with open(dest, "wb") as f:
            f.write(bytes(new_header))
            f.write(signal_headers)
            f.write(data_records)

        logger.debug(
            f"Sliced STR.edf: {num_records} records → {slice_count} records "
            f"({new_start} to {str_start_date + td(days=to_record) if slice_count > 0 else 'empty'})"
        )

    @staticmethod
    def _safe_copy(src: Path, dest: Path) -> None:
        """Copy file content and attempt to preserve timestamps.

        Falls back gracefully when full metadata copy fails, which happens
        on macOS when copying from FAT32 SD cards (chflags not supported).
        """
        import shutil

        shutil.copyfile(src, dest)
        try:
            shutil.copystat(src, dest)
        except PermissionError:
            # copystat's chflags call fails on macOS FAT32 → APFS copies.
            # Preserve mtime/atime manually instead.
            st = src.stat()
            os.utime(dest, (st.st_atime, st.st_mtime))

    @staticmethod
    def _files_match(src: Path, dest: Path) -> bool:
        """Check if two files match by size and modification time."""
        try:
            src_stat = src.stat()
            dest_stat = dest.stat()
            return (
                src_stat.st_size == dest_stat.st_size
                and abs(src_stat.st_mtime - dest_stat.st_mtime) < 2.0
            )
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Delegated utilities (from resmed_file_index)
    # ------------------------------------------------------------------

    def _get_night_date(self, timestamp: datetime) -> str:
        """Delegate to resmed_file_index.get_night_date()."""
        from snore.parsers.resmed_file_index import get_night_date

        return get_night_date(timestamp)

    def _scan_edf_files(self, datalog_dir: Path) -> list[Path]:
        """Delegate to resmed_file_index.scan_edf_files()."""
        from snore.parsers.resmed_file_index import scan_edf_files

        return scan_edf_files(datalog_dir)

    def _group_session_files(
        self, datalog_dir: Path
    ) -> dict[str, dict[str, dict[str, Path]]]:
        """Delegate to resmed_file_index.group_session_files()."""
        from snore.parsers.resmed_file_index import group_session_files

        return group_session_files(datalog_dir)

    def _parse_night_session(
        self,
        night_date: str,
        segments: dict[str, dict[str, Path]],
        device_info: DeviceInfo,
        base_path: Path,
        str_settings_cache: dict[date, dict[str, float]] | None = None,
        str_summaries_cache: dict[date, dict[str, float]] | None = None,
    ) -> UnifiedSession | None:
        """
        Parse all segments for a single night into one unified session.

        Multiple segments occur when the mask is removed/replaced during the night
        (bathroom breaks, water, etc.). This matches OSCAR's behavior of combining
        segments from the same night (noon-to-noon period).

        Args:
            night_date: Night date (YYYYMMDD format)
            segments: Dict mapping session_id to file dict
            device_info: Device information
            base_path: Base data path
            str_settings_cache: Pre-loaded STR.edf settings cache (optional)
            str_summaries_cache: Pre-loaded STR.edf summaries cache (optional)

        Returns:
            Single UnifiedSession representing the entire night
        """
        sorted_segments = sorted(segments.items(), key=lambda x: x[0])

        logger.debug(
            f"Parsing night {night_date} with {len(sorted_segments)} segment(s): "
            f"{[seg_id for seg_id, _ in sorted_segments]}"
        )

        eve_files = []
        for segment_id, files in sorted_segments:
            if "EVE" in files:
                eve_files.append(files["EVE"])
                logger.debug(
                    f"Found EVE file for segment {segment_id}: {files['EVE'].name}"
                )

        segment_sessions = []
        for segment_id, files in sorted_segments:
            try:
                segment_session = self._parse_session_group(
                    segment_id,
                    files,
                    device_info,
                    base_path,
                    str_settings_cache,
                    str_summaries_cache,
                )
                segment_sessions.append(segment_session)
            except ValueError as e:
                if "No valid data records" in str(e):
                    continue
                raise
            except Exception as e:
                logger.warning(f"Failed to parse segment {segment_id}: {e}")
                continue

        if not segment_sessions:
            logger.warning(
                f"Night {night_date} has no valid therapy segments (only CSL/EVE stub files). "
                f"This is likely a device self-test or brief power-on event. Skipping night."
            )
            return None

        if len(segment_sessions) == 1:
            session = segment_sessions[0]
            if eve_files:
                logger.debug(
                    f"Parsing {len(eve_files)} EVE file(s) for night {night_date}"
                )
                self._parse_eve_files_for_night(eve_files, session)
            return session

        logger.debug(f"Merging {len(segment_sessions)} segments for night {night_date}")

        merged_session = segment_sessions[0]

        merged_session.device_session_id = f"{night_date}_merged"

        merged_session.end_time = segment_sessions[-1].end_time

        cumulative_time_offset = 0.0
        for i, segment in enumerate(segment_sessions):
            if i == 0:
                cumulative_time_offset = (
                    segment.end_time - segment.start_time
                ).total_seconds()
            else:
                segment_start_offset = (
                    segment.start_time - merged_session.start_time
                ).total_seconds()

                gap_duration = segment_start_offset - cumulative_time_offset
                if gap_duration > 0:
                    merged_session.data_quality_notes.append(
                        f"Gap {i}: {gap_duration / 60:.1f} minutes "
                        f"({segment_sessions[i - 1].end_time.strftime('%H:%M:%S')} - "
                        f"{segment.start_time.strftime('%H:%M:%S')})"
                    )

                for waveform_type, segment_waveform in segment.waveforms.items():
                    if waveform_type in merged_session.waveforms:
                        merged_waveform = merged_session.waveforms[waveform_type]

                        adjusted_timestamps = (
                            np.asarray(segment_waveform.timestamps)
                            + segment_start_offset
                        )

                        merged_waveform.timestamps = np.concatenate(
                            [merged_waveform.timestamps, adjusted_timestamps]
                        )
                        merged_waveform.values = np.concatenate(
                            [merged_waveform.values, segment_waveform.values]
                        )

                        merged_waveform.min_value = float(
                            np.min(merged_waveform.values)
                        )
                        merged_waveform.max_value = float(
                            np.max(merged_waveform.values)
                        )
                        merged_waveform.mean_value = float(
                            np.mean(merged_waveform.values)
                        )
                    else:
                        merged_session.add_waveform(segment_waveform)

                for event in segment.events:
                    event.start_time = event.start_time + timedelta(
                        seconds=(
                            segment_start_offset
                            - (event.start_time - segment.start_time).total_seconds()
                        )
                    )
                    merged_session.add_event(event)

                cumulative_time_offset = (
                    segment.end_time - merged_session.start_time
                ).total_seconds()

        merged_session.data_quality_notes.insert(
            0,
            f"Night composed of {len(segment_sessions)} segment(s) - mask removed during sleep",
        )

        logger.debug(
            f"Merged night {night_date}: {len(segment_sessions)} segments, "
            f"total duration {(merged_session.end_time - merged_session.start_time).total_seconds() / 3600:.2f}h"
        )

        if eve_files:
            logger.debug(f"Parsing {len(eve_files)} EVE file(s) for night {night_date}")
            self._parse_eve_files_for_night(eve_files, merged_session)

        return merged_session

    def _parse_session_group(
        self,
        session_id: str,
        files: dict[str, Path],
        device_info: DeviceInfo,
        base_path: Path,
        str_settings_cache: dict[date, dict[str, float]] | None = None,
        str_summaries_cache: dict[date, dict[str, float]] | None = None,
    ) -> UnifiedSession:
        """Parse a single session from its file group."""
        from .formats.edf import get_edf_record_count

        start_time = datetime.strptime(session_id, "%Y%m%d_%H%M%S")

        session_duration_seconds = None
        for file_type in ["BRP", "PLD", "SA2"]:
            if file_type in files:
                try:
                    record_count = get_edf_record_count(files[file_type])
                    if record_count > 0:
                        with EDFReader(files[file_type]) as edf:
                            header = edf.get_header()
                            session_duration_seconds = (
                                record_count * header.record_duration
                            )
                            logger.debug(
                                f"Calculated session duration from {file_type}: "
                                f"{record_count} records × {header.record_duration}s = {session_duration_seconds}s "
                                f"({session_duration_seconds / 3600:.2f} hours)"
                            )
                            break
                except Exception as e:
                    logger.warning(f"Could not read duration from {file_type}: {e}")
                    continue

        if session_duration_seconds is None or session_duration_seconds == 0:
            raise ValueError("No valid data records in any files for this session")

        session = UnifiedSession(
            device_session_id=session_id,
            device_info=device_info,
            start_time=start_time,
            end_time=start_time + timedelta(seconds=session_duration_seconds),
            import_source="resmed_edf",
            parser_version=self.metadata.parser_version,
            raw_data_path=str(base_path),
        )

        if "SA2" in files:
            self._parse_statistics(files["SA2"], session)

        if "BRP" in files:
            self._parse_breathing_waveforms(files["BRP"], session)

        if "PLD" in files:
            self._parse_pressure_leak(files["PLD"], session)

        if str_settings_cache:
            session_date = session.start_time.date()
            if session_date in str_settings_cache:
                settings = self._convert_str_to_therapy_settings(
                    str_settings_cache[session_date]
                )
                if settings:
                    session.settings = settings
                    logger.debug(
                        f"Loaded settings for session {session_id}: mode={settings.mode}"
                    )

        if str_summaries_cache:
            session_date = session.start_time.date()
            if session_date in str_summaries_cache:
                summaries = str_summaries_cache[session_date]
                stats = session.statistics

                for stat_name, value in summaries.items():
                    if hasattr(stats, stat_name):
                        setattr(stats, stat_name, value)

                if summaries:
                    logger.debug(
                        f"Applied {len(summaries)} STR.edf summary stats to session {session_id}"
                    )
                    session.data_quality_notes.append(
                        f"Statistics supplemented with {len(summaries)} values from STR.edf"
                    )

        return session

    def _parse_statistics(self, file_path: Path, session: UnifiedSession) -> None:
        """
        Parse SA2 oximetry data file.

        SA2 files contain:
        - Pulse (heart rate in bpm) at 1Hz
        - SpO2 (oxygen saturation %) at 1Hz

        Note: Values of -1 indicate no oximeter was connected.
        """
        from .formats.edf import get_edf_record_count

        try:
            record_count = get_edf_record_count(file_path)
            file_size = file_path.stat().st_size

            if record_count == 0:
                logger.debug(
                    f"SA2 file {file_path.name} has 0 data records (device on but not used, size={file_size} bytes)"
                )
                session.data_quality_notes.append(
                    "SA2: No data (device turned on briefly, not used)"
                )
                session.has_statistics = True
                return

            logger.debug(
                f"SA2 file {file_path.name} has {record_count} records (size={file_size} bytes)"
            )

            with EDFReader(file_path) as edf:
                has_valid_data = False

                spo2_signal = self._find_signal(edf, ["SpO2"])
                if spo2_signal:
                    result = self._read_waveform(
                        edf,
                        spo2_signal,
                        WaveformType.SPO2,
                        UNIT_PERCENT,
                        valid_range=(70, 100),
                    )
                    if result is not None:
                        waveform, valid_data = result

                        time_below_90_seconds = int(np.sum(valid_data < 90))

                        session.add_waveform(waveform)

                        session.statistics.spo2_min = waveform.min_value
                        session.statistics.spo2_max = waveform.max_value
                        session.statistics.spo2_mean = waveform.mean_value
                        session.statistics.spo2_time_below_90 = time_below_90_seconds

                        has_valid_data = True
                        logger.debug(
                            f"Parsed SpO2 data: {len(valid_data)} valid samples, {time_below_90_seconds}s below 90%"
                        )
                    else:
                        logger.debug("SpO2 signal present but no valid data (all -1)")

                pulse_signal = self._find_signal(edf, ["Pulse"])
                if pulse_signal:
                    result = self._read_waveform(
                        edf,
                        pulse_signal,
                        WaveformType.PULSE,
                        UNIT_BPM,
                        valid_range=(40, 200),
                    )
                    if result is not None:
                        waveform, valid_data = result

                        session.add_waveform(waveform)

                        session.statistics.pulse_min = waveform.min_value
                        session.statistics.pulse_max = waveform.max_value
                        session.statistics.pulse_mean = waveform.mean_value

                        has_valid_data = True
                        logger.debug(
                            f"Parsed Pulse data: {len(valid_data)} valid samples"
                        )
                    else:
                        logger.debug("Pulse signal present but no valid data (all -1)")

                session.has_statistics = True

                if not has_valid_data:
                    logger.debug("No oximeter connected - SA2 file has no valid data")

        except Exception as e:
            logger.warning(f"Failed to parse SA2 statistics: {e}")
            session.data_quality_notes.append(f"SA2 parsing failed: {e}")

    def _parse_breathing_waveforms(
        self, file_path: Path, session: UnifiedSession
    ) -> None:
        """Parse BRP breathing waveform file."""
        from .formats.edf import get_edf_record_count

        try:
            record_count = get_edf_record_count(file_path)
            file_size = file_path.stat().st_size

            if record_count == 0:
                logger.debug(
                    f"BRP file {file_path.name} has 0 data records (device on but not used, size={file_size} bytes)"
                )
                session.data_quality_notes.append(
                    "BRP: No data (device turned on briefly, not used)"
                )
                return

            logger.debug(
                f"BRP file {file_path.name} has {record_count} records (size={file_size} bytes)"
            )

            with EDFReader(file_path) as edf:
                # BRP typically contains Flow Rate signal
                # ResMed uses names like "Flow", "Flow.40ms", "FlowRate"
                flow_signal = self._find_signal(edf, ["Flow"])

                if flow_signal:
                    result = self._read_waveform(
                        edf,
                        flow_signal,
                        WaveformType.FLOW_RATE,
                        UNIT_FLOW,
                        convert_lps_to_lpm=True,
                    )
                    if result is None:
                        logger.warning(f"No data in flow signal {flow_signal}")
                        return

                    waveform, data = result
                    session.add_waveform(waveform)
                    logger.debug(
                        f"Parsed {len(data)} flow samples from {file_path.name}"
                    )

        except Exception as e:
            logger.warning(f"Failed to parse breathing waveforms: {e}")
            session.data_quality_notes.append(f"BRP parsing failed: {e}")

    def _read_waveform(
        self,
        edf: EDFReader,
        signal: str,
        waveform_type: WaveformType,
        default_unit: str,
        *,
        valid_range: tuple[float, float] | None = None,
        convert_lps_to_lpm: bool = False,
    ) -> tuple[WaveformData, np.ndarray] | None:
        """
        Read an EDF signal and build a WaveformData with min/max/mean stats.

        Args:
            edf: Open EDF reader
            signal: Signal label to read
            waveform_type: Waveform type for the resulting WaveformData
            default_unit: Unit to use when the EDF physical dimension is empty
            valid_range: Optional (low, high) inclusive range; stats are
                computed over the valid subset while the full array is stored
            convert_lps_to_lpm: Convert L/s data to L/min when applicable

        Returns:
            (waveform, valid_data) tuple, or None if the signal has no
            (valid) data. valid_data is the subset used for stats.
        """
        data, info = edf.read_signal(signal)

        if valid_range is not None:
            low, high = valid_range
            valid_data = data[(data >= low) & (data <= high)]
            if len(valid_data) == 0:
                return None
        else:
            if len(data) == 0:
                return None
            valid_data = data

        unit = info.physical_dimension or default_unit
        if convert_lps_to_lpm and unit == "L/s":
            data = data * 60.0
            valid_data = data
            unit = UNIT_FLOW

        min_value, max_value, mean_value = extract_basic_stats(valid_data)
        waveform = WaveformData(
            waveform_type=waveform_type,
            sample_rate=edf.get_sample_rate(signal),
            unit=unit,
            timestamps=edf.get_timestamps(signal, data),
            values=data,
            min_value=min_value,
            max_value=max_value,
            mean_value=mean_value,
        )
        return waveform, valid_data

    def _find_signal(self, edf: Any, patterns: list[str]) -> str | None:
        """Find signal name matching any of the patterns."""
        signals = edf.list_signal_labels()
        for pattern in patterns:
            signal: str
            for signal in signals:
                if pattern.lower() in signal.lower():
                    return signal
        return None

    def _find_signal_excluding(
        self, edf: Any, patterns: list[str], exclude: set[str]
    ) -> str | None:
        """Find signal name matching any of the patterns, excluding already-matched signals."""
        signals = edf.list_signal_labels()
        for pattern in patterns:
            signal: str
            for signal in signals:
                if signal in exclude:
                    continue
                if pattern.lower() in signal.lower():
                    return signal
        return None

    def _parse_pressure_leak(self, file_path: Path, session: UnifiedSession) -> None:
        """Parse PLD pressure/leak file."""
        from .formats.edf import get_edf_record_count

        try:
            record_count = get_edf_record_count(file_path)
            file_size = file_path.stat().st_size

            if record_count == 0:
                logger.debug(
                    f"PLD file {file_path.name} has 0 data records (device on but not used, size={file_size} bytes)"
                )
                session.data_quality_notes.append(
                    "PLD: No data (device turned on briefly, not used)"
                )
                return

            logger.debug(
                f"PLD file {file_path.name} has {record_count} records (size={file_size} bytes)"
            )

            with EDFReader(file_path) as edf:
                # Parse all three pressure signals separately
                # ResMed signal names: "MaskPress.2s", "EPRPress.2s", "Press.2s" (AirSense 11)
                # or "MaskPressure", "Exp Pres", "Pressure" (older devices)
                mask_signal = self._find_signal(edf, ["MaskPress", "Mask Pres"])
                epap_signal = self._find_signal(edf, ["EPRPress", "Exp Pres"])

                claimed = {s for s in (mask_signal, epap_signal) if s is not None}
                therapy_signal = self._find_signal_excluding(
                    edf, ["Press", "Therapy Pres", "Pressure"], exclude=claimed
                )

                # Parse therapy pressure (device's target pressure)
                if therapy_signal:
                    result = self._read_waveform(
                        edf,
                        therapy_signal,
                        WaveformType.THERAPY_PRESSURE,
                        UNIT_PRESSURE,
                    )
                    if result is None:
                        logger.warning(
                            f"No data in therapy pressure signal {therapy_signal}"
                        )
                    else:
                        session.add_waveform(result[0])

                # Parse mask pressure (measured at mask)
                if mask_signal:
                    result = self._read_waveform(
                        edf, mask_signal, WaveformType.MASK_PRESSURE, UNIT_PRESSURE
                    )
                    if result is None:
                        logger.warning(f"No data in mask pressure signal {mask_signal}")
                    else:
                        session.add_waveform(result[0])

                # Parse EPAP (therapy pressure minus EPR)
                if epap_signal:
                    result = self._read_waveform(
                        edf, epap_signal, WaveformType.EPAP, UNIT_PRESSURE
                    )
                    if result is None:
                        logger.warning(f"No data in EPAP signal {epap_signal}")
                    else:
                        session.add_waveform(result[0])

                # ResMed uses names like "Leak.2s", "LeakRate"
                leak_signal = self._find_signal(edf, ["Leak"])

                if leak_signal:
                    result = self._read_waveform(
                        edf,
                        leak_signal,
                        WaveformType.LEAK_RATE,
                        UNIT_FLOW,
                        convert_lps_to_lpm=True,
                    )
                    if result is None:
                        logger.warning(f"No data in leak signal {leak_signal}")
                    else:
                        session.add_waveform(result[0])

                logger.debug(f"Parsed pressure/leak from {file_path.name}")

        except Exception as e:
            logger.warning(f"Failed to parse pressure/leak: {e}")
            session.data_quality_notes.append(f"PLD parsing failed: {e}")

    def _parse_events(self, file_path: Path, session: UnifiedSession) -> None:
        """Parse EVE events file."""
        from .formats.edf import EDFDiscontinuousReader, is_discontinuous_edf

        is_discontinuous = is_discontinuous_edf(file_path)

        if is_discontinuous:
            logger.debug(
                f"EVE file {file_path.name} is discontinuous (EDF+D format) - "
                f"using MNE library to read annotations"
            )
            session.data_quality_notes.append(
                "EVE file is discontinuous (mask removal detected during session)"
            )

        try:
            if is_discontinuous:
                with EDFDiscontinuousReader(file_path) as edf:
                    annotations = edf.read_annotations()
            else:
                with EDFReader(file_path) as edf:
                    annotations = edf.read_annotations()

            event_count = 0
            filtered_count = 0
            unknown_count = 0
            unknown_annotations = set()

            for annotation in annotations:
                event_type = None
                annotation_text = None

                for text in annotation.annotations:
                    if text in FILTERED_ANNOTATIONS:
                        filtered_count += 1
                        break

                    if text in EVENT_TYPE_MAP:
                        event_type = EVENT_TYPE_MAP[text]
                        annotation_text = text
                        break

                if annotation_text is None and event_type is None:
                    for text in annotation.annotations:
                        if text not in FILTERED_ANNOTATIONS:
                            unknown_annotations.add(text)
                            unknown_count += 1
                    continue

                if event_type is None:
                    continue

                duration = annotation.duration if annotation.duration else 10.0

                event = RespiratoryEvent(
                    event_type=event_type,
                    start_time=annotation.to_datetime(session.start_time),
                    duration_seconds=duration,
                )

                session.add_event(event)
                event_count += 1

            if is_discontinuous and event_count > 0:
                logger.debug(
                    f"Successfully parsed {event_count} events from discontinuous EVE file "
                    f"(mask removal periods detected)"
                )
            else:
                logger.debug(f"Parsed {event_count} events from {file_path.name}")

            if filtered_count > 0:
                logger.debug(f"Filtered out {filtered_count} non-event annotations")
            if unknown_count > 0:
                logger.warning(
                    f"Encountered {unknown_count} unknown annotations: {unknown_annotations}"
                )
                session.data_quality_notes.append(
                    f"Unknown event annotations: {', '.join(sorted(unknown_annotations))}"
                )

        except Exception as e:
            if "discontinuous" in str(e).lower():
                logger.warning(
                    "EVE file is discontinuous (mask removal during session) - events not imported"
                )
                session.data_quality_notes.append(
                    "EVE file is discontinuous (mask removal detected) - events cannot be imported"
                )
            else:
                logger.warning(f"Failed to parse events: {e}")
                session.data_quality_notes.append(f"EVE parsing failed: {e}")

    def _parse_eve_files_for_night(
        self, eve_files: list[Path], session: UnifiedSession
    ) -> None:
        """
        Parse all EVE files for a night and apply events to session based on timestamp filtering.

        Following OSCAR's behavior: EVE files store data for the whole day, so we read all EVE files
        and filter events to only include those within this session's time range.

        Args:
            eve_files: List of paths to EVE files for this night
            session: The session to add events to
        """
        from .formats.edf import (
            EDFDiscontinuousReader,
            get_edf_record_count,
            is_discontinuous_edf,
        )

        total_events_found = 0
        total_events_added = 0
        total_events_filtered = 0

        for eve_file in eve_files:
            try:
                record_count = get_edf_record_count(eve_file)
                if record_count == 0:
                    logger.debug(f"Skipping zero-record EVE file: {eve_file.name}")
                    continue

                is_discontinuous = is_discontinuous_edf(eve_file)

                if is_discontinuous:
                    with EDFDiscontinuousReader(eve_file) as edf:
                        annotations = edf.read_annotations()
                        eve_start_time = edf.get_header().start_datetime
                else:
                    with EDFReader(eve_file) as edf:
                        annotations = edf.read_annotations()
                        eve_start_time = edf.get_header().start_datetime

                logger.debug(
                    f"Processing EVE file {eve_file.name} with {len(annotations)} annotation(s)"
                )

                for annotation in annotations:
                    event_timestamp = annotation.to_datetime(eve_start_time)

                    if not (session.start_time <= event_timestamp <= session.end_time):
                        total_events_filtered += 1
                        continue

                    event_type = None

                    for text in annotation.annotations:
                        if text in FILTERED_ANNOTATIONS:
                            break

                        if text in EVENT_TYPE_MAP:
                            event_type = EVENT_TYPE_MAP[text]
                            break

                    if event_type is None:
                        continue

                    duration = annotation.duration if annotation.duration else 10.0

                    event = RespiratoryEvent(
                        event_type=event_type,
                        start_time=event_timestamp,
                        duration_seconds=duration,
                    )

                    session.add_event(event)
                    total_events_added += 1
                    total_events_found += 1

            except Exception as e:
                logger.warning(f"Failed to parse EVE file {eve_file.name}: {e}")
                continue

        if total_events_added > 0:
            logger.debug(
                f"Added {total_events_added} events to session from {len(eve_files)} EVE file(s) "
                f"({total_events_filtered} events filtered out by timestamp)"
            )
        elif total_events_found == 0:
            logger.debug(f"No events found in {len(eve_files)} EVE file(s)")
        else:
            logger.debug(
                f"No events within session time range (found {total_events_found} total events, "
                f"all filtered out)"
            )

    def _parse_str_settings(
        self, str_file: Path, session_date: date
    ) -> TherapySettings | None:
        """
        Parse therapy settings from STR.edf for a specific session date.

        STR.edf contains one data record per day since device initialization.
        Each signal has one sample per record, representing that day's setting value.

        Args:
            str_file: Path to STR.edf file
            session_date: Date of session to get settings for

        Returns:
            TherapySettings populated from STR.edf, or None if not found
        """
        try:
            with EDFReader(str_file) as edf:
                header = edf.get_header()

                str_start_date = header.start_datetime.date()
                days_offset = (session_date - str_start_date).days

                if days_offset < 0 or days_offset >= header.num_data_records:
                    logger.warning(
                        f"Session date {session_date} outside STR.edf range "
                        f"({str_start_date} + {header.num_data_records} days)"
                    )
                    return None

                settings_values = {}
                signals = edf.get_signal_info()

                for signal_label, setting_key in self.STR_SETTINGS_MAP.items():
                    if signal_label in signals:
                        data, _ = edf.read_signal(signal_label)
                        if len(data) > days_offset:
                            settings_values[setting_key] = data[days_offset]

                if not settings_values:
                    logger.debug(f"No settings found in STR.edf for {session_date}")
                    return None

                return self._convert_str_to_therapy_settings(settings_values)

        except Exception as e:
            logger.warning(f"Failed to parse STR.edf settings: {e}")
            return None

    def _preload_str_settings(
        self, str_file: Path
    ) -> dict[date, dict[str, float]] | None:
        """
        Pre-read all settings from STR.edf for all dates.

        This method reads the entire STR.edf file once and caches all settings
        in memory. Used to avoid concurrent file access issues when parsing
        sessions in parallel.

        Args:
            str_file: Path to STR.edf file

        Returns:
            Dictionary mapping date -> {setting_name: value}, or None if file
            doesn't exist or can't be read
        """
        if not str_file.exists():
            return None

        try:
            with EDFReader(str_file) as edf:
                header = edf.get_header()
                start_date = header.start_datetime.date()
                num_records = header.num_data_records

                all_settings: dict[date, dict[str, float]] = {}
                signals = edf.get_signal_info()

                for signal_label, setting_name in self.STR_SETTINGS_MAP.items():
                    if signal_label in signals:
                        data, _ = edf.read_signal(signal_label)

                        for record_idx in range(min(num_records, len(data))):
                            record_date = start_date + timedelta(days=record_idx)

                            if record_date not in all_settings:
                                all_settings[record_date] = {}

                            all_settings[record_date][setting_name] = float(
                                data[record_idx]
                            )

                logger.debug(
                    f"Preloaded STR.edf settings for {len(all_settings)} days "
                    f"({start_date} to {start_date + timedelta(days=num_records - 1)})"
                )
                return all_settings

        except Exception as e:
            logger.warning(f"Failed to preload STR.edf settings: {e}")
            return None

    def _preload_str_summaries(
        self, str_file: Path
    ) -> dict[date, dict[str, float]] | None:
        """
        Pre-read all summary percentiles from STR.edf for all dates.

        STR.edf contains pre-computed per-day statistics (medians, 95th percentiles, etc.)
        that can supplement or validate waveform-derived statistics.

        Args:
            str_file: Path to STR.edf file

        Returns:
            Dictionary mapping date -> {stat_name: value}, or None if file
            doesn't exist or can't be read
        """
        if not str_file.exists():
            return None

        try:
            with EDFReader(str_file) as edf:
                header = edf.get_header()
                start_date = header.start_datetime.date()
                num_records = header.num_data_records

                all_summaries: dict[date, dict[str, float]] = {}
                signals = edf.get_signal_info()

                for signal_patterns, stat_name in self.STR_SUMMARY_SIGNALS.items():
                    matched_signal = None
                    for pattern in signal_patterns:
                        if pattern in signals:
                            matched_signal = pattern
                            break

                    if matched_signal:
                        data, _ = edf.read_signal(matched_signal)

                        for record_idx in range(min(num_records, len(data))):
                            record_date = start_date + timedelta(days=record_idx)

                            if record_date not in all_summaries:
                                all_summaries[record_date] = {}

                            all_summaries[record_date][stat_name] = float(
                                data[record_idx]
                            )

                if all_summaries:
                    logger.debug(
                        f"Preloaded STR.edf summaries for {len(all_summaries)} days "
                        f"({start_date} to {start_date + timedelta(days=num_records - 1)})"
                    )
                return all_summaries if all_summaries else None

        except Exception as e:
            logger.warning(f"Failed to preload STR.edf summaries: {e}")
            return None

    def _convert_str_to_therapy_settings(
        self, values: dict[str, float]
    ) -> TherapySettings:
        """
        Convert raw STR.edf values to TherapySettings model.

        Args:
            values: Dictionary of setting keys to raw float values

        Returns:
            TherapySettings instance with proper type conversions
        """
        mode_value = values.get("mode")
        mode = (
            self.MODE_MAP.get(int(mode_value), TherapyMode.APAP)
            if mode_value is not None
            else TherapyMode.APAP
        )

        epr_type_value = values.get("epr_mode")
        epr_mode = (
            self.EPR_TYPE_MAP.get(int(epr_type_value), "Unknown")
            if epr_type_value is not None
            else None
        )

        mask_value = values.get("mask_type")
        mask_type = (
            self.MASK_TYPE_MAP.get(int(mask_value), "Unknown")
            if mask_value is not None
            else None
        )

        ramp_enabled_val = values.get("ramp_enabled")
        ramp_enabled = ramp_enabled_val == 2 if ramp_enabled_val is not None else None
        humidity_enabled_val = values.get("humidity_enabled")
        humidity_enabled = (
            humidity_enabled_val == 2 if humidity_enabled_val is not None else None
        )
        tube_temp_enabled_val = values.get("tube_temp_enabled")
        tube_temp_enabled = (
            tube_temp_enabled_val == 2 if tube_temp_enabled_val is not None else None
        )
        smart_start_val = values.get("smart_start")
        smart_start = smart_start_val == 2 if smart_start_val is not None else None

        climate_value = values.get("climate_control")
        climate_control = (
            self.CLIMATE_CONTROL_MAP.get(int(climate_value), "Manual")
            if climate_value is not None
            else None
        )

        def _validate_positive(
            value: float | None, min_val: float = 0.0
        ) -> float | None:
            """Return value if >= min_val, else None (filters sentinel values)."""
            return value if value is not None and value >= min_val else None

        ramp_time_value = values.get("ramp_time")
        ramp_time = (
            int(ramp_time_value)
            if ramp_time_value is not None and ramp_time_value >= 0 and ramp_enabled
            else None
        )

        epr_level_value = values.get("epr_level")
        epr_level = (
            int(epr_level_value)
            if epr_level_value is not None and 0 <= epr_level_value <= 3
            else None
        )

        humidity_level_value = values.get("humidity_level")
        humidity_level = (
            int(humidity_level_value)
            if humidity_level_value is not None and humidity_level_value >= 0
            else None
        )

        pressure_fixed = _validate_positive(values.get("pressure_fixed"), min_val=1.0)
        pressure_min = _validate_positive(values.get("pressure_min"), min_val=1.0)
        pressure_max = _validate_positive(values.get("pressure_max"), min_val=1.0)
        ramp_start_pressure = _validate_positive(
            values.get("ramp_start_pressure"), min_val=1.0
        )
        tube_temp = _validate_positive(values.get("tube_temp"), min_val=1.0)

        ab_filter_value = values.get("ab_filter")
        ab_filter = (
            self.AB_FILTER_MAP.get(int(ab_filter_value), "Unknown")
            if ab_filter_value is not None and ab_filter_value >= 0
            else None
        )

        return TherapySettings(
            mode=mode,
            pressure_fixed=pressure_fixed,
            pressure_min=pressure_min,
            pressure_max=pressure_max,
            epr_level=epr_level,
            epr_mode=epr_mode,
            ramp_enabled=ramp_enabled,
            ramp_time=ramp_time,
            ramp_start_pressure=ramp_start_pressure,
            humidity_enabled=humidity_enabled,
            humidity_level=humidity_level,
            tube_temp_enabled=tube_temp_enabled,
            tube_temp=tube_temp,
            climate_control=climate_control,
            smart_start=smart_start,
            mask_type=mask_type,
            ab_filter=ab_filter,
        )
