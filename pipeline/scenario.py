"""Analyze, build and numerically validate a curated scenario with the runtime toolkit.

uv run python -m pipeline.scenario analyze scenarios/sole-20261022-21/scenario.json
uv run python -m pipeline.scenario validate scenarios/sole-20261022-21/scenario.json
uv run python -m pipeline.scenario build scenarios/sole-20261022-21/scenario.json --out data/processed/sole
"""

import argparse
from dataclasses import asdict
from datetime import timedelta
import json
import math
from pathlib import Path
import os
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import xarray as xr

from game.scenario import load_scenario, utc, digest
from game.simulation.runtime import Command, EARTH_RADIUS_M


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def displacement(position, target):
    lon, lat = position
    north = math.radians(target[1] - lat) * EARTH_RADIUS_M
    east = math.radians(target[0] - lon) * EARTH_RADIUS_M * math.cos(math.radians(lat))
    return math.hypot(north, east), math.degrees(math.atan2(east, north)) % 360


def analyze(scenario):
    d = scenario.definition
    rows, sources = [], {}
    start, end = utc(d["start"]), utc(d["end"])
    with scenario.environment() as env:
        # Validate the complete target disk at native-grid crossings and its perimeter.
        target = d["mission"]["target_lon_lat"]
        radius = d["mission"]["radius_m"]
        points = [target, [d["initial"]["lon"], d["initial"]["lat"]]]
        for angle in np.linspace(0, 2 * math.pi, 33):
            points.append(
                [
                    target[0]
                    + math.degrees(radius * math.sin(angle) / EARTH_RADIUS_M)
                    / math.cos(math.radians(target[1])),
                    target[1] + math.degrees(radius * math.cos(angle) / EARTH_RADIUS_M),
                ]
            )
        for layer, item in d["environment"].items():
            with xr.open_dataset(scenario.root / item["path"]) as ds:
                sources[layer] = dict(
                    item,
                    variables={
                        k: {"unit": v.attrs.get("units"), "dimensions": list(v.dims)}
                        for k, v in ds.data_vars.items()
                    },
                    product=ds.attrs.get("subset:productId"),
                    dataset=ds.attrs.get("subset:datasetId"),
                    provenance={k: str(v) for k, v in ds.attrs.items()},
                    coverage={
                        k: [str(ds[k].values[0]), str(ds[k].values[-1])]
                        for k in ("time", "latitude", "longitude")
                    },
                    temporal_semantics={
                        "temperature": "UTC daily mean, held for UTC day",
                        "waves": "hourly instantaneous",
                        "currents": "hourly mean",
                        "wind": "hourly source-native wind",
                    }[layer],
                    issued_at=None,
                )
        with xr.open_dataset(
            scenario.root / d["environment"]["waves"]["path"]
        ) as waves:
            west, east, south, north = d["area_bbox"]
            box = waves.sel(longitude=slice(west, east), latitude=slice(south, north))
            for hour in range(math.ceil((end - start).total_seconds() / 3600) + 1):
                time = min(start + timedelta(hours=hour), end)
                for lon, lat in points:
                    env.sample(time, lon, lat)
                a = box.sel(
                    time=np.datetime64(time.replace(tzinfo=None)), method="nearest"
                )
                row = {
                    "time": time.isoformat(),
                    "source_snapshot_at": str(a.time.values),
                    "initial": asdict(
                        env.sample(time, d["initial"]["lon"], d["initial"]["lat"])
                    ),
                    "target": asdict(env.sample(time, *target)),
                    "statistics": {},
                }
                for name in (
                    "VHM0",
                    "VTPK",
                    "VHM0_WW",
                    "VTM01_WW",
                    "VHM0_SW1",
                    "VTM01_SW1",
                ):
                    if name in a:
                        v = a[name].values
                        row["statistics"][name] = {
                            "mean": float(np.nanmean(v)),
                            "max": float(np.nanmax(v)),
                            "missing_cells": int(np.isnan(v).sum()),
                        }
                for name in ("VMDR_WW", "VMDR_SW1"):
                    if name in a:
                        angles = np.deg2rad(a[name].values)
                        sine, cosine = (
                            float(np.nanmean(np.sin(angles))),
                            float(np.nanmean(np.cos(angles))),
                        )
                        row["statistics"][name] = {
                            "circular_mean_deg": math.degrees(math.atan2(sine, cosine))
                            % 360
                            if math.hypot(sine, cosine) > 1e-10
                            else None
                        }
                heights = a.VHM0.values
                indices = np.argwhere(np.isfinite(heights))
                indices = sorted(indices, key=lambda ij: float(heights[tuple(ij)]))[:5]
                row["lower_wave_cells"] = [
                    {
                        "lon": float(a.longitude[j]),
                        "lat": float(a.latitude[i]),
                        "hs_m": float(heights[i, j]),
                    }
                    for i, j in indices
                ]
                rows.append(row)
    return {
        "sources": sources,
        "hourly": rows,
        "information_policy": "Historical reconstruction drives physics; it is not a forecast released to players.",
        "reports": d["reports"],
    }


