"""Reproduce original routing reference baselines outside the TIDE source tree.

The runner deliberately keeps reference code and data out of this MIT project.
It downloads pinned public artifacts, checks their SHA-256 digests, prepares an
isolated runtime, executes the original entry points, and records measured
metrics against the published references.

Currently supported:

``visir2``
    Executes the official VISIR-2 analytic-current benchmark (Techy Example 3).
    The original GPL-3.0-or-later source is extracted and run from the cache
    directory only.  No upstream source is copied into TIDE.

``simroute``
    Runs an independent NumPy reimplementation of the published SIMROUTE Palma
    de Mallorca--Barcelona case.  It reads the pinned wave archive and checks
    the result against the published report.  No upstream code is copied.

Typical use::

    python -m agent.references.reproduce visir2 --cache /private/tmp/tide-ref-cache
    python -m agent.references.reproduce simroute --cache /private/tmp/tide-ref-cache

The first run needs network access.  Later runs can use ``--offline``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Sequence

DEFAULT_CACHE = Path("/private/tmp/tide-reference-cache")
USER_AGENT = "TIDE-reference-reproducer/1"


class ReferenceError(RuntimeError):
    """A reference baseline could not be reproduced."""


class ChecksumError(ReferenceError):
    """A downloaded or cached artifact has the wrong content."""


class DownloadError(ReferenceError):
    """An artifact could not be downloaded."""


class CommandError(ReferenceError):
    """A reference command failed."""


class CommandTimeout(CommandError):
    """A reference command exceeded its declared timeout."""

    def __init__(self, command: Sequence[str], timeout_s: float, log_path: Path):
        self.command = list(command)
        self.timeout_s = timeout_s
        self.log_path = log_path
        super().__init__(
            f"command exceeded {timeout_s:g} s: {' '.join(self.command)} "
            f"(log: {self.log_path})"
        )


@dataclass(frozen=True)
class Artifact:
    """A pinned public input file."""

    name: str
    url: str
    filename: str
    sha256: str
    size: int | None = None


@dataclass(frozen=True)
class CommandResult:
    """The result of a bounded subprocess."""

    command: list[str]
    returncode: int
    log_path: Path
    duration_s: float
    output: str


@dataclass(frozen=True)
class RuntimeInfo:
    """The isolated Python runtime used for the original VISIR-2 entry points."""

    python: Path
    python_version: str
    uv_version: str
    packages: dict[str, str]
    freeze: dict[str, str]


# Pinned artifacts.  The VISIR-2 source digest is the one recorded in
# agent/references/benchmarks.json.  The analytic namelist digests were measured
# from the public Zenodo record.
VISIR2_SOURCE = Artifact(
    name="visir2_source",
    url="https://zenodo.org/records/10960842/files/VISIR-2--gmd_2023.zip?download=1",
    filename="VISIR-2--gmd_2023.zip",
    sha256="62a3a79bb668e38459e35ca44114d07cdde12802df14ba0ca44bca75b5ed4a74",
    size=4_124_030,
)
VISIR2_ANALYTIC_CURRENT = Artifact(
    name="visir2_analytic_current",
    url="https://zenodo.org/records/8322460/files/current_short.zip?download=1",
    filename="current_short.zip",
    sha256="01cd73822cef63e1f740848624b352f5b3931eddc4c34d7f3fcc75be7c3f9784",
    size=2_293,
)
SIMROUTE_WAVES = Artifact(
    name="simroute_waves",
    url=(
        "https://raw.githubusercontent.com/ManelGrifoll/SIMROUTE/"
        "7d9a3381b8078013f63c3007c10f60dcf4f19f93/in/waves.npz"
    ),
    filename="simroute-waves.npz",
    sha256="10ad9eb9b445462b83e34772ee3f48d2c969fd5780f2929b36ff4eebf7270027",
    size=8_943_436,
)
SIMROUTE_REPORT = Artifact(
    name="simroute_published_report",
    url=(
        "https://raw.githubusercontent.com/ManelGrifoll/SIMROUTE/"
        "7d9a3381b8078013f63c3007c10f60dcf4f19f93/out/Res_Palma_Barna.txt"
    ),
    filename="Res_Palma_Barna.txt",
    sha256="5c3b619c419e826e6360b74af89970efb5ab57cc3d067271d41695205b0eb25a",
    size=876,
)

VISIR2_LICENSE_SHA256 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"
VISIR2_ANALYTIC_CSV = "__data/Validazioni/Analitiche_curr.csv"
VISIR2_ANALYTIC_CSV_SHA256 = "406b8565d1523d65f9b1c24f307ac9fc4e4f7e1457dd36b8590c0a4d06e9a713"
VISIR2_ANALYTIC_REFERENCE = {"L_over_L0": 1.00480754, "T_over_T0": 1.03038814}
VISIR2_ANALYTIC_TOLERANCE_PERCENT = 3.0

VISIR2_GRAPH_FILE = "__product/_a_Grafi/crt_analytic_nu05_inv025_T00/crt_analytic_nu05_inv025_T00.npz"
VISIR2_CAMPI_GLOB = "__product/_b_Campi/crt_analytic_nu05_inv025_T00/2000010100/curr_*.npz"
VISIR2_TRACCE_DIR = "__product/_d_Tracce/crt_analytic_nu05_inv025_T00/Techy_curr"

VISIR2_STAGES = (
    (
        "grafi",
        "from _a_Grafi.MAIN_Grafi import Main; Main('crt_analytic')",
        (VISIR2_GRAPH_FILE,),
    ),
    (
        "campi",
        "from _b_Campi.MAIN_Campi import MAIN; MAIN('techy')",
        (VISIR2_CAMPI_GLOB,),
    ),
    (
        "analitiche",
        "from _d_Tracce.tracce_analitiche import MAIN_Analitiche; "
        "MAIN_Analitiche('Techy_curr')",
        (
            VISIR2_TRACCE_DIR + "/time_voyageplan.json",
            VISIR2_TRACCE_DIR + "/dist_voyageplan.json",
        ),
    ),
)

# Exact environment used for the recorded measurement.  These versions are
# chosen for the original 2024 code: SciPy still provides interp2d, networkx
# still provides generate_unique_node, and pandas still provides DataFrame.append.
VISIR2_PACKAGES = {
    "numpy": "1.23.5",
    "scipy": "1.9.3",
    "pandas": "1.5.3",
    "networkx": "2.5.1",
    "decorator": "4.4.2",
    "matplotlib": "3.7.5",
    "munch": "2.5.0",
    "PyYAML": "6.0.1",
    "tqdm": "4.66.5",
    "netCDF4": "1.6.5",
    "requests": "2.31.0",
    "setuptools": "75.8.0",
    "prettytable": "3.9.0",
    "pyproj": "3.6.1",
    "dicttoxml": "1.7.4",
    "simplejson": "3.19.2",
}

SIMROUTE_COMMIT = "7d9a3381b8078013f63c3007c10f60dcf4f19f93"
SIMROUTE_WAVE_OFFSET_HOURS = 9
SIMROUTE_PUBLISHED_TOLERANCE = 0.005


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(path: Path, artifact: Artifact) -> Path:
    """Verify size and SHA-256 for one artifact."""

    if not path.is_file():
        raise ReferenceError(f"missing artifact: {path}")
    if artifact.size is not None:
        actual_size = path.stat().st_size
        if actual_size != artifact.size:
            raise ChecksumError(
                f"size mismatch for {artifact.filename}: "
                f"expected {artifact.size}, got {actual_size}"
            )
    actual_digest = sha256_file(path)
    if actual_digest != artifact.sha256:
        raise ChecksumError(
            f"sha256 mismatch for {artifact.filename}: "
            f"expected {artifact.sha256}, got {actual_digest}"
        )
    return path


def download_file(url: str, destination: Path, timeout: float = 120.0) -> Path:
    """Download one URL to a temporary file, then atomically replace the target."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            with partial.open("wb") as handle:
                shutil.copyfileobj(response, handle, length=1024 * 1024)
        os.replace(partial, destination)
    except (OSError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"cannot download {url}: {exc}") from exc
    return destination


