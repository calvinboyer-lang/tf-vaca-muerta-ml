# Caso de estudio y objetivo

**Denominación.** Desarrollo y despliegue de una plataforma de estimación y consulta de producción mensual de pozos shale de Vaca Muerta mediante Machine Learning en Google Cloud.

**Objetivo.** Diseñar, desarrollar y desplegar un servicio de Machine Learning propio que estime la producción mensual de petróleo (`prod_pet`, m³/mes) de pozos no convencionales de Vaca Muerta a partir de variables técnicas y operativas, con una API en Python sobre Kubernetes, entrenamiento y versionado del modelo, persistencia de resultados, infraestructura como código, CI/CD, observabilidad y autoescalado.

**Datos.** Datos abiertos de la Secretaría de Energía de la Nación (servicio ArcGIS REST): producción mensual por pozo (tabla 9), padrón de pozos (capa 0, para la edad del pozo) y trayectorias (capa 10, largo horizontal). Población: formación Vaca Muerta, recurso no convencional, subtipo shale, pozos petrolíferos en extracción efectiva, enero a junio de 2026: **12.321 registros de 2.212 pozos**, guardados como snapshot versionado (`csv.gz` + manifest con sha256).

**Modelo.** Regresión supervisada con un árbol de decisión (`DecisionTreeRegressor`, scikit-learn) dentro de un pipeline con imputación, escalado y codificación one-hot, en tres versiones con usos explícitos:

| Versión | Variables | Uso |
|---|---|---|
| `v1` | TEF, profundidad, mes, sistema de extracción | Línea base de la propuesta (descriptivo) |
| `v2` | TEF, profundidad, edad del pozo, sistema de extracción, operadora, área | Descriptivo: requiere el TEF del mes, que se conoce al cerrarlo |
| `v2_notef` | `v2` sin TEF | Pronóstico: no usa información posterior al inicio del mes (modelo por defecto de la API) |

**Repositorio.** <https://github.com/calvinboyer-lang/tf-vaca-muerta-ml> (código, charts, Terraform, workflows, documentación y capturas). Trabajo individual.

## Resultados principales

- **Modelo:** en el holdout fijo de 432 pozos, `v2` alcanza MAE 483,5 m³/mes y R² 0,733; `v2_notef` (pronóstico) MAE 560,8 y R² 0,635; `v1` MAE 696,1 y R² 0,466. La edad del pozo explica la mayor parte de la mejora.
- **Reproducibilidad:** el mismo snapshot dio métricas idénticas en la PC, en k3d y en un Job dentro de GKE.
- **Servicio en GKE:** ≈ 60 req/s por réplica; 3 réplicas ≈ 180 req/s y 5 réplicas ≈ 250 req/s; con autoescalado (2–10) hasta 295 req/s; **0 % de errores** con la configuración final.
- **Hallazgos de operación:** sondas de salud que reiniciaban réplicas saturadas, requests de CPU que impedían escalar, conexiones persistentes que dejaban sin tráfico a las réplicas nuevas y el tope del node pool que deja réplicas en `Pending`. Cada uno está medido, explicado y, cuando correspondía, corregido.

## Respuesta a la devolución de la propuesta

| Punto de la devolución | Qué se hizo | Dónde |
|---|---|---|
| 1. Reproducibilidad y R² | Se reprodujo el R² original (0,476 con profundidad 6 y separación por pozo; 0,511 separando filas al azar). Hiperparámetros, separación y dataset quedan en el `metadata.json` de cada versión. Se informa promedio ± desvío sobre 10 separaciones y una validación temporal | Resultados §2.2–2.4 |
| 2. Mes del año vs. edad del pozo; TEF | Se agregó la edad del pozo (padrón oficial), la operadora y el área (`v2`), y una versión sin TEF para pronosticar (`v2_notef`). La API busca las variables del pozo a partir de su id | Resultados §2.3–2.4; Endpoints |
| 3. Declinación | Se muestra y analiza: `v2` sigue la caída de los pozos jóvenes (−27 % contra −30 % real), pero en los maduros la estimación queda plana o escalonada | Resultados §2.7 y §3.0.1; Reflexión |
| 4. Comparación y promoción | Holdout fijo por pozo (hash del id) y promoción solo si el MAE mejora ≥ 1 % y el intervalo bootstrap del 90 % excluye el cero. Se mostraron los tres casos (igual, mejora no significativa, mejora significativa) | Resultados §2.6 |
| 5. Definiciones de arquitectura | Carga del modelo desde Cloud Storage al arrancar y cada 2 min, con verificación de sha256; PostgreSQL como StatefulSet con PVC; 5 alertas; Alloy para los logs; Workload Identity con escritura solo para la cuenta de entrenamiento; trabajo individual | Arquitectura |
| Escalado | Se midió el techo por réplica y se justificó el umbral del HPA con esos números | Resultados §3.2–3.4 |

Las referencias del tipo «§3.2.1» remiten a la numeración interna de cada capítulo; las del tipo «captura 7.5b» o «evidencia 7.5b», al anexo de evidencias.
