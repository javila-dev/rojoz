# Editor y Versionado de Plantillas Django

Este modulo permite disenar, versionar y publicar plantillas HTML para Django.
Su objetivo es el authoring y versionado de templates, no la generacion de PDFs.

## Que hace

- Editor visual (GrapesJS) para HTML/CSS.
- Insercion de variables `{{ }}`.
- Versionado automatico al guardar.
- Publicacion del HTML a una ruta definida por el usuario.
- Gestion de assets (imagenes).

## Que NO hace

- No conoce modelos ni estructuras de negocio.
- No genera PDFs ni hace preview con datos reales.

## Flujo de trabajo

1. Crear plantilla (nombre, slug, target_path).
2. Editar HTML/CSS en el editor visual.
3. Publicar para escribir el archivo en `target_path`.
4. La vista externa consume el template publicado y renderiza con contexto real.

## Configuracion

Definir la ruta base para publicacion:

```
DOCUMENTS_TEMPLATES_BASE_DIR = BASE_DIR / "templates" / "generated"
```

La ruta `target_path` siempre es relativa a esta base.

## Estructura

```
/app/documents/
├── models.py
├── views.py
├── urls.py
├── forms.py
├── admin.py
└── services/
    └── publisher.py
```
