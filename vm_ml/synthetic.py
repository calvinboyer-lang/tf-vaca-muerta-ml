"""Datos SINTÉTICOS con la misma forma que el snapshot real.

Solo para tests y para probar la cadena (entrenamiento, API, k3d) sin
descargar nada. Nunca se usan para reportar métricas del trabajo.
Cada pozo sigue una curva de declinación hiperbólica simple.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EMPRESAS = ["YPF S.A.", "VISTA ENERGY ARGENTINA SAU", "PAN AMERICAN ENERGY SL", "SHELL ARGENTINA S.A."]
AREAS = ["LOMA CAMPANA-LLL", "LA AMARGA CHICA", "BAJO DEL TORO NORTE", "BANDURRIA SUR", "CRUZ DE LORENA"]
EXTRACCION = ["Surgencia Natural", "Bombeo Mecánico", "Gas Lift", "Plunger Lift"]


def make_snapshot_frame(n_wells: int = 300, first: tuple[int, int] = (2024, 1), months: int = 18, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = first[0] * 12 + first[1] - 1
    rows = []
    for i in range(n_wells):
        idpozo = 150_000 + i
        empresa = EMPRESAS[i % len(EMPRESAS)]
        area = AREAS[rng.integers(len(AREAS))]
        profundidad = float(rng.normal(4500, 900))
        hz = float(rng.uniform(1000, 3500)) if rng.random() > 0.1 else np.nan
        qi = rng.lognormal(np.log(3500), 0.5) * (1 + (profundidad - 4500) / 9000) * (0.6 + (0 if np.isnan(hz) else hz / 5000))
        d, b = rng.uniform(0.08, 0.2), 0.8
        # algunos pozos empezaron antes de la ventana (edad censurada)
        first_prod = start + int(rng.integers(-24, months - 2))
        tipopozo = "Petrolífero" if rng.random() > 0.15 else "Gasífero"
        for m in range(start, start + months):
            age = m - first_prod
            if age < 0:
                continue
            tef = int(rng.choice([31, 30, 28, 25, 15], p=[0.55, 0.2, 0.1, 0.1, 0.05]))
            q = qi / (1 + b * d * age) ** (1 / b) * tef / 30
            q *= rng.lognormal(0, 0.15)
            extraccion = EXTRACCION[0] if age < 12 else EXTRACCION[1 + i % 3]
            rows.append(
                {
                    "anio": m // 12,
                    "mes": m % 12 + 1,
                    "idpozo": idpozo,
                    "prod_pet": round(q, 1) if tipopozo == "Petrolífero" else round(q * 0.05, 1),
                    "prod_gas": round(q * 0.4, 1),
                    "prod_agua": round(q * 0.1, 1),
                    "tef": tef,
                    "tipoextraccion": extraccion,
                    "tipoestado": "Extracción Efectiva" if rng.random() > 0.05 else "Parado Transitoriamente",
                    "tipopozo": tipopozo,
                    "empresa": empresa,
                    "profundidad": round(profundidad, 0),
                    "formacion": "vaca muerta",
                    "areayacimiento": area,
                    "cuenca": "NEUQUINA",
                    "provincia": "Neuquén",
                    "tipo_de_recurso": "NO CONVENCIONAL",
                    "sub_tipo_recurso": "SHALE",
                    "largo_horizontal_m": hz,
                }
            )
    return pd.DataFrame(rows)