def trial(scenario, env, name, seed, output):
    d = scenario.definition
    spec = d["trials"][name]
    sim = scenario.simulation(env, seed)
    state = scenario.initial_state(sim)
    target, budget = d["mission"]["target_lon_lat"], d["mission"]["budget_s"]
    records, commands, events = [], [], []
    reached = False
    reason = "budget_reached"
    while state.exposure.elapsed_s < budget:
        elapsed = state.exposure.elapsed_s
        distance, heading = displacement(sim.position(state), target)
        if distance <= d["mission"]["radius_m"]:
            reached = True
            reason = "target_reached"
            break
        speed = spec["speed_mps"]
        if elapsed < spec.get("wait_s", 0):
            speed = 0
            heading = d["initial"]["heading_deg"]
        if elapsed < spec.get("initial_duration_s", 0):
            heading = spec["initial_heading_deg"]
        # Check the objective every five seconds; commands change only at review times.
        if not commands or round(elapsed) % d["mission"]["review_interval_s"] == 0:
            command = Command(heading, speed)
            commands.append({"time_s": elapsed, **asdict(command)})
        result = sim.advance(state, command, min(5, budget - elapsed))
        state = result.state
        records.extend(asdict(s) for s in result.trajectory)
        events.extend(asdict(e) for e in result.events)
        if result.stopping_reason != "duration_reached":
            reason = result.stopping_reason
            break
    distance, _ = displacement(sim.position(state), target)
    if distance <= d["mission"]["radius_m"]:
        reached = True
        reason = "target_reached"
    result = {
        "trial": name,
        "seed": seed,
        "reached": reached,
        "stopping_reason": reason,
        "elapsed_s": state.exposure.elapsed_s,
        "distance_to_target_m": distance,
        "final_lon_lat": sim.position(state),
        "exposure": asdict(state.exposure),
        "max_roll_deg": max((abs(s["roll_deg"]) for s in records), default=0),
        "max_lateral_acceleration_mps2": max(
            (abs(s["lateral_acceleration_mps2"]) for s in records), default=0
        ),
        "commands": commands,
        "events": events,
        "wave_flags": list(state.wave_flags),
    }
    write_json(output, dict(result, trajectory=records))
    print(
        f"{name} seed={seed}: {reason}, {state.exposure.elapsed_s:g}s, remaining {distance:.0f}m",
        flush=True,
    )
    return result


def trial_job(definition, root, name, seed, output):
    from game.scenario import Scenario

    scenario = Scenario(definition, root)
    with scenario.environment() as environment:
        return trial(scenario, environment, name, seed, output)


def validate(scenario, output, seeds):
    analysis = analyze(scenario)
    trials = []
    jobs = [(name, seed) for seed in seeds for name in scenario.definition["trials"]]
    with ProcessPoolExecutor(
        max_workers=min(8, max(1, (os.cpu_count() or 2) - 2), len(jobs))
    ) as pool:
        pending = [
            pool.submit(
                trial_job,
                scenario.definition,
                scenario.root,
                name,
                seed,
                output / f"{name}-{seed}.json",
            )
            for name, seed in jobs
        ]
        for future in as_completed(pending):
            trials.append(future.result())
            write_json(output / "progress.json", trials)
    trials.sort(key=lambda t: (t["seed"], t["trial"]))
    good = all(any(t["reached"] for t in trials if t["seed"] == seed) for seed in seeds)
    differences = []
    for seed in seeds:
        group = [t for t in trials if t["seed"] == seed]
        differences.append(
            any(
                abs(
                    t["exposure"]["lateral_squared_m2_s3"]
                    - group[0]["exposure"]["lateral_squared_m2_s3"]
                )
                > 1e-6
                for t in group[1:]
            )
        )
    summary = {
        "scenario_id": scenario.definition["id"],
        "passed": good and all(differences),
        "checks": {
            "completion_each_seed": good,
            "exposure_difference_each_seed": all(differences),
        },
        "analysis": analysis,
        "trials": trials,
    }
    write_json(output / "validation.json", summary)
    return summary