def ensure_artifact(
    cache: Path,
    artifact: Artifact,
    *,
    offline: bool = False,
    timeout: float = 120.0,
    fetcher: Callable[[str, Path, float], Path] | None = None,
) -> Path:
    """Return a verified artifact, downloading it when necessary.

    An existing file is reused only when its digest matches.  A corrupt cached
    file is replaced on an online run and is an error on an offline run.
    """

    path = cache / "downloads" / artifact.filename
    if path.exists():
        try:
            return verify_artifact(path, artifact)
        except ChecksumError as exc:
            if offline:
                raise
            print(f"warning: replacing corrupt cache file: {exc}", file=sys.stderr)
            path.unlink(missing_ok=True)

    if offline:
        raise ReferenceError(
            f"offline mode needs a verified cached file: {path} "
            f"(sha256 {artifact.sha256})"
        )

    fetch = fetcher or download_file
    fetch(artifact.url, path, timeout)
    try:
        return verify_artifact(path, artifact)
    except ChecksumError:
        path.unlink(missing_ok=True)
        raise


def safe_extract_zip(
    zip_path: Path,
    destination: Path,
    expected_members: Iterable[str] | None = None,
) -> Path:
    """Extract a ZIP archive without allowing absolute or parent paths."""

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            raw_name = info.filename
            if not raw_name:
                continue
            member = PurePosixPath(raw_name)
            if (
                "\\" in raw_name
                or member.is_absolute()
                or any(part == ".." for part in member.parts)
            ):
                raise ReferenceError(f"unsafe zip member: {raw_name}")
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == 0o120000:
                raise ReferenceError(f"symbolic links are not allowed in zip: {raw_name}")
            if raw_name.endswith("/"):
                continue
            target = (destination / member).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise ReferenceError(f"unsafe zip member: {raw_name}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)

    if expected_members is not None:
        missing = [
            member
            for member in expected_members
            if not (destination / PurePosixPath(member)).is_file()
        ]
        if missing:
            raise ReferenceError(f"zip {zip_path.name} lacks required members: {missing}")
    return destination


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _write_json_atomic(path: Path, data: object) -> None:
    _write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 60.0,
    log_path: Path,
) -> CommandResult:
    """Run a subprocess with a hard timeout and a complete combined log."""

    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = completed.stdout or ""
        duration = time.monotonic() - started
        _write_text_atomic(log_path, output)
        if completed.returncode != 0:
            raise CommandError(
                f"command failed with code {completed.returncode}: "
                f"{' '.join(command)} (log: {log_path})"
            )
        return CommandResult(
            command=list(command),
            returncode=completed.returncode,
            log_path=log_path,
            duration_s=duration,
            output=output,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        _write_text_atomic(
            log_path,
            output + f"\n[reference runner] TIMEOUT after {timeout:g} s\n",
        )
        raise CommandTimeout(command, timeout, log_path) from exc


def _python_version(python: Path) -> str:
    completed = subprocess.run(
        [str(python), "-c", "import platform; print(platform.python_version())"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise CommandError(f"cannot run {python}: {completed.stdout.strip()}")
    return completed.stdout.strip()


def _freeze_runtime(uv: str, python: Path, timeout: float = 120.0) -> dict[str, str]:
    """Return the full installed package set, when uv can report it."""

    completed = subprocess.run(
        [uv, "pip", "freeze", "--python", str(python)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        return {}
    frozen: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        line = line.strip()
        if "==" not in line or line.startswith("-e "):
            continue
        name, version = line.split("==", 1)
        frozen[name] = version
    return dict(sorted(frozen.items(), key=lambda item: item[0].lower()))


def _uv_version(uv: str) -> str:
    completed = subprocess.run(
        [uv, "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise CommandError(f"cannot run {uv}: {completed.stdout.strip()}")
    return completed.stdout.strip()


def ensure_runtime(
    cache: Path,
    *,
    offline: bool,
    install_timeout: float,
    log_path: Path,
) -> RuntimeInfo:
    """Create or reuse the isolated Python 3.9 runtime for VISIR-2."""

    runtime_dir = cache / "runtime"
    venv = runtime_dir / "venv"
    marker = runtime_dir / "packages.json"
    python = venv / "bin" / "python"
    uv = shutil.which("uv")

    expected = dict(sorted(VISIR2_PACKAGES.items()))
    if python.is_file() and marker.is_file():
        try:
            recorded = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            recorded = {}
        if recorded.get("packages") == expected:
            freeze = recorded.get("freeze")
            if not freeze and uv is not None:
                freeze = _freeze_runtime(uv, python)
                if freeze:
                    recorded["freeze"] = freeze
                    _write_json_atomic(marker, recorded)
            return RuntimeInfo(
                python=python,
                python_version=recorded.get("python_version", _python_version(python)),
                uv_version=recorded.get("uv_version", "unknown"),
                packages=expected,
                freeze=freeze or {},
            )

    if offline:
        raise ReferenceError(
            f"offline mode needs a prepared runtime at {venv}; "
            "remove --offline for the first run"
        )

    if uv is None:
        raise ReferenceError(
            "uv is required to prepare the isolated reference runtime; "
            "install uv or provide a prepared cache"
        )

    _write_text_atomic(log_path, "")
    shutil.rmtree(venv, ignore_errors=True)
    marker.unlink(missing_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    create = run_command(
        [uv, "venv", "--python", "3.9", str(venv)],
        timeout=install_timeout,
        log_path=log_path.with_name("runtime-create.log"),
    )
    install_specs = [f"{name}=={version}" for name, version in expected.items()]
    install = run_command(
        [uv, "pip", "install", "--python", str(python), *install_specs],
        timeout=install_timeout,
        log_path=log_path.with_name("runtime-install.log"),
    )
    python_version = _python_version(python)
    uv_version = _uv_version(uv)
    freeze = _freeze_runtime(uv, python)
    _write_text_atomic(log_path, create.output + install.output)
    _write_json_atomic(
        marker,
        {
            "python": str(python),
            "python_version": python_version,
            "uv_version": uv_version,
            "platform": platform.platform(),
            "packages": expected,
            "freeze": freeze,
        },
    )
    return RuntimeInfo(
        python=python,
        python_version=python_version,
        uv_version=uv_version,
        packages=expected,
        freeze=freeze,
    )


def extract_visir2_source(cache: Path) -> Path:
    """Extract and verify the pinned original VISIR-2 source tree.

    The verified archive is always extracted again.  Reusing an extracted tree
    would let later edits inside the cache run as reference source, because the
    archive digest alone cannot vouch for the reused files.
    """

    source_dir = cache / "src" / "visir2-gmd2023"
    marker = source_dir / ".tide-source-sha256"
    source_zip = cache / "downloads" / VISIR2_SOURCE.filename
    verify_artifact(source_zip, VISIR2_SOURCE)

    shutil.rmtree(source_dir, ignore_errors=True)
    safe_extract_zip(source_zip, source_dir)
    verify_artifact(
        source_dir / "LICENSE",
        Artifact("license", "", "LICENSE", VISIR2_LICENSE_SHA256),
    )
    _write_text_atomic(marker, VISIR2_SOURCE.sha256 + "\n")
    return source_dir


def stage_visir2_inputs(cache: Path, work: Path) -> list[str]:
    """Copy the official analytic namelists into the external work tree."""

    input_zip = cache / "downloads" / VISIR2_ANALYTIC_CURRENT.filename
    staging = cache / "tmp" / "visir2-current-short"
    verify_artifact(input_zip, VISIR2_ANALYTIC_CURRENT)
    shutil.rmtree(staging, ignore_errors=True)
    expected = (
        "current_short/__namelist/_a_Grafi/crt_analytic.yaml",
        "current_short/__namelist/_b_Campi/techy.yaml",
        "current_short/__namelist/Analitiche/Techy_curr.yaml",
    )
    safe_extract_zip(input_zip, staging, expected_members=expected)
    source = staging / "current_short" / "__namelist"
    destination = work / "__namelist"
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return list(expected)


def prepare_visir2_work(cache: Path) -> tuple[Path, list[str]]:
    """Create a clean external work tree with the pinned original source."""

    source_dir = extract_visir2_source(cache)
    work = cache / "work" / "visir2"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(source_dir, work)
    shutil.rmtree(work / "__product", ignore_errors=True)
    staged = stage_visir2_inputs(cache, work)

    csv_path = work / VISIR2_ANALYTIC_CSV
    verify_artifact(
        csv_path,
        Artifact(
            "analytic_reference_csv",
            "",
            VISIR2_ANALYTIC_CSV,
            VISIR2_ANALYTIC_CSV_SHA256,
        ),
    )
    return work, staged


def parse_analytic_reference_csv(path: Path) -> dict:
    """Read the official analytic row and check its pinned values."""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    analytic_rows = [row for row in rows if row.get("version") == "analytic"]
    if len(analytic_rows) != 1:
        raise ReferenceError(
            f"expected exactly one analytic row in {path}, found {len(analytic_rows)}"
        )
    row = analytic_rows[0]
    try:
        reference = {
            "L_over_L0": float(row["L"]),
            "T_over_T0": float(row["T*"]),
        }
    except (KeyError, ValueError) as exc:
        raise ReferenceError(f"malformed analytic row in {path}: {row}") from exc

    for key, expected in VISIR2_ANALYTIC_REFERENCE.items():
        if not math.isclose(reference[key], expected, rel_tol=0.0, abs_tol=1e-12):
            raise ReferenceError(
                f"official analytic reference changed: {key}={reference[key]!r}, "
                f"expected {expected!r}"
            )
    return reference


def _last_voyage_metrics(path: Path) -> dict:
    try:
        waypoints = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceError(f"cannot read voyage plan {path}: {exc}") from exc
    if not isinstance(waypoints, list) or not waypoints:
        raise ReferenceError(f"empty voyage plan: {path}")
    last = waypoints[-1]
    try:
        distance = float(last["cumDist"])
        duration = float(last["cumTime"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReferenceError(f"voyage plan {path} has no numeric cumDist/cumTime") from exc
    if not (math.isfinite(distance) and math.isfinite(duration)):
        raise ReferenceError(f"non-finite metrics in {path}")
    return {
        "values": {"L_over_L0": distance, "T_over_T0": duration},
        "waypoints": len(waypoints),
        "arrival_iso": last.get("ISO_date"),
    }


def evaluate_analytic(
    measured: dict,
    reference: dict,
    tolerance_percent: float = VISIR2_ANALYTIC_TOLERANCE_PERCENT,
) -> dict:
    """Compare normalized metrics with the official 3 percent criterion."""

    delta_l = 100.0 * (
        measured["L_over_L0"] - reference["L_over_L0"]
    ) / reference["L_over_L0"]
    delta_t = 100.0 * (
        measured["T_over_T0"] - reference["T_over_T0"]
    ) / reference["T_over_T0"]
    passed = abs(delta_l) <= tolerance_percent and abs(delta_t) <= tolerance_percent
    return {
        "tolerance_percent": tolerance_percent,
        "delta_L_percent": delta_l,
        "delta_T_percent": delta_t,
        "passed": passed,
    }


def _environment_for_work(work: Path, cache: Path) -> dict[str, str]:
    mpl_config = cache / "runtime" / "mplconfig"
    mpl_config.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(work),
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(mpl_config),
        }
    )
    return env


def _resolve_expected(work: Path, expected: Sequence[str]) -> list[Path]:
    resolved: list[Path] = []
    for pattern in expected:
        if any(character in pattern for character in "*?["):
            matches = sorted(work.glob(pattern))
            if not matches:
                raise ReferenceError(f"stage did not create expected output: {pattern}")
            resolved.extend(matches)
        else:
            path = work / pattern
            if not path.is_file():
                raise ReferenceError(f"stage did not create expected output: {pattern}")
            resolved.append(path)
    return resolved


def _console_excerpt(log_text: str) -> list[str]:
    lines = log_text.splitlines()
    for index, line in enumerate(lines):
        if "Metrics for curr analytic run" not in line:
            continue
        excerpt: list[str] = []
        for item in lines[index:]:
            stripped = item.strip()
            if stripped:
                excerpt.append(stripped)
            if "PASSED" in item or len(excerpt) >= 24:
                break
        return excerpt
    return []


def run_visir2(
    cache: Path,
    *,
    offline: bool = False,
    stage_timeout: float = 600.0,
    install_timeout: float = 600.0,
    download_timeout: float = 120.0,
    result_json: Path | None = None,
) -> dict:
    """Run the official VISIR-2 analytic-current benchmark."""

    cache = Path(cache).expanduser().resolve()
    logs = cache / "logs" / "visir2"
    logs.mkdir(parents=True, exist_ok=True)

    print("VISIR-2: verifying pinned source and analytic inputs", file=sys.stderr)
    ensure_artifact(cache, VISIR2_SOURCE, offline=offline, timeout=download_timeout)
    ensure_artifact(cache, VISIR2_ANALYTIC_CURRENT, offline=offline, timeout=download_timeout)

    work, staged_inputs = prepare_visir2_work(cache)
    reference = parse_analytic_reference_csv(work / VISIR2_ANALYTIC_CSV)
    runtime = ensure_runtime(
        cache,
        offline=offline,
        install_timeout=install_timeout,
        log_path=logs / "runtime.log",
    )
    env = _environment_for_work(work, cache)

    records: list[dict] = []
    for index, (stage, code, expected) in enumerate(VISIR2_STAGES, start=1):
        log_path = logs / f"{index:02d}-{stage}.log"
        command = [str(runtime.python), "-c", code]
        print(f"VISIR-2: running official {stage} entry point", file=sys.stderr)
        result = run_command(
            command,
            cwd=work,
            env=env,
            timeout=stage_timeout,
            log_path=log_path,
        )
        outputs = _resolve_expected(work, expected)
        records.append(
            {
                "stage": stage,
                "command": ["python", "-c", code],
                "timeout_s": stage_timeout,
                "duration_s": result.duration_s,
                "log": log_path.name,
                "log_sha256": sha256_file(log_path),
                "outputs": [str(path.relative_to(work)) for path in outputs],
            }
        )

    final_log = (logs / f"{len(VISIR2_STAGES):02d}-analitiche.log").read_text(encoding="utf-8")
    official_verdict = (
        "Curr analytic PASSED"
        if "Curr analytic PASSED" in final_log
        else ("Curr analytic NOT PASSED" if "NOT PASSED" in final_log else "not reported")
    )

    tracce = work / VISIR2_TRACCE_DIR
    time_metrics = _last_voyage_metrics(tracce / "time_voyageplan.json")
    distance_metrics = _last_voyage_metrics(tracce / "dist_voyageplan.json")
    comparison = evaluate_analytic(time_metrics["values"], reference)

    if official_verdict != "Curr analytic PASSED":
        _write_json_atomic(
            cache / "results" / "visir2-failure.json",
            {
                "status": "failed",
                "reason": f"official verdict was {official_verdict!r}",
                "measured": time_metrics["values"],
                "reference": reference,
                "comparison": comparison,
                "official_verdict": official_verdict,
            },
        )
        raise ReferenceError(
            f"official analytic benchmark did not pass: {comparison} "
            f"(verdict: {official_verdict})"
        )
    if not comparison["passed"]:
        _write_json_atomic(
            cache / "results" / "visir2-failure.json",
            {
                "status": "failed",
                "reason": "measured metrics exceed the declared 3 percent tolerance",
                "measured": time_metrics["values"],
                "reference": reference,
                "comparison": comparison,
                "official_verdict": official_verdict,
            },
        )
        raise ReferenceError(
            f"measured metrics exceed {VISIR2_ANALYTIC_TOLERANCE_PERCENT} percent: {comparison}"
        )

    result = {
        "schema_version": 1,
        "benchmark": "visir2",
        "case": "official_analytic_current_techy",
        "status": "passed",
        "reproduction_kind": "original_reference_code_executed_externally",
        "measured_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "citation": (
            "Mannarini, G., Salinas, M. L., Carelli, L., Petacco, N., Orovic, J. "
            "(2024), Geoscientific Model Development 17, 4355-4382, "
            "https://doi.org/10.5194/gmd-17-4355-2024"
        ),
        "source": {
            "archive": asdict(VISIR2_SOURCE),
            "license": "GPL-3.0-or-later",
            "license_sha256": VISIR2_LICENSE_SHA256,
            "execution_note": (
                "The original source is extracted and executed only under the cache "
                "directory. No original VISIR-2 source is copied into TIDE."
            ),
        },
        "analytic_inputs": {
            "archive": asdict(VISIR2_ANALYTIC_CURRENT),
            "staged_namelists": staged_inputs,
            "reference_csv": {
                "path": VISIR2_ANALYTIC_CSV,
                "sha256": VISIR2_ANALYTIC_CSV_SHA256,
                "analytic_row": reference,
            },
        },
        "case_details": {
            "graph": "crt_analytic",
            "graph_product": "crt_analytic_nu05_inv025_T00",
            "forcing": "current only",
            "analytic_example": "Techy Example 3 (dynamic current)",
            "vessel": "Techy_example_3",
            "vmax_kts": 21.1,
            "start_lat_lon": [5.0, -45.0],
            "end_lat_lon": [3.0, -43.8],
            "departure_utc": "2000-01-01T00:00:00Z",
            "time_step_minutes": 5,
            "time_steps": 100,
            "spatial_interpolation": "Sint=1 (linear at edge centres)",
        },
        "runtime": {
            "python": runtime.python_version,
            "executable": "runtime/venv/bin/python",
            "uv": runtime.uv_version,
            "platform": platform.platform(),
            "packages": runtime.packages,
            "freeze": runtime.freeze,
            "environment": {
                "PYTHONPATH": "<external-work-tree>",
                "PYTHONHASHSEED": "0",
                "PYTHONNOUSERSITE": "1",
                "MPLBACKEND": "Agg",
            },
        },
        "official_commands": records,
        "measured": {
            **time_metrics["values"],
            "time_waypoints": time_metrics["waypoints"],
            "arrival_iso": time_metrics["arrival_iso"],
            "least_distance_L_over_L0": distance_metrics["values"]["L_over_L0"],
            "least_distance_T_over_T0": distance_metrics["values"]["T_over_T0"],
            "official_verdict": official_verdict,
            "official_metrics_excerpt": _console_excerpt(final_log),
        },
        "reference": reference,
        "comparison": comparison,
        "limitations": [
            "This is the official analytic-current benchmark (Techy Example 3), not the full Alboran numerical case.",
            "The original 2024 code is run in a pinned Python 3.9 environment with modern but compatible dependency versions; the upstream conda file names Python 3.7.3.",
            "The official pass criterion is |deltaL| <= 3 percent and |deltaT*| <= 3 percent, as implemented by Validazioni/analytic_results.py.",
            "Only public analytic namelists and the pinned source archive are downloaded. No environmental netCDF products or large Alboran inputs are used.",
            "The reference code remains GPL-3.0-or-later and external to this MIT repository.",
        ],
    }
    results_dir = cache / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(results_dir / "visir2-run.json", result)
    if result_json is not None:
        _write_json_atomic(Path(result_json), result)
    return result


def parse_simroute_report(text: str) -> dict:
    """Parse the three published rows from the SIMROUTE Palma report."""

    patterns = {
        "optimized_without_waves": r"Route optimized \(without waves\):\s+([0-9.]+)\s+([0-9.]+)",
        "optimized_with_waves": r"Route optimized \(with waves\):\s+([0-9.]+)\s+([0-9.]+)",
        "minimum_distance_with_waves": r"Route Minimum Distance with waves:\s+([0-9.]+)\s+([0-9.]+)",
    }
    result = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text)
        if match is None:
            raise ReferenceError(f"cannot parse SIMROUTE report row: {name}")
        result[name] = {"hours": float(match.group(1)), "miles": float(match.group(2))}
    return result


def _simroute_grid():
    import numpy as np

    nx, ny = 125, 109
    lon_min, lat_min = 2.0, 38.95
    step_deg = 1.5 / 60.0
    nodes = np.array(
        [
            (lon_min + index_x * step_deg, lat_min + index_y * step_deg)
            for index_y in range(ny)
            for index_x in range(nx)
        ]
    )
    neighbors = [
        (dx, dy)
        for dy in range(-4, 5)
        for dx in range(-4, 5)
        if (dx or dy) and math.gcd(abs(dx), abs(dy)) == 1
    ]
    return nx, ny, nodes, neighbors


def _simroute_geometry(nodes):
    import numpy as np

    def distance(node_a: int, node_b: int) -> float:
        lon1, lat1 = nodes[node_a]
        lon2, lat2 = nodes[node_b]
        cosine = (
            np.sin(np.deg2rad(lat1)) * np.sin(np.deg2rad(lat2))
            + np.cos(np.deg2rad(lat1))
            * np.cos(np.deg2rad(lat2))
            * np.cos(np.deg2rad(lon1 - lon2))
        )
        return 60.0 * np.rad2deg(np.arccos(cosine))

    def edge_angle(destination: int, origin: int) -> float:
        lon_origin, lat_origin = nodes[origin]
        lon_destination, lat_destination = nodes[destination]
        if lat_origin == lat_destination:
            return 270.0 if lon_origin > lon_destination else 90.0
        cosine = np.arccos(
            np.cos(np.deg2rad(90.0 - lat_origin))
            * np.cos(np.deg2rad(90.0 - lat_destination))
            + np.sin(np.deg2rad(90.0 - lat_origin))
            * np.sin(np.deg2rad(90.0 - lat_destination))
            * np.cos(np.deg2rad(lon_destination - lon_origin))
        )
        value = (
            np.cos(np.deg2rad(90.0 - lat_destination))
            - np.cos(cosine) * np.cos(np.deg2rad(90.0 - lat_origin))
        ) / (np.sin(cosine) * np.sin(np.deg2rad(90.0 - lat_origin)))
        angle = 0.0 if value > 1 else np.pi if value < -1 else np.arccos(value)
        angle = angle * 180.0 / np.pi
        return 360.0 - angle if lon_origin > lon_destination else angle

    return distance, edge_angle


def _simroute_model(hs, wave_from):
    """Return the geometry and edge-time functions for the Palma case."""

    import numpy as np

    nx, ny, nodes, neighbors = _simroute_grid()
    distance, edge_angle = _simroute_geometry(nodes)
    vmax = 16.1  # knots, published case speed

    def relative_angle(ship_angle: float, wave_angle: float) -> float:
        return (
            wave_angle - ship_angle
            if wave_angle > ship_angle
            else 360.0 - (ship_angle - wave_angle)
        )

    def speed_reduction(angle: float) -> float:
        if (45 <= angle <= 135) or (225 <= angle <= 315):
            return 0.0165
        if 135 < angle < 225:
            return 0.0083
        if (0 <= angle < 45) or (315 < angle <= 360):
            return 0.0248
        return 0.0

    def edge_time(origin: int, destination: int, cost: float) -> float:
        angle = edge_angle(destination, origin)
        length = distance(origin, destination)
        edge_cost = cost
        fraction = 1.0 - (edge_cost % 1.0)
        while True:
            hour_index = math.floor(edge_cost) + 1
            mean_hs = 0.5 * (hs[origin, hour_index] + hs[destination, hour_index])
            encounter = relative_angle(angle, wave_from[destination, hour_index])
            speed = (
                vmax
                - speed_reduction(encounter) * mean_hs * mean_hs * 3.2808 * 3.2808
            )
            if speed < 0:
                raise ReferenceError("SIMROUTE edge speed became negative")
            if speed * fraction >= length:
                return edge_cost + length / speed
            length -= speed * fraction
            edge_cost += fraction
            fraction = 1.0 - (edge_cost % 1.0)

    return nx, ny, distance, neighbors, edge_time


def _simroute_search(hs, wave_from, *, use_waves: bool, start: int = 1411, end: int = 12781):
    """Independent A* reproduction of the published SIMROUTE Palma case."""

    import numpy as np

    nx, ny, distance, neighbors, edge_time = _simroute_model(hs, wave_from)
    vmax = 16.1

    node_count = nx * ny
    closed = np.zeros(node_count, dtype=bool)
    cost_to_node = np.full(node_count, np.inf)
    parent = np.full(node_count, -1, dtype=int)
    priority = np.full(node_count, np.inf)
    settled_cost = np.full(node_count, np.nan)
    cost_to_node[start] = 0.0
    priority[start] = distance(start, end) / vmax
    current = start

    while current != end:
        closed[current] = True
        settled_cost[current] = cost_to_node[current]
        cost_to_node[current] = np.inf
        priority[current] = np.inf
        index_y, index_x = divmod(current, nx)
        for dx, dy in neighbors:
            next_x, next_y = index_x + dx, index_y + dy
            if not (0 <= next_x < nx and 0 <= next_y < ny):
                continue
            candidate = next_y * nx + next_x
            if closed[candidate] or np.isnan(hs[candidate, 1]):
                continue
            if use_waves:
                edge_cost = edge_time(current, candidate, settled_cost[current])
            else:
                edge_cost = distance(current, candidate) / vmax + settled_cost[current]
            heuristic = distance(candidate, end) / vmax
            if edge_cost + heuristic < priority[candidate]:
                cost_to_node[candidate] = edge_cost
                parent[candidate] = current
                priority[candidate] = edge_cost + heuristic
        current = int(np.argmin(priority))
        if not np.isfinite(priority[current]):
            raise ReferenceError("SIMROUTE search exhausted without a route")
        if current == end:
            closed[current] = True
            settled_cost[current] = cost_to_node[current]

    path = [end]
    node = end
    while node != start:
        node = int(parent[node])
        path.append(node)
    path.reverse()
    length = sum(distance(a, b) for a, b in zip(path, path[1:]))
    return {
        "path": path,
        "cost_hours": float(settled_cost[end]),
        "length_miles": float(length),
        "closed_nodes": int(closed.sum()),
    }


def _simroute_fixed_path_time(hs, wave_from, path: Sequence[int]) -> float:
    """Re-time a fixed path with the wave-affected edge model."""

    _, _, _, _, edge_time = _simroute_model(hs, wave_from)
    cost = 0.0
    for origin, destination in zip(path, path[1:]):
        cost = edge_time(origin, destination, cost)
    return cost


def run_simroute(
    cache: Path,
    *,
    offline: bool = False,
    download_timeout: float = 120.0,
) -> dict:
    """Run the independent SIMROUTE Palma reference reproduction."""

    try:
        import numpy as np
    except ImportError as exc:
        raise ReferenceError(
            "simroute needs NumPy; run with `uv run python -m agent.references.reproduce simroute`"
        ) from exc

    cache = Path(cache).expanduser().resolve()
    print("SIMROUTE: verifying pinned wave archive and published report", file=sys.stderr)
    waves_path = ensure_artifact(
        cache, SIMROUTE_WAVES, offline=offline, timeout=download_timeout
    )
    report_path = ensure_artifact(
        cache, SIMROUTE_REPORT, offline=offline, timeout=download_timeout
    )
    published = parse_simroute_report(report_path.read_text(encoding="utf-8"))

    with np.load(waves_path) as data:
        try:
            hs_all = data["arr_0"]
            wave_from_all = data["arr_2"]
        except KeyError as exc:
            raise ReferenceError(
                f"unexpected SIMROUTE wave archive keys: {data.files}"
            ) from exc
    if hs_all.shape[0] != 125 * 109:
        raise ReferenceError(f"unexpected SIMROUTE wave grid: {hs_all.shape}")
    if hs_all.shape[1] <= SIMROUTE_WAVE_OFFSET_HOURS:
        raise ReferenceError(f"too few SIMROUTE wave hours: {hs_all.shape}")
    hs = hs_all[:, SIMROUTE_WAVE_OFFSET_HOURS:]
    wave_from = wave_from_all[:, SIMROUTE_WAVE_OFFSET_HOURS:]

    without_waves = _simroute_search(hs, wave_from, use_waves=False)
    with_waves = _simroute_search(hs, wave_from, use_waves=True)
    minimum_distance_hours = _simroute_fixed_path_time(
        hs, wave_from, without_waves["path"]
    )
    minimum_distance_miles = without_waves["length_miles"]

    measured = {
        "optimized_without_waves": {
            "hours": without_waves["cost_hours"],
            "miles": without_waves["length_miles"],
        },
        "optimized_with_waves": {
            "hours": with_waves["cost_hours"],
            "miles": with_waves["length_miles"],
        },
        "minimum_distance_with_waves": {
            "hours": minimum_distance_hours,
            "miles": minimum_distance_miles,
        },
    }
    comparison = {
        name: {
            "delta_hours": measured[name]["hours"] - published[name]["hours"],
            "delta_miles": measured[name]["miles"] - published[name]["miles"],
            "passed": (
                abs(measured[name]["hours"] - published[name]["hours"])
                <= SIMROUTE_PUBLISHED_TOLERANCE
                and abs(measured[name]["miles"] - published[name]["miles"])
                <= SIMROUTE_PUBLISHED_TOLERANCE
            ),
        }
        for name in published
    }
    if not all(item["passed"] for item in comparison.values()):
        raise ReferenceError(f"SIMROUTE reproduction differs from the report: {comparison}")

    result = {
        "schema_version": 1,
        "benchmark": "simroute_palma_barna",
        "status": "passed",
        "reproduction_kind": "independent_reimplementation_of_published_algorithm",
        "measured_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python -m agent.references.reproduce simroute",
        "algorithm": (
            "A* on the published 125x109 grid with 48 neighbour directions, "
            "Bowditch WEN_form=1 speed loss and hourly wave forcing"
        ),
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "platform": platform.platform(),
        },
        "source": {
            "repository": "https://github.com/ManelGrifoll/SIMROUTE",
            "benchmark_snapshot_commit": SIMROUTE_COMMIT,
            "license": "No license file in the inspected repository; no upstream code is copied.",
            "waves": asdict(SIMROUTE_WAVES),
            "published_report": asdict(SIMROUTE_REPORT),
        },
        "case_details": {
            "route": "Palma de Mallorca to Barcelona",
            "start_utc": "2020-01-20T09:00:00Z",
            "grid_nodes": [125, 109],
            "grid_step_nm": 1.5,
            "start_node": 1411,
            "end_node": 12781,
            "cruise_speed_kn": 16.1,
            "wave_effect": "WEN_form=1 (Bowditch)",
            "wave_time_resolution_h": 1,
            "trimmed_hourly_steps": 39,
        },
        "measured": measured,
        "reference": published,
        "comparison": {
            "tolerance_hours": SIMROUTE_PUBLISHED_TOLERANCE,
            "tolerance_miles": SIMROUTE_PUBLISHED_TOLERANCE,
            "rows": comparison,
        },
        "limitations": [
            "This is an independent reimplementation, not execution of upstream SIMROUTE source.",
            "The published report rounds hours and miles to two decimals; tolerance is half a unit in that last digit.",
            "The implementation is derived from the published algorithm and inspected source behaviour; no upstream source is copied.",
        ],
    }
    results_dir = cache / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(results_dir / "simroute-run.json", result)
    return result


def _format_visir2_summary(result: dict) -> str:
    measured = result["measured"]
    comparison = result["comparison"]
    return "\n".join(
        [
            "VISIR-2 official analytic-current benchmark: PASSED",
            f"  measured L/L0 = {measured['L_over_L0']:.12f}",
            f"  measured T/T0 = {measured['T_over_T0']:.12f}",
            f"  reference L/L0 = {result['reference']['L_over_L0']:.8f}",
            f"  reference T/T0 = {result['reference']['T_over_T0']:.8f}",
            f"  delta L = {comparison['delta_L_percent']:.6f} %",
            f"  delta T = {comparison['delta_T_percent']:.6f} %",
            f"  tolerance = {comparison['tolerance_percent']:.1f} %",
        ]
    )


def _format_simroute_summary(result: dict) -> str:
    lines = ["SIMROUTE Palma-Barna independent reproduction: PASSED"]
    for name in ("optimized_without_waves", "optimized_with_waves", "minimum_distance_with_waves"):
        measured = result["measured"][name]
        reference = result["reference"][name]
        lines.append(
            f"  {name}: {measured['hours']:.6f} h / {measured['miles']:.6f} nm "
            f"(published {reference['hours']:.2f} h / {reference['miles']:.2f} nm)"
        )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent.references.reproduce",
        description="Reproduce original routing reference baselines outside TIDE.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    visir2 = subparsers.add_parser(
        "visir2",
        help="run the official VISIR-2 analytic-current benchmark",
    )
    visir2.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    visir2.add_argument("--offline", action="store_true")
    visir2.add_argument("--timeout", type=float, default=600.0, help="per-stage timeout [s]")
    visir2.add_argument(
        "--install-timeout", type=float, default=600.0, help="runtime setup timeout [s]"
    )
    visir2.add_argument(
        "--download-timeout", type=float, default=120.0, help="per-download timeout [s]"
    )
    visir2.add_argument(
        "--result-json",
        type=Path,
        default=None,
        help="optional extra path for the captured result JSON",
    )

    simroute = subparsers.add_parser(
        "simroute",
        help="run the independent SIMROUTE Palma-Barna reproduction",
    )
    simroute.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    simroute.add_argument("--offline", action="store_true")
    simroute.add_argument(
        "--download-timeout", type=float, default=120.0, help="per-download timeout [s]"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "visir2":
            result = run_visir2(
                args.cache,
                offline=args.offline,
                stage_timeout=args.timeout,
                install_timeout=args.install_timeout,
                download_timeout=args.download_timeout,
                result_json=args.result_json,
            )
            print(_format_visir2_summary(result))
            print(f"result: {args.cache.resolve() / 'results' / 'visir2-run.json'}")
        else:
            result = run_simroute(
                args.cache,
                offline=args.offline,
                download_timeout=args.download_timeout,
            )
            print(_format_simroute_summary(result))
            print(f"result: {args.cache.resolve() / 'results' / 'simroute-run.json'}")
    except (ReferenceError, CommandError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
