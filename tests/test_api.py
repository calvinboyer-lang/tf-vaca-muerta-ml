"""Tests de la API: lógica (service.py) siempre; endpoints HTTP si FastAPI está instalado."""

from __future__ import annotations

import pytest

from api.service import ModelNotLoaded, ServingState, WellNotFound
from training import train
from vm_ml import registry
from vm_ml.data import add_well_age, clean_raw, load_snapshot, write_snapshot
from vm_ml.storage import open_store
from vm_ml.synthetic import make_snapshot_frame


@pytest.fixture(scope="module")
def trained_store(tmp_path_factory):
    base = tmp_path_factory.mktemp("api")
    df = add_well_age(clean_raw(make_snapshot_frame(n_wells=120, months=10)))
    snap = write_snapshot(df, base / "snap", ["test"])
    store = base / "store"
    for model in ["v1", "v2", "v2_notef"]:
        assert train.main(["--model", model, "--dataset", str(snap.path), "--store", str(store)]) == 0
    return str(store), snap


@pytest.fixture()
def state(trained_store):
    st = ServingState(trained_store[0], ["v1", "v2", "v2_notef"], "v2_notef")
    st.reload()
    return st


def a_well(snap) -> int:
    df, _ = load_snapshot(snap.path)
    pop = df[(df.tipopozo == "Petrolífero") & (df.tipoestado == "Extracción Efectiva")]
    return int(pop["idpozo"].value_counts().index[0])


def test_loads_all_models(state):
    assert state.ready()
    assert set(state.handles) == {"v1", "v2", "v2_notef"}
    assert state.wells is not None and len(state.wells) > 0


def test_predict_by_well_fills_features(state, trained_store):
    idpozo = a_well(trained_store[1])
    out = state.predict(None, idpozo, None, None, {})
    assert out["model"] == "v2_notef"
    assert out["prediccion_m3_mes"] >= 0
    assert out["entrada"]["edad_meses"] is not None
    assert "tef" not in out["entrada"]  # v2_notef no usa TEF
    out_v2 = state.predict("v2", idpozo, 2026, 7, {"tef": 20})
    assert out_v2["entrada"]["tef"] == 20
    assert out_v2["periodo"] == "2026-07"


def test_predict_explicit_features(state):
    out = state.predict(
        "v2_notef", None, 2026, 3,
        {"profundidad": 3000, "edad_meses": 6, "tipoextraccion": "Surgencia Natural",
         "empresa": "YPF S.A.", "areayacimiento": "LOMA CAMPANA-LLL"},
    )
    assert out["prediccion_m3_mes"] >= 0


def test_predict_errors(state):
    with pytest.raises(WellNotFound):
        state.predict(None, 1, None, None, {})
    with pytest.raises(ValueError, match="faltan variables"):
        state.predict("v2_notef", None, 2026, 3, {"profundidad": 3000})
    with pytest.raises(ValueError, match="modelo desconocido"):
        state.predict("v9", None, 2026, 3, {})


def test_unknown_category_is_reported(state):
    out = state.predict("v2_notef", None, 2026, 3, {"profundidad": 3000, "edad_meses": 6,
                        "tipoextraccion": "Surgencia Natural", "empresa": "NUEVA SA", "areayacimiento": "LOMA CAMPANA-LLL"})
    assert out["categorias_no_vistas"] == ["empresa"]


def test_well_history_has_estimates(state, trained_store):
    hist = state.well_history(a_well(trained_store[1]))
    assert hist["meses"]
    assert {"estimado_v1", "estimado_v2", "estimado_v2_notef"} <= set(hist["meses"][0])


def test_reload_picks_new_version_and_keeps_old_on_error(state, trained_store):
    store = open_store(trained_store[0])
    old = state.handles["v2"].version
    assert train.main(["--model", "v2", "--dataset", str(trained_store[1].path), "--store", trained_store[0],
                       "--force-promote", "--max-depth", "4"]) == 0
    state.reload()
    assert state.handles["v2"].version != old
    # current.json apunta a un hash incorrecto -> no se carga y queda la versión anterior
    good = state.handles["v2"].version
    pointer = registry.current_pointer(store, "v2")
    registry.promote(store, "v2", pointer["version"] + "x", "0" * 64, 1.0, "test")
    state.reload()
    assert state.handles["v2"].version == good
    assert "v2" in state.errors


def test_not_ready_without_models(tmp_path):
    st = ServingState(str(tmp_path), ["v2_notef"], "v2_notef")
    st.reload()
    assert not st.ready()
    with pytest.raises(ModelNotLoaded):
        st.predict(None, None, 2026, 1, {})


# --- HTTP -------------------------------------------------------------------
@pytest.fixture()
def client(trained_store, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("STORE_URI", trained_store[0])
    monkeypatch.setenv("RELOAD_INTERVAL_S", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import api.main as main

    # Las métricas de Prometheus se registran una sola vez por proceso: en vez de
    # recargar el módulo se reemplaza el estado por uno que apunta al store de prueba.
    monkeypatch.setattr(main, "state", ServingState(trained_store[0], main.MODELS, "v2_notef", on_reload=main._on_reload))
    monkeypatch.setattr(main, "RELOAD_INTERVAL_S", 0)
    with TestClient(main.app) as c:
        yield c


def test_http_endpoints(client, trained_store):
    assert client.get("/livez").status_code == 200
    assert client.get("/healthz").status_code == 200
    idpozo = a_well(trained_store[1])
    r = client.post("/predict", json={"idpozo": idpozo})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "v2_notef"
    assert client.post("/predict", json={"idpozo": 1}).status_code == 404
    assert client.post("/predict", json={"model": "v2_notef", "anio": 2026, "mes": 1}).status_code == 422
    assert client.post("/predict", json={"idpozo": idpozo, "mes": 13}).status_code == 422
    metrics = client.get("/metrics").text
    assert "predict_requests_total" in metrics and 'model_info{model="v2"' in metrics
    assert set(client.get("/models").json()["models"]) == {"v1", "v2", "v2_notef"}
    assert client.get("/models/v2/versions").json()["versions"]
    assert client.get(f"/wells/{idpozo}").status_code == 200
    assert client.get("/wells", params={"q": "YPF"}).json()["resultados"]
    assert client.post("/admin/reload").status_code == 200
    assert client.get("/stats").status_code == 503  # sin DATABASE_URL
