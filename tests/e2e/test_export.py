"""Export commands exercised through the real binary against imported data.

CSV/JSON/raw export share a data-assembly path that was consolidated during
simplification with a "byte-identical output" claim. These tests confirm the
files are produced, well-formed, and carry the imported session — the stable
contract a downstream consumer (or the simplification rebase) must preserve.
"""

from __future__ import annotations

import json


def test_json_export_is_well_formed(snore, imported_db, tmp_path):
    out = tmp_path / "out.json"
    result = snore("export", "json", "--output", str(out), db=imported_db)
    assert result.returncode == 0, result.stderr or result.stdout
    assert out.exists()

    payload = json.loads(out.read_text())
    assert payload["snore_export_format"]
    assert payload["session_count"] == 1
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["device_session_id"] == "20240621_013454"


def test_raw_export_reconstructs_from_backup(snore, resmed_sd, tmp_path):
    """`export raw` rebuilds an OSCAR-shaped tree from the raw backup dir.

    Raw export reads the backup created at import time (not the DB), so this
    imports *with* backup into an isolated backup dir, then exports from it.
    """
    backup_dir = tmp_path / "backup"
    db = tmp_path / "raw.db"
    imported = snore(
        "import",
        str(resmed_sd),
        "--all",
        "--backup-dir",
        str(backup_dir),
        db=db,
    )
    assert imported.returncode == 0, imported.stderr or imported.stdout

    export_dir = tmp_path / "raw_out"
    result = snore(
        "export",
        "raw",
        "--output",
        str(export_dir),
        "--backup-dir",
        str(backup_dir),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    # Something was reconstructed on disk.
    assert export_dir.exists()
    assert any(export_dir.rglob("*.edf")), "raw export produced no EDF files"
