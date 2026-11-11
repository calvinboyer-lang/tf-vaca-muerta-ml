"""Constantes del caso de estudio. Todo lo que define "qué se modela" vive acá,
para que el notebook, el entrenamiento y la API usen exactamente lo mismo."""

from __future__ import annotations

# --- Fuente: servicio ArcGIS REST de la Secretaría de Energía -----------------
ARCGIS_BASE = (
    "https://services1.arcgis.com/gKFHG0iKXPnKkg1l/ArcGIS/rest/services/"
    "Hidrocarburos_Argentina_SENRes319/FeatureServer"
)
# Capa usada en el notebook original ("Producción de pozos - Junio 2026").
DEFAULT_LAYER = 9
ARCGIS_PAGE_SIZE = 2000

# Filtro aplicado en el origen: Vaca Muerta, no convencional, shale.
# Se descargan TODOS los tipos y estados de pozo: la edad del pozo se calcula
# con su historia completa, y la población de modelado se filtra después.
SOURCE_WHERE = (
    "formacion='vaca muerta' AND tipo_de_recurso='NO CONVENCIONAL' "
    "AND sub_tipo_recurso='SHALE'"
)

# Columnas que se conservan en el snapshot.
SNAPSHOT_COLUMNS = [
    "anio",
    "mes",
    "idpozo",
    "prod_pet",
    "prod_gas",
    "prod_agua",
    "tef",
    "tipoextraccion",
    "tipoestado",
    "tipopozo",
    "empresa",
    "profundidad",
    "formacion",
    "areayacimiento",
    "cuenca",
    "provincia",
    "tipo_de_recurso",
    "sub_tipo_recurso",
]

# --- Población de modelado ---------------------------------------------------
POPULATION = {
    "tipopozo": "Petrolífero",
    "tipoestado": "Extracción Efectiva",
}

TARGET = "prod_pet"  # m3 / mes

# --- Conjuntos de variables (versiones del modelo) ----------------------------
# v1        variables de la propuesta (línea base). "mes" es el mes del año.
# v2        se reemplaza "mes" (no aporta, corr ~0) por la edad del pozo y se
#           agregan operadora y área. Usa el TEF del mismo mes: es DESCRIPTIVO.
# v2_notef  igual que v2 pero sin TEF: única versión válida para PRONOSTICAR,
#           porque el TEF recién se conoce cuando el mes terminó.
FEATURE_SETS: dict[str, dict[str, list[str]]] = {
    "v1": {
        "numeric": ["tef", "profundidad"],
        "categorical": ["mes", "tipoextraccion"],
    },
    "v2": {
        "numeric": ["tef", "profundidad", "edad_meses"],
        "categorical": ["tipoextraccion", "empresa", "areayacimiento"],
    },
    "v2_notef": {
        "numeric": ["profundidad", "edad_meses"],
        "categorical": ["tipoextraccion", "empresa", "areayacimiento"],
    },
}

MODEL_USE = {
    "v1": "descriptivo (línea base de la propuesta)",
    "v2": "descriptivo: requiere el TEF del mes, que se conoce al cerrar el mes",
    "v2_notef": "pronóstico: no usa información posterior al inicio del mes",
}

# Hiperparámetros por defecto del árbol. v1 replica el notebook original
# (max_depth=6, min_samples_leaf=20). Con --tune se buscan por validación
# cruzada agrupada por pozo y quedan registrados en metadata.json.
DEFAULT_TREE_PARAMS: dict[str, dict[str, int]] = {
    "v1": {"max_depth": 6, "min_samples_leaf": 20},
    "v2": {"max_depth": 8, "min_samples_leaf": 20},
    "v2_notef": {"max_depth": 10, "min_samples_leaf": 20},
}
TUNE_GRID = {
    "max_depth": [4, 6, 8, 10, 12],
    "min_samples_leaf": [5, 10, 20, 50],
}

# --- Evaluación --------------------------------------------------------------
# Holdout FIJO por pozo: un pozo va a prueba si hash(SALT:idpozo) % 100 < 20.
# Es determinístico y no depende de una semilla ni del orden de las filas:
# todas las versiones (y los datasets ampliados) se comparan contra los
# mismos pozos de prueba, y un pozo nuevo cae siempre del mismo lado.
HOLDOUT_SALT = "tf-vaca-muerta-2026"
HOLDOUT_PCT = 20

# Validación repetida (desarrollo): 10 separaciones por pozo con semillas 0..9.
CV_SEEDS = list(range(10))
CV_TEST_SIZE = 0.25

# Validación temporal: los últimos N meses del snapshot quedan para prueba.
TEMPORAL_TEST_MONTHS = 2

# --- Promoción de versiones --------------------------------------------------
# Se promueve si, sobre el MISMO holdout fijo y con el dataset actual:
#   1) el MAE baja al menos MIN_REL_IMPROVEMENT respecto de la versión vigente, y
#   2) el intervalo bootstrap (por pozo, pareado) de la diferencia de MAE
#      excluye el cero al nivel BOOTSTRAP_CONFIDENCE.
MIN_REL_IMPROVEMENT = 0.01
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_CONFIDENCE = 0.90
