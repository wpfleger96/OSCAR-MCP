#!/usr/bin/env python3
"""
Script to create a test EVE.edf file with sample respiratory event annotations.
This creates a minimal EDF+ file with annotations for testing the EVE parser.
"""

import pyedflib
from datetime import datetime
from pathlib import Path


def create_eve_test_file():
    """Create a test EVE.edf file with sample event annotations."""

    # Create in the correct location with the actual session timestamp
    output_path = (
        Path(__file__).parent.parent
        / "resmed_sample"
        / "DATALOG"
        / "2024"
        / "20240621_013454_EVE.edf"
    )

    # Create EDF+ file
    # EVE files typically have no signals, just annotations
    start_time = datetime(2024, 6, 21, 1, 34, 54)  # Match the session timestamp from filename

    # Create EDF+ file with no signals (annotations only)
    with pyedflib.EdfWriter(str(output_path), 0, file_type=pyedflib.FILETYPE_EDFPLUS) as edf:
        # Set header information
        edf.setPatientCode("Anonymous")
        edf.setPatientName("Test Patient")
        edf.setStartdatetime(start_time)
        edf.setRecordingAdditional("ResMed Test EVE Data")

        # Add sample event annotations
        # Format: (onset_seconds, duration_seconds, description)
        # Session is ~2040 seconds (34 minutes) long, so keep events within that range
        annotations = [
            (0.0, -1, "Recording starts"),  # Standard start marker
            # Sample respiratory events with realistic timings within session bounds
            (120.0, 12.5, "Obstructive apnea"),  # OA at 2 min
            (300.0, 15.0, "Central apnea"),  # CA at 5 min
            (480.0, 18.2, "Hypopnea"),  # H at 8 min
            (660.0, 11.3, "Obstructive apnea"),  # OA at 11 min
            (840.0, 0.0, "Arousal"),  # RERA at 14 min (no duration)
            (1020.0, 14.7, "Hypopnea"),  # H at 17 min
            (1200.0, 13.1, "Obstructive apnea"),  # OA at 20 min
            (1380.0, 16.8, "Central apnea"),  # CA at 23 min
            (1560.0, 45.0, "Flow Limitation"),  # FL at 26 min
            (1740.0, 11.5, "Apnea"),  # UA (unclassified) at 29 min
            (1860.0, 12.9, "Hypopnea"),  # H at 31 min
            (1980.0, 14.2, "Obstructive apnea"),  # OA at 33 min
            # Test edge cases
            (2010.0, 0.0, "Hypopnea"),  # Zero duration (should default to 10s), near end
            (2030.0, -1, "SpO2 Desaturation"),  # Should be filtered out, near end
        ]

        for onset, duration, description in annotations:
            edf.writeAnnotation(onset, duration, description)

    print(f"Created test EVE file: {output_path}")
    print(f"  - Total annotations: {len(annotations)}")
    print("  - Event types: OA, CA, H, UA, RERA, FL")
    print("  - Test cases: zero duration, filtered annotations")


if __name__ == "__main__":
    create_eve_test_file()
