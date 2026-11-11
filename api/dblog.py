"""Registro de predicciones en PostgreSQL SIN bloquear la respuesta.

Cada predicción se encola; un hilo las inserta por lotes. Si la base no
responde, se descartan (y se cuentan en db_write_errors_total): la predicción
nunca espera a la base de datos. La cola tiene tope para no crecer sin límite.
"""

from __future__ import annotations

import json
import queue
import threading
import time

from vm_ml import db


class PredictionLogger:
    def __init__(self, on_error=None, max_queue: int = 10_000, batch: int = 200, flush_s: float = 1.0):
        self.enabled = db.database_url() is not None
        self.queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self.batch = batch
        self.flush_s = flush_s
        self.on_error = on_error or (lambda n: None)
        self.dropped = 0
        self.written = 0
        self._stop = threading.Event()
        if self.enabled:
            threading.Thread(target=self._run, name="db-writer", daemon=True).start()

    def log(self, record: dict) -> None:
        if not self.enabled:
            return
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1
            self.on_error(1)

    def _run(self) -> None:
        conn = None
        while not self._stop.is_set():
            items = []
            deadline = time.time() + self.flush_s
            while len(items) < self.batch and time.time() < deadline:
                try:
                    items.append(self.queue.get(timeout=max(0.0, deadline - time.time())))
                except queue.Empty:
                    break
            if not items:
                continue
            try:
                if conn is None or conn.closed:
                    conn = db.connect()
                    db.ensure_schema(conn)
                with conn.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO predictions (ts_utc, model, version, idpozo, request, prediction,
                               out_of_range, latency_ms, status_code, pod)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        [
                            (
                                r["ts_utc"], r["model"], r["version"], r.get("idpozo"),
                                json.dumps(r["request"], ensure_ascii=False), r.get("prediction"),
                                r.get("out_of_range", False), r["latency_ms"], r["status_code"], r.get("pod"),
                            )
                            for r in items
                        ],
                    )
                self.written += len(items)
            except Exception as exc:
                print(f"[db] no se pudieron guardar {len(items)} predicciones: {exc}", flush=True)
                self.dropped += len(items)
                self.on_error(len(items))
                conn = None
                time.sleep(2)

    def stop(self) -> None:
        self._stop.set()


def recent_stats(minutes: int) -> dict | None:
    """Resumen de las predicciones registradas en los últimos `minutes` minutos."""
    conn = db.connect()
    if conn is None:
        return None
    with conn, conn.cursor() as cur:
        db.ensure_schema(conn)
        cur.execute(
            """SELECT model, version, count(*),
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms),
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms),
                      avg(prediction), sum(CASE WHEN out_of_range THEN 1 ELSE 0 END),
                      sum(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END)
               FROM predictions WHERE ts_utc > now() - make_interval(mins => %s)
               GROUP BY model, version ORDER BY model, version""",
            (minutes,),
        )
        rows = cur.fetchall()
        cur.execute(
            """SELECT model, version, started_utc, holdout_mae, holdout_r2, cv_mae_mean, cv_mae_std,
                      duration_fit_s, promoted, decision
               FROM training_runs ORDER BY started_utc DESC LIMIT 20"""
        )
        runs = cur.fetchall()
    return {
        "ventana_min": minutes,
        "predicciones": [
            {"model": m, "version": v, "n": n, "latencia_p50_ms": p50, "latencia_p95_ms": p95,
             "prediccion_media": avg, "fuera_de_rango": oor, "errores_5xx": e5}
            for m, v, n, p50, p95, avg, oor, e5 in rows
        ],
        "entrenamientos": [
            {"model": m, "version": v, "inicio": str(s), "holdout_mae": mae, "holdout_r2": r2,
             "cv_mae": cvm, "cv_mae_std": cvs, "fit_s": fit, "promovida": p, "decision": d}
            for m, v, s, mae, r2, cvm, cvs, fit, p, d in runs
        ],
    }