def report_evidence(scenario):
    d = scenario.definition
    start, end = utc(d["start"]), utc(d["end"])
    evidence = []
    directory = scenario.root / d["reports"].get("archive_directory", "data/raw/aemet")
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text())
        if record.get("kind") == "official_forecast":
            if (
                utc(record["valid_from"]) < end
                and utc(record["valid_to"]) > start
                and utc(record["issued_at"]) < end
            ):
                evidence.append(record)
        elif record.get("kind") == "official_warning_archive":
            matches = [
                w
                for w in record["warnings"]
                if utc(w["valid_from"]) < end
                and utc(w["valid_to"]) > start
                and w["issued_at"]
                and utc(w["issued_at"]) < end
            ]
            if matches:
                evidence.append(dict(record, warnings=matches))
    return evidence


def build(path, out, validation=None):
    scenario = load_scenario(path)
    analysis = analyze(scenario)
    analysis["official_report_evidence"] = report_evidence(scenario)
    gameplay = scenario.gameplay() if 'gameplay' in scenario.definition else None
    if gameplay is not None:
        analysis['gameplay'] = dict(scenario.definition['gameplay'],
            region_snapshots=len(gameplay['regions']), reports=len(gameplay['reports']),
            cargo=gameplay['cargo'])
    reviewed = None
    if validation is not None:
        reviewed = json.loads(Path(validation).read_text())
        if not reviewed.get("passed") or reviewed.get("definition_sha256") != digest(
            path
        ):
            raise ValueError("Validation does not approve this scenario definition")
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "scenario.json", scenario.definition)
    write_json(out / "data-manifest.json", analysis)
    if gameplay is not None:
        write_json(out / 'gameplay.json', gameplay)
    start = analysis["hourly"][0]
    text = (
        f"# {scenario.definition['title']}\n\n开始：{scenario.definition['start']}。任务预算 {scenario.definition['mission']['budget_s'] / 3600:g} 小时。\n\n"
        f"起点有效波高 {start['initial']['hs']:.2f} m，峰值周期 {start['initial']['tp']:.2f} s。"
        f"向目标区撤离，比较时间与累计暴露。目标区代表较低浪高水域，不是港口或安全认证。\n\n"
        "历史重建用于模拟，不代表当时已发布的预报。同期 AEMET 海上通报的可用状态见数据清单；缺失时不补造历史通报。"
        "总浪用于现有受力计算；风浪和涌浪分量用于解释，不重复叠加能量。\n\n"
        "Generated using E.U. Copernicus Marine Service Information; "
        "https://doi.org/10.25423/CMCC/MEDSEA_MULTIYEAR_WAV_006_012_MEDWAM3I, "
        "https://doi.org/10.48670/mds-00375, https://doi.org/10.48670/moi-00305\n"
    )
    (out / "briefing.md").write_text(text)
    if gameplay is not None:
        text = text.replace('向目标区撤离，比较时间与累计暴露。目标区代表较低浪高水域，不是港口或安全认证。',
            '驶离整片随时间变化的险情海域，在时限内进入外部有效水域即完成脱困；内部低浪区不算完成。货损等级单独结算。')
        text += '\n本场景提供明确标注的游戏模拟预报，按游戏时间发布，不代表同期官方预报。船舶采用固定满载游戏模板；货损不改变水动力参数。\n'
        (out / 'briefing.md').write_text(text)
    if reviewed is not None:
        write_json(out / "validation.json", reviewed)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["analyze", "build", "validate"])
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data/runs/sole-20261022-21"))
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument(
        "--validation",
        type=Path,
        help="attach a passing report for this exact definition",
    )
    args = parser.parse_args()
    scenario = load_scenario(args.scenario)
    if args.command == "build":
        build(args.scenario, args.out, args.validation)
    elif args.command == "analyze":
        write_json(args.out / "analysis.json", analyze(scenario))
    else:
        result = validate(scenario, args.out, args.seeds)
        result["definition_sha256"] = digest(args.scenario)
        write_json(args.out / "validation.json", result)
        if not result["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
