# Reflexión crítica

> Las cifras salen de `docs/resultados.md` (corridas del 11 y 12/11/2026).

## 1. Alcances

- Plataforma completa y reproducible: datos versionados → entrenamiento con validación y promoción controlada → API con trazabilidad de versión → despliegue por IaC y CI/CD con OIDC → métricas, logs y alertas → pruebas de carga y escalado.
- Tres modelos con usos explícitos: descriptivo (v1, v2) y de pronóstico (v2_notef).
- Cada predicción indica qué versión respondió, con qué datos de entrada y de dónde salieron.
- Reproducibilidad comprobada: el mismo snapshot dio las mismas métricas en la PC del autor, en k3d y en un Job en GKE (v2_notef: MAE 560,8 m³, R² 0,635).
- Servicio medido en GKE: ≈ 60 req/s por réplica, hasta ≈ 250–295 req/s con 5–7 réplicas, 0 % de errores con la configuración final.

## 2. Limitaciones del modelo

- **Ventana corta de datos:** la tabla pública cubre un semestre de 2026. Las métricas tienen varianza alta entre separaciones (por eso se informa promedio ± desvío) y no prueban que el modelo funcione en otros años o contextos de precio y actividad.
- **Declinación:** con la edad del pozo, v2 sigue la caída de los pozos jóvenes (−27 % estimado contra −30 % real), pero en los pozos maduros el árbol asigna los 6 meses a la misma hoja y la estimación queda plana (ver `resultados.md` §2.7). Es una limitación estructural de un árbol sobre variables casi constantes por pozo. Alternativas a probar: la producción del mes anterior como variable (requiere la serie histórica) o curvas de Arps por pozo.
- **Edad del pozo:** sale de `FECHA_INIC` del padrón, que resultó ser el inicio de la **perforación**: la producción empieza unos 7 meses después (mediana). El desfase varía entre pozos (P10 = 5 meses) y agrega ruido. Además, ningún modelo capta la subida de los primeros meses de producción.
- **Variables faltantes:** etapas de fractura, arena y agua bombeadas (Adjunto IV, otro dataset), presiones, choke. El largo horizontal (capa 10) se evaluó y no mejora el modelo: cubre el 52 % de los pozos y su información ya está en la profundidad y la edad.
- **TEF:** v1 y v2 lo necesitan y solo se conoce al cerrar el mes. Si no se informa, la API supone el mes completo, lo que sobrestima en meses con paradas.
- **Categorías nuevas:** una operadora o área que no estaba en el entrenamiento se codifica como "ninguna categoría" (la respuesta lo marca en `categorias_no_vistas`).

## 2b. Limitaciones de la plataforma (halladas en las pruebas)

- **Una réplica es un solo proceso** y cada predicción cuesta ≈ 15 ms de CPU, sobre todo en armar el DataFrame de pandas y en el preprocesamiento. El techo por réplica es ≈ 60 req/s; por encima, solo crece la espera en cola.
- **Las sondas de salud** síncronas y con timeout de 1 s reiniciaron una réplica saturada y la saturación se volvió caída (52,7 % de errores). Se corrigió (sondas `async`, timeouts tolerantes), pero muestra lo frágil que es la configuración por defecto bajo carga.
- **El request de CPU** tiene que reflejar el consumo real. Con 250m (contra ≈ 900m reales) el scheduler apiló 3 réplicas en un nodo y rindieron menos que una. Con 700m escalan.
- **El balanceo de un Service reparte conexiones, no pedidos.** Con clientes que mantienen conexiones abiertas (keep-alive), las réplicas que agrega el HPA casi no reciben tráfico y el HPA se frena.
- **El tope del node pool** (4 nodos e2-standard-2) limita al HPA: con 10 réplicas pedidas, 3–4 quedaron `Pending` por falta de CPU. Los nodos nuevos tardan ≈ 70 s más la descarga de la imagen y la carga de los modelos: el escalado de pods es rápido, el de nodos domina.
- **La alerta de latencia** mide dentro del servidor (p95 ≤ 320 ms) y no ve la espera en cola que percibe el cliente (p95 de hasta 2,5 s): no se disparó en ninguna prueba.
- **Prometheus no tiene almacenamiento persistente:** al bajar los nodos se perdieron las métricas del primer día (quedaron capturas, los resultados de k6 y un respaldo exportado del segundo día).

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
| Inferencia | Preprocesamiento sin pandas por pedido (o un formato compilado del modelo), varios workers por réplica, pedidos por lote para estimar muchos pozos a la vez |
| Tráfico | Ingress o gateway HTTP (capa 7) que reparta pedidos y no conexiones, con TLS; límite de vida de las conexiones; *rate limiting* |
| Escalado | Requests calibrados con mediciones (como los 700m), node pool más grande o aprovisionamiento automático de nodos, nodos precalentados o réplicas mínimas según la demanda esperada |
| Seguridad | Autenticación de la API (API key u OAuth), `ADMIN_TOKEN` desde Secret Manager, red privada para GKE, formato de modelo sin ejecución de código (ONNX o `skops`) |
| Observabilidad | Alertas sobre la latencia que percibe el cliente (balanceador o sonda sintética), almacenamiento persistente para Prometheus, Alertmanager con notificaciones, SLOs y *runbooks* |
| Operación | Cluster regional o al menos multi-zona, PostgreSQL gestionado con backups (Cloud SQL), retención y archivado de la tabla de predicciones (380.947 filas en dos días de pruebas) |
| Entrega | Entornos dev/prod separados con aprobación manual, despliegues canary o blue-green de versiones del modelo, rollback automático si empeoran las métricas en línea |
| Costos | Apagado automático fuera de horario, nodos Spot para entrenamiento, política de limpieza del Artifact Registry (llegó a 7,95 GB por publicar en cada push), presupuestos y alertas de facturación |

## 5. Continuidad (Curso 4, IA generativa)

La misma plataforma (API, registro de versiones, PostgreSQL, observabilidad y despliegue) sirve de base para:

- un asistente que responda preguntas en lenguaje natural sobre los pozos ("¿cuánto produjo el pozo X?", "¿qué estima el modelo para julio?") llamando a `/wells` y `/predict` como herramientas (MCP);
- un modelo abierto servido en el cluster (p. ej. con Ollama), con las mismas métricas y alertas.
