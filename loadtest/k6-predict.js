// Prueba de carga de POST /predict (k6). Carga CERRADA: VUS usuarios virtuales
// mandan peticiones sin pausa (SLEEP=0), o sea VUS peticiones concurrentes.
//
// Variables (todas opcionales):
//   API_URL    http://api:8081 (adentro del cluster) | http://localhost:8081
//   VUS        usuarios virtuales concurrentes (1, 10, 50, 100, 150...)   [10]
//   DURATION   duración de la meseta                                     [2m]
//   RAMP       subida inicial (0s = arranque en seco)                     [15s]
//   SLEEP      pausa entre peticiones de cada VU, en segundos             [0]
//   MODEL      v1 | v2 | v2_notef (vacío = DEFAULT_MODEL de la API)
//   LABEL      etiqueta libre para identificar la corrida en los logs
//
// Al final imprime una línea "RESULT {...}" en JSON con throughput, latencias
// y tasa de errores: es lo que se copia a docs/resultados.md.
import http from "k6/http";
import { check, sleep } from "k6";

const API_URL = __ENV.API_URL || "http://api:8081";
const VUS = Number(__ENV.VUS || 10);
const DURATION = __ENV.DURATION || "2m";
const RAMP = __ENV.RAMP || "15s";
const SLEEP = Number(__ENV.SLEEP || 0);
const MODEL = __ENV.MODEL || "";
const LABEL = __ENV.LABEL || `vus${VUS}`;
// NO_REUSE=1: una conexión TCP nueva por petición. Con keep-alive (default) cada
// VU queda pegado al pod que eligió kube-proxy al conectar, y las réplicas que
// agrega el HPA durante la prueba casi no reciben tráfico (prueba hpa-vus150).
const NO_REUSE = __ENV.NO_REUSE === "1";

export const options = {
  scenarios: {
    carga: {
      executor: "ramping-vus",
      startVUs: RAMP === "0s" ? VUS : 1,
      stages: [
        { duration: RAMP, target: VUS },
        { duration: DURATION, target: VUS },
      ],
      gracefulRampDown: "5s",
    },
  },
  noConnectionReuse: NO_REUSE,
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
  // Umbrales solo informativos (no cortan la prueba).
  thresholds: {
    http_req_failed: [{ threshold: "rate<0.01", abortOnFail: false }],
    "http_req_duration{endpoint:predict}": [{ threshold: "p(95)<500", abortOnFail: false }],
  },
};

export function setup() {
  // Reintentos: si el cluster está reacomodando pods o nodos (p. ej. el HPA
  // bajando réplicas), la primera llamada puede fallar sin que la API esté caída.
  let res;
  for (let i = 1; i <= 10; i++) {
    res = http.get(`${API_URL}/wells/sample?n=500`, { timeout: "10s" });
    if (res.status === 200) return { ids: res.json().idpozo };
    console.warn(`setup: intento ${i}/10 falló (${res.status}); reintento en 3 s`);
    sleep(3);
  }
  throw new Error(`no pude obtener pozos de ${API_URL}/wells/sample: ${res.status} ${res.body}`);
}

export default function (data) {
  const idpozo = data.ids[Math.floor(Math.random() * data.ids.length)];
  const body = MODEL ? { idpozo, model: MODEL } : { idpozo };
  const res = http.post(`${API_URL}/predict`, JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    tags: { endpoint: "predict" },
  });
  check(res, { "status 200": (r) => r.status === 200 });
  if (SLEEP > 0) sleep(SLEEP);
}

export function handleSummary(data) {
  const m = data.metrics;
  const d = m["http_req_duration{endpoint:predict}"] || m.http_req_duration;
  const result = {
    label: LABEL,
    vus: VUS,
    duration: DURATION,
    no_reuse: NO_REUSE,
    model: MODEL || "default",
    requests: m.http_reqs.values.count,
    rps: Number(m.http_reqs.values.rate.toFixed(1)),
    p50_ms: Number(d.values.med.toFixed(1)),
    p90_ms: Number(d.values["p(90)"].toFixed(1)),
    p95_ms: Number(d.values["p(95)"].toFixed(1)),
    p99_ms: Number(d.values["p(99)"].toFixed(1)),
    max_ms: Number(d.values.max.toFixed(1)),
    error_rate: Number(m.http_req_failed.values.rate.toFixed(4)),
  };
  return { stdout: `\nRESULT ${JSON.stringify(result)}\n` };
}
