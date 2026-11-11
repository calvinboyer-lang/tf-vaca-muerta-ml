"""Persistencia en PostgreSQL (opcional: solo si existe DATABASE_URL).

Tablas:
  training_runs   un registro por entrenamiento (métricas, duración, decisión)
  predictions     un registro por petición a /predict (entrada, salida, latencia)
  wells           atributos por pozo para que la API los busque por idpozo

Si DATABASE_URL no está definida o la base no responde, las funciones no hacen
nada (o devuelven None) y lo informan en el log: la predicción no debe caerse
porque la base esté momentáneamente fuera de servicio.
"""

from __future__ import annotations

import json
import os

SCHEMA = """
CREATE TABLE IF NOT EXISTS training_runs (
    id              BIGSERIAL PRIMARY KEY,
    started_utc     TIMESTAMPTZ NOT NULL,
    model           TEXT NOT NULL,
    version         TEXT NOT NULL UNIQUE,
    dataset_id      TEXT NOT NULL,
    dataset_sha256  TEXT NOT NULL,
    rows_train      INTEGER,
    rows_holdout    INTEGER,
    params          JSONB,
    holdout_mae     DOUBLE PRECISION,
    holdout_rmse    DOUBLE PRECISION,
    holdout_r2      DOUBLE PRECISION,
    cv_mae_mean     DOUBLE PRECISION,
    cv_mae_std      DOUBLE PRECISION,
    duration_fit_s  DOUBLE PRECISION,
    duration_total_s DOUBLE PRECISION,
    promoted        BOOLEAN NOT NULL,
    decision        TEXT,
    metadata        JSONB
);

CREATE TABLE IF NOT EXISTS predictions (
    id              BIGSERIAL PRIMARY KEY,
    ts_utc          TIMESTAMPTZ NOT NULL DEFAULT now(),
    model           TEXT NOT NULL,
    version         TEXT NOT NULL,
    idpozo          BIGINT,
    request         JSONB NOT NULL,
    prediction      DOUBLE PRECISION,
    out_of_range    BOOLEAN NOT NULL DEFAULT false,
    latency_ms      DOUBLE PRECISION,
    status_code     INTEGER NOT NULL,
    pod             TEXT
);
CREATE INDEX IF NOT EXISTS predictions_ts_idx ON predictions (ts_utc);

CREATE TABLE IF NOT EXISTS wells (
    idpozo            BIGINT PRIMARY KEY,
    empresa           TEXT,
    areayacimiento    TEXT,
    profundidad       DOUBLE PRECISION,
    tipoextraccion    TEXT,
    primera_prod_idx  INTEGER,
    ultimo_mes_idx    INTEGER,
    dataset_id        TEXT NOT NULL
);
"""


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or None


def connect():
    url = database_url()
    if not url:
        return None
    import psycopg  # import diferido

    return psycopg.connect(url, connect_timeout=5, autocommit=True)


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA)


def record_training_run(metadata: dict) -> bool:
    try:
        conn = connect()
    except Exception as exc:  # la base es auxiliar: no frena el entrenamiento
        print(f"[db] sin conexión ({exc}); el run queda solo en metadata.json", flush=True)
        return False
    if conn is None:
        return False
    holdout = metadata["metrics"]["holdout"]
    cv = metadata["validation"]["repeated"]["tree"]["summary"]["mae"]
    with conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO training_runs (started_utc, model, version, dataset_id, dataset_sha256,
                    rows_train, rows_holdout, params, holdout_mae, holdout_rmse, holdout_r2,
                    cv_mae_mean, cv_mae_std, duration_fit_s, duration_total_s, promoted, decision, metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (version) DO NOTHING
                """,
                (
                    metadata["started_utc"],
                    metadata["model"],
                    metadata["version"],
                    metadata["dataset"]["dataset_id"],
                    metadata["dataset"]["sha256"],
                    metadata["rows"]["train"],
                    metadata["rows"]["holdout"],
                    json.dumps(metadata["params"]),
                    holdout["mae"],
                    holdout["rmse"],
                    holdout["r2"],
                    cv["mean"],
                    cv["std"],
                    metadata["durations_s"]["fit"],
                    metadata["durations_s"]["total"],
                    metadata["promotion"]["promoted"],
                    metadata["promotion"]["reason"],
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
    return True
