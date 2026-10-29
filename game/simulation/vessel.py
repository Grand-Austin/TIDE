"""Pinned S175 source conversion and strict loading of prepared runtime data.

The equivalent-matrix calculation follows MSS computeManeuveringModel,
copyright Thor I. Fossen, MIT; see data/vessels/s175/MSS-LICENSE.txt.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.io import loadmat

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "ce0b40ed101411fc4eebd064b35c5a96916106ad"
RAW = ROOT / "data/raw/mss/HYDRO/vessels_shipx/s175/s175.mat"
OUTPUT = ROOT / "data/processed/s175"
RAW_SHA256 = "bac9c93feadd98dc1380c2b18c07b5b6b536c30989c2e02f9255fa14dbe60c79"


@dataclass(frozen=True)
class Vessel:
    main: dict
    MRB: np.ndarray
    MA: np.ndarray
    M: np.ndarray
    Minv: np.ndarray
    D: np.ndarray
    G: np.ndarray
    freqs: np.ndarray
    headings: np.ndarray
    force_rao: np.ndarray
    max_speed_mps: float
    nonlinear_456: np.ndarray


def _validate(vessel: Vessel):
    for name in ("MRB", "MA", "M", "Minv", "D", "G"):
        matrix = getattr(vessel, name)
        if matrix.shape != (6, 6) or not np.all(np.isfinite(matrix)):
            raise ValueError(f"Invalid {name} matrix")
        if not np.allclose(matrix, matrix.T, rtol=1e-10, atol=1e-6):
            raise ValueError(f"{name} is not symmetric")
    if np.linalg.eigvalsh(vessel.M).min() <= 0:
        raise ValueError("Mass matrix must be positive definite")
    if np.linalg.eigvalsh(vessel.D).min() < -1e-6:
        raise ValueError("Damping must be positive semidefinite")
    if np.linalg.eigvalsh(vessel.G).min() < -1e-6 or np.any(np.diag(vessel.G)[2:5] <= 0):
        raise ValueError("Invalid hydrostatic restoring matrix")
    if not np.allclose(vessel.M @ vessel.Minv, np.eye(6), atol=1e-10):
        raise ValueError("Inconsistent inverse mass matrix")
    for name in ("freqs", "headings"):
        values = getattr(vessel, name)
        if values.ndim != 1 or len(values) < 2 or not np.all(np.isfinite(values)) or np.any(np.diff(values) <= 0):
            raise ValueError(f"Invalid {name} axis")
    if vessel.freqs[0] <= 0 or vessel.headings[0] != 0 or vessel.headings[-1] >= 2*np.pi:
        raise ValueError("Invalid hydrodynamic axis range")
    if vessel.force_rao.shape != (6, len(vessel.freqs), len(vessel.headings)) or not np.all(np.isfinite(vessel.force_rao)):
        raise ValueError("Invalid force RAOs")
    if not np.isfinite(vessel.max_speed_mps) or vessel.max_speed_mps <= 0:
        raise ValueError("Invalid source speed range")
    for key in ("g", "Lpp", "B", "T", "rho", "m", "k44", "k55", "k66"):
        if not np.isfinite(vessel.main[key]) or vessel.main[key] <= 0:
            raise ValueError(f"Invalid vessel parameter {key}")


def prepare_s175(raw_path=RAW, output_dir=OUTPUT) -> Path:
    raw_path, output_dir = Path(raw_path), Path(output_dir)
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if digest != RAW_SHA256:
        raise ValueError("S175 source checksum mismatch")
    source = loadmat(raw_path, simplify_cells=True)["vessel"]
    if source["hydrodynamic_reference"] != "CG" or source["hydrodynamic_axes"] != "FSD":
        raise ValueError("Expected S175 data about CG in FSD axes")
    freqs = np.asarray(source["freqs"], dtype=float)
    headings = np.asarray(source["headings"], dtype=float)
    fine = np.linspace(freqs[0], freqs[-1], 100)
    weight = fine**-5 * np.exp(-0.74/fine**4)
    weight /= np.trapezoid(weight, fine)
    equivalent = {}
    for key in ("A", "B"):
        values = PchipInterpolator(freqs, source[key][:, :, :, 0], axis=2)(fine)
        equivalent[key] = np.trapezoid(values * weight, fine, axis=2)
    mrb = np.asarray(source["MRB"], dtype=float)
    mass = mrb + equivalent["A"]
    restoring = np.zeros((6,6))
    restoring[2:5,2:5] = source["C"][2:5,2:5,0,0]
    indices = np.array([0,1,5])
    decay = np.minimum(120.0, 0.95*np.diag(mass)[indices]/np.diag(equivalent["B"])[indices])
    viscous = np.zeros((6,6))
    viscous[indices,indices] = np.diag(mass)[indices]/decay - np.diag(equivalent["B"])[indices]
    restored = np.array([2,3,4])
    viscous[restored,restored] = 2*np.array([0,.1,0])*np.sqrt(np.diag(mass)[restored]*np.diag(restoring)[restored])
    rao = np.stack([np.asarray(a)[:,:,0]*np.exp(1j*np.asarray(p)[:,:,0])
                    for a,p in zip(source["forceRAO"]["amp"],source["forceRAO"]["phase"])])
    main = {k: v.tolist() if isinstance(v,np.ndarray) else v for k,v in source["main"].items()}
    vessel = Vessel(main,mrb,equivalent["A"],mass,np.linalg.inv(mass),equivalent["B"]+viscous,
                    restoring,freqs,headings,rao,float(max(source["velocities"])),np.zeros(3))
    _validate(vessel)
    provenance = {
        "schema_version":1, "source_commit":COMMIT, "source_sha256":digest,
        "loading_condition":"Design waterline", "axes":"FSD", "reference":"CG",
        "local_frame":"NED", "units":"m, s, rad, kg, N, Nm",
        "source_parameters":main,
        "model_assumptions":{
            "hydrodynamic_speed_index":0, "reference_peak_rad_s":1.0,
            "equivalent_matrix_spectrum":"MSS power-based PM weighting",
            "T126_s":decay.tolist(), "T126_rule":"min(120, 0.95*Mii/Beqii)",
            "delta_zeta_345":[0,.1,0], "nonlinear_456":[0,0,0],
            "note":"Teaching damping assumptions; not experimental S175 calibration",
        },
        "matrix_relative_antisymmetry":{k:float(np.linalg.norm(a-a.T)/np.linalg.norm(a))
                                       for k,a in (("M",mass),("B_eq",equivalent["B"]),("G",restoring))},
    }
    output_dir.mkdir(parents=True,exist_ok=True)
    destination = output_dir / "vessel.npz"
    np.savez(destination, **{k:getattr(vessel,k) for k in ("MRB","MA","M","Minv","D","G","freqs","headings","force_rao","nonlinear_456")},
             main_json=np.array(json.dumps(main,sort_keys=True)),max_speed_mps=vessel.max_speed_mps)
    provenance["prepared_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (output_dir/"provenance.json").write_text(json.dumps(provenance,indent=2,sort_keys=True)+"\n")
    return destination


def load_s175(path=OUTPUT) -> Vessel:
    path=Path(path)
    if path.is_dir():
        path=path/"vessel.npz"
    provenance=json.loads(path.with_name("provenance.json").read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest()!=provenance["prepared_sha256"]:
        raise ValueError("Prepared vessel checksum mismatch; rerun preparation")
    with np.load(path,allow_pickle=False) as data:
        vessel=Vessel(main=json.loads(str(data["main_json"])), max_speed_mps=float(data["max_speed_mps"]),
                      **{k:data[k] for k in ("MRB","MA","M","Minv","D","G","freqs","headings","force_rao","nonlinear_456")})
    _validate(vessel)
    for value in vars(vessel).values():
        if isinstance(value,np.ndarray):
            value.flags.writeable=False
    return vessel


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw",type=Path,default=RAW)
    parser.add_argument("--output",type=Path,default=OUTPUT)
    args=parser.parse_args()
    print(prepare_s175(args.raw,args.output))
