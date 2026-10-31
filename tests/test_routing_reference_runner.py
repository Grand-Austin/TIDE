"""Unit tests for the isolated routing-reference runner.

These tests never use the network and never execute the original reference
model.  They cover manifest/path/checksum/timeout handling with small fixtures.
"""
import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest

from agent.references import reproduce as rr

REFERENCES = Path(__file__).resolve().parents[1] / "agent" / "references"


def _fixture_artifact(content: bytes, name: str = "fixture.bin") -> rr.Artifact:
    return rr.Artifact(
        name=name,
        url=f"https://example.invalid/{name}",
        filename=name,
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def test_reference_manifests_are_pinned_and_complete():
    assert rr.VISIR2_SOURCE.sha256 == (
        "62a3a79bb668e38459e35ca44114d07cdde12802df14ba0ca44bca75b5ed4a74"
    )
    assert rr.VISIR2_SOURCE.size == 4_124_030
    assert rr.VISIR2_ANALYTIC_CURRENT.sha256 == (
        "01cd73822cef63e1f740848624b352f5b3931eddc4c34d7f3fcc75be7c3f9784"
    )
    assert rr.VISIR2_ANALYTIC_CURRENT.size == 2_293
    assert rr.VISIR2_ANALYTIC_CSV_SHA256 == (
        "406b8565d1523d65f9b1c24f307ac9fc4e4f7e1457dd36b8590c0a4d06e9a713"
    )
    assert rr.VISIR2_ANALYTIC_REFERENCE == {
        "L_over_L0": 1.00480754,
        "T_over_T0": 1.03038814,
    }
    assert rr.VISIR2_ANALYTIC_TOLERANCE_PERCENT == 3.0
    assert rr.SIMROUTE_WAVES.sha256 == (
        "10ad9eb9b445462b83e34772ee3f48d2c969fd5780f2929b36ff4eebf7270027"
    )
    assert rr.SIMROUTE_REPORT.sha256 == (
        "5c3b619c419e826e6360b74af89970efb5ab57cc3d067271d41695205b0eb25a"
    )
    for artifact in (
        rr.VISIR2_SOURCE,
        rr.VISIR2_ANALYTIC_CURRENT,
        rr.SIMROUTE_WAVES,
        rr.SIMROUTE_REPORT,
    ):
        assert artifact.url.startswith("https://")
        assert len(artifact.sha256) == 64
        assert int(artifact.sha256, 16) >= 0


def test_ensure_artifact_downloads_and_reuses_verified_cache(tmp_path):
    content = b"pinned reference bytes\n"
    artifact = _fixture_artifact(content)
    calls = []

    def fetch(url: str, path: Path, timeout: float) -> Path:
        calls.append((url, path, timeout))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    path = rr.ensure_artifact(tmp_path, artifact, timeout=3.0, fetcher=fetch)
    assert path.read_bytes() == content
    assert len(calls) == 1

    def forbidden_fetch(*_args):
        raise AssertionError("a verified cache file must be reused")

    reused = rr.ensure_artifact(
        tmp_path, artifact, offline=True, fetcher=forbidden_fetch
    )
    assert reused == path
    assert len(calls) == 1


def test_ensure_artifact_rejects_wrong_checksum_and_removes_bad_file(tmp_path):
    artifact = _fixture_artifact(b"expected!")

    def fetch(_url: str, path: Path, _timeout: float) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"corrupt!!")
        return path

    with pytest.raises(rr.ChecksumError, match="sha256 mismatch"):
        rr.ensure_artifact(tmp_path, artifact, fetcher=fetch)
    assert not (tmp_path / "downloads" / artifact.filename).exists()


def test_ensure_artifact_rejects_wrong_size(tmp_path):
    content = b"size-check"
    artifact = rr.Artifact(
        name="size-check",
        url="https://example.invalid/size-check",
        filename="size-check.bin",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content) + 1,
    )

    def fetch(_url: str, path: Path, _timeout: float) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    with pytest.raises(rr.ChecksumError, match="size mismatch"):
        rr.ensure_artifact(tmp_path, artifact, fetcher=fetch)
    assert not (tmp_path / "downloads" / artifact.filename).exists()


def test_ensure_artifact_replaces_corrupt_cache_online(tmp_path):
    content = b"good"
    artifact = _fixture_artifact(content)
    cached = tmp_path / "downloads" / artifact.filename
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"old-corrupt")
    calls = []

    def fetch(_url: str, path: Path, _timeout: float) -> Path:
        calls.append(path)
        path.write_bytes(content)
        return path

    path = rr.ensure_artifact(tmp_path, artifact, fetcher=fetch)
    assert path.read_bytes() == content
    assert len(calls) == 1


def test_ensure_artifact_offline_missing_is_an_error(tmp_path):
    artifact = _fixture_artifact(b"missing")
    with pytest.raises(rr.ReferenceError, match="offline"):
        rr.ensure_artifact(tmp_path, artifact, offline=True)


