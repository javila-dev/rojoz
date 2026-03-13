import json
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.apps import apps as django_apps
from django.db import models as dj_models
from django.conf import settings
import html as html_lib
import re
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile
import hashlib
from django.views.decorators.http import require_http_methods
from django.contrib import messages

from .models import (
    AssetCategory,
    TemplateAsset,
    PDFTemplate,
    TemplateVersion,
    PageSize,
    Orientation,
    TemplateContextAlias,
)
from .forms import AssetCategoryForm, TemplateAssetForm, PDFTemplateForm
from .services.publisher import publish_template


# =============================================================================
# WEASYPRINT NORMALIZATION FUNCTIONS
# =============================================================================

def _flatten_media_queries(css: str) -> str:
    """Aplana media queries para WeasyPrint (que no las soporta bien)."""
    # Estrategia: contar llaves para encontrar el cierre correcto del media query
    import re

    pattern = r"@media\s*\([^)]+\)\s*\{"

    result = []
    pos = 0

    for match in re.finditer(pattern, css, flags=re.IGNORECASE):
        # Agregar todo antes del media query
        result.append(css[pos:match.start()])

        # Buscar el cierre del media query contando llaves
        start = match.end()
        depth = 1
        i = start

        while i < len(css) and depth > 0:
            if css[i] == '{':
                depth += 1
            elif css[i] == '}':
                depth -= 1
            i += 1

        # Extraer el contenido del media query (sin las llaves externas)
        content = css[start:i-1] if depth == 0 else css[start:]
        result.append(content)

        pos = i

    # Agregar el resto del CSS
    result.append(css[pos:])

    return ''.join(result)


def _clean_malformed_css(css: str) -> str:
    """Limpia CSS malformado con anidaciones inválidas."""
    # Detectar y corregir selectores anidados inválidos como #icra{...#ialxl{...}}
    # Esto ocurre cuando GrapesJS genera CSS mal formado

    # Patrón para encontrar selectores anidados: #id{props;#id2{props;}}
    pattern = r'(#[\w-]+)\{([^{}]*?)(#[\w-]+)\{'

    # Separar selectores anidados
    prev_css = ""
    while prev_css != css:
        prev_css = css
        css = re.sub(pattern, r'\1{\2}\3{', css)

    # Limpiar llaves sueltas al final
    css = re.sub(r'\}+\s*$', '', css)

    return css


def _normalize_asset_urls(html: str) -> str:
    """Normaliza URLs de assets para que apunten a S3 en lugar de localhost."""
    endpoint = getattr(settings, "AWS_S3_ENDPOINT_URL", "")
    if not endpoint:
        return html
    endpoint = endpoint.rstrip("/") + "/"
    html = html.replace("https://s3.2asoft.tech/", endpoint)
    html = html.replace("http://s3.2asoft.tech/", endpoint)
    return html


def _denormalize_asset_urls_for_browser(html: str) -> str:
    """Convierte URLs internas (minio:9000) a URLs accesibles desde el navegador."""
    # Convertir URLs internas a URLs públicas de minio
    # Esto es para mostrar las imágenes en el editor GrapesJS
    html = html.replace("http://minio:9000/", "https://s3.2asoft.tech/")
    html = html.replace("https://minio:9000/", "https://s3.2asoft.tech/")
    return html


def _normalize_css_for_weasyprint(css: str) -> str:
    """Normaliza CSS para que se vea igual en WeasyPrint que en el editor."""
    # Limpiar CSS malformado primero
    css = _clean_malformed_css(css)

    # Aplanar media queries
    css = _flatten_media_queries(css)

    # Reemplazar text-align:start por text-align:left (pero preservar justify)
    css = css.replace("text-align:start", "text-align:left")

    # Agregar fixes de WeasyPrint para tablas (sin sobrescribir text-align:justify)
    css += "\n/* WeasyPrint fixes */\n"
    css += "table{table-layout:fixed;width:100%;}td,th{vertical-align:top;}"
    css += "#ilsi{display:table !important;width:100% !important;color:#000 !important;}"
    css += "#ilsi tr{display:table-row !important;}#ilsi td{display:table-cell !important;}"

    return css


