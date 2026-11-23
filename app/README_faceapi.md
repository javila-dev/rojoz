# Face Recognition Service

Microservicio de reconocimiento facial **1:1**, reutilizable por varias aplicaciones. Expone una API HTTP simple (FastAPI + InsightFace), es **stateless** (no guarda personas ni embeddings) y no tiene conocimiento de ningún dominio de negocio.

No hace 1:N ("quién de todos es esta persona"), no tiene UI, y no incluye anti-spoof/liveness avanzado en esta versión.

## 1. Overview

- `GET /health` — vivo, modelo cargado, versión.
- `POST /v1/enroll` — recibe una foto, valida su calidad y devuelve un embedding L2-normalizado de 512 dimensiones.
- `POST /v1/match` — recibe una foto + un embedding de control (+ umbral opcional) y devuelve si es la misma persona.

Cada app cliente decide dónde guardar el embedding devuelto por `/v1/enroll` — este servicio no lo persiste.

### Entorno de producción

- **Base URL:** `https://face.2asoft.tech`
- **Auth:** header `X-API-Key`, una key compartida por todas las apps clientes. La key real **no está en este repo** — pedísela a quien administra el deploy en Dokploy.
- Probá primero `GET https://face.2asoft.tech/health` (sin auth) para confirmar que el servicio está arriba antes de integrar `/v1/enroll` / `/v1/match`.

## 2. Quick start

Con `docker compose` (recomendado para desarrollo local):

```bash
cp .env.example .env   # editar FACE_API_KEYS al menos
docker compose up --build
```

O con `docker` directo:

```bash
docker build -t face-recognition-service .
docker run -d --name face-service -p 8000:8000 \
  -e FACE_API_KEYS=un-uuid-por-app-cliente \
  face-recognition-service
```

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/v1/enroll \
  -H "X-API-Key: un-uuid-por-app-cliente" \
  -F "image=@foto.jpg"

curl -X POST http://localhost:8000/v1/match \
  -H "X-API-Key: un-uuid-por-app-cliente" \
  -F "image=@foto_en_vivo.jpg" \
  -F 'embedding=[0.01, -0.02, ...]' \
  -F "threshold=0.40"
