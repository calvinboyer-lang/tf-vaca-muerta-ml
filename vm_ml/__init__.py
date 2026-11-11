"""vm_ml: código compartido del Trabajo Final (Vaca Muerta, producción mensual).

Lo usan el notebook, el servicio de entrenamiento (training/) y la API (api/):

- config.py      constantes del caso (población, fuentes, conjuntos de variables)
- data.py        descarga (ArcGIS / CSV de datos.gob.ar), snapshot y edad del pozo
- features.py    tabla de modelado y pipelines de scikit-learn
- evaluation.py  holdout fijo por pozo, validación repetida, validación temporal
- registry.py    versionado, metadata.json, puntero "current" y regla de promoción
- storage.py     almacenamiento local o Google Cloud Storage (gs://)
"""

__version__ = "0.1.0"
