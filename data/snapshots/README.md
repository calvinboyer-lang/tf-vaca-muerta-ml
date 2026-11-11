# Snapshots del dataset

Cada snapshot (`vm_shale_<desde>_<hasta>.csv.gz`) se genera con
`.\tasks.ps1 data-build` y va acompañado de su `.manifest.json` (sha256,
fuentes, rango de meses, cantidad de filas y pozos, origen de la edad).

Se versionan en git (son chicos: Vaca Muerta shale ya filtrado) y el servicio
de entrenamiento los copia al bucket (`datasets/`). No editar a mano: el
sha256 se verifica al cargar.
