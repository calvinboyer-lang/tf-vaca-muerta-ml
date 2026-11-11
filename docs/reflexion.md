# Reflexión crítica

> **Borrador.** Estructura y argumentos que no dependen de las mediciones; las cifras se completan con `docs/resultados.md`.

## 1. Alcances

- Plataforma completa y reproducible: datos versionados → entrenamiento con validación y promoción controlada → API con trazabilidad de versión → despliegue por IaC y CI/CD con OIDC → métricas, logs y alertas → pruebas de carga y escalado.
- Tres modelos con usos explícitos: descriptivo (v1, v2) y de pronóstico (v2_notef).
- Cada predicción indica qué versión respondió, con qué datos de entrada y de dónde salieron.

## 2. Limitaciones del modelo

- **Ventana corta de datos:** la tabla pública cubre un semestre de 2026. Las métricas tienen varianza alta entre separaciones (por eso se informa promedio ± desvío) y no prueban que el modelo funcione en otros años o contextos de precio y actividad.
- **Declinación:** con la edad del pozo, v2 sigue la caída de los pozos jóvenes (−27 % estimado contra −30 % real), pero en los pozos maduros el árbol asigna los 6 meses a la misma hoja y la estimación queda plana (ver `resultados.md` §2.7). Es una limitación estructural de un árbol sobre variables casi constantes por pozo. Alternativas a probar: la producción del mes anterior como variable (requiere la serie histórica) o curvas de Arps por pozo.
- **Edad del pozo:** sale de `FECHA_INIC` del padrón, que resultó ser el inicio de la **perforación**: la producción empieza unos 7 meses después (mediana). El desfase varía entre pozos (P10 = 5 meses) y agrega ruido. Además, ningún modelo capta la subida de los primeros meses de producción.
- **Variables faltantes:** etapas de fractura, arena y agua bombeadas (Adjunto IV, otro dataset), presiones, choke. El largo horizontal (capa 10) se evaluó y no mejora el modelo: cubre el 52 % de los pozos y su información ya está en la profundidad y la edad.
- **TEF:** v1 y v2 lo necesitan y solo se conoce al cerrar el mes. Si no se informa, la API supone el mes completo, lo que sobrestima en meses con paradas.
- **Categorías nuevas:** una operadora o área que no estaba en el entrenamiento se codifica como "ninguna categoría" (la respuesta lo marca en `categorias_no_vistas`).

## 3. Implicancias éticas

- **Datos:** públicos y agregados por pozo, sin datos personales. Igual se cita la fuente, la fecha de descarga y el filtro aplicado (manifest), y no se redistribuye el dataset completo de la Secretaría, solo el subconjunto filtrado.
- **Sesgos:** la población está dominada por pocas operadoras y áreas: YPF tiene el 66 % de los registros y Loma Campana el 27 %. El modelo aprende mejor esos casos. Incluir la operadora puede reproducir diferencias operativas o de reporte entre empresas en lugar de diferencias geológicas. Pozos parados o con producción cero se excluyen por diseño (extracción efectiva), así que el modelo no sirve para estimar paradas.
- **Uso:** es una estimación académica con error medio del orden de 560 m³/mes (v2_notef, holdout) frente a una producción mediana de 698 m³/mes. No debe usarse para decisiones de inversión, regalías, reservas ni seguridad sin validación de ingeniería. La API lo dice en su descripción y devuelve el MAE de referencia en cada respuesta.
- **Transparencia:** cada versión documenta datos, parámetros, métricas y por qué fue o no promovida. Las predicciones quedan registradas con versión y latencia.

## 4. Qué falta para producción real

| Área | Falta |
|---|---|
| Datos | Ingesta automática mensual, validación de esquema y de rangos (p. ej. Great Expectations), monitoreo de *drift* de entradas y salidas |
| Modelo | Serie histórica multi-año, validación temporal estricta, intervalos de predicción (no solo un valor), modelos que sigan la declinación |
| Seguridad | Autenticación de la API (API key u OAuth), TLS e Ingress con WAF, `ADMIN_TOKEN` desde Secret Manager, red privada para GKE, formato de modelo sin ejecución de código (ONNX o `skops`) |
| Operación | Cluster regional o al menos multi-zona, PostgreSQL gestionado con backups (Cloud SQL), Alertmanager con notificaciones, SLOs y *runbooks* |
| Entrega | Entornos dev/prod separados con aprobación manual, despliegues canary o blue-green de versiones del modelo, rollback automático si empeoran las métricas en línea |
| Costos | Apagado automático fuera de horario, nodos Spot para entrenamiento, presupuestos y alertas de facturación |

## 5. Continuidad (Curso 4, IA generativa)

La misma plataforma (API, registro de versiones, PostgreSQL, observabilidad y despliegue) sirve de base para:

- un asistente que responda preguntas en lenguaje natural sobre los pozos ("¿cuánto produjo el pozo X?", "¿qué estima el modelo para julio?") llamando a `/wells` y `/predict` como herramientas (MCP);
- un modelo abierto servido en el cluster (p. ej. con Ollama), con las mismas métricas y alertas.
