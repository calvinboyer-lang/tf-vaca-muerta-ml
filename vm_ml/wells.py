"""Índice de pozos: atributos y fecha de primera producción por idpozo.

Lo usa la API para que el usuario mande solo el idpozo (y opcionalmente el
mes a estimar): la edad, la operadora, el área, la profundidad y el sistema de
extracción se toman del dataset con el que se entrenó el modelo.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data import month_label


@dataclass
class Well:
    idpozo: int
    empresa: str | None
    areayacimiento: str | None
    profundidad: float | None
    tipoextraccion: str | None
    tef_ultimo: float | None
    primera_prod_idx: int | None
    ultimo_mes_idx: int
    edad_censurada: bool
    history: pd.DataFrame = field(repr=False)

    def summary(self) -> dict:
        return {
            "idpozo": self.idpozo,
            "empresa": self.empresa,
            "areayacimiento": self.areayacimiento,
            "profundidad": self.profundidad,
            "tipoextraccion": self.tipoextraccion,
            "primera_produccion": month_label(self.primera_prod_idx) if self.primera_prod_idx is not None else None,
            "ultimo_mes": month_label(self.ultimo_mes_idx),
            "edad_censurada": self.edad_censurada,
            "meses_con_datos": int(len(self.history)),
        }


def _clean(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return value


class WellIndex:
    def __init__(self, df: pd.DataFrame, dataset_id: str):
        self.dataset_id = dataset_id
        df = df.sort_values(["idpozo", "mes_idx"])
        self._wells: dict[int, Well] = {}
        cols = ["mes_idx", "anio", "mes", "prod_pet", "prod_gas", "tef", "tipoextraccion", "tipoestado", "tipopozo"]
        for idpozo, g in df.groupby("idpozo", sort=False):
            last = g.iloc[-1]
            first = g["primera_prod_idx"].dropna()
            self._wells[int(idpozo)] = Well(
                idpozo=int(idpozo),
                empresa=_clean(last.get("empresa")),
                areayacimiento=_clean(last.get("areayacimiento")),
                profundidad=_clean(float(last["profundidad"])) if pd.notna(last.get("profundidad")) else None,
                tipoextraccion=_clean(last.get("tipoextraccion")),
                tef_ultimo=_clean(float(last["tef"])) if pd.notna(last.get("tef")) else None,
                primera_prod_idx=int(first.iloc[0]) if len(first) else None,
                ultimo_mes_idx=int(last["mes_idx"]),
                edad_censurada=bool(g["edad_censurada"].iloc[-1]) if "edad_censurada" in g else False,
                history=g[[c for c in cols if c in g.columns]].reset_index(drop=True),
            )

    def __len__(self) -> int:
        return len(self._wells)

    def get(self, idpozo: int) -> Well | None:
        return self._wells.get(int(idpozo))

    def search(self, text: str, limit: int = 20) -> list[dict]:
        text = text.strip().lower()
        out = []
        for w in self._wells.values():
            hay = f"{w.idpozo} {w.empresa or ''} {w.areayacimiento or ''}".lower()
            if text in hay:
                out.append(w.summary())
                if len(out) >= limit:
                    break
        return out


def build_request_row(well: Well | None, period_idx: int | None, overrides: dict) -> tuple[dict, list[str]]:
    """Arma las variables de entrada combinando el pozo y lo que mandó el usuario.

    Devuelve (fila, fuentes), donde fuentes indica de dónde salió cada valor.
    El período por defecto es el mes siguiente al último dato del pozo.
    """
    row: dict = {}
    sources: list[str] = []
    if well is not None:
        if period_idx is None:
            period_idx = well.ultimo_mes_idx + 1
        row.update(
            {
                "empresa": well.empresa,
                "areayacimiento": well.areayacimiento,
                "profundidad": well.profundidad,
                "tipoextraccion": well.tipoextraccion,
            }
        )
        if well.primera_prod_idx is not None:
            row["edad_meses"] = max(0, period_idx - well.primera_prod_idx)
        sources.append(f"pozo {well.idpozo} (dataset)")
    if period_idx is not None:
        row["mes"] = period_idx % 12 + 1
        row["periodo"] = month_label(period_idx)
        if overrides.get("tef") is None:
            # Supuesto explícito: si no se informa el TEF, el pozo produce el mes completo.
            row["tef"] = calendar.monthrange(period_idx // 12, period_idx % 12 + 1)[1]
            sources.append("tef supuesto = días del mes")
    explicit = {k: v for k, v in overrides.items() if v is not None}
    if explicit:
        row.update(explicit)
        sources.append("valores enviados: " + ", ".join(sorted(explicit)))
    return row, sources
