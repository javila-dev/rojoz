"""Extracción de facturas de compra con OpenAI + matching local."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any, Optional

from users.models import IntegrationSettings
from warehouse.models import Item, Supplier, SupplierItemCode
from warehouse.services.numbers import parse_decimal


class InvoiceAIError(Exception):
    """Error recuperable de extracción / configuración."""


@dataclass
class ExtractedLine:
    description: str = ""
    quantity: Decimal = Decimal("0")
    unit_cost: Decimal = Decimal("0")
    sku: str = ""
    item: Optional[Item] = None
    confidence: str = "none"  # high | medium | none
    match_reason: str = ""


@dataclass
class ExtractedInvoice:
    supplier_nit: str = ""
    supplier_name: str = ""
    invoice_number: str = ""
    invoice_date: Optional[date] = None
    document_type: str = "INVOICE"  # INVOICE | REMISSION
    supplier: Optional[Supplier] = None
    lines: list[ExtractedLine] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    subtotal: Decimal = Decimal("0")
    iva_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    # none | already_included | applied_19 | unclear
    tax_mode: str = "none"
    tax_note: str = ""


IVA_RATE = Decimal("0.19")
IVA_FACTOR = Decimal("1.19")


def _dec(value) -> Decimal:
    return parse_decimal(value, default="0")


def _parse_date(raw) -> Optional[date]:
    if not raw:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    text = str(raw).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def match_supplier(nit: str = "", name: str = "") -> Optional[Supplier]:
    nit = (nit or "").strip()
    name = (name or "").strip()
    if nit:
        clean = re.sub(r"[^\d]", "", nit)
        qs = Supplier.objects.filter(is_active=True)
        for s in qs.exclude(nit=""):
            if re.sub(r"[^\d]", "", s.nit) == clean:
                return s
    if name:
        s = Supplier.objects.filter(is_active=True, name__iexact=name).first()
        if s:
            return s
        s = Supplier.objects.filter(is_active=True, name__icontains=name).first()
        if s:
            return s
    return None


def match_item_for_supplier(
    supplier: Optional[Supplier],
    supplier_sku: str = "",
    description: str = "",
) -> tuple[Optional[Item], str, str]:
    """
    Returns (item, confidence, reason).
    Order: supplier alias → internal SKU → name similarity.
    """
    sku_norm = SupplierItemCode.normalize_sku(supplier_sku)
    if supplier and sku_norm:
        alias = (
            SupplierItemCode.objects.select_related("item")
            .filter(supplier=supplier, supplier_sku=sku_norm, item__is_active=True)
            .first()
        )
        if alias:
            return alias.item, "high", "sku_proveedor"

    if sku_norm:
        item = Item.objects.filter(is_active=True, sku__iexact=sku_norm).first()
        if item:
            return item, "high", "sku_interno"

    desc = (description or "").strip()
    if len(desc) >= 3:
        best = None
        best_ratio = 0.0
        for item in Item.objects.filter(is_active=True).only("id", "sku", "name")[:500]:
            ratio = SequenceMatcher(None, desc.lower(), item.name.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best = item
        if best and best_ratio >= 0.82:
            return best, "medium", f"nombre:{best_ratio:.2f}"

    return None, "none", ""


def learn_supplier_sku(
    supplier: Supplier,
    supplier_sku: str,
    item: Item,
    unit_cost: Optional[Decimal] = None,
) -> Optional[SupplierItemCode]:
    sku_norm = SupplierItemCode.normalize_sku(supplier_sku)
    if not sku_norm:
        return None
    # No aprender textos largos / frases (suelen ser descripciones, no códigos).
    if len(sku_norm) > 40 or " " in sku_norm:
        return None
    defaults = {"item": item}
    if unit_cost is not None:
        defaults["last_unit_cost"] = unit_cost
    obj, _ = SupplierItemCode.objects.update_or_create(
        supplier=supplier,
        supplier_sku=sku_norm,
        defaults=defaults,
    )
    return obj


def _build_openai_client(api_key: str):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise InvoiceAIError(
            "Falta el paquete openai. Instálalo (warehouse/requirements-ai.txt)."
        ) from exc
    return OpenAI(api_key=api_key)


EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["INVOICE", "REMISSION"],
            "description": (
                "INVOICE = solo factura electrónica / DIAN (CUFE, QR DIAN, FE, etc.). "
                "REMISSION = remisión, nota de entrega, despacho, packing list, "
                "factura de talonario / preimpresa / manual / en bloque o cualquier "
                "soporte de entrega cuando la factura electrónica puede llegar después "
                "(a menudo acumulada a fin de mes)."
            ),
        },
        "supplier_nit": {"type": "string"},
        "supplier_name": {"type": "string"},
        "invoice_number": {
            "type": "string",
            "description": "Número del documento (factura o remisión).",
        },
        "invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
        "subtotal": {
            "type": "number",
            "description": "Subtotal antes de IVA si aparece; si no, 0",
        },
        "iva_amount": {
            "type": "number",
            "description": "Valor del IVA si aparece aparte; si no, 0",
        },
        "total": {
            "type": "number",
            "description": "Total a pagar del documento",
        },
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_cost": {
                        "type": "number",
                        "description": (
                            "Costo unitario TAL CUAL en la línea de detalle "
                            "(sin agregar IVA si en la factura el IVA va aparte)."
                        ),
                    },
                    "sku": {
                        "type": "string",
                        "description": (
                            "Código/referencia del producto en el documento (SKU del proveedor). "
                            "Vacío si no trae código; NUNCA copies la descripción aquí."
                        ),
                    },
                },
                "required": ["description", "quantity", "unit_cost", "sku"],
            },
        },
    },
    "required": [
        "document_type",
        "supplier_nit",
        "supplier_name",
        "invoice_number",
        "invoice_date",
        "subtotal",
        "iva_amount",
        "total",
        "lines",
    ],
}


SYSTEM_PROMPT = (
    "Eres un extractor de documentos de compra colombianos para una constructora "
    "(facturas electrónicas y remisiones / soportes de ingreso). "
    "Devuelve solo JSON válido según el schema. "
    "Clasifica document_type con esta regla de negocio: "
    "INVOICE solo si es factura electrónica DIAN "
    "(CUFE/cufe, código QR DIAN, leyenda de factura electrónica, FE, etc.). "
    "REMISSION en todos estos casos: remisión, nota de entrega, despacho, "
    "entrega de mercancía, packing list, y también factura de talonario, "
    "factura preimpresa, factura manual, factura en bloque/papel carbón "
    "u otro comprobante físico de papelería — aunque diga 'Factura' en el título. "
    "En esta empresa el talonario/preimpreso es soporte de ingreso (origen remisión) "
    "porque la factura electrónica a menudo llega acumulada a fin de mes. "
    "Si duda entre talonario/manual y electrónica, elige REMISSION. "
    "El campo sku es SOLO un código/referencia de producto que aparezca explícitamente "
    "en la línea (código del proveedor). "
    "Si la línea no trae código, deja sku como cadena vacía. "
    "Nunca pongas la descripción, nombre del producto ni texto libre en sku. "
    "unit_cost debe ser el valor unitario de la línea de detalle, sin inventar IVA. "
    "Si el documento muestra subtotal, IVA y total aparte, extráelos en esos campos "
    "(en remisiones el IVA a menudo no aparece: usa 0). "
    "Fechas en YYYY-MM-DD. Cantidades y montos numéricos sin símbolo de moneda. "
    "Si un dato no aparece, usa cadena vacía o 0."
)


def _file_to_data_url(content: bytes, content_type: str) -> str:
    b64 = base64.b64encode(content).decode("ascii")
    return f"data:{content_type};base64,{b64}"


def call_openai_extract(
    content: bytes,
    content_type: str,
    *,
    api_key: str,
    model: str = "gpt-5.6-terra",
) -> dict:
    import io

    if not content:
        raise InvoiceAIError("El archivo de factura está vacío.")
    client = _build_openai_client(api_key)
    ctype = (content_type or "application/octet-stream").split(";")[0].strip().lower()

    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Extrae los datos de esta factura de compra. "
                "Incluye todas las líneas de productos/materiales."
            ),
        }
    ]

    uploaded_file_id = None
    try:
        if ctype.startswith("image/"):
            data_url = _file_to_data_url(content, ctype)
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_url, "detail": "high"},
                }
            )
        elif ctype == "application/pdf":
            uploaded = client.files.create(
                file=("factura.pdf", io.BytesIO(content), "application/pdf"),
                purpose="user_data",
            )
            uploaded_file_id = uploaded.id
            # Chat Completions no acepta PDF como image_url; usar Responses API
            try:
                resp = client.responses.create(
                    model=model or "gpt-5.6-terra",
                    temperature=0,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "purchase_invoice",
                            "strict": True,
                            "schema": EXTRACT_SCHEMA,
                        }
                    },
                    input=[
                        {
                            "role": "system",
                            "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_file", "file_id": uploaded_file_id},
                                {
                                    "type": "input_text",
                                    "text": (
                                        "Extrae los datos de esta factura de compra. "
                                        "Incluye todas las líneas de productos/materiales."
                                    ),
                                },
                            ],
                        },
                    ],
                )
                text = getattr(resp, "output_text", None) or ""
                if not text and getattr(resp, "output", None):
                    # Fallback parse
                    for item in resp.output:
                        for part in getattr(item, "content", []) or []:
                            if getattr(part, "type", "") == "output_text":
                                text += getattr(part, "text", "") or ""
                if not text:
                    raise InvoiceAIError("OpenAI no devolvió datos del PDF.")
                return json.loads(text)
            finally:
                if uploaded_file_id:
                    try:
                        client.files.delete(uploaded_file_id)
                    except Exception:
                        pass
        else:
            raise InvoiceAIError(
                "Formato no soportado. Usa PDF, JPG, PNG o WEBP."
            )

        resp = client.chat.completions.create(
            model=model or "gpt-5.6-terra",
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "purchase_invoice",
                    "strict": True,
                    "schema": EXTRACT_SCHEMA,
                },
            },
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except InvoiceAIError:
        raise
    except Exception as exc:
        raise InvoiceAIError(f"OpenAI no pudo leer la factura: {exc}") from exc

    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise InvoiceAIError("OpenAI no devolvió datos.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvoiceAIError("Respuesta de OpenAI no es JSON válida.") from exc


def extract_invoice_from_file(
    content: bytes,
    content_type: str,
    *,
    settings_obj: Optional[IntegrationSettings] = None,
) -> ExtractedInvoice:
    settings_obj = settings_obj or IntegrationSettings.get_solo()
    if not settings_obj.openai_ready:
        raise InvoiceAIError(
            "OpenAI no está configurado. Actívalo e ingresa la API key en Integraciones."
        )

    raw = call_openai_extract(
        content,
        content_type,
        api_key=settings_obj.openai_api_key.strip(),
        model=(settings_obj.openai_model or "gpt-5.6-terra").strip(),
    )
    return build_extracted_from_raw(raw)


def _lines_subtotal(lines: list[ExtractedLine]) -> Decimal:
    total = Decimal("0")
    for line in lines:
        total += (line.quantity or Decimal("0")) * (line.unit_cost or Decimal("0"))
    return total.quantize(Decimal("0.01"))


# Diferencias de redondeo aceptables al reconciliar totales (COP).
TAX_AMOUNT_TOLERANCE = Decimal("1000")


def _approx_equal(
    a: Decimal, b: Decimal, *, tol: Decimal = TAX_AMOUNT_TOLERANCE
) -> bool:
    """Igualdad con tolerancia fija en pesos (hasta $1000 por aproximaciones)."""
    return abs(a - b) <= tol


def reconcile_line_taxes(extracted: ExtractedInvoice) -> ExtractedInvoice:
    """
    Compara suma de líneas vs total/subtotal/IVA de la factura.
    Si las líneas parecen netas y el total ≈ suma * 1.19, aplica IVA 19% al unit_cost
    para que el costo de inventario refleje el total pagado.
    """
    sum_lines = _lines_subtotal(extracted.lines)
    subtotal = extracted.subtotal or Decimal("0")
    iva = extracted.iva_amount or Decimal("0")
    total = extracted.total or Decimal("0")

    if sum_lines <= 0:
        extracted.tax_mode = "none"
        extracted.tax_note = ""
        return extracted

    # Caso 1: total ≈ suma de líneas → precios ya finales (con IVA o sin IVA en factura).
    if total > 0 and _approx_equal(total, sum_lines):
        extracted.tax_mode = "already_included"
        extracted.tax_note = (
            "El total de la factura coincide con la suma de ítems; "
            "no se agregó IVA a los costos."
        )
        return extracted

    # Caso 2: hay IVA explícito / total ≈ suma*1.19 / subtotal≈suma y total≈suma+iva
    looks_net = False
    if total > 0 and _approx_equal(total, (sum_lines * IVA_FACTOR).quantize(Decimal("0.01"))):
        looks_net = True
    elif (
        subtotal > 0
        and _approx_equal(subtotal, sum_lines)
        and iva > 0
        and (
            _approx_equal(iva, (sum_lines * IVA_RATE).quantize(Decimal("0.01")))
            or (total > 0 and _approx_equal(total, subtotal + iva))
        )
    ):
        looks_net = True
    elif (
        iva > 0
        and total > 0
        and _approx_equal(total, sum_lines + iva)
        and _approx_equal(iva, (sum_lines * IVA_RATE).quantize(Decimal("0.01")))
    ):
        looks_net = True

    if looks_net:
        for line in extracted.lines:
            if line.unit_cost and line.unit_cost > 0:
                # COP: redondear cada costo unitario al peso entero (sin centavos).
                line.unit_cost = (line.unit_cost * IVA_FACTOR).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
        extracted.tax_mode = "applied_19"
        extracted.tax_note = (
            "Se detectó IVA 19% aparte del detalle; "
            "los costos unitarios se ajustaron ×1.19 y se redondearon al peso."
        )
        return extracted

    extracted.tax_mode = "unclear"
    if total > 0:
        extracted.tax_note = (
            f"Revisa IVA: suma ítems ${sum_lines:,.2f} vs total factura ${total:,.2f}"
            + (f" (IVA leído ${iva:,.2f})" if iva > 0 else "")
            + ". No se ajustaron costos automáticamente."
        )
    else:
        extracted.tax_mode = "none"
        extracted.tax_note = ""
    return extracted


def _classify_document_type(raw: dict) -> str:
    """
    INVOICE = factura electrónica DIAN.
    REMISSION = remisión / entrega / talonario / preimpresa / manual.
    """
    doc_raw = (raw.get("document_type") or "").strip()
    blob = " ".join(
        str(raw.get(k) or "")
        for k in ("document_type", "invoice_number", "supplier_name", "tax_note")
    ).lower()
    # Señales de soporte físico / remisión (prioridad sobre el literal "factura").
    remission_keys = (
        "remis",
        "nota de entrega",
        "despacho",
        "packing",
        "talonario",
        "preimpres",
        "factura manual",
        "manual /",
        "en bloque",
        "papel carbon",
        "papel carbón",
        "copia carbon",
        "copia carbón",
    )
    if any(k in blob for k in remission_keys):
        return "REMISSION"
    doc_up = doc_raw.upper().replace("Í", "I").replace("Ó", "O")
    if doc_up in ("REMISSION", "REMISION"):
        return "REMISSION"
    if doc_up in ("INVOICE", "FACTURA", "FV", "FE"):
        return "INVOICE"
    if "cufe" in blob or "factura electron" in blob or "factura electrón" in blob:
        return "INVOICE"
    # Valor desconocido no vacío: tratar como remisión (talonario ambiguo).
    return "REMISSION" if doc_raw else "INVOICE"


def build_extracted_from_raw(raw: dict) -> ExtractedInvoice:
    """Aplica matching local (útil también en tests sin OpenAI)."""
    supplier = match_supplier(
        nit=raw.get("supplier_nit") or "",
        name=raw.get("supplier_name") or "",
    )
    document_type = _classify_document_type(raw)
    result = ExtractedInvoice(
        supplier_nit=raw.get("supplier_nit") or "",
        supplier_name=raw.get("supplier_name") or "",
        invoice_number=(raw.get("invoice_number") or "").strip(),
        invoice_date=_parse_date(raw.get("invoice_date")),
        document_type=document_type,
        supplier=supplier,
        raw=raw,
        subtotal=_dec(raw.get("subtotal")),
        iva_amount=_dec(raw.get("iva_amount")),
        total=_dec(raw.get("total")),
    )
    for row in raw.get("lines") or []:
        sku = (row.get("sku") or "").strip()
        desc = (row.get("description") or "").strip()
        # Evitar que la IA meta la descripción como "SKU" (rompe aprendizaje proveedor+sku).
        if sku and desc and sku.casefold() == desc.casefold():
            sku = ""
        elif sku and len(sku) > 40 and " " in sku:
            sku = ""
        qty = _dec(row.get("quantity"))
        cost = _dec(row.get("unit_cost"))
        item, conf, reason = match_item_for_supplier(supplier, sku, desc)
        result.lines.append(
            ExtractedLine(
                description=desc,
                quantity=qty,
                unit_cost=cost,
                sku=sku,
                item=item,
                confidence=conf,
                match_reason=reason,
            )
        )
    return reconcile_line_taxes(result)


def apply_extracted_to_invoice(invoice, extracted: ExtractedInvoice) -> dict:
    """
    Aplica extracción a una factura en borrador:
    actualiza cabecera, crea líneas con match y guarda pendientes en JSON.
    Reemplaza líneas y pendientes previas (re-lectura limpia).
    """
    from warehouse.models import PurchaseInvoiceLine

    if extracted.supplier:
        invoice.supplier = extracted.supplier
    if extracted.invoice_number:
        invoice.invoice_number = extracted.invoice_number
    if extracted.invoice_date:
        invoice.invoice_date = extracted.invoice_date
    if extracted.document_type in (
        invoice.DocumentType.INVOICE,
        invoice.DocumentType.REMISSION,
    ):
        invoice.document_type = extracted.document_type

    invoice.lines.all().delete()

    matched = 0
    pending: list[dict] = []
    for line in extracted.lines:
        if line.item is not None and line.quantity > 0:
            PurchaseInvoiceLine.objects.create(
                invoice=invoice,
                item=line.item,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                description=(line.description or "").strip(),
                supplier_sku=(line.sku or "").strip(),
            )
            if invoice.supplier_id and line.sku:
                learn_supplier_sku(
                    invoice.supplier, line.sku, line.item, line.unit_cost
                )
            matched += 1
        else:
            pending.append(
                {
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_cost": str(line.unit_cost),
                    "sku": line.sku,
                    "confidence": line.confidence,
                    "match_reason": line.match_reason,
                }
            )

    invoice.ai_pending_lines = pending
    invoice.save()
    return {
        "matched": matched,
        "pending": len(pending),
        "total": len(extracted.lines),
    }
