"""Obtención de datos y armado del snapshot.

Fuentes soportadas:
  1. Servicio ArcGIS REST de la Secretaría de Energía (una o varias capas).
  2. CSV de datos.gob.ar "Producción de petróleo y gas por pozo (Capítulo IV)"
     (por ejemplo, la serie de no convencionales), filtrado por bloques.
  3. Opcional: padrón de pozos con fecha de primera producción.

El snapshot es un CSV comprimido (data/snapshots/<id>.csv.gz) acompañado de un
manifest JSON con el sha256, la fuente y el rango de meses. El entrenamiento
referencia el snapshot por su id y su hash: así cada versión del modelo es
reproducible.
"""

from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import config


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def norm_text(value: object) -> str:
    """Minúsculas, sin acentos y sin espacios extremos ("Petrolífero" -> "petrolifero")."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def month_index(anio: pd.Series, mes: pd.Series) -> pd.Series:
    """Índice mensual entero (anio*12 + mes - 1) para restar fechas en meses."""
    return anio.astype(int) * 12 + mes.astype(int) - 1


def month_label(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


# ----------------------------------------------------------------------------
# ArcGIS REST
# ----------------------------------------------------------------------------
def _session():
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = "tf-vaca-muerta-ml/0.1 (trabajo academico)"
    return session


def arcgis_describe(base: str = config.ARCGIS_BASE) -> list[dict]:
    """Lista capas y tablas del FeatureServer con sus campos."""
    session = _session()
    root = session.get(base, params={"f": "json"}, timeout=60)
    root.raise_for_status()
    info = root.json()
    items = []
    for kind in ("layers", "tables"):
        for item in info.get(kind, []):
            detail = session.get(f"{base}/{item['id']}", params={"f": "json"}, timeout=60)
            detail.raise_for_status()
            fields = [f["name"] for f in detail.json().get("fields", [])]
            items.append({"id": item["id"], "name": item["name"], "kind": kind[:-1], "fields": fields})
    return items


def arcgis_count(layer: int, where: str, base: str = config.ARCGIS_BASE) -> int:
    session = _session()
    resp = session.get(
        f"{base}/{layer}/query",
        params={"where": where, "returnCountOnly": "true", "f": "json"},
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return int(payload["count"])


def arcgis_fetch(
    layer: int,
    where: str = config.SOURCE_WHERE,
    fields: list[str] | None = None,
    base: str = config.ARCGIS_BASE,
    page_size: int = config.ARCGIS_PAGE_SIZE,
    retries: int = 3,
) -> pd.DataFrame:
    """Descarga paginada (el servicio devuelve como máximo `page_size` filas)."""
    fields = fields or config.SNAPSHOT_COLUMNS
    out_fields = ",".join(["OBJECTID", *fields])
    session = _session()
    rows: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "false",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "orderByFields": "OBJECTID",
            "f": "json",
        }
        for attempt in range(1, retries + 1):
            try:
                resp = session.get(f"{base}/{layer}/query", params=params, timeout=120)
                resp.raise_for_status()
                payload = resp.json()
                if "error" in payload:
                    raise RuntimeError(payload["error"])
                break
            except Exception:
                if attempt == retries:
                    raise
                time.sleep(2 * attempt)
        features = payload.get("features", [])
        if not features:
            break
        rows.extend(f["attributes"] for f in features)
        print(f"  capa {layer}: {len(rows):,} filas", flush=True)
        if len(features) < page_size and not payload.get("exceededTransferLimit"):
            break
        offset += len(features)
    df = pd.DataFrame(rows)
    return df.drop(columns=["OBJECTID"], errors="ignore")


def arcgis_fetch_by_ids(
    layer: int,
    id_field: str,
    ids: list[int],
    fields: list[str] | str = "*",
    base: str = config.ARCGIS_BASE,
    chunk: int = 150,
) -> pd.DataFrame:
    """Descarga de una capa solo las filas de los pozos indicados (consultas IN por bloques)."""
    out_fields = fields if isinstance(fields, str) else ",".join(fields)
    session = _session()
    frames = []
    ids = sorted({int(i) for i in ids})
    for start in range(0, len(ids), chunk):
        block = ids[start : start + chunk]
        params = {
            "where": f"{id_field} IN ({','.join(map(str, block))})",
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json",
        }
        # POST: la lista de ids no entra cómoda en una URL
        resp = session.post(f"{base}/{layer}/query", data=params, timeout=120)
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(payload["error"])
        frames.append(pd.DataFrame([f["attributes"] for f in payload.get("features", [])]))
        print(f"  capa {layer}: {min(start + chunk, len(ids)):,}/{len(ids):,} pozos consultados", flush=True)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# Capa 0 "Pozos" (padrón): IDENTIFICA = idpozo, FECHA_INIC = fecha de inicio.
POZOS_LAYER = 0
POZOS_FIELDS = ["IDENTIFICA", "SIGLA", "FECHA_INIC", "FECHA_FIN_", "ASFECHA_IN", "TIPO_ESTAD", "PROFUNDIDA", "TIPO_POZO"]
# Capa 10 "Trayectorias de pozos (Vaca Muerta)": idpo, md, tvd, hz (largo horizontal).
TRAYECTORIAS_LAYER = 10
TRAYECTORIAS_FIELDS = ["idpo", "md", "tvd", "hz", "pnl_md"]


def _epoch_to_month_idx(series: pd.Series) -> pd.Series:
    """ArcGIS entrega las fechas como milisegundos desde 1970 (UTC)."""
    values = pd.to_numeric(series, errors="coerce")
    dates = pd.to_datetime(values, unit="ms", errors="coerce", utc=True)
    if dates.isna().all():  # por si vienen como texto
        dates = pd.to_datetime(series, errors="coerce", dayfirst=True, utc=True)
    return dates.dt.year * 12 + dates.dt.month - 1


def padron_from_pozos(pozos: pd.DataFrame) -> pd.DataFrame:
    """Capa Pozos -> idpozo, primera_prod_idx (mes de FECHA_INIC)."""
    pozos = pozos.copy()
    pozos.columns = [c.upper() for c in pozos.columns]
    out = pd.DataFrame(
        {
            "idpozo": pd.to_numeric(pozos["IDENTIFICA"], errors="coerce"),
            "primera_prod_idx": _epoch_to_month_idx(pozos["FECHA_INIC"]),
        }
    ).dropna()
    out = out.astype({"idpozo": "int64", "primera_prod_idx": "int64"})
    return out.groupby("idpozo", as_index=False)["primera_prod_idx"].min()


def trajectory_features(tray: pd.DataFrame) -> pd.DataFrame:
    """Capa Trayectorias -> una fila por pozo (si hay varias trayectorias, la de mayor largo horizontal)."""
    tray = tray.copy()
    tray.columns = [c.lower() for c in tray.columns]
    for col in ["md", "tvd", "hz"]:
        tray[col] = pd.to_numeric(tray[col], errors="coerce")
    tray["idpozo"] = pd.to_numeric(tray["idpo"], errors="coerce")
    tray = tray.dropna(subset=["idpozo"]).sort_values("hz", ascending=False)
    tray = tray.drop_duplicates("idpozo").astype({"idpozo": "int64"})
    return tray.rename(columns={"hz": "largo_horizontal_m", "md": "md_m", "tvd": "tvd_m"})[
        ["idpozo", "largo_horizontal_m", "md_m", "tvd_m"]
    ]


# ----------------------------------------------------------------------------
# CSV de datos.gob.ar (puede pesar cientos de MB): se filtra por bloques
# ----------------------------------------------------------------------------
def filter_large_csv(path: str | Path, chunksize: int = 200_000) -> pd.DataFrame:
    keep = []
    for chunk in pd.read_csv(path, chunksize=chunksize, low_memory=False, encoding_errors="replace"):
        chunk.columns = [c.strip().lower() for c in chunk.columns]
        mask = (
            chunk["formacion"].map(norm_text).eq("vaca muerta")
            & chunk["tipo_de_recurso"].map(norm_text).eq("no convencional")
            & chunk["sub_tipo_recurso"].map(norm_text).eq("shale")
        )
        cols = [c for c in config.SNAPSHOT_COLUMNS if c in chunk.columns]
        keep.append(chunk.loc[mask, cols])
        print(f"  {path}: {sum(len(k) for k in keep):,} filas Vaca Muerta shale", flush=True)
    return pd.concat(keep, ignore_index=True) if keep else pd.DataFrame(columns=config.SNAPSHOT_COLUMNS)


# ----------------------------------------------------------------------------
# Padrón de pozos (fecha de primera producción)
# ----------------------------------------------------------------------------
def load_padron(path: str | Path, id_col: str | None = None, date_col: str | None = None) -> pd.DataFrame:
    """Devuelve columnas idpozo y primera_prod_idx (índice mensual).

    Si no se indican las columnas, se detectan: el id es la columna que contiene
    "idpozo" y la fecha la primera que contiene "primera" o "inicio".
    """
    padron = pd.read_csv(path, low_memory=False, encoding_errors="replace")
    padron.columns = [c.strip().lower() for c in padron.columns]
    id_col = id_col or next((c for c in padron.columns if "idpozo" in c), None)
    date_col = date_col or next(
        (c for c in padron.columns if "primera" in c or ("inicio" in c and "fecha" in c)), None
    )
    if not id_col or not date_col:
        raise ValueError(
            f"No pude detectar las columnas del padrón. Columnas: {list(padron.columns)}. "
            "Indicalas con --padron-id-col y --padron-date-col."
        )
    dates = pd.to_datetime(padron[date_col], errors="coerce", dayfirst=True)
    out = pd.DataFrame(
        {
            "idpozo": pd.to_numeric(padron[id_col], errors="coerce"),
            "primera_prod_idx": dates.dt.year * 12 + dates.dt.month - 1,
        }
    ).dropna()
    out = out.astype({"idpozo": "int64", "primera_prod_idx": "int64"})
    return out.groupby("idpozo", as_index=False)["primera_prod_idx"].min()


# ----------------------------------------------------------------------------
# Snapshot
# ----------------------------------------------------------------------------
@dataclass
class SnapshotInfo:
    dataset_id: str
    path: Path
    manifest_path: Path
    manifest: dict


def clean_raw(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    for col in ["anio", "mes", "idpozo", "prod_pet", "prod_gas", "prod_agua", "tef", "profundidad"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["anio", "mes", "idpozo"])
    df = df.astype({"anio": "int64", "mes": "int64", "idpozo": "int64"})
    # Una fila por pozo y mes. Si una fuente se repite (capas superpuestas o
    # declaraciones rectificadas) queda la última leída.
    df = df.drop_duplicates(subset=["idpozo", "anio", "mes"], keep="last")
    return df.sort_values(["idpozo", "anio", "mes"]).reset_index(drop=True)


def add_well_age(df: pd.DataFrame, padron: pd.DataFrame | None = None) -> pd.DataFrame:
    """Agrega fecha de primera producción y edad del pozo en meses.

    Prioridad: 1) padrón oficial, 2) primer mes con producción (petróleo o gas)
    observado en el snapshot. En el caso 2, si ese primer mes coincide con el
    primer mes del snapshot, la edad está "censurada" (el pozo pudo empezar
    antes) y se marca en `edad_censurada`.
    """
    df = df.copy()
    df["mes_idx"] = month_index(df["anio"], df["mes"])
    first_window = int(df["mes_idx"].min())

    produced = df[(df["prod_pet"].fillna(0) > 0) | (df["prod_gas"].fillna(0) > 0)]
    observed = produced.groupby("idpozo")["mes_idx"].min().rename("primera_obs_idx")
    df = df.merge(observed, on="idpozo", how="left")

    if padron is not None and not padron.empty:
        df = df.merge(padron, on="idpozo", how="left")
        df["fuente_edad"] = np.where(df["primera_prod_idx"].notna(), "padron", "historia")
        df["primera_prod_idx"] = df["primera_prod_idx"].fillna(df["primera_obs_idx"])
    else:
        df["primera_prod_idx"] = df["primera_obs_idx"]
        df["fuente_edad"] = "historia"

    df["edad_meses"] = (df["mes_idx"] - df["primera_prod_idx"]).clip(lower=0)
    df["edad_censurada"] = (df["fuente_edad"] == "historia") & (df["primera_obs_idx"] == first_window)
    # primera_obs_idx se conserva para validar la fecha del padrón contra la historia.
    return df


def write_snapshot(
    df: pd.DataFrame,
    out_dir: str | Path,
    sources: list[str],
    padron_source: str | None = None,
) -> SnapshotInfo:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    first, last = int(df["mes_idx"].min()), int(df["mes_idx"].max())
    dataset_id = f"vm_shale_{month_label(first).replace('-', '')}_{month_label(last).replace('-', '')}"
    path = out_dir / f"{dataset_id}.csv.gz"
    # mtime=0 en gzip: el mismo contenido produce el mismo archivo (y el mismo hash).
    df.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})

    pop = population(df)
    manifest = {
        "dataset_id": dataset_id,
        "file": path.name,
        "sha256": sha256_file(path),
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
        "padron": padron_source,
        "source_filter": config.SOURCE_WHERE,
        "months": {"first": month_label(first), "last": month_label(last), "count": last - first + 1},
        "rows_shale": int(len(df)),
        "wells_shale": int(df["idpozo"].nunique()),
        "population": config.POPULATION,
        "rows_population": int(len(pop)),
        "wells_population": int(pop["idpozo"].nunique()),
        "edad": {
            "fuente_padron_pct": round(100 * float((pop["fuente_edad"] == "padron").mean()), 2),
            "censurada_pct": round(100 * float(pop["edad_censurada"].mean()), 2),
            "sin_dato_pct": round(100 * float(pop["edad_meses"].isna().mean()), 2),
            "padron_vs_historia": _padron_check(pop, first),
        },
        "largo_horizontal_cobertura_pct": (
            round(100 * float(pop["largo_horizontal_m"].notna().mean()), 2) if "largo_horizontal_m" in pop else 0.0
        ),
    }
    manifest_path = out_dir / f"{dataset_id}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return SnapshotInfo(dataset_id, path, manifest_path, manifest)


def _padron_check(pop: pd.DataFrame, first_window: int) -> dict | None:
    """Pozos que empezaron DENTRO de la ventana: meses entre la fecha del padrón y la
    primera producción observada (valida qué representa la fecha del padrón)."""
    if "primera_obs_idx" not in pop or not (pop["fuente_edad"] == "padron").any():
        return None
    wells = pop[(pop["fuente_edad"] == "padron") & (pop["primera_obs_idx"] > first_window)]
    wells = wells.drop_duplicates("idpozo")
    if wells.empty:
        return None
    diff = wells["primera_obs_idx"] - wells["primera_prod_idx"]
    return {
        "pozos": int(len(wells)),
        "mediana_meses": float(diff.median()),
        "p10_meses": float(diff.quantile(0.1)),
        "p90_meses": float(diff.quantile(0.9)),
    }


def load_snapshot(path: str | Path) -> tuple[pd.DataFrame, dict]:
    """Lee el snapshot y verifica su hash contra el manifest (si existe)."""
    path = Path(path)
    manifest_path = path.with_name(path.name.replace(".csv.gz", ".manifest.json"))
    manifest: dict = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual = sha256_file(path)
        if manifest.get("sha256") and manifest["sha256"] != actual:
            raise ValueError(f"sha256 de {path.name} no coincide con su manifest: el snapshot fue modificado")
    else:
        manifest = {"dataset_id": path.name.replace(".csv.gz", ""), "sha256": sha256_file(path)}
    df = pd.read_csv(path, low_memory=False)
    return df, manifest


def population(df: pd.DataFrame) -> pd.DataFrame:
    """Pozos petrolíferos en extracción efectiva (población del TF)."""
    mask = pd.Series(True, index=df.index)
    for col, value in config.POPULATION.items():
        mask &= df[col].map(norm_text).eq(norm_text(value))
    return df[mask].copy()
