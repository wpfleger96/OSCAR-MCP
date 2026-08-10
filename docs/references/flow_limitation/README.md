# Flow Limitation Reference Images

These two PNGs are screenshots from **OSCAR — The Guide** (Apnea Board Wiki,
https://www.apneaboard.com/wiki/), kept here as developer reference material only.

- `OSCAR_flow_limitation_classes.png` — the 7 inspiratory waveform class examples
- `OSCAR_flow_limitation_chart.png` — the corresponding severity chart

**These files must never be shipped in the UI build and must never be imported
from `ui/`.** They exist solely so developers can visually verify that the
programmatic class definitions remain accurate.

The authoritative programmatic definitions live in:
- `ui/src/utils/flowLimitation.ts` (TypeScript, mirrors the Python constants)
- `src/snore/constants.py` (`FLOW_LIMITATION_CLASSES`)
