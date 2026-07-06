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

import hashlib
import json
import logging
import math
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
        # S.AS.StartPress is the first-choice ramp pressure for APAP on S10 (OSCAR :1938)
        "S.AS.StartPress": "as_start_press",
        "S.EPR.Level": "epr_level",
        "S.EPR.EPRType": "epr_type_raw",
        "S.EPR.EPREnable": "epr_enable_raw",  # EPR on/off gate (OSCAR :2202-2215)
        "S.EPR.ClinEnable": "epr_clin_enable_raw",  # clinician override gate
        "S.RampEnable": "ramp_enabled",
        "S.RampTime": "ramp_time",
        "S.ClimateControl": "climate_control",
        "S.HumEnable": "humidity_enabled",
        "S.HumLevel": "humidity_level",
        "S.TempEnable": "tube_temp_enabled",
        "S.Temp": "tube_temp",
        "S.SmartStart": "smart_start",
        "S.SmartStop": "smart_stop_raw",  # OSCAR :2324-2327
        "S.ABFilter": "ab_filter",
        "S.Mask": "mask_type",
        "S.Tube": "tube_raw",  # tube type (OSCAR :2343)
        "S.PtAccess": "pt_access_raw",  # patient access level (OSCAR :2311-2317)
        "S.AS.Comfort": "comfort_raw",  # Response/comfort setting (OSCAR :2180-2183)
        # S10 bare timing/control signals shared by vAuto and bilevel modes.
        # OSCAR uses sigprefix "S." for S10 (vAuto :2390-2411; bilevel :2347-2392);
        # S11 uses "S.VA." / "S.S." respectively.
        "S.Cycle": "s10_cycle",
        "S.Trigger": "s10_trigger",
        "S.TiMax": "s10_ti_max",
        "S.TiMin": "s10_ti_min",
        # S10 bilevel-only bare signals (OSCAR bilevel :2347-2392).
        "S.RiseEnable": "s10_rise_enable",
        "S.EasyBreathe": "s10_easy_breathe",
        "S.RiseTime": "s10_rise_time",
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

    # S9/10-basis EPR type map — keyed on the post-normalization value that both
    # families resolve to: S10 raw+1 and S11 raw+1−1 = raw (OSCAR :2195-2215).
    EPR_TYPE_MAP = {0: "Off", 1: "Ramp Only", 2: "Full Time"}
    # S9/10-series mask codes per OSCAR resmed_loader.cpp (S.Mask signal).
    # 11-series devices emit raw 2–4; normalize by −2 before lookup.
    MASK_TYPE_MAP = {0: "Pillows", 1: "Full Face", 2: "Nasal"}
    # Shared climate control and AB-filter maps on the S10 basis.
    # S11 raw values are normalized by −1 before lookup (OSCAR :2292-2299).
    # Climate control option labels: OSCAR :233-234.
    CLIMATE_CONTROL_MAP = {0: "Auto", 1: "Manual"}
    AB_FILTER_MAP = {0: "Standard", 1: "Antibacterial"}
    # Trigger/cycle sensitivity maps — S10 basis (S11 raw−1 before lookup).
    # OSCAR :288-294 (Trigger), :280-286 (Cycle). "Med" spelling matches OSCAR.
    TRIGGER_MAP = {0: "Very Low", 1: "Low", 2: "Med", 3: "High", 4: "Very High"}
    CYCLE_MAP = {0: "Very Low", 1: "Low", 2: "Med", 3: "High", 4: "Very High"}
    # APAP response (S.AS.Comfort) labels — S10 basis (OSCAR :251-255).
    RESPONSE_MAP = {0: "Standard", 1: "Soft"}
    # Patient access: S10 (OSCAR :224-228); S11 uses PT_VIEW_MAP instead (OSCAR :263-267).
    PT_ACCESS_MAP = {0: "Plus", 1: "On"}
    # Patient view: S11 (OSCAR :263-267). OSCAR's effective display is "Off"/"On"
    # (:269-270 overwrites :263-267 via addOption), but "Advanced"/"Simple" matches
    # the AirSense 11 user interface — intentional deviation.
    PT_VIEW_MAP = {0: "Advanced", 1: "Simple"}
    # Tube type — community-sourced tube-diameter naming; raw value, no normalization.
    # OSCAR declares RMS9_TubeType but never initializes, labels, or stores S.Tube.
    TUBE_TYPE_MAP = {15: "SlimLine", 19: "Standard"}

    # These two tables are underscore-private because they are internals of the
    # two-step mode decode (translate S11 raw → S10 basis, then map to TherapyMode).
    # The per-signal lookup tables (EPR_TYPE_MAP, MASK_TYPE_MAP, etc.) are public
    # because they are referenced independently of the mode decode path.

    # S11 raw mode → S10-basis rms9_mode (OSCAR :1857-1888).
    # Values absent from this table (0, 5, and any unknown) → warn + skip.
    _S11_MODE_TO_S10 = {
        1: 1,  # AutoSet → APAP
        2: 11,  # AutoSet for Her → A4Her APAP
        3: 0,  # CPAP → CPAP
        4: 3,  # BiLevel-S → BILEVEL_FIXED
        6: 7,  # ASV → ASV
        7: 8,  # ASVAuto → ASV_VARIABLE_EPAP (ASV_AUTO)
        8: 6,  # VAuto → BILEVEL_AUTO_FIXED_PS (BIPAP_AUTO)
    }

    # Unified S9/S10-basis mode map used for both families after S11 translation.
    _MODE_MAP = {
        0: TherapyMode.CPAP,
        1: TherapyMode.APAP,
        2: TherapyMode.BIPAP,
        3: TherapyMode.BIPAP,  # VPAP S (BILEVEL_FIXED)
        # OSCAR collapses S10 modes 3/4/5 all to BILEVEL_FIXED; 4/5 are S/T
        # variants — BIPAP_ST here is a deliberate local deviation.
        4: TherapyMode.BIPAP_ST,
        5: TherapyMode.BIPAP_ST,
        6: TherapyMode.BIPAP_AUTO,  # VAuto
        7: TherapyMode.ASV,
        8: TherapyMode.ASV_AUTO,  # ASV variable-EPAP (OSCAR MODE_ASV_VARIABLE_EPAP)
        9: TherapyMode.IVAPS,
        # 10 → PAC: warn+skip (absent)
        11: TherapyMode.APAP,  # APAP for Her
    }

    STR_VAUTO_SIGNALS = {
        "S.VA.StartPress": "va_start_press",
        "S.VA.MaxIPAP": "va_max_ipap",
        "S.VA.MinEPAP": "va_min_epap",
        "S.VA.PS": "va_ps",
        "S.VA.TiMax": "va_ti_max",
        "S.VA.TiMin": "va_ti_min",
        "S.VA.Trigger": "va_trigger",
        "S.VA.Cycle": "va_cycle",
    }
    STR_SMODE_SIGNALS = {
        "S.S.StartPress": "s_start_press",
        "S.S.IPAP": "s_ipap",
        "S.S.EPAP": "s_epap",
        "S.S.EasyBreathe": "s_easy_breathe",
        "S.S.TiMax": "s_ti_max",
        "S.S.TiMin": "s_ti_min",
        "S.S.RiseEnable": "s_rise_enable",
        "S.S.RiseTime": "s_rise_time",
        "S.S.Trigger": "s_trigger",
        "S.S.Cycle": "s_cycle",
    }
    STR_AFH_SIGNALS = {
        "S.AFH.StartPress": "afh_start_press",
        "S.AFH.MaxPress": "afh_max_press",
        "S.AFH.MinPress": "afh_min_press",
    }
    # S10 bilevel pressure signals (S.BL.*); timing/rise/easybreathe use bare "S.*"
    # and are loaded via STR_SETTINGS_MAP into s10_* keys (OSCAR :2347-2392).
    # S.BL.* timing entries here are kept as defensive fallbacks only — real S10
    # devices emit bare S.Cycle/S.Trigger/S.TiMax/S.TiMin/S.RiseEnable/S.EasyBreathe.
    # S11 bilevel uses S.S.* already covered by STR_SMODE_SIGNALS.
    STR_BILEVEL_S10_SIGNALS = {
        "S.BL.IPAP": "bl_ipap",
        "S.BL.EPAP": "bl_epap",
        "S.BL.StartPress": "bl_start_press",
        "S.BL.EasyBreathe": "bl_easy_breathe",
        "S.BL.RiseEnable": "bl_rise_enable",
        "S.BL.RiseTime": "bl_rise_time",
        "S.BL.Cycle": "bl_cycle",
        "S.BL.Trigger": "bl_trigger",
        "S.BL.TiMax": "bl_ti_max",
        "S.BL.TiMin": "bl_ti_min",
    }
    # ASV fixed-EPAP pressures (OSCAR :2093-2108); same signals for both families.
    STR_ASV_SIGNALS = {
        "S.AV.StartPress": "av_start_press",
        "S.AV.EPAP": "av_epap",
        "S.AV.MinPS": "av_min_ps",
        "S.AV.MaxPS": "av_max_ps",
    }
    # ASV variable-EPAP pressures (OSCAR :2109-2127); same signals for both families.
    STR_ASV_AUTO_SIGNALS = {
        "S.AA.StartPress": "aa_start_press",
        "S.AA.MinEPAP": "aa_min_epap",
        "S.AA.MaxEPAP": "aa_max_epap",
        "S.AA.MinPS": "aa_min_ps",
        "S.AA.MaxPS": "aa_max_ps",
    }
    # iVAPS pressures (OSCAR :2049-2092); same signals for both families.
    STR_IVAPS_SIGNALS = {
        "S.i.StartPress": "iv_start_press",
        "S.i.EPAP": "iv_epap",
        "S.i.EPAPAuto": "iv_epap_auto",
        "S.i.MinPS": "iv_min_ps",
        "S.i.MaxPS": "iv_max_ps",
        "S.i.MinEPAP": "iv_min_epap",
        "S.i.MaxEPAP": "iv_max_epap",
    }

    # Merged view of all STR signal groups — invariant across files, built once.
    ALL_STR_SIGNAL_MAPS = {
        **STR_SETTINGS_MAP,
        **STR_VAUTO_SIGNALS,
        **STR_SMODE_SIGNALS,
        **STR_AFH_SIGNALS,
        **STR_BILEVEL_S10_SIGNALS,
        **STR_ASV_SIGNALS,
        **STR_ASV_AUTO_SIGNALS,
        **STR_IVAPS_SIGNALS,
    }

    _ELEVEN_SERIES_RE = re.compile(r"(AirSense|AirCurve)\s*11")

    @staticmethod
    def _is_eleven_series(model: str) -> bool:
        """Return True if the device model string identifies an 11-series machine."""
        return bool(ResmedEDFParser._ELEVEN_SERIES_RE.search(model or ""))

    def __init__(self) -> None:
        """Initialize ResMed parser."""
        super().__init__()
        self._data_root: Path | None = None
        self._root_metadata: DataRoot | None = None
        self._all_roots: list[DataRoot] = []
        self._finder = DataRootFinder()
        self._str_series11: bool = False

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
        """Extract device serial number from Identification.json or .tgt (S9 fallback)."""
        id_json = path / "Identification.json"
        if id_json.exists():
            try:
                with open(id_json) as f:
                    data = json.load(f)
                fg = data.get("FlowGenerator", {})
                profiles = fg.get("IdentificationProfiles", {})
                product = profiles.get("Product", {})
                serial = product.get("SerialNumber")
                return serial if isinstance(serial, str) else None
            except Exception:
                return None

        # S9 devices ship Identification.tgt (key=value lines) instead of JSON.
        # OSCAR parseIdentFile :2467-2513: reads tgt when json is absent.
        return self._parse_tgt_field(path / "Identification.tgt", "SerialNumber")

    def _parse_tgt_field(self, tgt_path: Path, field: str) -> str | None:
        """Return the value for a key from an Identification.tgt key=value file."""
        if not tgt_path.exists():
            return None
        try:
            # Real ResMed .tgt files are a few hundred bytes; reject anything
            # larger to guard against corrupt no-newline binary content.
            if tgt_path.stat().st_size > 4096:
                return None
            with open(tgt_path, encoding="ascii", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        key, _, value = line.partition("=")
                        if key.strip() == field:
                            return value.strip() or None
        except Exception:
            pass
        return None

    def _detect_series11(self, root_path: Path) -> bool:
        """Return True when the device is Series 11 based on ProductCode.

        OSCAR rule: modelnumber >= 39000 → Series 11 STR mode encoding.
        Falls back to .tgt parsing for S9 devices that ship Identification.tgt
        instead of Identification.json (OSCAR parseIdentFile :2467-2513).
        """
        id_json = root_path / "Identification.json"
        if id_json.exists():
            try:
                with open(id_json, encoding="utf-8") as f:
                    data = json.load(f)
                fg = data.get("FlowGenerator", {})
                profiles = fg.get("IdentificationProfiles", {})
                product = profiles.get("Product", {})
                code = product.get("ProductCode")
                # int(float(...)) handles JSON numbers stored as floats (e.g. 39000.0).
                return code is not None and int(float(str(code))) >= 39000
            except Exception:
                return False

        # S9 devices use Identification.tgt; ProductCode in tgt is always < 39000.
        code_str = self._parse_tgt_field(
            root_path / "Identification.tgt", "ProductCode"
        )
        if code_str is not None:
            try:
                return int(code_str) >= 39000
            except ValueError:
                pass
        return False

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
        progress_callback: Callable[[str], None] | None = None,
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
            progress_callback: Optional callback for progress messages
        """
        path = Path(path)

        path, night_items = self._discover_session_files(path, sort_by)
        night_items = self._filter_night_items(night_items, date_from, date_to)
        total_nights = len(night_items)

        device_info = self.get_device_info(path)

        self._str_series11 = self._detect_series11(path)
        str_settings_cache, str_summaries_cache = self._load_str_caches(path)

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

                completed = 0

                def emit_progress() -> None:
                    nonlocal completed
                    completed += 1
                    if progress_callback:
                        progress_callback(
                            f"Parsing session {completed}/{total_nights}..."
                        )

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
                        emit_progress()
                        continue

                    emit_progress()

                    if session is None:
                        continue

                    yield session
                    sessions_yielded += 1
        else:
            completed = 0

            def emit_progress() -> None:
                nonlocal completed
                completed += 1
                if progress_callback:
                    progress_callback(f"Parsing session {completed}/{total_nights}...")

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
                    emit_progress()
                    continue

                emit_progress()

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
                else:
                    if "unreadable" in snapshot_name:
                        logger.warning(
                            f"Cannot distinguish STR snapshot (file unreadable): "
                            f"STR_Backup/{snapshot_name} already exists"
                        )
                    else:
                        logger.debug(
                            f"Duplicate STR snapshot skipped: "
                            f"STR_Backup/{snapshot_name} already exists"
                        )
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

            for idx, (night_str, src_files) in enumerate(sorted(flat.items()), 1):
                if progress_callback:
                    progress_callback(f"Backing up night {idx}/{total_nights}...")
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
        """Derive STR_Backup snapshot filename from the EDF header start date.

        Falls back to ``STR-unknown-<hash>.edf`` (SHA-256 prefix) when the
        header is too short or cannot be decoded, so distinct unparseable files
        get distinct snapshot names and re-importing the same file stays
        idempotent.
        """
        try:
            with open(str_path, "rb") as f:
                header = f.read(256)
            if len(header) < 184:
                logger.warning(
                    f"STR header too short ({len(header)} bytes) in {str_path}; "
                    "falling back to content-hash snapshot name"
                )
                return f"STR-unknown-{ResmedEDFParser._file_content_hash(str_path)}.edf"
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
            return f"STR-unknown-{ResmedEDFParser._file_content_hash(str_path)}.edf"

    @staticmethod
    def _file_content_hash(path: Path) -> str:
        """Return the first 12 hex chars of the SHA-256 of the file at ``path``.

        Uses chunked I/O so large or corrupted files on SD cards do not cause
        unbounded memory use. Returns the literal string ``"unreadable"`` on
        any error so callers remain non-raising.
        """
        try:
            with open(path, "rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
            return digest[:12]
        except Exception:
            return "unreadable"

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

    @staticmethod
    def _therapy_date(start_time: datetime) -> date:
        """
        Return the therapy-day date for a session start time.

        ResMed STR.edf records run noon-to-noon: a record for day D covers
        the 24-hour window starting at 12:00 on day D.  A session that begins
        after midnight (e.g. 01:11 on May 28) belongs to the therapy night
        that started on May 27, so the correct record key is May 27.

        Subtracting 12 hours before extracting the date maps every session to
        its owning STR record, regardless of whether it crosses midnight.
        """
        return (start_time - timedelta(hours=12)).date()

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
            therapy_day = self._therapy_date(session.start_time)
            if therapy_day in str_settings_cache:
                settings = self._convert_str_to_therapy_settings(
                    str_settings_cache[therapy_day],
                )
                if settings:
                    session.settings = settings
                    logger.debug(
                        f"Loaded settings for session {session_id}: mode={settings.mode}"
                    )

        if str_summaries_cache:
            therapy_day = self._therapy_date(session.start_time)
            if therapy_day in str_summaries_cache:
                summaries = str_summaries_cache[therapy_day]
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
        self, str_file: Path, session_date: date, is_eleven_series: bool | None = None
    ) -> TherapySettings | None:
        """
        Parse therapy settings from STR.edf for a specific session date.

        STR.edf contains one data record per day since device initialization.
        Each signal has one sample per record, representing that day's setting value.

        Args:
            str_file: Path to STR.edf file
            session_date: Date of session to get settings for
            is_eleven_series: Passed through unchanged to _convert_str_to_therapy_settings;
                None lets the converter fall back to ProductCode-derived self._str_series11.

        Returns:
            TherapySettings populated from STR.edf, None if not found, or None
            for no-usage days where ResMed writes all-sentinel (negative) values
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

                for signal_label, setting_key in self.ALL_STR_SIGNAL_MAPS.items():
                    if signal_label in signals:
                        data, _ = edf.read_signal(signal_label)
                        if len(data) > days_offset:
                            settings_values[setting_key] = data[days_offset]

                if not settings_values:
                    logger.debug(f"No settings found in STR.edf for {session_date}")
                    return None

                return self._convert_str_to_therapy_settings(
                    settings_values, is_eleven_series
                )

        except Exception as e:
            logger.warning(f"Failed to parse STR.edf settings: {e}")
            return None

    def _load_str_caches(
        self, root_path: Path
    ) -> tuple[
        dict[date, dict[str, float]] | None, dict[date, dict[str, float]] | None
    ]:
        """
        Merge STR settings and summaries from STR_Backup/*.edf and STR.edf.

        When two files share the same EDF start-date (e.g., a rolled backup and
        the primary), only the longer file (more records) is used — matching
        OSCAR's behaviour (resmed_loader.cpp :896-908, :980-993).  Files whose
        header cannot be read are always included as a safe fallback.

        Two-pass approach: first read only EDF headers to select per-start-date
        winners cheaply, then fully load only winning files.  If a selected winner's
        full load returns no settings, the next-best candidate for that start-date
        is tried (corrupt-file fallback).

        Returns (None, None) when no files are readable.
        """
        backup_dir = root_path / "STR_Backup"
        backup_files: list[Path] = (
            sorted(backup_dir.glob("STR-*.edf")) if backup_dir.is_dir() else []
        )
        primary = root_path / "STR.edf"
        all_files = backup_files + ([primary] if primary.exists() else [])

        if not all_files:
            return None, None

        # First pass: header-only reads to select winners per start-date.
        # candidates_per_start: start_date → [(num_records, file_idx), ...] in discovery order
        candidates_per_start: dict[date, list[tuple[int, int]]] = {}
        no_header_indices: list[int] = []

        for idx, f in enumerate(all_files):
            result = self._read_str_file_header(f)
            if result is None:
                no_header_indices.append(idx)
                continue
            start_date, num_records = result
            candidates_per_start.setdefault(start_date, []).append((num_records, idx))

        # For each start-date, sort candidates descending by (num_records, idx) so
        # the winner is first and later files break ties (matching the >= semantics
        # from OSCAR :896-908, :980-993).
        ordered_per_start: dict[date, list[int]] = {
            sd: [i for _, i in sorted(cands, key=lambda x: (x[0], x[1]), reverse=True)]
            for sd, cands in candidates_per_start.items()
        }

        # Build reverse map: file_idx → its start_date (winners only).
        idx_to_start: dict[int, date] = {
            idxs[0]: sd for sd, idxs in ordered_per_start.items()
        }

        # All winner indices plus no-header indices, in file-discovery order.
        winner_indices = set(idx_to_start)
        to_process = sorted(winner_indices | set(no_header_indices))

        merged_settings: dict[date, dict[str, float]] = {}
        merged_summaries: dict[date, dict[str, float]] = {}
        loaded_start_dates: set[date] = set()

        # Second pass: fully load winners; on empty settings fall back to next candidate.
        for idx in to_process:
            f = all_files[idx]

            if idx in no_header_indices:
                # Header unreadable — load unconditionally as safe fallback.
                s, u = self._preload_str_file(f)
                for d, vals in (s or {}).items():
                    merged_settings.setdefault(d, {}).update(vals)
                for d, vals in (u or {}).items():
                    merged_summaries.setdefault(d, {}).update(vals)
                continue

            start_date = idx_to_start[idx]
            if start_date in loaded_start_dates:
                continue  # already loaded a successful candidate for this start-date

            # Try each candidate in order (winner first, then fallbacks).
            for candidate_idx in ordered_per_start[start_date]:
                s, u = self._preload_str_file(all_files[candidate_idx])
                if s:
                    loaded_start_dates.add(start_date)
                    for d, vals in s.items():
                        merged_settings.setdefault(d, {}).update(vals)
                    for d, vals in (u or {}).items():
                        merged_summaries.setdefault(d, {}).update(vals)
                    break
            # If all candidates fail, nothing is merged for this start-date.

        return (merged_settings or None), (merged_summaries or None)

    def _read_str_file_header(self, str_file: Path) -> tuple[date, int] | None:
        """
        Read the EDF header of a STR file to obtain start-date and record count.

        Opens and immediately closes the file — no signal data is read.  Used by
        _load_str_caches to cheaply select per-start-date winners before doing the
        more expensive full signal load.

        Returns (start_date, num_data_records) or None on any failure.
        """
        if not str_file.exists():
            return None
        try:
            with EDFReader(str_file) as edf:
                header = edf.get_header()
                return header.start_datetime.date(), header.num_data_records
        except Exception:
            return None

    def _preload_str_file(
        self, str_file: Path
    ) -> tuple[
        dict[date, dict[str, float]] | None, dict[date, dict[str, float]] | None
    ]:
        """
        Open STR.edf exactly once and read all settings and summaries in one pass.

        Returns (settings_by_date, summaries_by_date); either element may be None
        if no data was found for that signal group.  Returns (None, None) on failure.
        """
        if not str_file.exists():
            return None, None

        try:
            with EDFReader(str_file) as edf:
                header = edf.get_header()
                # ResMed STR.edf records run noon-to-noon: the EDF header start
                # timestamp is always local noon of the first day (OSCAR :1595,
                # comment :1293 "each STR.edf record starts at 12 noon").
                # pyedflib returns this as a naive datetime; .date() extracts the
                # correct calendar date without any timezone conversion.
                start_date = header.start_datetime.date()
                num_records = header.num_data_records

                # Precompute per-record dates once; avoids re-adding timedelta
                # inside the per-signal × per-record inner loop.
                record_dates = [
                    start_date + timedelta(days=i) for i in range(num_records)
                ]

                signals = edf.get_signal_info()

                # --- settings loop ---
                all_settings: dict[date, dict[str, float]] = {}
                for signal_label, setting_name in self.ALL_STR_SIGNAL_MAPS.items():
                    if signal_label in signals:
                        data, _ = edf.read_signal(signal_label)
                        for record_idx in range(min(num_records, len(data))):
                            record_date = record_dates[record_idx]
                            if record_date not in all_settings:
                                all_settings[record_date] = {}
                            all_settings[record_date][setting_name] = float(
                                data[record_idx]
                            )

                # --- summaries loop ---
                all_summaries: dict[date, dict[str, float]] = {}
                for signal_patterns, stat_name in self.STR_SUMMARY_SIGNALS.items():
                    matched_signal = None
                    for pattern in signal_patterns:
                        if pattern in signals:
                            matched_signal = pattern
                            break
                    if matched_signal:
                        data, _ = edf.read_signal(matched_signal)
                        for record_idx in range(min(num_records, len(data))):
                            value = float(data[record_idx])
                            # Skip sentinel values: ResMed writes negative values
                            # on no-usage days; all physical stats are non-negative.
                            if not (value >= 0):
                                continue
                            record_date = record_dates[record_idx]
                            if record_date not in all_summaries:
                                all_summaries[record_date] = {}
                            all_summaries[record_date][stat_name] = value

                logger.debug(
                    f"Preloaded STR file {str_file.name}: {len(all_settings)} settings-days, "
                    f"{len(all_summaries)} summary-days "
                    f"({start_date} to {start_date + timedelta(days=num_records - 1)})"
                )
                return (all_settings or None), (all_summaries or None)

        except Exception as e:
            logger.warning(f"Failed to preload STR file {str_file.name}: {e}")
            return None, None

    def _preload_str_settings(
        self, str_file: Path
    ) -> dict[date, dict[str, float]] | None:
        """Thin wrapper — returns the settings half of _preload_str_file."""
        return self._preload_str_file(str_file)[0]

    def _preload_str_summaries(
        self, str_file: Path
    ) -> dict[date, dict[str, float]] | None:
        """Thin wrapper — returns the summaries half of _preload_str_file."""
        return self._preload_str_file(str_file)[1]

    def _convert_str_to_therapy_settings(
        self, values: dict[str, float], is_eleven_series: bool | None = None
    ) -> TherapySettings | None:
        """
        Convert raw STR.edf values to TherapySettings model.

        Returns None when the record is a no-usage sentinel day (all values
        negative/NaN), when the mode signal is absent, or when the mode value
        is unknown/unimplemented for the device family.

        Mode-specific fields are selected based on the active therapy mode so
        dormant presets for inactive modes are never stored.  All enum-valued
        signals are normalized to the S9/S10 basis before lookup (S11 emits
        raw values one higher; OSCAR resmed_loader.cpp :1869-1975).

        Args:
            values: Dictionary of setting keys to raw float values (as preloaded
                by _preload_str_settings — keys come from STR_SETTINGS_MAP and
                the signal group dicts).
            is_eleven_series: Explicit family override; defaults to
                self._str_series11 (set once from ProductCode by _detect_series11).
        """
        if values and all(not (v >= 0) for v in values.values()):
            return None

        # Authoritative family flag: ProductCode-based detection wins; param is
        # kept only for callers that need an explicit override.
        series11: bool = (
            self._str_series11 if is_eleven_series is None else is_eleven_series
        )

        # ------------------------------------------------------------------
        # Mode decode: translate S11 raw to S10 basis, then apply unified map.
        # ------------------------------------------------------------------
        mode_value = values.get("mode")
        if mode_value is None:
            logger.warning("STR record has no mode signal; discarding")
            return None
        if isinstance(mode_value, float) and math.isnan(mode_value):
            logger.warning("STR record has NaN mode value; discarding")
            return None

        raw_mode = int(mode_value)
        if series11:
            s10_mode = self._S11_MODE_TO_S10.get(raw_mode)
            if s10_mode is None:
                logger.warning(
                    "Unknown/unimplemented S11 mode %d; discarding record", raw_mode
                )
                return None
        else:
            s10_mode = raw_mode

        mode = self._MODE_MAP.get(s10_mode)
        if mode is None:
            logger.warning(
                "Unknown ResMed therapy mode (S10 basis %d); discarding record",
                s10_mode,
            )
            return None

        # ------------------------------------------------------------------
        # Helpers
        # ------------------------------------------------------------------
        def _pos(key: str, min_val: float = 0.0) -> float | None:
            v = values.get(key)
            return v if v is not None and v >= min_val else None

        def _nn(key: str) -> float | None:
            v = values.get(key)
            return v if v is not None and v >= 0 else None

        def _norm(key: str) -> float | None:
            """Return normalized (S10-basis) value for a "-1 family" enum signal.

            S11 devices emit these signals one higher than S10 (OSCAR :1869-1975).
            Subtracting 1 for S11 lets all downstream maps use S10 keys unchanged.
            NaN raw values return None so downstream boolean comparisons don't
            silently evaluate as False instead of unknown.
            """
            v = values.get(key)
            if v is None or math.isnan(v):
                return None
            return v - 1 if series11 else v

        def _apply_timing(
            ti_max_v: float | None,
            ti_min_v: float | None,
            trigger_v: float | None,
            cycle_v: float | None,
        ) -> None:
            """Write ti_max/ti_min/trigger/cycle into other_settings when present."""
            if ti_max_v is not None and ti_max_v >= 0:
                other_settings["ti_max"] = f"{ti_max_v:.1f}"
            if ti_min_v is not None and ti_min_v >= 0:
                other_settings["ti_min"] = f"{ti_min_v:.1f}"
            if trigger_v is not None and trigger_v >= 0:
                code = int(trigger_v)
                other_settings["trigger"] = self.TRIGGER_MAP.get(code, str(code))
            if cycle_v is not None and cycle_v >= 0:
                code = int(cycle_v)
                other_settings["cycle"] = self.CYCLE_MAP.get(code, str(code))

        # ------------------------------------------------------------------
        # Universal fields (all modes)
        # ------------------------------------------------------------------

        # Ramp enable raw=2 (S10) / raw=3 (S11→after norm=2) means SmartRamp.
        # OSCAR :2263-2267: S11 raw−1; raw=1 on S10 basis means normal ramp on.
        ramp_enabled_norm = _norm("ramp_enabled")
        ramp_enabled = ramp_enabled_norm == 1 if ramp_enabled_norm is not None else None
        smart_ramp = ramp_enabled_norm == 2 if ramp_enabled_norm is not None else False

        ramp_time_value = values.get("ramp_time")
        ramp_time = (
            int(ramp_time_value)
            if ramp_time_value is not None and ramp_time_value >= 0 and ramp_enabled
            else None
        )

        # Humidity/temp enable: S10 raw=1 → on; S11 raw=2 → norm=1 → on.
        # OSCAR :2329-2339: if (AS_eleven) --s_HumEnable / --s_TempEnable.
        hum_norm = _norm("humidity_enabled")
        humidity_enabled = hum_norm == 1 if hum_norm is not None else None
        humidity_level_value = values.get("humidity_level")
        humidity_level = (
            int(humidity_level_value)
            if humidity_level_value is not None and humidity_level_value >= 0
            else None
        )

        temp_norm = _norm("tube_temp_enabled")
        tube_temp_enabled = temp_norm == 1 if temp_norm is not None else None
        tube_temp = _pos("tube_temp", min_val=1.0)

        # Climate control: normalized to S10 basis (0=Auto, 1=Manual).
        # OSCAR :2297-2299: if (AS_eleven) --s_ClimateControl.
        climate_norm = _norm("climate_control")
        climate_control = (
            self.CLIMATE_CONTROL_MAP.get(int(climate_norm), str(int(climate_norm)))
            if climate_norm is not None and climate_norm >= 0
            else None
        )

        # SmartStart (S.SmartStart): S10 basis 0=off, 1=on, 2=SmartRamp (OSCAR :2319-2322).
        # smart_start=True for both On and SmartRamp (it is an enabled start feature);
        # when raw==2, "smart_ramp"="True" is additionally recorded in other_settings.
        ss_norm = _norm("smart_start")
        smart_start = ss_norm >= 1 if ss_norm is not None else None

        # SmartStop: S10 basis 0=off, 1=on (OSCAR :2324-2327).
        ss_stop_norm = _norm("smart_stop_raw")
        smart_stop = ss_stop_norm == 1 if ss_stop_norm is not None else None

        # Mask type: S11 raw 2–4; normalized by −2 (OSCAR :2302-2309).
        mask_value = values.get("mask_type")
        if mask_value is not None:
            mask_code = int(mask_value)
            if series11:
                mask_code -= 2
            mask_type = self.MASK_TYPE_MAP.get(mask_code, "Unknown")
        else:
            mask_type = None

        # AB filter: normalized to S10 basis (0=Standard, 1=Antibacterial).
        # OSCAR :2292-2295: if (AS_eleven) --s_ABFilter.
        ab_norm = _norm("ab_filter")
        ab_filter = (
            self.AB_FILTER_MAP.get(int(ab_norm), str(int(ab_norm)))
            if ab_norm is not None and ab_norm >= 0
            else None
        )

        # Patient access/view: S11 emits pt_view (OSCAR :2311-2317 s_PtView--);
        # S10 emits pt_access. S11 path uses _norm (−1); S10 path reads raw value
        # (OSCAR does not decrement s_PtAccess).
        if series11:
            pt_norm = _norm("pt_access_raw")
            pt_view: str | None = (
                self.PT_VIEW_MAP.get(int(pt_norm), str(int(pt_norm)))
                if pt_norm is not None and pt_norm >= 0
                else None
            )
            pt_access: str | None = None
        else:
            pt_view = None
            pt_access_raw = values.get("pt_access_raw")
            if pt_access_raw is not None and pt_access_raw >= 0:
                code = int(pt_access_raw)
                pt_access = self.PT_ACCESS_MAP.get(code, str(code))
            else:
                pt_access = None

        # Tube type (raw, no normalization — OSCAR :2343 does no −1 for S11).
        tube_raw = values.get("tube_raw")
        if tube_raw is not None and tube_raw >= 0:
            code = int(tube_raw)
            tube: str | None = self.TUBE_TYPE_MAP.get(code, str(code))
        else:
            tube = None

        # Response/Comfort (S.AS.Comfort): S11 raw−1 (OSCAR :2180-2183).
        comfort_norm = _norm("comfort_raw")
        if comfort_norm is not None and comfort_norm >= 0:
            code = int(comfort_norm)
            response: str | None = self.RESPONSE_MAP.get(code, str(code))
        else:
            response = None

        # ------------------------------------------------------------------
        # Mode-specific fields
        # ------------------------------------------------------------------
        pressure_fixed: float | None = None
        pressure_min: float | None = None
        pressure_max: float | None = None
        ipap: float | None = None
        epap: float | None = None
        ps: float | None = None
        ramp_start_pressure: float | None = None
        epr_level: int | None = None
        epr_mode: str | None = None
        other_settings: dict[str, str] = {}

        # Build EPR type and gated enable; applies to CPAP and APAP only
        # (OSCAR :2187-2215 limits EPR decode to those two modes).
        def _decode_epr() -> tuple[int | None, str | None]:
            """Return (epr_level, epr_mode_str) with EPREnable/ClinEnable gating."""
            epr_level_v = _nn("epr_level")
            lvl = (
                int(epr_level_v)
                if epr_level_v is not None and 0 <= epr_level_v <= 3
                else None
            )

            # S.EPR.EPRType: S10 raw+1 maps to {1:Ramp Only, 2:Full Time};
            # S11 raw+1−1=raw maps to {0:Off, 1:Ramp Only, 2:Full Time}.
            # OSCAR :2195-2199: epr += 1; if (AS_eleven) epr--.
            epr_type_raw = values.get("epr_type_raw")
            if epr_type_raw is not None and epr_type_raw >= 0:
                epr_type_code = int(epr_type_raw) + (0 if series11 else 1)
            else:
                epr_type_code = None

            # EPREnable / ClinEnable gating (OSCAR :2201-2215).
            epr_enable_raw = _norm("epr_enable_raw")
            if epr_enable_raw is not None:
                epr_on = epr_enable_raw >= 1
                if epr_on:
                    clin_raw = _norm("epr_clin_enable_raw")
                    # EPREnable-on + ClinEnable-absent → EPR Off, matching OSCAR
                    # :2201-2215 which initializes clin_epr_on=0 and only sets it
                    # if ClinEnable is present; absent ClinEnable is NOT "permit".
                    clin_on = clin_raw is not None and clin_raw >= 1
                else:
                    clin_on = False
                if not (epr_on and clin_on):
                    return 0, "Off"

            mode_str = (
                self.EPR_TYPE_MAP.get(epr_type_code, "Unknown")
                if epr_type_code is not None
                else None
            )
            return lvl, mode_str

        if mode == TherapyMode.CPAP:
            pressure_fixed = _pos("pressure_fixed", min_val=1.0)
            ramp_start_pressure = _pos("ramp_start_pressure", min_val=1.0)
            epr_level, epr_mode = _decode_epr()

        elif mode == TherapyMode.APAP:
            # A4Her uses AFH signals; S10 AutoSet first-checks S.AS.StartPress
            # then S.A.StartPress (OSCAR :1938); standard APAP falls back to
            # S.A.StartPress from ramp_start_pressure key.
            p_min = _pos("pressure_min", min_val=1.0) or _pos(
                "afh_min_press", min_val=1.0
            )
            p_max = _pos("pressure_max", min_val=1.0) or _pos(
                "afh_max_press", min_val=1.0
            )
            pressure_min = p_min
            pressure_max = p_max

            rsp = (
                _pos("as_start_press", min_val=1.0)
                or _pos("ramp_start_pressure", min_val=1.0)
                or _pos("afh_start_press", min_val=1.0)
            )
            ramp_start_pressure = rsp
            epr_level, epr_mode = _decode_epr()

        elif mode == TherapyMode.BIPAP_AUTO:
            # VAuto: EPAP/IPAP/PS use S.VA.* for both families.
            # Cycle/Trigger/TiMax/TiMin: S10 uses bare S.* signals, S11 S.VA.*.
            # OSCAR :2390-2411: sigprefix "S." for S10, "S.VA." for S11.
            epap = _nn("va_min_epap")
            ipap = _nn("va_max_ipap")
            ps = _nn("va_ps")
            ramp_start_pressure = _nn("va_start_press")

            if series11:
                ti_max_v = values.get("va_ti_max")
                ti_min_v = values.get("va_ti_min")
                trigger_v = _norm("va_trigger")
                cycle_v = _norm("va_cycle")
            else:
                # S10 bare signals (S.TiMax etc.) are not enum-offset; no -1 norm.
                ti_max_v = values.get("s10_ti_max")
                ti_min_v = values.get("s10_ti_min")
                trigger_v = _nn("s10_trigger")
                cycle_v = _nn("s10_cycle")

            _apply_timing(ti_max_v, ti_min_v, trigger_v, cycle_v)

        elif mode in (TherapyMode.BIPAP, TherapyMode.BIPAP_ST):
            # S11 bilevel uses S.S.* (STR_SMODE_SIGNALS); S10 uses bare "S.*"
            # for timing/rise/easybreathe and "S.BL.*" for pressures only.
            # OSCAR :2347-2392: sigprefix "S." for S10, "S.S." for S11.
            if series11:
                ipap = _nn("s_ipap")
                epap = _nn("s_epap")
                ramp_start_pressure = _nn("s_start_press")
                ti_max_v = values.get("s_ti_max")
                ti_min_v = values.get("s_ti_min")
                trigger_v = _norm("s_trigger")  # S11 enum-offset
                cycle_v = _norm("s_cycle")
                rise_enable_norm = _norm("s_rise_enable")
                rise_time_v = values.get("s_rise_time")
                easy_breathe_v = _norm("s_easy_breathe")
            else:
                ipap = _nn("bl_ipap")
                epap = _nn("bl_epap")
                ramp_start_pressure = _nn("bl_start_press")
                # S10: prefer bare S.* timing signals (OSCAR :2347-2392);
                # fall back to S.BL.* if bare key absent (defensive — real S10
                # devices emit bare signals, but older/unknown firmware may not).
                ti_max_v = (
                    values.get("s10_ti_max")
                    if values.get("s10_ti_max") is not None
                    else values.get("bl_ti_max")
                )
                ti_min_v = (
                    values.get("s10_ti_min")
                    if values.get("s10_ti_min") is not None
                    else values.get("bl_ti_min")
                )
                trigger_v = (
                    _nn("s10_trigger")
                    if values.get("s10_trigger") is not None
                    else _nn("bl_trigger")
                )
                cycle_v = (
                    _nn("s10_cycle")
                    if values.get("s10_cycle") is not None
                    else _nn("bl_cycle")
                )
                rise_enable_norm = (
                    _nn("s10_rise_enable")
                    if values.get("s10_rise_enable") is not None
                    else _nn("bl_rise_enable")
                )
                rise_time_v = (
                    values.get("s10_rise_time")
                    if values.get("s10_rise_time") is not None
                    else values.get("bl_rise_time")
                )
                easy_breathe_v = (
                    _nn("s10_easy_breathe")
                    if values.get("s10_easy_breathe") is not None
                    else _nn("bl_easy_breathe")
                )

            if ipap is not None and epap is not None:
                ps = round(ipap - epap, 2)
            _apply_timing(ti_max_v, ti_min_v, trigger_v, cycle_v)
            if rise_enable_norm is not None and rise_enable_norm >= 1:
                if rise_time_v is not None and rise_time_v >= 0:
                    other_settings["rise_time"] = str(int(rise_time_v))
            if easy_breathe_v is not None and easy_breathe_v >= 0:
                other_settings["easy_breathe"] = str(int(easy_breathe_v) == 1)

        elif mode == TherapyMode.ASV:
            # Fixed-EPAP ASV: S.AV.EPAP, S.AV.MinPS, S.AV.MaxPS (OSCAR :2093-2108).
            epap = _nn("av_epap")
            min_ps = _nn("av_min_ps")
            max_ps = _nn("av_max_ps")
            ramp_start_pressure = _nn("av_start_press")
            if epap is not None:
                other_settings["min_epap"] = f"{epap:.1f}"
                other_settings["max_epap"] = f"{epap:.1f}"
            if min_ps is not None:
                other_settings["min_ps"] = f"{min_ps:.1f}"
            if max_ps is not None:
                other_settings["max_ps"] = f"{max_ps:.1f}"
                ipap = round(epap + max_ps, 2) if epap is not None else None

        elif mode == TherapyMode.ASV_AUTO:
            # Variable-EPAP ASV: S.AA.MinEPAP, S.AA.MaxEPAP, S.AA.MinPS, S.AA.MaxPS
            # (OSCAR :2109-2127).
            min_epap = _nn("aa_min_epap")
            max_epap = _nn("aa_max_epap")
            min_ps = _nn("aa_min_ps")
            max_ps = _nn("aa_max_ps")
            ramp_start_pressure = _nn("aa_start_press")
            if min_epap is not None:
                other_settings["min_epap"] = f"{min_epap:.1f}"
                epap = min_epap
            if max_epap is not None:
                other_settings["max_epap"] = f"{max_epap:.1f}"
            if min_ps is not None:
                other_settings["min_ps"] = f"{min_ps:.1f}"
            if max_ps is not None:
                other_settings["max_ps"] = f"{max_ps:.1f}"
                if max_epap is not None:
                    ipap = round(max_epap + max_ps, 2)

        elif mode == TherapyMode.IVAPS:
            # iVAPS: S.i.EPAP / S.i.EPAPAuto, S.i.MinPS/MaxPS, S.i.MinEPAP/MaxEPAP
            # (OSCAR :2049-2092).
            iv_epap = _nn("iv_epap")
            iv_epap_auto = _nn("iv_epap_auto")
            iv_min_epap = _nn("iv_min_epap")
            iv_max_epap = _nn("iv_max_epap")
            iv_min_ps = _nn("iv_min_ps")
            iv_max_ps = _nn("iv_max_ps")
            ramp_start_pressure = _nn("iv_start_press")

            epap_auto = iv_epap_auto is not None and iv_epap_auto >= 1
            if epap_auto:
                if iv_min_epap is not None:
                    other_settings["min_epap"] = f"{iv_min_epap:.1f}"
                    epap = iv_min_epap
                if iv_max_epap is not None:
                    other_settings["max_epap"] = f"{iv_max_epap:.1f}"
            else:
                if iv_epap is not None:
                    other_settings["min_epap"] = f"{iv_epap:.1f}"
                    other_settings["max_epap"] = f"{iv_epap:.1f}"
                    epap = iv_epap
            if iv_min_ps is not None:
                other_settings["min_ps"] = f"{iv_min_ps:.1f}"
            if iv_max_ps is not None:
                other_settings["max_ps"] = f"{iv_max_ps:.1f}"
                ipap = round((epap or 0) + iv_max_ps, 2) if epap is not None else None
            other_settings["epap_auto"] = str(epap_auto)

        # Accumulate universal other_settings after mode-specific handling.
        if smart_stop is not None:
            other_settings["smart_stop"] = str(smart_stop)
        # smart_ramp can come from S.RampEnable=2 or S.SmartStart=2 (on S10 basis).
        if smart_ramp or (ss_norm is not None and ss_norm >= 2):
            other_settings["smart_ramp"] = "True"
        if tube is not None:
            other_settings["tube"] = tube
        if pt_view is not None:
            other_settings["pt_view"] = pt_view
        elif pt_access is not None:
            other_settings["pt_access"] = pt_access
        if response is not None:
            other_settings["response"] = response

        return TherapySettings(
            mode=mode,
            pressure_fixed=pressure_fixed,
            pressure_min=pressure_min,
            pressure_max=pressure_max,
            ipap=ipap,
            epap=epap,
            ps=ps,
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
            other_settings=other_settings,
        )
