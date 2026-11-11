"""Datos del TF: descubrir fuentes, descargar y armar el snapshot versionado.

Desde la raíz del repo (PowerShell o bash):

  1) Ver capas y tablas del servicio ArcGIS (para encontrar series y padrón):
       python scripts/data_vm.py discover

  2) Descargar Vaca Muerta shale de una o varias capas ArcGIS:
       python scripts/data_vm.py arcgis --layers 9
       python scripts/data_vm.py arcgis --layers 9 10 11

  3) (Alternativa) Filtrar un CSV grande de datos.gob.ar ya descargado:
       python scripts/data_vm.py csv --path C:\\Descargas\\produccion_no_convencional.csv

     Además descarga, para esos pozos, la capa 0 "Pozos" (padrón con FECHA_INIC)
     y la capa 10 "Trayectorias" (largo horizontal hz, md, tvd).

  4) Armar el snapshot (une las descargas, calcula la edad del pozo):
       python scripts/data_vm.py build
       python scripts/data_vm.py build --padron C:\\Descargas\\padron_pozos.csv

  Demo sin internet (datos SINTÉTICOS, solo para probar la cadena):
       python scripts/data_vm.py synthetic

Las descargas quedan en data/raw/ (ignorado por git) y el snapshot en
data/snapshots/ (se versiona en git junto con su manifest).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from vm_ml import config  # noqa: E402
from vm_ml.data import (  # noqa: E402
    POZOS_FIELDS,
    POZOS_LAYER,
    TRAYECTORIAS_FIELDS,
    TRAYECTORIAS_LAYER,
    add_well_age,
    arcgis_count,
    arcgis_describe,
    arcgis_fetch,
    arcgis_fetch_by_ids,
    clean_raw,
    filter_large_csv,
    load_padron,
    padron_from_pozos,
    trajectory_features,
    write_snapshot,
)

RAW = ROOT / "data" / "raw"
SNAPSHOTS = ROOT / "data" / "snapshots"


def cmd_discover(args) -> None:
    items = arcgis_describe(args.base)
    needed = {"idpozo", "anio", "mes", "prod_pet"}
    for item in items:
        fields = {f.lower() for f in item["fields"]}
        tag = ""
        if needed <= fields:
            tag = "  <- producción mensual por pozo"
        elif "idpozo" in fields and any("fecha" in f or "inicio" in f for f in fields):
            tag = "  <- posible padrón (tiene idpozo y fechas)"
        print(f"[{item['kind']} {item['id']:>3}] {item['name']}{tag}")
        if args.fields or tag:
            print("        campos:", ", ".join(item["fields"]))
        if needed <= fields:
            try:
                print(f"        filas Vaca Muerta shale: {arcgis_count(item['id'], config.SOURCE_WHERE, args.base):,}")
            except Exception as exc:  # algunas capas no tienen todos los campos del filtro
                print(f"        (no se pudo contar con el filtro: {exc})")
    (RAW / "arcgis_capas.json").parent.mkdir(parents=True, exist_ok=True)
    (RAW / "arcgis_capas.json").write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDetalle guardado en {RAW / 'arcgis_capas.json'}")


def cmd_arcgis(args) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    for layer in args.layers:
        print(f"Descargando capa {layer} ...")
        df = arcgis_fetch(layer, base=args.base)
        out = RAW / f"arcgis_capa{layer}_{stamp}.csv.gz"
        df.to_csv(out, index=False, compression="gzip")
        months = sorted({(int(a), int(m)) for a, m in zip(df["anio"], df["mes"])})
        print(f"  -> {out.name}: {len(df):,} filas, {df['idpozo'].nunique():,} pozos, "
              f"meses {months[0]}..{months[-1]} ({len(months)})")
    if args.no_extras:
        return
    ids = set()
    for f in RAW.glob("arcgis_capa*.csv.gz"):
        ids |= set(pd.read_csv(f, usecols=["idpozo"])["idpozo"].dropna().astype(int))
    print(f"Padrón (capa {POZOS_LAYER} Pozos) para {len(ids):,} pozos ...")
    pozos = arcgis_fetch_by_ids(POZOS_LAYER, "IDENTIFICA", sorted(ids), POZOS_FIELDS, base=args.base)
    pozos.to_csv(RAW / f"arcgis_pozos_{stamp}.csv.gz", index=False, compression="gzip")
    print(f"  -> arcgis_pozos_{stamp}.csv.gz: {len(pozos):,} filas")
    print(f"Trayectorias (capa {TRAYECTORIAS_LAYER}) para {len(ids):,} pozos ...")
    tray = arcgis_fetch_by_ids(TRAYECTORIAS_LAYER, "idpo", sorted(ids), TRAYECTORIAS_FIELDS, base=args.base)
    tray.to_csv(RAW / f"arcgis_trayectorias_{stamp}.csv.gz", index=False, compression="gzip")
    print(f"  -> arcgis_trayectorias_{stamp}.csv.gz: {len(tray):,} filas")


def cmd_csv(args) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    df = filter_large_csv(args.path)
    out = RAW / f"csv_{Path(args.path).stem}.csv.gz"
    df.to_csv(out, index=False, compression="gzip")
    print(f"  -> {out.name}: {len(df):,} filas Vaca Muerta shale")


def cmd_build(args) -> None:
    if args.inputs:
        inputs = [Path(p) for p in args.inputs]
    else:
        inputs = sorted(RAW.glob("arcgis_capa*.csv.gz")) + sorted(RAW.glob("csv_*.csv.gz"))
    if not inputs:
        sys.exit("No hay descargas en data/raw/. Corré primero 'arcgis' o 'csv'.")
    print("Producción:", ", ".join(p.name for p in inputs))
    frames = [pd.read_csv(p, low_memory=False) for p in inputs]
    df = clean_raw(pd.concat(frames, ignore_index=True))

    # Edad: padrón CSV indicado, o la capa Pozos descargada (FECHA_INIC).
    padron, padron_source = None, None
    if args.padron:
        padron = load_padron(args.padron, args.padron_id_col, args.padron_date_col)
        padron_source = Path(args.padron).name
    else:
        pozos_files = sorted(RAW.glob("arcgis_pozos_*.csv.gz"))
        if pozos_files and not args.sin_padron:
            padron = padron_from_pozos(pd.read_csv(pozos_files[-1]))
            padron_source = f"{pozos_files[-1].name} (FECHA_INIC)"
    print("Edad del pozo:", padron_source or "primer mes con producción en la historia descargada")

    tray_files = sorted(RAW.glob("arcgis_trayectorias_*.csv.gz"))
    if tray_files:
        df = df.merge(trajectory_features(pd.read_csv(tray_files[-1])), on="idpozo", how="left")
        print("Trayectorias:", tray_files[-1].name)
    # La edad se calcula con TODA la historia descargada; recién después se
    # recortan los meses (--desde), así la edad no queda censurada al recortar.
    df = add_well_age(df, padron)
    if args.desde:
        anio, mes = (int(x) for x in args.desde.split("-"))
        df = df[df["mes_idx"] >= anio * 12 + mes - 1].reset_index(drop=True)
    keep = [c for c in config.SNAPSHOT_COLUMNS if c in df.columns]
    keep += ["mes_idx", "primera_obs_idx", "primera_prod_idx", "edad_meses", "edad_censurada", "fuente_edad"]
    keep += [c for c in ["largo_horizontal_m", "md_m", "tvd_m"] if c in df.columns]
    sources = [p.name for p in inputs] + [p.name for p in tray_files[-1:]]
    info = write_snapshot(df[keep], SNAPSHOTS, sources, padron_source)
    print(json.dumps(info.manifest, indent=2, ensure_ascii=False))
    print(f"\nSnapshot: {info.path}")


def cmd_synthetic(args) -> None:
    from vm_ml.synthetic import make_snapshot_frame

    out_dir = ROOT / "data" / "synthetic"
    df = make_snapshot_frame(n_wells=args.wells, seed=args.seed)
    df = add_well_age(clean_raw(df))
    info = write_snapshot(df, out_dir, ["SINTETICO vm_ml.synthetic"])
    print(f"Snapshot SINTÉTICO (no usar para el informe): {info.path}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default=config.ARCGIS_BASE, help="URL del FeatureServer")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="listar capas/tablas del servicio ArcGIS")
    d.add_argument("--fields", action="store_true", help="mostrar los campos de todas las capas")
    d.set_defaults(func=cmd_discover)

    a = sub.add_parser("arcgis", help="descargar Vaca Muerta shale de capas ArcGIS")
    a.add_argument("--layers", type=int, nargs="+", default=[config.DEFAULT_LAYER])
    a.add_argument("--no-extras", action="store_true", help="no descargar padrón (capa 0) ni trayectorias (capa 10)")
    a.set_defaults(func=cmd_arcgis)

    c = sub.add_parser("csv", help="filtrar un CSV de datos.gob.ar")
    c.add_argument("--path", required=True)
    c.set_defaults(func=cmd_csv)

    b = sub.add_parser("build", help="armar el snapshot versionado")
    b.add_argument("--inputs", nargs="*", help="archivos de data/raw (default: todos)")
    b.add_argument("--padron", help="CSV del padrón de pozos con fecha de primera producción")
    b.add_argument("--padron-id-col")
    b.add_argument("--padron-date-col")
    b.add_argument("--sin-padron", action="store_true", help="ignorar la capa Pozos y usar solo la historia")
    b.add_argument("--desde", help="descartar meses anteriores, formato AAAA-MM (la edad usa toda la historia)")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("synthetic", help="snapshot SINTÉTICO para probar sin internet")
    s.add_argument("--wells", type=int, default=300)
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(func=cmd_synthetic)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
