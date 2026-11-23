"""Verificación visual de evidencia de custodia con Gemini.

Flujo:
1) Normalizar el nombre del ítem a etiqueta genérica (sin marca/medida).
2) Preguntar a visión si la foto parece ese objeto genérico.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Optional

from django.utils import timezone

from users.models import IntegrationSettings
from warehouse.models import Item

logger = logging.getLogger(__name__)


class ItemVisionError(Exception):
    """Error recuperable de Gemini / configuración."""


@dataclass
class VisionCheckResult:
    ready: bool
    accepted: bool
    needs_override: bool
    verdict: str  # yes | no | uncertain | error | skipped | unknown_label
    confidence: Optional[float]
    label: str
    reason: str
    raw_name: str = ""
    min_confidence: int = 65
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _require_genai():
    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise ItemVisionError(
            "Falta el paquete google-generativeai. "
            "Instálalo (warehouse/requirements-ai.txt)."
        ) from exc
    return genai


def _extract_json(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _genai_json(
    *,
    api_key: str,
    model: str,
    prompt: str,
    image_bytes: bytes | None = None,
    mime: str = "image/jpeg",
) -> dict:
    genai = _require_genai()
    genai.configure(api_key=api_key)
    gm = genai.GenerativeModel(
        model_name=model or "gemini-2.5-flash",
        generation_config={
            "temperature": 0.1,
            "response_mime_type": "application/json",
        },
    )
    parts: list = [prompt]
    if image_bytes:
        parts.append({"mime_type": mime or "image/jpeg", "data": image_bytes})
    try:
        resp = gm.generate_content(parts)
    except Exception as exc:
        logger.exception("Gemini generate_content failed")
        raise ItemVisionError(f"Gemini no respondió: {exc}") from exc
    text = ""
    try:
        text = (resp.text or "").strip()
    except Exception:
        text = ""
    data = _extract_json(text)
    if not data:
        raise ItemVisionError("Gemini devolvió una respuesta ilegible.")
    return data


NORMALIZE_PROMPT = """Eres un ayudante de bodega de ferretería y construcción en Colombia.
Te dan el nombre de un ítem del inventario. Devuelve SOLO JSON:

{{"label":"<nombre genérico>", "ok":true}}

Reglas para label:
- Solo el tipo de objeto, lo más genérico posible.
- Sin marca, modelo, SKU, medidas, pulgadas, voltaje, color ni estado.
- 1 a 4 palabras en español, minúsculas.
- Ejemplos:
  "TALADRO PERCUTOR BOSCH 1/2" → "taladro"
  "CASCO SEGURIDAD AMARILLO" → "casco de seguridad"
  "DISCO CORTE METAL 4.5" → "disco de corte"
  "GUANTES CARNZA T9" → "guantes"

Si no puedes deducir un objeto concreto:
{{"label":"", "ok":false}}
"""


VISION_PROMPT = """Eres un control de bodega. Mira la foto y responde si se ve el objeto indicado.

Objeto buscado (genérico): "{label}"

Responde SOLO JSON:
{{
  "match": "yes" | "no" | "uncertain",
  "confidence": <número 0 a 100>,
  "reason": "<una frase corta en español>"
}}

