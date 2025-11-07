# Test Fixtures for OSCAR-MCP

This directory contains test data fixtures used for integration and validation testing.

## Real CPAP Session Data (2025)

Located in `real_sessions/`, these are actual CPAP session files from 2025 used for realistic end-to-end testing.

### Fixture Descriptions

#### 1. `2025_baseline/` - Normal Baseline Session
- **Date**: February 15, 2025 (03:24 AM)
- **Purpose**: Baseline for normal, stable therapy
- **Characteristics**: Mid-therapy period, likely stable breathing patterns
- **Use Cases**:
  - Basic functionality validation
  - Performance benchmarking
  - Expected behavior verification
- **Files**: BRP, PLD, SA2, EVE, CSL (standard ResMed format)

#### 2. `2025_early_therapy/` - Early Therapy Adjustment
- **Date**: January 10, 2025 (00:07 AM)
- **Purpose**: Early therapy session with potential adjustments
- **Characteristics**: Beginning of therapy, may show adjustment patterns
- **Use Cases**:
  - Event detection validation
  - Flow limitation pattern testing
  - Algorithm robustness with varying therapy
- **Files**: BRP, PLD, SA2, EVE, CSL

#### 3. `2025_spring/` - Spring Seasonal Variation
- **Date**: March 17, 2025 (04:11 AM)
- **Purpose**: Temporal robustness testing
- **Characteristics**: Different time period, potential seasonal variations
- **Use Cases**:
  - Algorithm consistency across time periods
  - Seasonal pattern analysis
  - Temporal stability validation
- **Files**: BRP, PLD, SA2, EVE, CSL

#### 4. `2025_summer/` - Summer Session
- **Date**: June 15, 2025 (23:55 PM)
- **Purpose**: Duration and sleep pattern variation
- **Characteristics**: Mid-year session, different start time
- **Use Cases**:
  - Duration variation testing
  - Start time impact analysis
  - Long-term consistency
- **Files**: BRP, PLD, SA2, EVE, CSL

#### 5. `2025_multi_segment/` - Multi-Segment Session
- **Date**: January 7, 2025 (01:41 AM)
- **Purpose**: Discontinuity and segment handling
- **Characteristics**: Session with potential mask-off events
- **Use Cases**:
  - Discontinuity handling validation
  - Segment merging logic
  - Gap detection testing
- **Files**: BRP, PLD, SA2, EVE, CSL

## File Formats

All sessions use the standard ResMed EDF format:
- **BRP**: Breath-by-breath data (flow, pressure waveforms)
- **PLD**: Pulse oximetry data (SpO2, pulse rate)
- **SA2**: Summary and statistics
- **EVE**: Event markers (apneas, hypopneas, etc.)
- **CSL**: Clinical summary log

## Usage in Tests

### Loading Fixtures

```python
from tests.helpers.fixtures_loader import load_real_session

# Load a specific fixture
session_data = load_real_session("2025_baseline")

# Import to test database
from tests.helpers.fixtures_loader import import_to_test_db
import_to_test_db("2025_baseline", db_session)
```

### Integration Tests

These fixtures are used in:
- `tests/integration/test_real_data.py` - Real data processing validation
- `tests/integration/test_pipeline.py` - End-to-end pipeline tests
- `tests/integration/test_database_roundtrip.py` - Data integrity tests

## Size and Git LFS

These fixtures are relatively small (few KB each) and are committed directly to the repository.
If fixtures grow large in the future, consider:
- Git LFS for large binary files
- `.gitignore` entries with separate download mechanism
- Compressed archives

## Adding New Fixtures

To add new test fixtures:

1. Copy session directory to `real_sessions/`
2. Name it descriptively (e.g., `2025_description/`)
3. Update this README with:
   - Date and time
   - Purpose and characteristics
   - Use cases
4. Add tests in `integration/test_real_data.py`

## Data Privacy

These are **personal CPAP session files** and should be:
- Used only for development and testing
- Not shared publicly without consent
- Excluded from public repositories if sensitive

## Synthetic Fixtures

Located in `synthetic/` (to be created):
- Controlled test data for unit tests
- Generated programmatically
- No privacy concerns
- Version controlled

---

**Last Updated**: November 5, 2025
**Fixture Count**: 5 real sessions (2025)
**Total Size**: ~25 KB