def test_safe_extract_zip_accepts_relative_members(tmp_path):
    archive_path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("root/__namelist/_a_Grafi/crt.yaml", "productName: crt\n")
        archive.writestr("root/data.txt", "ok\n")

    destination = tmp_path / "extracted"
    rr.safe_extract_zip(
        archive_path,
        destination,
        expected_members=("root/data.txt", "root/__namelist/_a_Grafi/crt.yaml"),
    )
    assert (destination / "root" / "data.txt").read_text() == "ok\n"


@pytest.mark.parametrize(
    "member",
    ["../escape.txt", "root/../../escape.txt", "..\\escape.txt", "../escape/"],
)
def test_safe_extract_zip_rejects_parent_paths(tmp_path, member):
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, "unsafe")
    destination = tmp_path / "nested" / "extracted"

    with pytest.raises(rr.ReferenceError, match="unsafe zip member"):
        rr.safe_extract_zip(archive_path, destination)
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "nested" / "escape.txt").exists()


def test_safe_extract_zip_reports_missing_expected_members(tmp_path):
    archive_path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("root/data.txt", "ok\n")
    with pytest.raises(rr.ReferenceError, match="lacks required members"):
        rr.safe_extract_zip(archive_path, tmp_path / "out", expected_members=("missing.txt",))


def test_extract_visir2_source_re_extracts_verified_archive(tmp_path, monkeypatch):
    license_bytes = b"GPL-3.0-or-later fixture\n"
    module_bytes = b"VALUE = 'original'\n"
    archive_path = tmp_path / "downloads" / "fixture-source.zip"
    archive_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("LICENSE", license_bytes)
        archive.writestr("_a_Grafi/MAIN_Grafi.py", module_bytes)

    artifact = _fixture_artifact(
        archive_path.read_bytes(), name="fixture-source.zip"
    )
    monkeypatch.setattr(rr, "VISIR2_SOURCE", artifact)
    monkeypatch.setattr(
        rr, "VISIR2_LICENSE_SHA256", hashlib.sha256(license_bytes).hexdigest()
    )

    source = rr.extract_visir2_source(tmp_path)
    module = source / "_a_Grafi" / "MAIN_Grafi.py"
    assert module.read_bytes() == module_bytes

    module.write_bytes(b"VALUE = 'tampered'\n")
    source = rr.extract_visir2_source(tmp_path)
    assert module.read_bytes() == module_bytes
    assert (source / "LICENSE").read_bytes() == license_bytes


def test_run_command_captures_output_and_log(tmp_path):
    log_path = tmp_path / "success.log"
    result = rr.run_command(
        [sys.executable, "-c", "print('reference output')"],
        timeout=10.0,
        log_path=log_path,
    )
    assert result.returncode == 0
    assert result.duration_s >= 0.0
    assert "reference output" in result.output
    assert "reference output" in log_path.read_text()


def test_run_command_timeout_is_bounded_and_logged(tmp_path):
    log_path = tmp_path / "timeout.log"
    started = time.monotonic()
    with pytest.raises(rr.CommandTimeout) as caught:
        rr.run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=0.2,
            log_path=log_path,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 4.0
    assert caught.value.timeout_s == 0.2
    assert caught.value.log_path == log_path
    assert "TIMEOUT" in log_path.read_text()


def test_run_command_failure_is_an_error(tmp_path):
    log_path = tmp_path / "failure.log"
    with pytest.raises(rr.CommandError, match="code 3"):
        rr.run_command(
            [sys.executable, "-c", "raise SystemExit(3)"],
            timeout=10.0,
            log_path=log_path,
        )
    assert log_path.is_file()


def test_parse_analytic_reference_csv_accepts_official_fixture(tmp_path):
    csv_path = tmp_path / "Analitiche_curr.csv"
    csv_path.write_text(
        "\ufeffversion,nu,invDx,L,deltaL,T*,deltaT*,Sint\n"
        "VISIR-1b,5,25,1.018,1.312934017,1.056,2.485651669,1\n"
        "analytic,,,1.00480754,0,1.03038814,0,\n",
        encoding="utf-8",
    )
    assert rr.parse_analytic_reference_csv(csv_path) == rr.VISIR2_ANALYTIC_REFERENCE


def test_parse_analytic_reference_csv_rejects_changed_reference(tmp_path):
    csv_path = tmp_path / "Analitiche_curr.csv"
    csv_path.write_text(
        "version,nu,invDx,L,deltaL,T*,deltaT*,Sint\n"
        "analytic,,,1.1,0,1.2,0,\n",
        encoding="utf-8",
    )
    with pytest.raises(rr.ReferenceError, match="reference changed"):
        rr.parse_analytic_reference_csv(csv_path)


def test_parse_analytic_reference_csv_requires_one_analytic_row(tmp_path):
    csv_path = tmp_path / "Analitiche_curr.csv"
    csv_path.write_text(
        "version,nu,invDx,L,deltaL,T*,deltaT*,Sint\n"
        "VISIR-1b,5,25,1.018,0,1.056,0,1\n",
        encoding="utf-8",
    )
    with pytest.raises(rr.ReferenceError, match="exactly one analytic row"):
        rr.parse_analytic_reference_csv(csv_path)