def _normalize_html_structure(html: str) -> str:
    """Normaliza la estructura HTML eliminando body duplicados."""
    # Buscar body duplicados y limpiarlos
    body = re.sub(r"</?body[^>]*>", "", html, flags=re.IGNORECASE)
    return body


def _remove_grapesjs_placeholders(html: str) -> str:
    """Elimina texto placeholder de GrapesJS (como 'Celda' en las tablas)."""
    # Eliminar el texto "Celda" que aparece después de las imágenes y antes de </td>
    # Patrón: busca "Celda" entre > y </td>, opcionalmente con espacios
    html = re.sub(r'(/>)\s*Celda\s*(</td>)', r'\1\2', html, flags=re.IGNORECASE)

    # También eliminar "Celda" que aparezca después de otros tags de cierre y antes de </td>
    html = re.sub(r'(</[^>]+>)\s*Celda\s*(</td>)', r'\1\2', html, flags=re.IGNORECASE)

    # Eliminar "Celda" si es el único contenido de una celda
    html = re.sub(r'(<td[^>]*>)\s*Celda\s*(</td>)', r'\1\2', html, flags=re.IGNORECASE)

    # Eliminar placeholders "Columna 1", "Columna 2", etc. en headers y celdas
    html = re.sub(r'(<th[^>]*>)\s*Columna\s*\d+\s*(</th>)', r'\1\2', html, flags=re.IGNORECASE)
    html = re.sub(r'(<td[^>]*>)\s*Columna\s*\d+\s*(</td>)', r'\1\2', html, flags=re.IGNORECASE)

    return html


def _unescape_django_templates(html: str) -> str:
    """Des-escapa el contenido HTML dentro de los wrappers de templates Django."""
    import html as html_module

    # Buscar todos los divs con class="django-template-wrapper"
    pattern = r'(<div[^>]*class="[^"]*django-template-wrapper[^"]*"[^>]*>)(.*?)(</div>)'

    def unescape_content(match):
        opening_tag = match.group(1)
        content = match.group(2)
        closing_tag = match.group(3)

        # Des-escapar entidades HTML en el contenido (&lt; → <, &gt; → >, etc.)
        unescaped_content = html_module.unescape(content)

        return opening_tag + unescaped_content + closing_tag

    return re.sub(pattern, unescape_content, html, flags=re.DOTALL | re.IGNORECASE)


# =============================================================================
# INDEX
# =============================================================================

def index(request):
    """Página principal del módulo de documentos."""
    templates_count = PDFTemplate.objects.count()
    assets_count = TemplateAsset.objects.count()

    recent_templates = PDFTemplate.objects.order_by("-updated_at")[:5]

    return render(request, "documents/index.html", {
        "templates_count": templates_count,
        "assets_count": assets_count,
        "recent_templates": recent_templates,
    })


# =============================================================================
# ASSETS
# =============================================================================

def asset_list(request):
    """Lista de assets agrupados por categoría."""
    categories = AssetCategory.objects.prefetch_related("assets").all()
    assets = TemplateAsset.objects.select_related("category").all()
    return render(request, "documents/asset_list.html", {
        "categories": categories,
        "assets": assets,
    })


def asset_create(request):
    """Crear nuevo asset."""
    if request.method == "POST":
        form = TemplateAssetForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Asset creado exitosamente.")
            return redirect("documents:asset_list")
    else:
        form = TemplateAssetForm()

    categories = AssetCategory.objects.all()
    return render(request, "documents/asset_form.html", {
        "form": form,
        "categories": categories,
        "is_edit": False,
    })


def asset_edit(request, pk):
    """Editar asset existente."""
    asset = get_object_or_404(TemplateAsset, pk=pk)

    if request.method == "POST":
        form = TemplateAssetForm(request.POST, request.FILES, instance=asset)
        if form.is_valid():
            form.save()
            messages.success(request, "Asset actualizado exitosamente.")
            return redirect("documents:asset_list")
    else:
        form = TemplateAssetForm(instance=asset)

    categories = AssetCategory.objects.all()
    return render(request, "documents/asset_form.html", {
        "form": form,
        "asset": asset,
        "categories": categories,
        "is_edit": True,
    })


