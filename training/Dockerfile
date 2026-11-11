# Imagen del servicio de entrenamiento. Se construye desde la RAÍZ del repo:
#   docker build -f training/Dockerfile -t training:local .
# Corre como Job de Kubernetes (helm/charts/training) o local:
#   docker run --rm -v ${PWD}/data:/app/data training:local --model v2 \
#     --dataset data/snapshots/<id>.csv.gz --store /app/data/artifacts
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY training/requirements.txt training/requirements.txt
RUN pip install --no-cache-dir -r training/requirements.txt

COPY vm_ml/ vm_ml/
COPY training/ training/

# Sin root: el proceso no puede modificar la imagen.
RUN useradd --uid 10001 --create-home trainer
USER 10001

ENTRYPOINT ["python", "-m", "training.train"]