Reglas:
- "yes" solo si el objeto principal de la foto es claramente ese tipo de cosa.
- No exijas marca, modelo ni estado perfecto.
- Si la foto está vacía, borrosa, o no se entiende → "uncertain".
- Si se ve otra cosa distinta → "no".
- confidence es tu certeza (0-100).
"""


def normalize_item_label(
    raw_name: str,
    *,
    settings_obj: Optional[IntegrationSettings] = None,
) -> str:
    """Normaliza nombre de catálogo → etiqueta genérica. Vacío si no sabe."""
    settings_obj = settings_obj or IntegrationSettings.get_solo()
    if not settings_obj.gemini_ready:
        raise ItemVisionError("Gemini no está configurado.")
    name = " ".join((raw_name or "").split()).strip()
    if not name:
        return ""
    data = _genai_json(
        api_key=settings_obj.gemini_api_key.strip(),
        model=(settings_obj.gemini_model or "gemini-2.5-flash").strip(),
        prompt=NORMALIZE_PROMPT + f'\nNombre del ítem: "{name}"\n',
    )
    if not data.get("ok"):
        return ""
    label = " ".join(str(data.get("label") or "").split()).strip().lower()
    if not label or label in {"unknown", "desconocido", "n/a", "na"}:
        return ""
    return label[:80]


def get_or_create_item_label(
    item: Item,
    *,
    settings_obj: Optional[IntegrationSettings] = None,
    force: bool = False,
) -> str:
    """Usa cache en el ítem; regenera si cambió el nombre."""
    settings_obj = settings_obj or IntegrationSettings.get_solo()
    current_name = (item.name or "").strip()
    cached = (getattr(item, "vision_label", "") or "").strip()
    cached_for = (getattr(item, "vision_label_for_name", "") or "").strip()
    if (
        not force
        and cached
        and cached_for
        and cached_for.upper() == current_name.upper()
    ):
        return cached
    label = normalize_item_label(current_name, settings_obj=settings_obj)
    item.vision_label = label
    item.vision_label_for_name = current_name[:40]
    item.save(update_fields=["vision_label", "vision_label_for_name"])
    return label


def verify_item_photo(
    *,
    item: Item,
    image_bytes: bytes,
    settings_obj: Optional[IntegrationSettings] = None,
    mime: str = "image/jpeg",
) -> VisionCheckResult:
    """Normaliza + visión. Si Gemini no está listo → skipped (override permitido)."""
    settings_obj = settings_obj or IntegrationSettings.get_solo()
    raw_name = (item.name or "").strip()
    min_conf = int(settings_obj.gemini_min_confidence or 65)

    if not settings_obj.gemini_ready:
        return VisionCheckResult(
            ready=False,
            accepted=False,
            needs_override=True,
            verdict="skipped",
            confidence=None,
            label="",
            reason="Gemini no está activo; el almacenista debe confirmar la foto.",
            raw_name=raw_name,
            min_confidence=min_conf,
        )

    if not image_bytes:
        return VisionCheckResult(
            ready=True,
            accepted=False,
            needs_override=True,
            verdict="error",
            confidence=None,
            label="",
            reason="Falta la foto de evidencia.",
            raw_name=raw_name,
            min_confidence=min_conf,
            error="missing_photo",
        )

    try:
        label = get_or_create_item_label(item, settings_obj=settings_obj)
    except ItemVisionError as exc:
        return VisionCheckResult(
            ready=True,
            accepted=False,
            needs_override=True,
            verdict="error",
            confidence=None,
            label="",
            reason=str(exc),
            raw_name=raw_name,
            min_confidence=min_conf,
            error="normalize_failed",
        )

    if not label:
        return VisionCheckResult(
            ready=True,
            accepted=False,
            needs_override=True,
            verdict="unknown_label",
            confidence=None,
            label="",
            reason=(
                "No se pudo sacar un nombre genérico del ítem; "
                "confirma tú si la foto es correcta."
            ),
            raw_name=raw_name,
            min_confidence=min_conf,
        )

    try:
        data = _genai_json(
            api_key=settings_obj.gemini_api_key.strip(),
            model=(settings_obj.gemini_model or "gemini-2.5-flash").strip(),
            prompt=VISION_PROMPT.format(label=label),
            image_bytes=image_bytes,
            mime=mime,
        )
    except ItemVisionError as exc:
        return VisionCheckResult(
            ready=True,
            accepted=False,
            needs_override=True,
            verdict="error",
            confidence=None,
            label=label,
            reason=str(exc),
            raw_name=raw_name,
            min_confidence=min_conf,
            error="vision_failed",
        )

    match = str(data.get("match") or "uncertain").strip().lower()
    if match not in {"yes", "no", "uncertain"}:
        match = "uncertain"
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None:
        confidence = max(0.0, min(100.0, confidence))
    reason = " ".join(str(data.get("reason") or "").split()).strip()[:240]

    accepted = match == "yes" and (
        confidence is None or confidence >= min_conf
    )
    if match == "yes" and confidence is not None and confidence < min_conf:
        match = "uncertain"
        if not reason:
            reason = f"Certeza {confidence:.0f}% por debajo del mínimo ({min_conf}%)."
        needs_override = True
    else:
        needs_override = not accepted

    if not reason:
        if match == "yes":
            reason = "La foto parece corresponder al ítem."
        elif match == "no":
            reason = "La foto no parece el ítem indicado."
        else:
            reason = "No hay certeza suficiente; revisa la foto."

    return VisionCheckResult(
        ready=True,
        accepted=accepted,
        needs_override=needs_override,
        verdict=match if accepted or match != "yes" else "yes",
        confidence=confidence,
        label=label,
        reason=reason,
        raw_name=raw_name,
        min_confidence=min_conf,
    )


def apply_vision_to_instance(obj, result: VisionCheckResult, user=None, override=False):
    """Copia resultado de visión a IssueLine / ReturnLine."""
    obj.vision_label = (result.label or "")[:80]
    obj.vision_verdict = (result.verdict or "skipped")[:20]
    obj.vision_confidence = result.confidence
    obj.vision_reason = (result.reason or "")[:255]
    obj.vision_override = bool(override)
    obj.vision_checked_at = timezone.now()
    if override and user is not None:
        obj.vision_override_by = user
    elif not override:
        obj.vision_override_by = None
