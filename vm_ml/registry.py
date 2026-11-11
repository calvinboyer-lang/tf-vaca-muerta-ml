"""Registro de modelos: versiones inmutables + puntero a la versión vigente.

Estructura (local o en el bucket):

    datasets/<dataset_id>.csv.gz                  snapshot usado al entrenar
    datasets/<dataset_id>.manifest.json
    models/<modelo>/<version>/model.joblib        pipeline completo
    models/<modelo>/<version>/metadata.json       datos, parámetros, métricas, decisión
    models/<modelo>/current.json                  versión vigente + sha256

Seguridad del artefacto: joblib ejecuta código al cargar. Por eso la API solo
carga un archivo cuyo sha256 coincide con el registrado en current.json, y en
GCP solo la cuenta del entrenamiento puede escribir en el bucket (la de la
API tiene permiso de lectura).
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone

import joblib

from . import config

CURRENT = "current.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_version(model_name: str, store=None) -> str:
    """<modelo>-AAAAMMDDTHHMMSS (UTC). Las versiones son inmutables: si ya existe
    una con ese nombre (dos entrenamientos en el mismo segundo) se agrega un sufijo."""
    base = f"{model_name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    if store is None:
        return base
    version, n = base, 1
    while store.exists(_model_key(model_name, version, "metadata.json")):
        n += 1
        version = f"{base}-{n}"
    return version


def _model_key(model_name: str, version: str, filename: str) -> str:
    return f"models/{model_name}/{version}/{filename}"


def dump_pipeline(pipeline) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    joblib.dump(pipeline, buffer, compress=3)
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def save_version(store, model_name: str, version: str, pipeline, metadata: dict) -> str:
    data, digest = dump_pipeline(pipeline)
    metadata = {**metadata, "artifact": {"file": "model.joblib", "sha256": digest, "bytes": len(data)}}
    store.write_bytes(_model_key(model_name, version, "model.joblib"), data)
    store.write_bytes(
        _model_key(model_name, version, "metadata.json"),
        json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"),
    )
    return digest


def update_metadata(store, model_name: str, version: str, patch: dict) -> dict:
    key = _model_key(model_name, version, "metadata.json")
    metadata = json.loads(store.read_bytes(key))
    metadata.update(patch)
    store.write_bytes(key, json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"))
    return metadata


def read_metadata(store, model_name: str, version: str) -> dict:
    return json.loads(store.read_bytes(_model_key(model_name, version, "metadata.json")))


def current_pointer(store, model_name: str) -> dict | None:
    key = f"models/{model_name}/{CURRENT}"
    if not store.exists(key):
        return None
    return json.loads(store.read_bytes(key))


def load_pipeline(store, model_name: str, version: str, expected_sha256: str):
    data = store.read_bytes(_model_key(model_name, version, "model.joblib"))
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"sha256 de {model_name}/{version} no coincide ({actual[:12]} != {expected_sha256[:12]}): no se carga"
        )
    return joblib.load(io.BytesIO(data))


def load_current(store, model_name: str):
    """(pipeline, metadata) de la versión vigente, o (None, None) si no hay."""
    pointer = current_pointer(store, model_name)
    if not pointer:
        return None, None
    pipeline = load_pipeline(store, model_name, pointer["version"], pointer["sha256"])
    return pipeline, read_metadata(store, model_name, pointer["version"])


def promote(store, model_name: str, version: str, sha256: str, holdout_mae: float, reason: str) -> dict:
    pointer = {
        "model": model_name,
        "version": version,
        "sha256": sha256,
        "holdout_mae": holdout_mae,
        "promoted_utc": utc_now(),
        "reason": reason,
    }
    store.write_bytes(
        f"models/{model_name}/{CURRENT}", json.dumps(pointer, indent=2, ensure_ascii=False).encode("utf-8")
    )
    return pointer


def list_versions(store, model_name: str) -> list[dict]:
    out = []
    for key in store.list(f"models/{model_name}/"):
        if key.endswith("/metadata.json"):
            out.append(json.loads(store.read_bytes(key)))
    return sorted(out, key=lambda m: m.get("version", ""))


def decide_promotion(candidate_mae: float, current_mae: float | None, bootstrap: dict | None) -> tuple[bool, str]:
    """Regla de promoción documentada en config.py."""
    if current_mae is None:
        return True, "no hay versión vigente"
    rel = (current_mae - candidate_mae) / current_mae
    if rel < config.MIN_REL_IMPROVEMENT:
        return False, (
            f"mejora relativa {rel:.2%} < {config.MIN_REL_IMPROVEMENT:.0%} "
            f"(MAE {candidate_mae:.1f} vs vigente {current_mae:.1f})"
        )
    if bootstrap and bootstrap["ci_low"] <= 0:
        return False, (
            f"mejora {rel:.2%} no significativa: IC {bootstrap['confidence']:.0%} de la diferencia "
            f"[{bootstrap['ci_low']:.1f}, {bootstrap['ci_high']:.1f}] incluye 0"
        )
    return True, f"mejora {rel:.2%} con IC {bootstrap['confidence']:.0%} > 0" if bootstrap else f"mejora {rel:.2%}"
