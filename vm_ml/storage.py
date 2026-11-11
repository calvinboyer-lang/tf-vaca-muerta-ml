"""Almacenamiento de artefactos: carpeta local o Google Cloud Storage.

    store = open_store("./artifacts")          # local (k3d, pruebas)
    store = open_store("gs://mi-bucket/vm")    # GCS (GKE, Workload Identity)

En GCS se usan las credenciales por defecto (ADC): en GKE las de la cuenta de
servicio asociada por Workload Identity; en tu PC, las de
`gcloud auth application-default login`. Nunca una llave JSON.
"""

from __future__ import annotations

from pathlib import Path


class LocalStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.uri = str(self.root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # escritura atómica: nadie lee un archivo a medias

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in base.rglob("*") if p.is_file())


class GCSStore:
    def __init__(self, uri: str):
        from google.cloud import storage  # import diferido: no hace falta en local

        without_scheme = uri[len("gs://") :]
        bucket, _, prefix = without_scheme.partition("/")
        self.uri = uri.rstrip("/")
        self.prefix = prefix.strip("/")
        self.bucket = storage.Client().bucket(bucket)

    def _blob(self, key: str):
        name = f"{self.prefix}/{key}" if self.prefix else key
        return self.bucket.blob(name)

    def exists(self, key: str) -> bool:
        return self._blob(key).exists()

    def read_bytes(self, key: str) -> bytes:
        return self._blob(key).download_as_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        self._blob(key).upload_from_string(data)

    def list(self, prefix: str) -> list[str]:
        full = f"{self.prefix}/{prefix}" if self.prefix else prefix
        cut = len(self.prefix) + 1 if self.prefix else 0
        return sorted(b.name[cut:] for b in self.bucket.client.list_blobs(self.bucket, prefix=full))


def open_store(uri: str):
    return GCSStore(uri) if uri.startswith("gs://") else LocalStore(uri)
