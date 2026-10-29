"""Fetch the pinned S175 source files, verify them, and prepare runtime arrays."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from urllib.request import urlopen

from game.simulation.vessel import prepare_s175

COMMIT = "ce0b40ed101411fc4eebd064b35c5a96916106ad"
ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "HYDRO/vessels_shipx/s175/s175.mat": "bac9c93feadd98dc1380c2b18c07b5b6b536c30989c2e02f9255fa14dbe60c79",
    "HYDRO/vessels_shipx/s175/input.hyd": "e2856b13f368d7ba8c28a2b0b37e03fb062c648e289bdd463968bcdfb260e3c9",
    "LICENSE": "922bb433450a9e39368009c29db7b07857520b8e3aad20797f5dc082df6dcce1",
}


def fetch_sources(directory: Path, files=FILES) -> Path:
    for name, expected in files.items():
        target = directory / name
        if target.exists():
            data = target.read_bytes()
        else:
            with urlopen(f"https://raw.githubusercontent.com/cybergalactic/MSS/{COMMIT}/{name}", timeout=60) as response:
                data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Source checksum mismatch: {name}; no data were replaced")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
    return directory / "HYDRO/vessels_shipx/s175/s175.mat"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-directory", type=Path, default=ROOT / "data/raw/mss")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/s175")
    args = parser.parse_args()
    print(prepare_s175(fetch_sources(args.raw_directory), args.output))


if __name__ == "__main__":
    main()