def asset_delete(request, pk):
    """Eliminar asset."""
    asset = get_object_or_404(TemplateAsset, pk=pk)

    if request.method == "POST":
        asset.delete()
        messages.success(request, "Asset eliminado exitosamente.")
        return redirect("documents:asset_list")

    return render(request, "documents/asset_delete.html", {"asset": asset})


# =============================================================================
# PDF TEMPLATES
# =============================================================================

def template_list(request):
    """Lista de plantillas PDF."""
    templates = PDFTemplate.objects.select_related("created_by").all()
    return render(request, "documents/template_list.html", {
        "templates": templates,
    })


def template_create(request):
    """Crear nueva plantilla PDF."""
    if request.method == "POST":
        form = PDFTemplateForm(request.POST)
        if form.is_valid():
            template = form.save(commit=False)
            if request.user.is_authenticated:
                template.created_by = request.user
            template.save()
            messages.success(request, "Plantilla creada. Ahora puedes editarla en el editor visual.")
            return redirect("documents:editor", pk=template.pk)
    else:
        form = PDFTemplateForm()

    return render(request, "documents/template_form.html", {
        "form": form,
        "page_sizes": PageSize.choices,
        "orientations": Orientation.choices,
        "is_edit": False,
    })


def template_detail(request, pk):
    """Detalle de plantilla PDF."""
    template = get_object_or_404(
        PDFTemplate.objects.select_related("created_by")
        .prefetch_related("custom_variables", "versions", "context_aliases"),
        pk=pk
    )
    return render(request, "documents/template_detail.html", {
        "template": template,
    })


def template_edit(request, pk):
    """Editar metadatos de plantilla (no el contenido)."""
    template = get_object_or_404(PDFTemplate, pk=pk)

    if request.method == "POST":
        form = PDFTemplateForm(request.POST, instance=template)
        if form.is_valid():
            form.save()
            messages.success(request, "Plantilla actualizada.")
            return redirect("documents:template_detail", pk=pk)
    else:
        form = PDFTemplateForm(instance=template)

    return render(request, "documents/template_form.html", {
        "form": form,
        "template": template,
        "page_sizes": PageSize.choices,
        "orientations": Orientation.choices,
        "is_edit": True,
    })


def template_delete(request, pk):
    """Eliminar plantilla."""
    template = get_object_or_404(PDFTemplate, pk=pk)

    if request.method == "POST":
        template.delete()
        messages.success(request, "Plantilla eliminada.")
        return redirect("documents:template_list")

    return render(request, "documents/template_delete.html", {"template": template})


# =============================================================================
# EDITOR GRAPESJS
# =============================================================================

def editor(request, pk):
    """Editor visual GrapesJS para plantillas."""
    template = get_object_or_404(
        PDFTemplate.objects.all(),
        pk=pk
    )
    assets = TemplateAsset.objects.select_related("category").all()

    # Obtener variables custom de la plantilla
    custom_variables = template.custom_variables.all()
    context_aliases = template.context_aliases.all()

    # Limpiar placeholders de GrapesJS al cargar en el editor
    # Esto evita que el usuario vea "Celda" y otros placeholders
    clean_html = _remove_grapesjs_placeholders(template.html_content or "")

    # Convertir URLs internas (minio:9000) a URLs accesibles desde el navegador (localhost:9000)
    # para que las imágenes se vean en el editor
    clean_html = _denormalize_asset_urls_for_browser(clean_html)

    return render(request, "documents/editor.html", {
        "template": template,
        "clean_html_content": clean_html,
        "assets": assets,
        "custom_variables": custom_variables,
        "context_aliases": context_aliases,
        "page_sizes": PageSize.choices,
        "orientations": Orientation.choices,
    })


