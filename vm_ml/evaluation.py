"""Evaluación: holdout fijo por pozo, validación repetida, validación temporal,
búsqueda de hiperparámetros y bootstrap pareado para comparar versiones."""

from __future__ import annotations

import hashlib
import itertools
from typing import Callable

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit

from . import config

PipelineFactory = Callable[[], object]


def metrics(y_true, y_pred) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": float(r2_score(y_true, y_pred)),
    }


def summarize(runs: list[dict[str, float]]) -> dict[str, dict[str, float]]:
    table = pd.DataFrame(runs)
    return {
        col: {"mean": float(table[col].mean()), "std": float(table[col].std(ddof=1)) if len(table) > 1 else 0.0}
        for col in ["mae", "rmse", "r2"]
    }


# ----------------------------------------------------------------------------
# Holdout fijo
# ----------------------------------------------------------------------------
def holdout_mask(groups: pd.Series, salt: str = config.HOLDOUT_SALT, pct: int = config.HOLDOUT_PCT) -> np.ndarray:
    """True si el pozo pertenece al conjunto de prueba fijo (hash del idpozo)."""

    def bucket(idpozo: int) -> int:
        return int(hashlib.sha256(f"{salt}:{int(idpozo)}".encode()).hexdigest(), 16) % 100

    buckets = {g: bucket(g) for g in pd.unique(groups)}
    return groups.map(buckets).to_numpy() < pct


# ----------------------------------------------------------------------------
# Validación repetida sobre el conjunto de desarrollo
# ----------------------------------------------------------------------------
def repeated_group_validation(
    make: PipelineFactory,
    X: pd.DataFrame,
    y: pd.Series,
    groups: pd.Series,
    seeds: list[int] = config.CV_SEEDS,
    test_size: float = config.CV_TEST_SIZE,
) -> dict:
    runs = []
    for seed in seeds:
        split = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
        train_idx, test_idx = next(split.split(X, y, groups))
        model = make()
        model.fit(X.iloc[train_idx], y.iloc[train_idx])
        runs.append({"seed": seed, **metrics(y.iloc[test_idx], model.predict(X.iloc[test_idx]))})
    return {"seeds": seeds, "test_size": test_size, "runs": runs, "summary": summarize(runs)}


# ----------------------------------------------------------------------------
# Validación temporal
# ----------------------------------------------------------------------------
def temporal_validation(
    make: PipelineFactory,
    X: pd.DataFrame,
    y: pd.Series,
    groups: pd.Series,
    months: pd.Series,
    is_holdout: np.ndarray,
    test_months: int = config.TEMPORAL_TEST_MONTHS,
) -> dict:
    """Entrena con los meses anteriores al corte y prueba con los últimos meses.

    - mismos_pozos: pozos de desarrollo, meses futuros (pronóstico de pozos conocidos).
    - pozos_nuevos: pozos del holdout, meses futuros (lo más exigente).
    """
    last = int(months.max())
    cutoff = last - test_months
    train = (~is_holdout) & (months.to_numpy() <= cutoff)
    result: dict = {"cutoff_mes_idx": cutoff, "test_months": test_months}
    if train.sum() == 0:
        return {**result, "error": "sin datos de entrenamiento antes del corte"}
    model = make()
    model.fit(X[train], y[train])
    for name, mask in {
        "mismos_pozos": (~is_holdout) & (months.to_numpy() > cutoff),
        "pozos_nuevos": is_holdout & (months.to_numpy() > cutoff),
    }.items():
        if mask.sum() > 0:
            result[name] = {"rows": int(mask.sum()), **metrics(y[mask], model.predict(X[mask]))}
    return result


# ----------------------------------------------------------------------------
# Hiperparámetros
# ----------------------------------------------------------------------------
def tune_tree(
    make_with_params: Callable[[dict], object],
    X: pd.DataFrame,
    y: pd.Series,
    groups: pd.Series,
    grid: dict[str, list] = config.TUNE_GRID,
    n_splits: int = 5,
) -> tuple[dict, list[dict]]:
    """Grilla con GroupKFold sobre el conjunto de desarrollo (nunca el holdout)."""
    folds = list(GroupKFold(n_splits=n_splits).split(X, y, groups))
    table = []
    keys = list(grid)
    for values in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, values))
        maes = []
        for train_idx, test_idx in folds:
            model = make_with_params(params)
            model.fit(X.iloc[train_idx], y.iloc[train_idx])
            maes.append(mean_absolute_error(y.iloc[test_idx], model.predict(X.iloc[test_idx])))
        table.append({**params, "mae_mean": float(np.mean(maes)), "mae_std": float(np.std(maes, ddof=1))})
    best = min(table, key=lambda row: row["mae_mean"])
    return {k: best[k] for k in keys}, table


# ----------------------------------------------------------------------------
# Comparación pareada de dos versiones sobre el mismo holdout
# ----------------------------------------------------------------------------
def paired_bootstrap_mae(
    y_true: pd.Series,
    pred_candidate: np.ndarray,
    pred_current: np.ndarray,
    groups: pd.Series,
    iterations: int = config.BOOTSTRAP_ITERATIONS,
    confidence: float = config.BOOTSTRAP_CONFIDENCE,
    seed: int = 0,
) -> dict:
    """Bootstrap por pozo de (MAE vigente - MAE candidata). Positivo = candidata mejor.

    Se remuestrean POZOS (no filas), porque las filas de un mismo pozo no son
    independientes entre sí.
    """
    frame = pd.DataFrame(
        {
            "g": groups.to_numpy(),
            "err_new": np.abs(y_true.to_numpy() - pred_candidate),
            "err_old": np.abs(y_true.to_numpy() - pred_current),
        }
    )
    per_well = frame.groupby("g").agg(n=("err_new", "size"), new=("err_new", "sum"), old=("err_old", "sum"))
    rng = np.random.default_rng(seed)
    n = len(per_well)
    diffs = np.empty(iterations)
    for i in range(iterations):
        sample = per_well.iloc[rng.integers(0, n, n)]
        rows = sample["n"].sum()
        diffs[i] = (sample["old"].sum() - sample["new"].sum()) / rows
    alpha = (1 - confidence) / 2
    observed = (per_well["old"].sum() - per_well["new"].sum()) / per_well["n"].sum()
    return {
        "mae_diff": float(observed),
        "ci_low": float(np.quantile(diffs, alpha)),
        "ci_high": float(np.quantile(diffs, 1 - alpha)),
        "confidence": confidence,
        "iterations": iterations,
        "wells": int(n),
    }