```

También se puede enviar la imagen como JSON con base64/data-URL en vez de multipart — ver la sección de API.

## 3. API reference

Documentación interactiva autogenerada en `/docs` (Swagger UI) y `/redoc`.

### `GET /health`

Sin autenticación (para liveness/readiness checks de infraestructura).

```json
{"status": "ok", "model": "buffalo_l", "version": "1.0.0", "backend": "insightface"}
```

`status` es `"not_ready"` (HTTP 503) mientras el modelo todavía no cargó o si la carga falló.

### `POST /v1/enroll`

Requiere `X-API-Key`. Body: `multipart/form-data` con campo `image`, **o** JSON `{"image": "<base64 o data-URL>"}`.

Opcional en el body: `max_yaw`, `max_pitch`, `max_roll` (grados 0–90). Si mandás cualquiera, `bad_pose` **bloquea** `ok` en esa request; si no mandás ninguno, `bad_pose` sigue siendo aviso no bloqueante (compat CliniQ).

Respuesta (siempre HTTP 200 salvo imagen realmente inválida/ilegible → 400):

```json
{
  "ok": true,
  "embedding": [0.0123, -0.0456, 0.0789, "... 512 floats en total"],
  "det_score": 0.93,
  "face_ratio": 18.4,
  "laplacian_var": 342.1,
  "brightness": 128.5,
  "issues": []
}
```

Si la calidad no pasa, `ok: false`, `embedding` es `null` y `issues` trae el/los código(s) de motivo (ver tabla abajo).

### `POST /v1/match`

Requiere `X-API-Key`. Body: `multipart/form-data` con `image` + `embedding` (JSON-string de 512 floats) + `threshold` opcional, **o** JSON `{"image", "embedding", "threshold"}`. Mismos opcionales de pose que enroll (`max_yaw` / `max_pitch` / `max_roll`).

```json
{
  "ok": true,
  "match_score": 0.71,
  "match_passed": true,
  "threshold": 0.40,
  "det_score": 0.95,
  "face_ratio": 21.3,
  "issues": []
}
```

El servicio decide `match_passed` comparando la similitud coseno (`match_score`, en `[-1, 1]`) contra `threshold` (o `FACE_MATCH_THRESHOLD` si no se envía). La app cliente no necesita reimplementar esa decisión.

Si la foto en vivo no pasa calidad (incluye más de un rostro detectado, o `bad_pose` en modo estricto), `ok: false` con `issues` y sin `match_score`/`match_passed`.

Calidad OK pero no es la misma persona: `match_passed: false` (sin código de issue nuevo). Bodega interpreta eso como fallo de identidad.

## 4. Authentication

Header `X-API-Key: <key>` o `Authorization: Bearer <key>`. Las keys válidas se configuran en `FACE_API_KEYS`.

Por ahora se usa **una sola key compartida** entre todas las apps clientes (simplemente no pongas coma en `FACE_API_KEYS`). El servicio también soporta varias keys separadas por coma si más adelante hace falta una por app (por ejemplo, para poder revocar el acceso de una app sin afectar a las demás) — no hace falta cambiar código para eso, solo cargar más de un valor en la env var.

No hay endpoint de rotación: rotar la key es redeployar con el env var actualizado.

Sin key válida → HTTP 401. `/health` es la única excepción, queda público.

## 5. Environment variables

| Variable | Default | Descripción |
|---|---|---|
| `FACE_API_KEYS` | *(vacío)* | Keys válidas, separadas por coma. |
| `FACE_MODEL_NAME` | `buffalo_l` | Paquete de modelos InsightFace. |
| `FACE_PROVIDERS` | `cpu` | `cpu` o `cuda`. |
| `FACE_MATCH_THRESHOLD` | `0.40` | Umbral de match por defecto (si la request no manda uno). |
| `FACE_MIN_DET_SCORE` | `0.50` | Confianza mínima de detección de rostro. |
| `FACE_MIN_FACE_RATIO` | `5.0` | % mínimo del frame que debe ocupar el rostro. |
| `FACE_MIN_LAPLACIAN_VAR` | `50.0` | Piso de nitidez (varianza del laplaciano). Calibrado con fotos reales: crops nítidos dan ~250-275, desenfoque apenas visible ~65, desenfoque notorio ~34. 50 deja margen amplio contra fotos nítidas y sigue filtrando desenfoque real. Puede necesitar ajuste por cámara. |
| `FACE_MIN_BRIGHTNESS` / `FACE_MAX_BRIGHTNESS` | `40.0` / `220.0` | Rango de brillo aceptable (0-255, escala de grises). |
| `FACE_MAX_IMAGE_MB` | `10.0` | Tamaño máximo de imagen aceptado. |
| `FACE_MIN_RESOLUTION` | `200` | Resolución mínima (px, en cualquier eje). |
| `FACE_MODELS_ROOT` | `/app/models` | Dónde vive el modelo horneado en la imagen. |
| `PORT` | `8000` | Puerto del servidor. |
| `APP_VERSION` | `1.0.0` | Se refleja en `/health`. |

Todos los umbrales de calidad son ajustables sin tocar código — cada app cliente/cámara puede necesitar valores distintos.

## 6. Model & Docker build strategy

El modelo se **hornea en build-time**, nunca se descarga en runtime:

```dockerfile
ARG FACE_MODEL_NAME=buffalo_l
ENV FACE_MODEL_NAME=${FACE_MODEL_NAME}
RUN python -c "...FaceAnalysis(name='${FACE_MODEL_NAME}', ...).prepare(...)"
```

El build-arg queda fijado como `ENV`, así el default de runtime **siempre** coincide con lo que se horneó. Si necesitás otro modelo, reconstruí la imagen:

```bash
docker build --build-arg FACE_MODEL_NAME=buffalo_s -t face-recognition-service:buffalo_s .
```

**No cambies `FACE_MODEL_NAME` en runtime sin reconstruir la imagen** — el contenedor intentaría descargar el modelo al arrancar, lo cual puede fallar o ser muy lento en un entorno sin salida a internet.

## 7. Resource sizing

`buffalo_l` (default) necesita más RAM/CPU que `buffalo_s`. Como referencia orientativa: contá con **~2-3GB de RAM** y un par de CPUs para `buffalo_l` en producción. Si el host es chico, `buffalo_s` es una alternativa más liviana (build con `--build-arg FACE_MODEL_NAME=buffalo_s`).

## 8. GPU/CUDA (opcional)

`FACE_PROVIDERS=cuda` es soportado a nivel de código, pero **no** por el `Dockerfile` por defecto: hace falta `onnxruntime-gpu` en vez de `onnxruntime` y una imagen base con CUDA. Tratalo como un camino avanzado/manual, no como algo que la imagen por defecto resuelve sola.

## 9. Threshold tuning guidance

`FACE_MATCH_THRESHOLD` y los umbrales de calidad son puntos de partida razonables, no constantes universales. La similitud coseno depende de la cámara, iluminación y ángulo típico de cada app cliente — calibrá empíricamente con fotos reales antes de llevar a producción.

## 10. Issue code reference

| Código | Cuándo aparece |
|---|---|
| `no_face` | No se detectó ningún rostro. |
| `multiple_faces` | Se detectó más de un rostro (bloquea siempre, también en `/v1/match`). |
| `low_confidence` | `det_score` por debajo de `FACE_MIN_DET_SCORE`. |
| `face_too_small` | El rostro ocupa menos de `FACE_MIN_FACE_RATIO` % del frame. |
| `blurry` | Varianza del laplaciano por debajo de `FACE_MIN_LAPLACIAN_VAR`. |
| `too_dark` / `too_bright` | Brillo fuera de `[FACE_MIN_BRIGHTNESS, FACE_MAX_BRIGHTNESS]`. |
| `low_resolution` | La imagen es más chica que `FACE_MIN_RESOLUTION`. |
| `bad_pose` | Ángulo (yaw/pitch/roll) extremo. Sin `max_yaw`/`max_pitch`/`max_roll` en la request: aviso no bloqueante. Si la request manda cualquiera de esos límites (0–90°): **bloquea** `ok` y el mensaje puede incluir el ángulo real. |
| `invalid_image` | La imagen no se pudo decodificar, o falta el campo `image`. |
| `image_too_large` | Supera `FACE_MAX_IMAGE_MB`. |
| `unsupported_format` | Reservado para formatos no soportados. |
| `embedding_dimension_mismatch` | El embedding recibido en `/v1/match` no tiene 512 dimensiones o no es una lista numérica válida. |

## 11. Testing

```bash
pip install -r requirements-dev.txt
pytest
```

Todos los tests (unitarios e integración) corren con el modelo de InsightFace **mockeado** — no descargan ni cargan el modelo real, corren en segundos. Para un smoke test end-to-end con el modelo real, hay que levantar el contenedor y pegarle a `/v1/enroll` / `/v1/match` con fotos reales (no forma parte de la suite automática por el costo de cargar el modelo).

## 12. Deploy en Dokploy

`docker-compose.yml` no tiene nada de Traefik ni de redes/labels — eso lo resuelve Dokploy solo:

1. Crear la app como "Docker Compose", apuntando a este repo.
2. Cargar las env vars (`FACE_API_KEYS` como mínimo) en la pestaña Environment de Dokploy.
3. Si otra app necesita pegarle por dominio público, configurar el dominio en Dokploy con **Container Port = 8000** (o el valor de `PORT` que uses). Si solo lo va a consumir otro contenedor dentro del mismo Dokploy, ni hace falta dominio: alcanza con la red interna que Dokploy arma entre los servicios del mismo proyecto.

## 13. Nota sobre CliniQ

Este servicio se diseñó para ser contract-compatible en espíritu con el `face-service` interno de CliniQ (mismo stack: FastAPI + InsightFace), pensando en que CliniQ pueda migrar a este servicio más adelante. Este repositorio no tiene ninguna dependencia de CliniQ ni código específico de esa app — cualquier trabajo de adaptación (por ejemplo, un cliente HTTP del lado de CliniQ) se hace en el repo de CliniQ, no acá.
