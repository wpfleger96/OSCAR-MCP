"""Helpers for end-to-end tests that drive the real ``snore`` binary.

These tests exercise the application the way a user does: by invoking the
installed console script as a subprocess and (for the API) booting a real
``snore serve`` process. Nothing here imports ``snore`` in-process — the point
is to test the actual entry point, argument parsing, exit codes, stdout, and
the persisted SQLite file end to end.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import httpx


def _resolve_snore_bin() -> str:
    """Locate the ``snore`` console script.

    Under ``uv run pytest`` (local and CI) the project venv's ``bin`` is on
    PATH and ``sys.executable`` lives next to the ``snore`` script, so this is
    deterministic without paying the cost of a nested ``uv run``.
    """
    candidate = Path(sys.executable).parent / "snore"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("snore")
    if found:
        return found
    raise RuntimeError(
        "Could not locate the 'snore' console script. Run `just sync` / `uv sync` first."
    )


SNORE_BIN = _resolve_snore_bin()

# Generous default: a real import + multi-mode analysis on fixture data takes
# tens of seconds; keep headroom for slow CI runners.
DEFAULT_TIMEOUT = 300


def base_env(home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build an isolated environment for a subprocess invocation.

    ``HOME`` is redirected so commands that touch ``~/.snore`` (raw backups,
    logs) never pollute the developer's real home or the CI runner.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["NO_COLOR"] = "1"  # strip ANSI so substring assertions are stable
    env["PYTHONUNBUFFERED"] = "1"
    env["COLUMNS"] = "200"  # wide enough that Rich tables don't truncate fields
    if extra:
        env.update(extra)
    return env


def run_snore(
    *args: str,
    home: Path,
    db: str | Path | None = None,
    stdin: str | None = None,
    extra_env: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Invoke ``snore`` as a subprocess and return the completed process.

    ``--db`` is appended automatically when ``db`` is provided. The result is
    returned regardless of exit code so callers can assert on it.
    """
    cmd: list[str] = [SNORE_BIN, *args]
    if db is not None:
        cmd += ["--db", str(db)]
    return subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
        env=base_env(home, extra_env),
        timeout=timeout,
    )


def import_fixture(
    source: str | Path,
    db: str | Path,
    home: Path,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    """Import a device-data directory into ``db`` via the real import command.

    Defaults to ``--all`` (no source prompt) and ``--no-backup`` (no writes to
    ``~/.snore/raw``) unless the caller overrides those via ``extra_args``.
    """
    args = ["import", str(source)]
    if "--no-backup" not in extra_args:
        args.append("--no-backup")
    if "--all" not in extra_args:
        args.append("--all")
    args.extend(extra_args)
    return run_snore(*args, db=db, home=home)


def synthesize_resmed_sd(
    dest: Path, identification: Path, str_edf: Path, edf_files: Sequence[Path]
) -> Path:
    """Build a ResMed-SD-card-shaped directory the importer will auto-detect.

    The multi-segment recorded fixtures ship as flat ``*.edf`` files, which the
    importer's source detection rejects. Reconstructing the expected
    ``Identification.json`` + ``STR.edf`` + ``DATALOG/<year>/`` layout lets the
    real CLI parse the discontinuous (mask-off gap) session end to end.
    """
    sd = dest / "sdcard"
    datalog = sd / "DATALOG" / "2025"
    datalog.mkdir(parents=True, exist_ok=True)
    shutil.copy(identification, sd / "Identification.json")
    shutil.copy(str_edf, sd / "STR.edf")
    for edf in edf_files:
        shutil.copy(edf, datalog / edf.name)
    return sd


def synthesize_multi_night_sd(
    dest: Path,
    identification: Path,
    str_edf: Path,
    night_dirs: Sequence[Path],
) -> Path:
    """Compose several real nights' EDF files into one importable SD card.

    Each ``*.edf`` is filed under ``DATALOG/<year>/`` using its filename's 4-digit
    year prefix (ResMed names files ``YYYYMMDD_HHMMSS_*.edf``), so the device
    night (2024) and the recorded nights (2025) land in the right year folders.
    The result is a single multi-night import — 100% real data — used to exercise
    the cross-night surface (date filtering, trends, day-splitting) that a single
    night can't reach.
    """
    sd = dest / "sdcard"
    sd.mkdir(parents=True, exist_ok=True)
    shutil.copy(identification, sd / "Identification.json")
    shutil.copy(str_edf, sd / "STR.edf")
    for night in night_dirs:
        for edf in sorted(Path(night).glob("*.edf")):
            year = edf.name[:4]
            datalog = sd / "DATALOG" / year
            datalog.mkdir(parents=True, exist_ok=True)
            shutil.copy(edf, datalog / edf.name)
    return sd


def _wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LiveServer:
    """A running ``snore serve`` subprocess exposing its base URL."""

    def __init__(self, base_url: str, log_path: Path) -> None:
        self.base_url = base_url
        self.log_path = log_path

    def get(self, path: str, **kwargs: object) -> httpx.Response:
        kwargs.setdefault("timeout", 10)
        return httpx.get(self.base_url + path, **kwargs)

    def logs(self) -> str:
        try:
            return self.log_path.read_text()
        except OSError:
            return ""


@contextmanager
def live_server(
    db: str | Path, home: Path, ready_timeout: float = 45.0
) -> Iterator[LiveServer]:
    """Boot ``snore serve`` against ``db`` on an ephemeral port.

    Yields a :class:`LiveServer`; guarantees the process is terminated on exit.
    """
    host = "127.0.0.1"
    port = _free_port()
    log_path = home / f"serve-{port}.log"
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            [SNORE_BIN, "serve", "--host", host, "--port", str(port), "--db", str(db)],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=base_env(home),
        )
    server = LiveServer(f"http://{host}:{port}", log_path)
    try:
        if not _wait_for_port(host, port, ready_timeout):
            raise RuntimeError(
                f"snore serve did not become ready on {host}:{port}.\n"
                f"--- server log ---\n{server.logs()}"
            )
        # Port open != app ready; poll the schema endpoint until it answers.
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            try:
                if server.get("/openapi.json").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        yield server
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
