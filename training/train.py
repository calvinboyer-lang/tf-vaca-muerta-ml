"""Servicio de entrenamiento: cargar -> validar -> entrenar -> comparar -> versionar.

Uso (desde la raíz del repo):

    python -m training.train --model v2 --dataset data/snapshots/<id>.csv.gz --store ./artifacts
    python -m training.train --model v2_notef --dataset ... --store gs://<bucket>/vm --tune

Pasos:
  1. Lee el snapshot y verifica su sha256; lo copia al store (datasets/).
  2. Separa el HOLDOUT FIJO por pozo (hash del idpozo, 20 %). Nunca se entrena con él.
  3. Opcional --tune: hiperparámetros por GroupKFold sobre el conjunto de desarrollo.
  4. Validación repetida (10 semillas, por pozo) -> promedio y desvío.
  5. Validación temporal (últimos meses) -> mismos pozos y pozos nuevos.
  6. Entrena el modelo final con el conjunto de desarrollo y mide train/holdout.
  7. Evalúa la versión vigente sobre el MISMO holdout y decide la promoción
     (mejora mínima + bootstrap pareado por pozo).
  8. Guarda model.joblib + metadata.json; si corresponde, actualiza current.json.
  9. Si existe DATABASE_URL, registra el run en PostgreSQL.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time

import numpy as np
import sklearn

from vm_ml import config, db, registry
from vm_ml.data import load_snapshot, month_label
from vm_ml.evaluation import (
    holdout_mask,
    metrics,
    paired_bootstrap_mae,
    repeated_group_validation,
    temporal_validation,
    tune_tree,
)
from vm_ml.features import feature_columns, make_pipeline, model_frame
from vm_ml.storage import open_store


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", required=True, choices=sorted(config.FEATURE_SETS))
    p.add_argument("--dataset", required=True, help="ruta local del snapshot .csv.gz, o datasets/<id>.csv.gz del store")
    p.add_argument("--store", default="./artifacts", help="carpeta local o gs://bucket/prefijo")
    p.add_argument("--tune", action="store_true", help="buscar max_depth y min_samples_leaf por GroupKFold")
    p.add_argument("--max-depth", type=int, help="fija max_depth (ignora el default de config)")
    p.add_argument("--min-samples-leaf", type=int)
    p.add_argument("--force-promote", action="store_true", help="promover sin comparar (documentar el motivo)")
    p.add_argument("--no-promote", action="store_true", help="solo registrar la versión")
    return p.parse_args(argv)


def resolve_dataset(store, dataset: str) -> tuple[str, dict]:
    """Devuelve una ruta local al snapshot. Si viene del store, lo baja a /tmp."""
    from pathlib import Path
    import tempfile

    local = Path(dataset)
    if local.exists():
        return str(local), {}
    data = store.read_bytes(dataset)
    tmpdir = Path(tempfile.mkdtemp())
    target = tmpdir / Path(dataset).name
    target.write_bytes(data)
    manifest_key = dataset.replace(".csv.gz", ".manifest.json")
    if store.exists(manifest_key):
        target.with_name(target.name.replace(".csv.gz", ".manifest.json")).write_bytes(store.read_bytes(manifest_key))
    return str(target), {}


def copy_dataset_to_store(store, path: str, manifest: dict) -> str:
    from pathlib import Path

    key = f"datasets/{manifest['dataset_id']}.csv.gz"
    if not store.exists(key):
        store.write_bytes(key, Path(path).read_bytes())
        store.write_bytes(
            key.replace(".csv.gz", ".manifest.json"),
            json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
        )
    return key


def main(argv=None) -> int:
    args = parse_args(argv)
    t0 = time.perf_counter()
    started = registry.utc_now()
    store = open_store(args.store)
    name = args.model

    # 1. datos ---------------------------------------------------------------
    path, _ = resolve_dataset(store, args.dataset)
    df, manifest = load_snapshot(path)
    dataset_key = copy_dataset_to_store(store, path, manifest)
    X, y, groups, months = model_frame(df, name)
    print(f"[datos] {manifest['dataset_id']}: {len(X):,} filas, {groups.nunique():,} pozos", flush=True)

    # 2. holdout fijo --------------------------------------------------------
    is_hold = holdout_mask(groups)
    dev = ~is_hold
    Xd, yd, gd = X[dev], y[dev], groups[dev]
    Xh, yh, gh = X[is_hold], y[is_hold], groups[is_hold]
    print(f"[split] desarrollo {dev.sum():,} filas / {gd.nunique():,} pozos; "
          f"holdout {is_hold.sum():,} filas / {gh.nunique():,} pozos", flush=True)

    # 3. hiperparámetros -----------------------------------------------------
    params = dict(config.DEFAULT_TREE_PARAMS[name])
    tuning = None
    if args.tune:
        t_tune = time.perf_counter()
        params, table = tune_tree(lambda p: make_pipeline(name, "tree", p), Xd, yd, gd)
        tuning = {"method": "GroupKFold(5) sobre desarrollo", "grid": config.TUNE_GRID,
                  "results": table, "duration_s": round(time.perf_counter() - t_tune, 2)}
        print(f"[tune] mejores parámetros {params}", flush=True)
    if args.max_depth is not None:
        params["max_depth"] = args.max_depth
    if args.min_samples_leaf is not None:
        params["min_samples_leaf"] = args.min_samples_leaf

    # 4. validación repetida -------------------------------------------------
    t_val = time.perf_counter()
    repeated = {
        kind: repeated_group_validation(lambda k=kind: make_pipeline(name, k, params if k == "tree" else None), Xd, yd, gd)
        for kind in ["tree", "linear", "dummy_mean"]
    }
    # 5. validación temporal -------------------------------------------------
    temporal = temporal_validation(lambda: make_pipeline(name, "tree", params), X, y, groups, months, is_hold)
    temporal["cutoff"] = month_label(temporal["cutoff_mes_idx"])
    duration_validation = time.perf_counter() - t_val
    s = repeated["tree"]["summary"]
    print(f"[cv] árbol MAE {s['mae']['mean']:.1f} ± {s['mae']['std']:.1f}, "
          f"R² {s['r2']['mean']:.3f} ± {s['r2']['std']:.3f}", flush=True)

    # 6. modelo final --------------------------------------------------------
    pipeline = make_pipeline(name, "tree", params)
    t_fit = time.perf_counter()
    pipeline.fit(Xd, yd)
    duration_fit = time.perf_counter() - t_fit
    pred_h = pipeline.predict(Xh)
    m_train = metrics(yd, pipeline.predict(Xd))
    m_hold = metrics(yh, pred_h)
    baselines = {}
    for kind in ["dummy_mean", "dummy_median", "linear"]:
        ref = make_pipeline(name, kind).fit(Xd, yd)
        baselines[kind] = metrics(yh, ref.predict(Xh))
    print(f"[holdout] MAE {m_hold['mae']:.1f}  RMSE {m_hold['rmse']:.1f}  R² {m_hold['r2']:.3f} "
          f"(train MAE {m_train['mae']:.1f}, R² {m_train['r2']:.3f})", flush=True)

    # 7. comparación con la versión vigente ---------------------------------
    comparison = None
    current_mae = None
    try:
        current_pipe, current_meta = registry.load_current(store, name)
    except Exception as exc:
        current_pipe, current_meta = None, None
        comparison = {"error": f"no se pudo cargar la versión vigente: {exc}"}
    if current_pipe is not None:
        pred_cur = current_pipe.predict(Xh)
        current_mae = metrics(yh, pred_cur)["mae"]
        boot = paired_bootstrap_mae(yh, pred_h, pred_cur, gh)
        comparison = {
            "current_version": current_meta["version"],
            "current_mae_same_holdout": current_mae,
            "current_mae_when_trained": current_meta["metrics"]["holdout"]["mae"],
            "bootstrap": boot,
        }
    if args.force_promote:
        promoted, reason = True, "promoción forzada (--force-promote)"
    elif args.no_promote:
        promoted, reason = False, "--no-promote"
    else:
        promoted, reason = registry.decide_promotion(
            m_hold["mae"], current_mae, comparison.get("bootstrap") if comparison else None
        )

    # 8. versionado -----------------------------------------------------------
    version = registry.new_version(name, store)
    numeric, categorical = feature_columns(name)
    cats = {
        col: sorted(Xd[col].unique().tolist()) for col in categorical
    }
    metadata = {
        "version": version,
        "model": name,
        "use": config.MODEL_USE[name],
        "started_utc": started,
        "dataset": {**manifest, "store_key": dataset_key},
        "population": config.POPULATION,
        "target": config.TARGET,
        "features": {"numeric": numeric, "categorical": categorical, "categories": cats},
        "estimator": "DecisionTreeRegressor(random_state=42)",
        "params": params,
        "tuning": tuning,
        "split": {
            "strategy": "holdout fijo por pozo: sha256(salt:idpozo) % 100 < pct",
            "salt": config.HOLDOUT_SALT,
            "pct": config.HOLDOUT_PCT,
            "final_model_trained_on": "desarrollo (el holdout nunca se usa para entrenar)",
        },
        "rows": {"train": int(len(Xd)), "holdout": int(len(Xh))},
        "wells": {"train": int(gd.nunique()), "holdout": int(gh.nunique())},
        "metrics": {"train": m_train, "holdout": m_hold, "baselines_holdout": baselines},
        "validation": {"repeated": repeated, "temporal": temporal},
        "target_stats_train": {
            "p50": float(np.quantile(yd, 0.5)),
            "p99": float(np.quantile(yd, 0.99)),
            "max": float(yd.max()),
        },
        "comparison": comparison,
        "promotion": {
            "promoted": promoted,
            "reason": reason,
            "rule": {
                "min_rel_improvement": config.MIN_REL_IMPROVEMENT,
                "bootstrap_confidence": config.BOOTSTRAP_CONFIDENCE,
                "bootstrap_iterations": config.BOOTSTRAP_ITERATIONS,
            },
        },
        "durations_s": {
            "validation": round(duration_validation, 3),
            "fit": round(duration_fit, 3),
            "total": None,
        },
        "environment": {
            "python": platform.python_version(),
            "sklearn": sklearn.__version__,
            "numpy": np.__version__,
        },
    }
    metadata["durations_s"]["total"] = round(time.perf_counter() - t0, 3)
    digest = registry.save_version(store, name, version, pipeline, metadata)
    metadata["artifact"] = {"file": "model.joblib", "sha256": digest}
    if promoted:
        registry.promote(store, name, version, digest, m_hold["mae"], reason)
    print(f"[versión] {version} {'PROMOVIDA' if promoted else 'no promovida'}: {reason}", flush=True)

    # 9. base de datos (opcional) --------------------------------------------
    if db.record_training_run(metadata):
        print("[db] run registrado en training_runs", flush=True)

    print(json.dumps({
        "version": version,
        "promoted": promoted,
        "holdout": m_hold,
        "cv_mae": s["mae"],
        "duration_total_s": metadata["durations_s"]["total"],
        "store": store.uri,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
