"""Lista las versiones de un modelo y marca la vigente.

    python scripts/versions.py --model v2 --store ./artifacts
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vm_ml import registry  # noqa: E402
from vm_ml.storage import open_store  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--store", default="./artifacts")
    args = p.parse_args()
    store = open_store(args.store)
    current = registry.current_pointer(store, args.model) or {}
    print(f"{'':2}{'versión':28}{'dataset':28}{'MAE hold':>10}{'R² hold':>9}{'CV MAE':>18}{'fit s':>7}  decisión")
    for m in registry.list_versions(store, args.model):
        h = m["metrics"]["holdout"]
        cv = m["validation"]["repeated"]["tree"]["summary"]["mae"]
        mark = "*" if m["version"] == current.get("version") else " "
        print(
            f"{mark} {m['version']:28}{m['dataset']['dataset_id']:28}{h['mae']:10.1f}{h['r2']:9.3f}"
            f"{cv['mean']:11.1f} ± {cv['std']:4.0f}{m['durations_s']['fit']:7.2f}  {m['promotion']['reason']}"
        )
    print("\n* = versión vigente (current.json)")


if __name__ == "__main__":
    main()
