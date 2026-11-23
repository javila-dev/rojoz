"""Parseo y formato de cantidades/decimales (soporta formato CO y EN)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError

# Unidades que solo admiten cantidades enteras.
INTEGER_UNITS = frozenset({"UND", "CAJA", "PAR", "ROLLO"})
# Unidades que admiten decimales.
DECIMAL_UNITS = frozenset({"KG", "M", "M2", "L"})


def unit_allows_decimals(unit: str | None) -> bool:
    """True si la unidad permite fracciones (kg, m, litro…)."""
    return (unit or "").upper() in DECIMAL_UNITS


def parse_decimal(value: Any, default: str | Decimal = "0") -> Decimal:
    """Convierte texto/número a Decimal.

    Acepta:
    - 9.47 / 9,47
    - 1.234,56 (miles con punto, decimal coma)
    - 1,234.56 (miles con coma, decimal punto)
    """
    if isinstance(default, Decimal):
        fallback = default
    else:
        fallback = Decimal(str(default or "0"))
    if value is None or value == "":
        return fallback
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))

    text = str(value).strip().replace(" ", "").replace("\u00a0", "")
    if not text:
        return fallback

    negative = text.startswith("-")
    if negative:
        text = text[1:]

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            # 1.234,56
            text = text.replace(".", "").replace(",", ".")
        else:
            # 1,234.56
            text = text.replace(",", "")
    elif "," in text:
        # Solo coma → separador decimal (es-CO)
        text = text.replace(",", ".")

    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError):
        return fallback
    return -result if negative else result


def quantize_money(value: Any) -> Decimal:
    return parse_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def quantize_qty(value: Any) -> Decimal:
    return parse_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_qty(value: Any) -> str:
    """Texto estable para inputs type=number (punto decimal, sin ceros inútiles)."""
    qty = quantize_qty(value)
    if qty == qty.to_integral():
        return format(qty.quantize(Decimal("1")), "f")
    text = format(qty, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def normalize_quantity_for_unit(
    value: Any,
    unit: str | None,
    *,
    item_label: str = "",
    allow_negative: bool = False,
) -> Decimal:
    """Normaliza y valida cantidad según la unidad del ítem.

    - UND/CAJA/PAR/ROLLO → entero obligatorio
    - KG/M/M2/L → hasta 2 decimales
    """
    qty = quantize_qty(value)
    if qty == 0:
        return qty
    if not allow_negative and qty < 0:
        label = item_label or "este ítem"
        raise ValidationError(f"La cantidad de {label} debe ser mayor que cero.")
    if not unit_allows_decimals(unit):
        # Comparar valor absoluto (ajustes COUNT_CORR pueden ser negativos).
        if abs(qty) != abs(qty).to_integral_value():
            label = item_label or "este ítem"
            unit_txt = (unit or "UND").upper()
            raise ValidationError(
                f"La cantidad de {label} debe ser entera "
                f"(unidad {unit_txt}; no se permiten decimales)."
            )
        return qty.to_integral_value()
    return qty


def qty_input_step(unit: str | None) -> str:
    return "0.01" if unit_allows_decimals(unit) else "1"