@require_http_methods(["POST"])
def editor_save(request, pk):
    """Guardar contenido del editor GrapesJS."""
    template = get_object_or_404(PDFTemplate, pk=pk)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    create_version = data.get("create_version", False)
    new_version_number = None
    if create_version:
        # Crear versión antes de guardar
        last_version = template.versions.order_by("-version_number").first()
        new_version_number = (last_version.version_number + 1) if last_version else 1

        TemplateVersion.objects.create(
            template=template,
            version_number=new_version_number,
            html_content=template.html_content,
            css_content=template.css_content,
            components_json=template.components_json,
            styles_json=template.styles_json,
            change_description=data.get("change_description", ""),
            created_by=request.user if request.user.is_authenticated else None,
        )

    # Obtener contenido del editor
    html_content = data.get("html", "")
    css_content = data.get("css", "")

    # Aplicar transformaciones de normalización para WeasyPrint
    # Esto asegura que el HTML guardado se vea igual en el editor y en el PDF
    html_content = _normalize_html_structure(html_content)
    html_content = _remove_grapesjs_placeholders(html_content)  # Eliminar "Celda" y otros placeholders
    html_content = _unescape_django_templates(html_content)  # Des-escapar <p> y otros tags en templates Django
    html_content = _normalize_asset_urls(html_content)  # Convertir URLs a S3
    css_content = _normalize_css_for_weasyprint(css_content)

    # Actualizar plantilla con contenido normalizado
    template.html_content = html_content
    template.css_content = css_content
    if "project_data" in data:
        template.components_json = data.get("project_data")
    elif "components" in data:
        template.components_json = data.get("components")

    if "styles" in data:
        template.styles_json = data.get("styles")

    # Actualizar configuración de página si se envía
    if "page_size" in data:
        template.page_size = data["page_size"]
    if "orientation" in data:
        template.orientation = data["orientation"]
    if "margin_top" in data and data["margin_top"] is not None:
        template.margin_top = data["margin_top"]
    if "margin_bottom" in data and data["margin_bottom"] is not None:
        template.margin_bottom = data["margin_bottom"]
    if "margin_left" in data and data["margin_left"] is not None:
        template.margin_left = data["margin_left"]
    if "margin_right" in data and data["margin_right"] is not None:
        template.margin_right = data["margin_right"]

    template.save()

    # Publicar automáticamente el archivo HTML físico
    published_path = None
    publish_error = None
    if template.target_path:
        try:
            abs_path = publish_template(template)
            published_path = str(abs_path)
        except Exception as e:
            publish_error = str(e)

    response_data = {
        "success": True,
        "message": "Plantilla guardada y normalizada para WeasyPrint",
        "version": new_version_number,
    }

    if published_path:
        response_data["published_path"] = published_path
        response_data["message"] += f" y publicada en {published_path}"

    if publish_error:
        response_data["publish_warning"] = publish_error

    return JsonResponse(response_data)


def api_assets(request):
    """API para obtener todos los assets disponibles."""
    assets = TemplateAsset.objects.select_related("category").all()
    data = [
        {
            "id": asset.id,
            "name": asset.name,
            "src": asset.file.url,
            "category": asset.category.name,
            "category_type": asset.category.type,
            "width": asset.width,
            "height": asset.height,
        }
        for asset in assets
    ]
    return JsonResponse({"assets": data})


def api_apps(request):
    """API para listar apps disponibles para variables."""
    excluded = {"admin", "auth", "contenttypes", "sessions", "messages", "staticfiles"}
    apps = [
        {
            "label": app.label,
            "name": app.verbose_name,
        }
        for app in django_apps.get_app_configs()
        if app.label not in excluded
    ]
    apps.sort(key=lambda a: a["label"])
    return JsonResponse({"apps": apps})


def api_models(request):
    """API para listar modelos de una app."""
    app_label = request.GET.get("app")
    if not app_label:
        return JsonResponse({"error": "app requerida"}, status=400)
    try:
        app_config = django_apps.get_app_config(app_label)
    except LookupError:
        return JsonResponse({"error": "app no encontrada"}, status=404)

    models = [
        {
            "name": model.__name__,
            "label": model._meta.model_name,
            "verbose_name": model._meta.verbose_name,
        }
        for model in app_config.get_models()
    ]
    models.sort(key=lambda m: m["label"])
    return JsonResponse({"models": models})


