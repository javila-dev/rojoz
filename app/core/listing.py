"""Utilidades comunes para las vistas de listado: búsqueda, filtros y paginación.

Convención de parámetros GET en todos los listados:
- ``q``: búsqueda libre sobre los campos principales.
- ``page``: número de página.
- Filtros específicos con nombres en español (``estado``, ``proyecto``, ``desde``, ``hasta``...).

La plantilla ``partials/pagination.html`` conserva todos los filtros al cambiar de página.
"""
from datetime import date
from functools import reduce
import operator

from django.core.paginator import Paginator
from django.db.models import Q

DEFAULT_PAGE_SIZE = 25


def paginate(request, queryset, per_page=DEFAULT_PAGE_SIZE):
    """Devuelve la página pedida en ``?page=`` (la última si se pasa de rango)."""
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


def search(queryset, query, fields):
    """Filtra ``queryset`` por ``query`` (icontains) en cualquiera de ``fields``.

    Cada palabra debe aparecer en alguno de los campos, así "ana gómez" encuentra
    "Ana María Gómez". Aplica ``distinct()`` porque los campos pueden cruzar relaciones.
    """
    query = (query or "").strip()
    if not query:
        return queryset
    for term in query.split():
        condition = reduce(operator.or_, (Q(**{f"{field}__icontains": term}) for field in fields))
        queryset = queryset.filter(condition)
    return queryset.distinct()


def parse_date(value):
    """Fecha ISO (AAAA-MM-DD) del formulario, o ``None`` si viene vacía o inválida."""
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None
