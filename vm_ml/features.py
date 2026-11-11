"""Tabla de modelado y pipelines de scikit-learn.

El pipeline completo (imputación + escalado + one-hot + modelo) se guarda en
un único artefacto: la API recibe columnas "crudas" y aplica exactamente el
mismo preprocesamiento que se usó al entrenar.
"""

from __future__ import annotations

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeRegressor

from . import config
from .data import population


def feature_columns(model_name: str) -> tuple[list[str], list[str]]:
    spec = config.FEATURE_SETS[model_name]
    return list(spec["numeric"]), list(spec["categorical"])


def prepare_features(df: pd.DataFrame, model_name: str) -> pd.DataFrame:
    """Selecciona y tipa las columnas de entrada de un modelo.

    Las categóricas se pasan a texto ("mes" incluido: es una categoría, no una
    magnitud). Sirve tanto para entrenar como para la API.
    """
    numeric, categorical = feature_columns(model_name)
    missing = [c for c in numeric + categorical if c not in df.columns]
    if missing:
        raise KeyError(f"faltan columnas para {model_name}: {missing}")
    X = pd.DataFrame(index=df.index)
    for col in numeric:
        X[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    for col in categorical:
        X[col] = df[col].map(lambda v: "desconocido" if pd.isna(v) else str(v).strip())
    return X


def model_frame(df: pd.DataFrame, model_name: str) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    """Población del TF -> (X, y, grupos por pozo, índice mensual)."""
    pop = population(df)
    pop = pop[pop[config.TARGET].notna()]
    X = prepare_features(pop, model_name)
    y = pop[config.TARGET].astype(float)
    groups = pop["idpozo"].astype("int64")
    months = pop["mes_idx"].astype("int64")
    return X, y, groups, months


def preprocessor(model_name: str) -> ColumnTransformer:
    numeric, categorical = feature_columns(model_name)
    return ColumnTransformer(
        transformers=[
            (
                "numericas",
                Pipeline([("imputar", SimpleImputer(strategy="median")), ("escalar", StandardScaler())]),
                numeric,
            ),
            (
                "categoricas",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                categorical,
            ),
        ]
    )


def make_pipeline(model_name: str, kind: str = "tree", params: dict | None = None) -> Pipeline:
    """kind: tree (modelo principal), linear o dummy (referencias)."""
    params = params or {}
    if kind == "tree":
        estimator = DecisionTreeRegressor(random_state=42, **params)
    elif kind == "linear":
        estimator = LinearRegression()
    elif kind == "dummy_median":
        estimator = DummyRegressor(strategy="median")
    elif kind == "dummy_mean":
        estimator = DummyRegressor(strategy="mean")
    else:
        raise ValueError(f"kind desconocido: {kind}")
    return Pipeline([("preprocesamiento", preprocessor(model_name)), ("modelo", estimator)])