def api_fields(request):
    """API para listar campos de un modelo (incluye FKs navegables)."""
    app_label = request.GET.get("app")
    model_label = request.GET.get("model")
    if not app_label or not model_label:
        return JsonResponse({"error": "app y model requeridos"}, status=400)
    try:
        model = django_apps.get_model(app_label, model_label)
    except LookupError:
        return JsonResponse({"error": "modelo no encontrado"}, status=404)

    sensitive = {"password", "token", "secret", "api_key", "apikey", "private_key", "salt"}
    fields = []
    for field in model._meta.get_fields():
        if field.auto_created:
            continue
        name = field.name
        if any(key in name.lower() for key in sensitive):
            continue

        is_relation = field.is_relation and (field.many_to_one or field.one_to_one)
        if field.one_to_many or field.many_to_many:
            continue

        if not field.concrete and not is_relation:
            continue

        item = {
            "name": name,
            "label": field.verbose_name or name,
            "type": field.get_internal_type() if hasattr(field, "get_internal_type") else field.__class__.__name__,
            "is_relation": bool(is_relation),
        }
        if is_relation and field.related_model:
            item["related_app"] = field.related_model._meta.app_label
            item["related_model"] = field.related_model._meta.model_name
        fields.append(item)

    fields.sort(key=lambda f: f["name"])
    return JsonResponse({"fields": fields})


@require_http_methods(["GET", "POST"])
def api_context_aliases(request, pk):
    template = get_object_or_404(PDFTemplate, pk=pk)
    if request.method == "GET":
        aliases = [
            {
                "id": alias.id,
                "alias": alias.alias,
                "app_label": alias.app_label,
                "model_label": alias.model_label,
            }
            for alias in template.context_aliases.all()
        ]
        return JsonResponse({"aliases": aliases})

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    alias = (data.get("alias") or "").strip()
    app_label = (data.get("app_label") or "").strip()
    model_label = (data.get("model_label") or "").strip()
    if not alias or not app_label or not model_label:
        return JsonResponse({"error": "alias, app_label y model_label son requeridos"}, status=400)

    obj, _ = TemplateContextAlias.objects.update_or_create(
        template=template,
        alias=alias,
        defaults={"app_label": app_label, "model_label": model_label},
    )
    return JsonResponse({
        "success": True,
        "id": obj.id,
        "alias": obj.alias,
        "app_label": obj.app_label,
        "model_label": obj.model_label,
    })


@require_http_methods(["POST"])
def api_context_alias_delete(request, pk, alias_id):
    template = get_object_or_404(PDFTemplate, pk=pk)
    alias = get_object_or_404(TemplateContextAlias, pk=alias_id, template=template)
    alias.delete()
    return JsonResponse({"success": True})


