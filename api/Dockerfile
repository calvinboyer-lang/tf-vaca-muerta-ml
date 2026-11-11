# Imagen de la API. Se construye desde la RAÍZ del repo:
#   docker build -f api/Dockerfile -t api:local .
# El modelo NO va dentro de la imagen: se descarga al arrancar desde STORE_URI
# (gs://... en GKE, una carpeta montada en k3d) y se verifica su sha256.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8081 STORE_URI=/artifacts
WORKDIR /app

COPY api/requirements.txt api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

COPY vm_ml/ vm_ml/
COPY api/ api/

RUN useradd --uid 10001 --create-home api
USER 10001
EXPOSE 8081

# Un proceso uvicorn por pod: se escala con réplicas (HPA), no con workers,
# así el uso de CPU por pod refleja la carga real y el HPA decide bien.
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8081", "--no-access-log"]
