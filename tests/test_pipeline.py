"""Tests del paquete vm_ml y del entrenamiento, con datos SINTÉTICOS."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from training import train
from vm_ml import config, registry
from vm_ml.data import add_well_age, clean_raw, load_snapshot, norm_text, population, write_snapshot
from vm_ml.evaluation import holdout_mask, paired_bootstrap_mae
from vm_ml.features import model_frame
from vm_ml.storage import open_store
from vm_ml.synthetic import make_snapshot_frame


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    out = tmp_path_factory.mktemp("snap")
    df = add_well_age(clean_raw(make_snapshot_frame(n_wells=150, months=12)))
    return write_snapshot(df, out, ["test"])


def test_norm_text():
    assert norm_text("  Petrolífero ") == "petrolifero"
    assert norm_text("Extracción  Efectiva") == "extraccion efectiva"


def test_population_filter(snapshot):
    df, _ = load_snapshot(snapshot.path)
    pop = population(df)
    assert set(pop["tipopozo"]) == {"Petrolífero"}
    assert set(pop["tipoestado"]) == {"Extracción Efectiva"}


def test_well_age_and_censoring():
    df = pd.DataFrame(
        {
            "anio": [2026] * 4,
            "mes": [1, 2, 3, 3],
            "idpozo": [1, 1, 1, 2],
            "prod_pet": [10, 9, 8, 50],
            "prod_gas": [1, 1, 1, 5],
        }
    )
    out = add_well_age(clean_raw(df))
    pozo1 = out[out.idpozo == 1]
    assert pozo1["edad_meses"].tolist() == [0, 1, 2]
    assert pozo1["edad_censurada"].all()  # empezó en el primer mes de la ventana
    pozo2 = out[out.idpozo == 2]
    assert pozo2["edad_meses"].tolist() == [0]
    assert not pozo2["edad_censurada"].any()


def test_padron_has_priority():
    df = clean_raw(pd.DataFrame({"anio": [2026], "mes": [3], "idpozo": [7], "prod_pet": [5], "prod_gas": [0]}))
    padron = pd.DataFrame({"idpozo": [7], "primera_prod_idx": [2025 * 12 + 2]})  # 2025-03
    out = add_well_age(df, padron)
    assert out["edad_meses"].iloc[0] == 12
    assert out["fuente_edad"].iloc[0] == "padron"


def test_snapshot_hash_is_checked(snapshot, tmp_path):
    copy = tmp_path / snapshot.path.name
    copy.write_bytes(snapshot.path.read_bytes() + b"x")
    (tmp_path / snapshot.manifest_path.name).write_text(snapshot.manifest_path.read_text())
    with pytest.raises(ValueError, match="sha256"):
        load_snapshot(copy)


def test_holdout_is_stable_and_by_well(snapshot):
    df, _ = load_snapshot(snapshot.path)
    X, y, groups, _ = model_frame(df, "v2")
    mask = holdout_mask(groups)
    # ningún pozo queda de los dos lados
    assert not (set(groups[mask]) & set(groups[~mask]))
    # determinístico e independiente del orden de las filas
    shuffled = groups.sample(frac=1, random_state=1)
    again = pd.Series(holdout_mask(shuffled), index=shuffled.index).reindex(groups.index)
    assert (again.to_numpy() == mask).all()
    assert 0.1 < mask.mean() < 0.3


@pytest.mark.parametrize("model_name", sorted(config.FEATURE_SETS))
def test_feature_sets(snapshot, model_name):
    df, _ = load_snapshot(snapshot.path)
    X, *_ = model_frame(df, model_name)
    spec = config.FEATURE_SETS[model_name]
    assert list(X.columns) == spec["numeric"] + spec["categorical"]
    if model_name == "v2_notef":
        assert "tef" not in X.columns


def test_bootstrap_detects_clear_improvement():
    rng = np.random.default_rng(0)
    groups = pd.Series(np.repeat(np.arange(60), 5))
    y = pd.Series(rng.normal(1000, 200, len(groups)))
    good = y.to_numpy() + rng.normal(0, 50, len(groups))
    bad = y.to_numpy() + rng.normal(0, 300, len(groups))
    res = paired_bootstrap_mae(y, good, bad, groups)
    assert res["ci_low"] > 0
    same = paired_bootstrap_mae(y, good, good, groups)
    assert same["mae_diff"] == 0


def test_promotion_rule():
    assert registry.decide_promotion(500, None, None)[0]
    assert not registry.decide_promotion(499, 500, {"ci_low": 1, "ci_high": 2, "confidence": 0.9})[0]
    assert not registry.decide_promotion(450, 500, {"ci_low": -3, "ci_high": 90, "confidence": 0.9})[0]
    assert registry.decide_promotion(450, 500, {"ci_low": 10, "ci_high": 90, "confidence": 0.9})[0]


def test_training_end_to_end(snapshot, tmp_path):
    store_dir = tmp_path / "store"
    args = ["--model", "v2", "--dataset", str(snapshot.path), "--store", str(store_dir)]
    assert train.main(args) == 0
    store = open_store(str(store_dir))
    pointer = registry.current_pointer(store, "v2")
    meta = registry.read_metadata(store, "v2", pointer["version"])
    # metadata con lo que pide la devolución
    assert meta["params"] == config.DEFAULT_TREE_PARAMS["v2"]
    assert meta["split"]["salt"] == config.HOLDOUT_SALT
    assert meta["dataset"]["sha256"] == snapshot.manifest["sha256"]
    assert len(meta["validation"]["repeated"]["tree"]["runs"]) == len(config.CV_SEEDS)
    assert meta["durations_s"]["fit"] >= 0
    assert store.exists(f"datasets/{snapshot.dataset_id}.csv.gz")

    # el artefacto vigente se carga y predice
    pipe, _ = registry.load_current(store, "v2")
    df, _ = load_snapshot(snapshot.path)
    X, *_ = model_frame(df, "v2")
    assert len(pipe.predict(X.head(3))) == 3

    # misma configuración y mismos datos: no se promueve (holdout fijo)
    assert train.main(args) == 0
    versions = registry.list_versions(store, "v2")
    assert len(versions) == 2
    assert versions[-1]["promotion"]["promoted"] is False
    assert registry.current_pointer(store, "v2")["version"] == pointer["version"]


def test_artifact_tampering_is_rejected(snapshot, tmp_path):
    store_dir = tmp_path / "store"
    train.main(["--model", "v1", "--dataset", str(snapshot.path), "--store", str(store_dir)])
    store = open_store(str(store_dir))
    pointer = registry.current_pointer(store, "v1")
    key = f"models/v1/{pointer['version']}/model.joblib"
    store.write_bytes(key, store.read_bytes(key) + b"\0")
    with pytest.raises(ValueError, match="sha256"):
        registry.load_current(store, "v1")
    json.dumps(pointer)  # el puntero es JSON serializable


def test_arcgis_padron_and_trajectories():
    from vm_ml.data import padron_from_pozos, trajectory_features

    # FECHA_INIC llega como milisegundos desde 1970: 2025-03-15 -> índice mensual de 2025-03
    ms = int(pd.Timestamp("2025-03-15", tz="UTC").timestamp() * 1000)
    pozos = pd.DataFrame({"IDENTIFICA": [7.0, 8.0], "FECHA_INIC": [ms, None]})
    padron = padron_from_pozos(pozos)
    assert padron.to_dict("records") == [{"idpozo": 7, "primera_prod_idx": 2025 * 12 + 2}]

    tray = pd.DataFrame({"idpo": [7, 7, 8], "md": [6000, 6100, 5000], "tvd": [3000, 3000, 2900], "hz": [2500, 2800, None]})
    feats = trajectory_features(tray).set_index("idpozo")
    assert feats.loc[7, "largo_horizontal_m"] == 2800
    assert 8 in feats.index