@require_http_methods(["POST"])
def api_analyze_template_context(request, pk):
    """Analiza el HTML de la plantilla y devuelve variables/tags detectados."""
    template = get_object_or_404(PDFTemplate, pk=pk)
    html = template.html_content or ""
    html = html_lib.unescape(html)
    html = html_lib.unescape(html)
    html = html.replace("&#123;", "{").replace("&#125;", "}").replace("&lcub;", "{").replace("&rcub;", "}")
    html = html.replace("&nbsp;", " ")
    plain = re.sub(r"<[^>]+>", " ", html)

    # Quitar bloques de control (for/with/while) antes de extraer variables del cuerpo
    def strip_blocks(text: str) -> str:
        patterns = [
            r"\{%\s*for\b[\s\S]*?%\}[\s\S]*?\{%\s*endfor\s*%\}",
            r"\{%\s*with\b[\s\S]*?%\}[\s\S]*?\{%\s*endwith\s*%\}",
            r"\{%\s*while\b[\s\S]*?%\}[\s\S]*?\{%\s*endwhile\s*%\}",
        ]
        for pattern in patterns:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        return text

    stripped_html = strip_blocks(html)
    stripped_plain = strip_blocks(plain)

    # Variables: {{ var|filter }}
    var_pattern = re.compile(r"\{\{\s*([^}]+?)\s*\}\}", re.DOTALL)
    var_matches = var_pattern.findall(stripped_html) + var_pattern.findall(stripped_plain)
    variables = set()
    for expr in var_matches:
        left = expr.split("|")[0].strip()
        left = left.split()[0] if left else ""
        if left:
            variables.add(left)

    # For/with tags: build variable roots and ignore loop variables
    loop_vars = set()
    context_vars = set()
    for_pattern = re.compile(r"\{%\s*for\s+([\w_]+)\s+in\s+([^%]+?)%\}")
    for_match = for_pattern.findall(html) + for_pattern.findall(plain)
    for item, iterable in for_match:
        loop_vars.add(item.strip())
        iterable = iterable.strip()
        if iterable:
            context_vars.add(iterable.split("|")[0].split()[0].strip())

    with_pattern = re.compile(r"\{%\s*with\s+([^%]+?)%\}")
    with_match = with_pattern.findall(html) + with_pattern.findall(plain)
    for expr in with_match:
        parts = [p.strip() for p in expr.split() if p.strip()]
        for part in parts:
            if "=" in part:
                _, rhs = part.split("=", 1)
                rhs = rhs.strip()
                if rhs:
                    context_vars.add(rhs.split("|")[0].split()[0].strip())

    filtered = set()
    for v in variables:
        root = v.split(".")[0] if v else ""
        if not root or root in loop_vars:
            continue
        filtered.add(v)
    filtered |= context_vars
    top_level = sorted({v.split(".")[0] for v in filtered if v})
    return JsonResponse({
        "variables": top_level,
    })


