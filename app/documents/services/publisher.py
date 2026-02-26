from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from documents.models import TemplateStatus

def get_templates_base_dir() -> Path:
    base_dir = getattr(settings, "DOCUMENTS_TEMPLATES_BASE_DIR", None)
    if base_dir:
        return Path(base_dir)
    return Path(settings.BASE_DIR) / "templates" / "generated"


def validate_target_path(target_path: str) -> str:
    if not target_path:
        raise ValidationError("La ruta de publicación es requerida.")

    path = Path(target_path)
    if path.is_absolute():
        raise ValidationError("La ruta de publicación debe ser relativa.")

    if ".." in path.parts:
        raise ValidationError("La ruta de publicación no puede contener '..'.")

    if not target_path.endswith(".html"):
        raise ValidationError("La ruta de publicación debe terminar en .html.")

    return target_path


def resolve_target_path(target_path: str) -> Path:
    base_dir = get_templates_base_dir()
    return base_dir / target_path


def build_published_html(template) -> str:
    """
    Construye el HTML final para publicar.
    NOTA: Las transformaciones de WeasyPrint ya se aplican al guardar en el editor,
    por lo que template.html_content y template.css_content ya están normalizados.
    """
    page_css = template.get_page_css()
    css = f"{page_css}\n{template.css_content}".strip()
    if css:
        css_block = f"<style>\n{css}\n</style>"
    else:
        css_block = ""

    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head>\n"
        "  <meta charset=\"UTF-8\">\n"
        f"{css_block}\n"
        "</head>\n"
        "<body>\n"
        f"{template.html_content}\n"
        "</body>\n"
        "</html>\n"
    )


def publish_template(template) -> Path:
    target_path = validate_target_path(template.target_path)
    abs_path = resolve_target_path(target_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(build_published_html(template), encoding="utf-8")
    template.status = TemplateStatus.PUBLISHED
    template.published_at = timezone.now()
    template.save(update_fields=["status", "published_at"])
    return abs_path
