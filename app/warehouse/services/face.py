"""Cliente facial para Bodega.

Prioridad:
1. Servicio remoto (WAREHOUSE_FACE_URL) — face.2asoft.tech / face-api
2. Stub local — solo tests/CI (WAREHOUSE_FACE_BACKEND=stub o sin URL)

Contrato remoto: ver README_faceapi.md
  POST /v1/enroll  { image, max_yaw?, max_pitch?, max_roll? }
  POST /v1/match   { image, embedding[512], threshold?, max_yaw?, max_pitch?, max_roll? }
  Mandar max_yaw/pitch/roll activa modo estricto (bad_pose bloquea ok).
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from django.conf import settings

from warehouse.models import FaceSettings

logger = logging.getLogger(__name__)


class FaceDependencyError(RuntimeError):
    """Faltan deps locales (stub) o el servicio remoto no está usable."""


class FaceServiceError(RuntimeError):
    """Error al hablar con el microservicio facial."""


# ── Traducción semántica Rojoz ← códigos del face-api (README §10) ─────────
# Contrato: la API manda `issues[].code` (y/o match_passed). Bodega NO inventa
# el fallo: solo traduce el código a lenguaje de bodega + tip de mejora.
#
# Códigos del servicio (README):
#   no_face, multiple_faces, low_confidence, face_too_small, blurry,
#   too_dark, too_bright, low_resolution, bad_pose, invalid_image,
#   image_too_large, unsupported_format, embedding_dimension_mismatch
#
# Códigos solo de Bodega (no vienen del microservicio):
#   mismatch          ← match_passed=false sin issue de calidad
#   not_enrolled      ← trabajador sin embedding
#   service / missing / error / crop ← errores locales de Bodega

# Alias que a veces llegan del stub o de versiones viejas → código README.
CODE_ALIASES = {
    "multi_face": "multiple_faces",
    "low_det": "low_confidence",
    "pose_yaw": "bad_pose",
    "pose_pitch": "bad_pose",
}

# Qué pasó (semántico).
ISSUE_MESSAGES = {
    # --- face-api (README) ---
    "no_face": "No se ve tu cara en la foto.",
    "multiple_faces": "En la foto aparece más de una persona.",
    "low_confidence": "No se reconoce tu cara con suficiente claridad.",
    "face_too_small": "Tu cara se ve muy lejos o pequeña en la foto.",
    "blurry": "La foto salió borrosa.",
    "too_dark": "Hay poca luz en la foto.",
    "too_bright": "Hay demasiada luz o contraluz.",
    "low_resolution": "La foto no tiene suficiente calidad.",
    "bad_pose": "La cabeza está muy girada (no miras de frente).",
    "invalid_image": "No se pudo leer esa foto.",
    "image_too_large": "La foto es demasiado pesada.",
    "unsupported_format": "Ese tipo de imagen no se puede usar.",
    "embedding_dimension_mismatch": "La foto de control ya no es compatible con el sistema.",
    # --- solo Bodega ---
    "mismatch": "No pudimos confirmar que seas la persona registrada.",
    "not_enrolled": "Esta persona aún no tiene foto de control.",
    "service": "El reconocimiento facial no está disponible ahora.",
    "service_unavailable": "El reconocimiento facial no está disponible ahora.",
    "missing": "Falta la foto para validar.",
    "error": "No se pudo revisar la foto.",
    "crop": "No se pudo leer bien la foto.",
    "unknown": "No se pudo validar la foto.",
}

# Qué hacer para pasar.
ISSUE_TIPS = {
    "no_face": "Mira de frente a la cámara, sin gorra ni lentes oscuros, con buena luz en la cara.",
    "multiple_faces": "Quédate solo en el encuadre: una sola persona frente a la cámara.",
    "low_confidence": "Acércate un poco, mira de frente y mejora la luz sobre tu cara.",
    "face_too_small": "Acércate hasta que tu cara llene el óvalo de la guía.",
    "blurry": "Quédate quieto un segundo al capturar; no muevas la cámara.",
    "too_dark": "Busca más luz de frente (evita quedar a contraluz).",
    "too_bright": "Evita el sol o focos fuertes detrás de ti; la luz debe darte de frente.",
    "low_resolution": "Vuelve a capturar con la cámara del equipo.",
    "bad_pose": "Endereza la cabeza y mira directo a la cámara, como en la foto de control.",
    "invalid_image": "Vuelve a capturar la foto desde la cámara.",
    "image_too_large": "Vuelve a capturar; no uses un archivo aparte.",
    "unsupported_format": "Vuelve a capturar la foto desde la cámara.",
    "embedding_dimension_mismatch": "Pide a un administrador que re-enrolar la foto de control.",
    "mismatch": (
        "Mira de frente (como cuando te enrolaron), con buena luz y sin taparte "
        "la cara. Si sigue fallando, pide re-enrolar la foto de control."
    ),
    "not_enrolled": "Completa el enrolamiento con foto antes de recibir entregas.",
    "service": "Intenta de nuevo en un momento. Si continúa, avisa a sistemas.",
    "service_unavailable": "Intenta de nuevo en un momento. Si continúa, avisa a sistemas.",
    "missing": "Captura la foto e intenta de nuevo.",
    "error": "Vuelve a capturar la foto e intenta otra vez.",
    "crop": "Vuelve a capturar la foto e intenta otra vez.",
    "unknown": "Vuelve a capturar mirando de frente, con buena luz.",
}


def normalize_issue_code(code: str) -> str:
    key = (code or "").strip() or "unknown"
    return CODE_ALIASES.get(key, key)


@dataclass
class QualityIssue:
    code: str
    message: str


def user_facing_issue(code: str, fallback_message: str = "") -> dict:
    """Traduce un código de la API (o local) a mensaje + tip semánticos.

    Conserva el `code` original (normalizado) para soporte/logs; el texto
    lo define siempre Rojoz. Códigos desconocidos → copy genérico + tip de recaptura.
    """
    key = normalize_issue_code(code)
    message = ISSUE_MESSAGES.get(key) or ISSUE_MESSAGES["unknown"]
    tip = ISSUE_TIPS.get(key) or ISSUE_TIPS["unknown"]
    # fallback_message solo se usa si no hay traducción (código nuevo del API).
    if key not in ISSUE_MESSAGES and fallback_message:
        message = fallback_message.strip() or message
    return {"code": key, "message": message, "tip": tip}


def format_issues_for_api(issues: list | None) -> list[dict]:
    """Traduce issues del face-api; no inventa códigos que la API no mandó."""
    out: list[dict] = []
    seen: set[str] = set()
    for issue in issues or []:
        raw_code = getattr(issue, "code", None) or "unknown"
        key = normalize_issue_code(raw_code)
        if key in seen:
            continue
        seen.add(key)
        out.append(user_facing_issue(key))
    return out


def issues_for_match_result(result: "FaceResult") -> list[dict]:
    """UI de entrega: primero lo que dijo la API; si solo falló el match, mismatch."""
    issues = format_issues_for_api(result.issues)
    # Calidad que reportó el API (con max_yaw/pitch, bad_pose ya bloquea ok).
    quality_codes = {
        "no_face",
        "multiple_faces",
        "low_confidence",
        "face_too_small",
        "blurry",
        "too_dark",
        "too_bright",
        "low_resolution",
        "bad_pose",
        "invalid_image",
        "image_too_large",
        "unsupported_format",
        "embedding_dimension_mismatch",
    }
    has_quality = any(i["code"] in quality_codes for i in issues)
    if result.match_passed is False and not has_quality:
        if not any(i["code"] == "mismatch" for i in issues):
            issues.append(user_facing_issue("mismatch"))
    elif result.match_passed is False and has_quality:
        # Falló calidad y/o identidad: si no hay mismatch y el score falló, añádelo.
        if not any(i["code"] == "mismatch" for i in issues):
            # Solo si no hay bloqueantes de captura (cara ilegible): aún así
            # conviene decir que no se confirmó identidad cuando match_passed=false.
            if not any(
                i["code"] in {"no_face", "multiple_faces", "invalid_image"}
                for i in issues
            ):
                issues.append(user_facing_issue("mismatch"))
    return issues


@dataclass
class FaceResult:
    ok: bool
    issues: list[QualityIssue] = field(default_factory=list)
    embedding: Any = None
    det_score: float = 0.0
    face_ratio: float = 0.0
    laplacian_var: float = 0.0
    brightness: float = 0.0
    match_score: Optional[float] = None
    match_passed: Optional[bool] = None


def _face_url() -> str:
    return (getattr(settings, "WAREHOUSE_FACE_URL", "") or "").strip().rstrip("/")


def _face_api_key() -> str:
    return (getattr(settings, "WAREHOUSE_FACE_API_KEY", "") or "").strip()


def _backend_name() -> str:
    """remote | stub — nunca carga InsightFace en-proceso."""
    forced = (getattr(settings, "WAREHOUSE_FACE_BACKEND", "") or "").strip().lower()
    if forced == "stub":
        return "stub"
    if forced in {"remote", "api", "http"}:
        if not _face_url():
            raise FaceServiceError(
                "WAREHOUSE_FACE_BACKEND=remote pero falta WAREHOUSE_FACE_URL."
            )
        return "remote"
    if _face_url():
        return "remote"
    return "stub"


def decode_data_url(data_url: str) -> bytes:
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)


def _to_data_url(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _embedding_to_list(enrolled) -> list[float]:
    import array

    if enrolled is None:
        raise FaceServiceError("Embedding de enrolamiento inválido.")
    if isinstance(enrolled, (list, tuple, array.array)):
        return [float(x) for x in enrolled]
    if isinstance(enrolled, (bytes, bytearray, memoryview)):
        raw = bytes(enrolled)
        if len(raw) % 4:
            raise FaceServiceError("Embedding de enrolamiento inválido.")
        return [float(x) for x in array.array("f", raw)]
    try:
        import numpy as np

        arr = np.asarray(enrolled, dtype=np.float32).reshape(-1)
        return [float(x) for x in arr.tolist()]
    except Exception as exc:
        raise FaceServiceError("Embedding de enrolamiento inválido.") from exc


def _parse_issues(raw) -> list[QualityIssue]:
    """Conserva los códigos que manda el face-api (normalizados al README)."""
    issues: list[QualityIssue] = []
    for item in raw or []:
        if isinstance(item, dict):
            code = normalize_issue_code(str(item.get("code") or "unknown"))
        else:
            code = normalize_issue_code(str(item))
        # El message técnico del API se descarta; Rojoz traduce por código.
        issues.append(QualityIssue(code, ISSUE_MESSAGES.get(code, code)))
    return issues


def _remote_post(path: str, payload: dict, timeout: float = 30.0) -> dict:
    url = f"{_face_url()}{path}"
    key = _face_api_key()
    if not key:
        raise FaceServiceError(
            "Falta WAREHOUSE_FACE_API_KEY para el servicio facial."
        )
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Key": key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if exc.code == 401:
            raise FaceServiceError("API key del servicio facial rechazada.") from exc
        if exc.code >= 500:
            raise FaceServiceError(
                f"Servicio facial no disponible ({exc.code}). {detail}"
            ) from exc
        # 400: imagen inválida, etc.
        try:
            data = json.loads(detail) if detail else {}
            if isinstance(data, dict) and data.get("issues") is not None:
                return data
            if isinstance(data, dict) and data.get("detail"):
                raise FaceServiceError(str(data["detail"])) from exc
        except FaceServiceError:
            raise
        except Exception:
            pass
        raise FaceServiceError(
            f"Error del servicio facial ({exc.code}): {detail or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise FaceServiceError(
            f"No se pudo conectar al servicio facial: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise FaceServiceError("Timeout al llamar al servicio facial.") from exc


def _result_from_remote(data: dict, *, for_match: bool) -> FaceResult:
    issues = _parse_issues(data.get("issues"))
    emb = data.get("embedding")
    embedding = None
    if emb is not None:
        try:
            import numpy as np

            embedding = np.asarray(emb, dtype=np.float32)
        except Exception:
            embedding = emb

    # El API reporta face_ratio en % (ej. 18.4); Bodega históricamente usaba 0–1.
    face_ratio = float(data.get("face_ratio") or 0.0)
    if face_ratio > 1.0:
        face_ratio = face_ratio / 100.0

    result = FaceResult(
        ok=bool(data.get("ok")),
        issues=issues,
        embedding=embedding,
        det_score=float(data.get("det_score") or 0.0),
        face_ratio=face_ratio,
        laplacian_var=float(data.get("laplacian_var") or 0.0),
        brightness=float(data.get("brightness") or 0.0),
    )
    if for_match:
        score = data.get("match_score")
        passed = data.get("match_passed")
        result.match_score = float(score) if score is not None else None
        result.match_passed = bool(passed) if passed is not None else None
        if result.match_passed is False and not any(
            i.code == "mismatch" for i in result.issues
        ):
            thr = data.get("threshold")
            msg = ISSUE_MESSAGES["mismatch"]
            if result.match_score is not None and thr is not None:
                # Interno: se conserva el detalle técnico solo en logs si hace falta;
                # a la UI va el mensaje semántico vía format_issues_for_api.
                msg = ISSUE_MESSAGES["mismatch"]
            result.issues.append(QualityIssue("mismatch", msg))
            result.ok = False
    return result


def _pose_limits_payload(settings_obj: FaceSettings) -> dict:
    """Límites de pose para el face-api (activa modo estricto: bad_pose bloquea ok).

    Campos del servicio: max_yaw / max_pitch / max_roll (grados 0–90).
    Bodega solo configura yaw y pitch; con mandar cualquiera basta para estricto.
    """
    payload = {}
    yaw = getattr(settings_obj, "max_yaw_deg", None)
    pitch = getattr(settings_obj, "max_pitch_deg", None)
    if yaw is not None:
        payload["max_yaw"] = float(yaw)
    if pitch is not None:
        payload["max_pitch"] = float(pitch)
    return payload


def _analyze_remote_enroll(
    image_bytes: bytes, settings_obj: FaceSettings
) -> FaceResult:
    payload = {"image": _to_data_url(image_bytes)}
    payload.update(_pose_limits_payload(settings_obj))
    data = _remote_post("/v1/enroll", payload)
    return _result_from_remote(data, for_match=False)


def _analyze_remote_match(
    image_bytes: bytes,
    enrolled_embedding,
    settings_obj: FaceSettings,
) -> FaceResult:
    payload = {
        "image": _to_data_url(image_bytes),
        "embedding": _embedding_to_list(enrolled_embedding),
        "threshold": float(settings_obj.match_threshold),
    }
    payload.update(_pose_limits_payload(settings_obj))
    data = _remote_post("/v1/match", payload)
    return _result_from_remote(data, for_match=True)


def analyze_enrollment(
    image_bytes: bytes, settings_obj: Optional[FaceSettings] = None
) -> FaceResult:
    settings_obj = settings_obj or FaceSettings.get_solo()
    backend = _backend_name()
    if backend == "remote":
        return _analyze_remote_enroll(image_bytes, settings_obj)
    return _analyze_stub(image_bytes, settings_obj, enrolled=None)


def analyze_match(
    image_bytes: bytes,
    enrolled_embedding,
    settings_obj: Optional[FaceSettings] = None,
) -> FaceResult:
    settings_obj = settings_obj or FaceSettings.get_solo()
    backend = _backend_name()
    if backend == "remote":
        return _analyze_remote_match(
            image_bytes, enrolled_embedding, settings_obj
        )
    return _analyze_stub(image_bytes, settings_obj, enrolled=enrolled_embedding)


# ── Stub (tests / CI) ──────────────────────────────────────────


def _require_numpy_cv2():
    try:
        import numpy as np
        import cv2
    except ImportError as exc:
        raise FaceDependencyError(
            "Faltan dependencias del stub facial. Instala con: "
            "pip install numpy opencv-python-headless"
        ) from exc
    return np, cv2


def decode_image_bytes(data: bytes):
    np, cv2 = _require_numpy_cv2()
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("No se pudo decodificar la imagen.")
    return img


def _laplacian_var(gray) -> float:
    np, cv2 = _require_numpy_cv2()
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _brightness(gray) -> float:
    np, _cv2 = _require_numpy_cv2()
    return float(np.mean(gray))


def _quality_from_crop(crop_bgr, full_bgr, bbox, settings_obj: FaceSettings):
    np, cv2 = _require_numpy_cv2()

    issues: list[QualityIssue] = []
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h, w = full_bgr.shape[:2]
    area = max((x2 - x1) * (y2 - y1), 1)
    face_ratio = area / float(max(h * w, 1))
    if face_ratio < settings_obj.min_face_ratio:
        issues.append(
            QualityIssue(
                "face_too_small", ISSUE_MESSAGES["face_too_small"]
            )
        )

    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    lap = _laplacian_var(gray)
    if lap < settings_obj.min_laplacian_var:
        issues.append(QualityIssue("blurry", ISSUE_MESSAGES["blurry"]))

    bright = _brightness(gray)
    if bright < settings_obj.min_brightness:
        issues.append(QualityIssue("too_dark", ISSUE_MESSAGES["too_dark"]))
    if bright > settings_obj.max_brightness:
        issues.append(QualityIssue("too_bright", ISSUE_MESSAGES["too_bright"]))

    return issues, face_ratio, lap, bright


def _analyze_stub(image_bytes: bytes, settings_obj: FaceSettings, enrolled) -> FaceResult:
    """Dev/CI: center crop + hash embedding. No usar en producción."""
    import hashlib

    np, cv2 = _require_numpy_cv2()
    img = decode_image_bytes(image_bytes)

    h, w = img.shape[:2]
    side = int(min(h, w) * 0.4)
    cx, cy = w // 2, h // 2
    x1, y1 = max(0, cx - side // 2), max(0, cy - side // 2)
    x2, y2 = min(w, x1 + side), min(h, y1 + side)
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return FaceResult(
            ok=False, issues=[QualityIssue("no_face", ISSUE_MESSAGES["no_face"])]
        )

    issues, face_ratio, lap, bright = _quality_from_crop(
        crop, img, (x1, y1, x2, y2), settings_obj
    )
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    digest = hashlib.sha256(cv2.resize(gray, (32, 32)).tobytes()).digest()
    rng = np.random.RandomState(int.from_bytes(digest[:4], "little"))
    emb = rng.randn(512).astype(np.float32)
    emb /= np.linalg.norm(emb) + 1e-8

    result = FaceResult(
        ok=len(issues) == 0,
        issues=issues,
        embedding=emb,
        det_score=0.99,
        face_ratio=face_ratio,
        laplacian_var=lap,
        brightness=bright,
    )
    if enrolled is not None and result.ok:
        score = float(np.dot(emb, np.asarray(enrolled, dtype=np.float32)))
        result.match_score = score
        result.match_passed = score >= settings_obj.match_threshold
        if not result.match_passed:
            result.ok = False
            result.issues.append(
                QualityIssue(
                    "mismatch",
                    f"La cara no coincide con el enrolamiento (score {score:.2f}).",
                )
            )
    return result


def embedding_to_bytes(emb) -> bytes:
    np, _cv2 = _require_numpy_cv2()
    return np.asarray(emb, dtype=np.float32).tobytes()


def bytes_to_embedding(data: bytes):
    np, _cv2 = _require_numpy_cv2()
    return np.frombuffer(data, dtype=np.float32)


def file_to_bytes(uploaded) -> bytes:
    if hasattr(uploaded, "read"):
        data = uploaded.read()
        if hasattr(uploaded, "seek"):
            uploaded.seek(0)
        return data
    if isinstance(uploaded, (bytes, bytearray)):
        return bytes(uploaded)
    raise ValueError("Archivo de imagen no válido.")


def data_url_to_content_file(data_url: str, filename: str):
    from django.core.files.base import ContentFile

    raw = decode_data_url(data_url)
    return ContentFile(raw, name=filename)


def remote_health() -> dict:
    """GET /health del servicio (sin auth). Útil para diagnóstico."""
    base = _face_url()
    if not base:
        return {"status": "unconfigured"}
    req = urllib.request.Request(f"{base}/health", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