def test_evaluate_analytic_uses_declared_three_percent_tolerance():
    measured = {"L_over_L0": 1.017587668235335, "T_over_T0": 1.0568394582308234}
    comparison = rr.evaluate_analytic(measured, rr.VISIR2_ANALYTIC_REFERENCE)
    assert comparison["passed"] is True
    assert comparison["tolerance_percent"] == 3.0
    assert comparison["delta_L_percent"] == pytest.approx(1.2718981224339767)
    assert comparison["delta_T_percent"] == pytest.approx(2.5671217674170315)

    outside = rr.evaluate_analytic(
        {"L_over_L0": 1.10, "T_over_T0": 1.10}, rr.VISIR2_ANALYTIC_REFERENCE
    )
    assert outside["passed"] is False


def test_parse_simroute_report_reads_published_rows():
    report = """
Route optimized (without waves):           9.40            151.38
Route optimized (with waves):             14.97            161.56
Route Minimum Distance with waves:        16.93            151.38
"""
    assert rr.parse_simroute_report(report) == {
        "optimized_without_waves": {"hours": 9.40, "miles": 151.38},
        "optimized_with_waves": {"hours": 14.97, "miles": 161.56},
        "minimum_distance_with_waves": {"hours": 16.93, "miles": 151.38},
    }


def test_committed_visir2_result_is_consistent_with_the_manifest():
    result = json.loads((REFERENCES / "visir2-run.json").read_text(encoding="utf-8"))
    assert result["schema_version"] == 1
    assert result["benchmark"] == "visir2"
    assert result["status"] == "passed"
    assert result["source"]["archive"]["sha256"] == rr.VISIR2_SOURCE.sha256
    assert result["analytic_inputs"]["archive"]["sha256"] == rr.VISIR2_ANALYTIC_CURRENT.sha256
    assert result["analytic_inputs"]["reference_csv"]["sha256"] == rr.VISIR2_ANALYTIC_CSV_SHA256
    assert result["reference"] == rr.VISIR2_ANALYTIC_REFERENCE
    assert result["comparison"]["passed"] is True
    assert result["comparison"]["tolerance_percent"] == rr.VISIR2_ANALYTIC_TOLERANCE_PERCENT
    assert result["measured"]["L_over_L0"] == pytest.approx(1.017587668235335)
    assert result["measured"]["T_over_T0"] == pytest.approx(1.0568394582308234)
    assert result["measured"]["official_verdict"] == "Curr analytic PASSED"
    assert "THIS RUN" in "\n".join(result["measured"]["official_metrics_excerpt"])

    recalculated = rr.evaluate_analytic(result["measured"], result["reference"])
    assert recalculated["delta_L_percent"] == pytest.approx(
        result["comparison"]["delta_L_percent"]
    )
    assert recalculated["delta_T_percent"] == pytest.approx(
        result["comparison"]["delta_T_percent"]
    )

    stages = [record["stage"] for record in result["official_commands"]]
    assert stages == ["grafi", "campi", "analitiche"]
    for record in result["official_commands"]:
        assert 0 < record["timeout_s"] <= 600
        assert record["log"].endswith(".log")
        assert record["outputs"]


def test_committed_benchmark_metadata_matches_visir2_result():
    result = json.loads((REFERENCES / "visir2-run.json").read_text(encoding="utf-8"))
    benchmark = json.loads((REFERENCES / "benchmarks.json").read_text(encoding="utf-8"))
    visir2 = benchmark["visir2"]
    reproduction = visir2["reproduction"]

    assert visir2["status"] == "official_analytic_current_reproduced"
    assert visir2["alboran_full_numerical_status"] == "not_run"
    assert reproduction["result_file"] == "agent/references/visir2-run.json"
    assert reproduction["case"] == result["case"]
    assert reproduction["reproduction_kind"] == result["reproduction_kind"]
    assert reproduction["measured_utc"] == result["measured_utc"]
    assert reproduction["source_archive_sha256"] == (
        result["source"]["archive"]["sha256"]
    )
    assert reproduction["source_archive_sha256"] == rr.VISIR2_SOURCE.sha256
    assert reproduction["analytic_input_sha256"] == (
        result["analytic_inputs"]["archive"]["sha256"]
    )
    assert reproduction["analytic_reference_csv_sha256"] == (
        result["analytic_inputs"]["reference_csv"]["sha256"]
    )
    assert reproduction["stage_log_sha256"] == {
        record["log"]: record["log_sha256"]
        for record in result["official_commands"]
    }

    measured = result["measured"]
    for key, value in reproduction["measured"].items():
        assert measured[key] == value
    assert reproduction["reference"] == result["reference"]
    assert reproduction["comparison"] == result["comparison"]
    assert reproduction["official_verdict"] == measured["official_verdict"]
    assert reproduction["runtime"]["python"] == result["runtime"]["python"]
    for package, version in reproduction["runtime"].items():
        if package != "python":
            assert result["runtime"]["packages"][package] == version

    assert visir2["available_reference_benchmarks"]["analytic_current"]["status"] == (
        "reproduced_official"
    )
    assert visir2["available_reference_benchmarks"]["alboran_current_wind"][
        "status"
    ] == "not_run"