@require_http_methods(["POST"])
def api_download_google_font(request):
    """Descarga Google Fonts y devuelve CSS con rutas locales."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    font_url = (data.get("url") or "").strip()
    if not font_url:
        return JsonResponse({"error": "URL requerida"}, status=400)

    parsed = urlparse(font_url)
    if parsed.scheme not in {"http", "https"}:
        return JsonResponse({"error": "URL inválida"}, status=400)
    if parsed.netloc not in {"fonts.googleapis.com"}:
        return JsonResponse({"error": "Solo se permiten URLs de Google Fonts"}, status=400)

    fonts_dir = Path(getattr(settings, "DOCUMENTS_FONTS_DIR", settings.BASE_DIR / "pdf_templates" / "fonts"))
    fonts_dir.mkdir(parents=True, exist_ok=True)

    try:
        with urlopen(font_url) as resp:
            css_text = resp.read().decode("utf-8")
    except Exception:
        return JsonResponse({"error": "No se pudo descargar el CSS"}, status=400)

    # Descargar archivos de fuentes referenciados
    urls = re.findall(r"url\\(([^)]+)\\)", css_text)
    local_css = css_text
    for raw in urls:
        clean = raw.strip().strip("'\"")
        if not clean:
            continue
        font_parsed = urlparse(clean)
        if font_parsed.netloc not in {"fonts.gstatic.com"}:
            continue
        filename = Path(font_parsed.path).name
        if not filename:
            continue
        local_path = fonts_dir / filename
        if not local_path.exists():
            try:
                with urlopen(clean) as fresp:
                    local_path.write_bytes(fresp.read())
            except Exception:
                continue
        local_css = local_css.replace(clean, f"fonts/{filename}")

    return JsonResponse({
        "css": local_css,
    })


@require_http_methods(["GET"])
def api_fonts_list(request):
    fonts_dir = Path(getattr(settings, "DOCUMENTS_FONTS_DIR", settings.BASE_DIR / "pdf_templates" / "fonts"))
    fonts_dir.mkdir(parents=True, exist_ok=True)
    exts = {".ttf", ".otf", ".woff", ".woff2"}
    by_family = {}
    for path in sorted(fonts_dir.glob("*")):
        if path.suffix.lower() not in exts:
            continue
        family = path.stem.replace("_", " ").replace("-", " ").strip()
        # normalizar family quitando variantes comunes
        family_base = re.sub(r"\b(thin|extralight|light|regular|medium|semibold|bold|extrabold|black|italic)\b", "", family, flags=re.IGNORECASE)
        family_base = re.sub(r"\s+", " ", family_base).strip()
        if not family_base:
            family_base = family
        by_family.setdefault(family_base, path.name)

    fonts = [{"family": fam, "file": fname} for fam, fname in sorted(by_family.items())]
    return JsonResponse({"fonts": fonts})


@require_http_methods(["POST"])
def api_fonts_upload(request):
    if "file" not in request.FILES:
        return JsonResponse({"error": "Archivo requerido"}, status=400)
    upload = request.FILES["file"]
    if not upload.name.lower().endswith(".zip"):
        return JsonResponse({"error": "Debe ser un .zip"}, status=400)

    fonts_dir = Path(getattr(settings, "DOCUMENTS_FONTS_DIR", settings.BASE_DIR / "pdf_templates" / "fonts"))
    fonts_dir.mkdir(parents=True, exist_ok=True)
    exts = {".ttf", ".otf", ".woff", ".woff2"}

    def file_hash(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    existing_hashes = {}
    for existing in fonts_dir.glob("*"):
        if existing.suffix.lower() not in exts:
            continue
        try:
            existing_hashes[file_hash(existing)] = existing.name
        except Exception:
            continue

    skipped = []
    saved = []
    try:
        with zipfile.ZipFile(upload) as zf:
            for member in zf.infolist():
                name = Path(member.filename).name
                if not name:
                    continue
                if Path(name).suffix.lower() not in exts:
                    continue
                target = fonts_dir / name
                with zf.open(member) as src:
                    data = src.read()
                tmp = fonts_dir / f".tmp_{name}"
                with tmp.open("wb") as dst:
                    dst.write(data)
                try:
                    h = file_hash(tmp)
                    if h in existing_hashes:
                        skipped.append(name)
                        tmp.unlink(missing_ok=True)
                        continue
                    existing_hashes[h] = name
                except Exception:
                    pass
                tmp.replace(target)
                saved.append(name)
    except Exception:
        return JsonResponse({"error": "No se pudo procesar el zip"}, status=400)

    return JsonResponse({"success": True, "saved": saved, "skipped": skipped})


# =============================================================================
# PUBLICACIÓN
# =============================================================================

@require_http_methods(["POST"])
def template_publish(request, pk):
    """Publicar plantilla: escribir HTML final en target_path."""
    template = get_object_or_404(PDFTemplate, pk=pk)
    try:
        publish_template(template)
        messages.success(request, "Plantilla publicada correctamente.")
    except Exception as e:
        messages.error(request, f"No se pudo publicar: {e}")
    return redirect("documents:template_detail", pk=pk)


# =============================================================================
# VERSIONS
# =============================================================================

def version_list(request, pk):
    """Lista de versiones de una plantilla."""
    template = get_object_or_404(PDFTemplate, pk=pk)
    versions = template.versions.select_related("created_by").all()
    return render(request, "documents/version_list.html", {
        "template": template,
        "versions": versions,
    })


@require_http_methods(["POST"])
def version_restore(request, pk, version):
    """Restaurar una versión anterior."""
    template = get_object_or_404(PDFTemplate, pk=pk)
    version_obj = get_object_or_404(TemplateVersion, template=template, version_number=version)

    # Crear versión de respaldo antes de restaurar
    last_version = template.versions.order_by("-version_number").first()
    new_version_number = last_version.version_number + 1 if last_version else 1

    TemplateVersion.objects.create(
        template=template,
        version_number=new_version_number,
        html_content=template.html_content,
        css_content=template.css_content,
        components_json=template.components_json,
        styles_json=template.styles_json,
        change_description=f"Respaldo antes de restaurar v{version}",
        created_by=request.user if request.user.is_authenticated else None,
    )

    # Restaurar contenido
    template.html_content = version_obj.html_content
    template.css_content = version_obj.css_content
    template.components_json = version_obj.components_json
    template.styles_json = version_obj.styles_json
    template.save()

    messages.success(request, f"Versión {version} restaurada exitosamente.")
    return redirect("documents:editor", pk=pk)
