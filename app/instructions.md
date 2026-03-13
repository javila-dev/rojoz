# INSTRUCCIONES MAESTRAS PARA COPILOT - PROYECTO ROJOZ

## 1. Rol y Objetivo
Actúa como un **Arquitecto de Software Senior** especializado en el stack **Django 5 + HTMX + DaisyUI**.
Estamos construyendo el MVP de **Constructora Rojoz**, una plataforma modular para ventas inmobiliarias.

---

## 2. Estructura del Proyecto (Monolito Modular)
El proyecto usa Docker y una arquitectura modular estricta.
- **`config/`**: Settings globales.
- **`users/`**: Modelo `User`, Roles, Perfiles y Sagrilaft.
- **`inventory/`**: Proyectos, Casas y Acabados.
- **`sales/`**: Cotizador, Contratos y Firmas.
- **`finance/`**: Cartera, Recaudos y Comisiones.

---

## 3. Stack Tecnológico (Estricto)

### Backend
- **Framework:** Django 5.x (Python 3.11)
- **Base de Datos:** PostgreSQL
- **Admin:** `django-unfold`
- **Archivos:** `django-storages` + MinIO.

### MinIO: Buckets Público vs Privado
Se usan dos buckets:
- Público: `construccion-media-public` (imágenes, media no sensible)
- Privado: `construccion-media-private` (PDFs/contratos, anexos)

Variables de entorno:
- `AWS_PUBLIC_MEDIA_BUCKET`
- `AWS_PRIVATE_MEDIA_BUCKET`
- `AWS_S3_ENDPOINT_URL` (default `http://minio:9000`)
- `AWS_S3_CUSTOM_DOMAIN` (default `localhost:9000`)
- `AWS_S3_URL_PROTOCOL` (default `http:`)

Cómo hacer público el bucket en Docker (servicio `minio`):
```bash
docker exec -it minio mc alias set local http://minio:9000 minioadmin minioadmin
docker exec -it minio mc anonymous set download local/construccion-media-public
```

Alternativa usando contenedor temporal `minio/mc` (reemplaza `<tu_red>`):
```bash
docker run --rm --network <tu_red> minio/mc \
  mc alias set local http://minio:9000 minioadmin minioadmin
docker run --rm --network <tu_red> minio/mc \
  mc anonymous set download local/construccion-media-public
```

### Frontend (Low-Code / CSS-First)
- **Templating:** Django Templates (DTL).
- **UI Framework:** **TailwindCSS** + **DaisyUI 4**.
- **Interactividad Servidor:** **HTMX** (AJAX declarativo).
- **Interactividad Cliente:** **NULA/MÍNIMA**. Usa los componentes CSS de DaisyUI. **No uses JavaScript ni Alpine.js** a menos que sea estrictamente imposible hacerlo con CSS.

---

## 4. Reglas de Desarrollo (The Golden Rules)

### A. Reglas de Frontend (DaisyUI Strict)
1.  **Componentes CSS-Only:**
    -   **Modales:** Usa la etiqueta `<dialog>` y el método `.showModal()` (nativo) o el truco del checkbox. No crees estados JS `open/close`.
    -   **Dropdowns:** Usa siempre `<details>` y `<summary>`.
    -   **Drawers/Menús:** Usa el componente `drawer` de DaisyUI (basado en checkbox).
2.  **Steps & Wizards:** Usa el componente `<ul class="steps">`.
3.  **HTMX First:** Para lógica dinámica, ve al servidor.
    -   *Ejemplo:* Si el usuario selecciona una casa, usa `hx-get` para traer el precio actualizado. No calcules precios en JS.

### B. Reglas de Backend (Django)
1.  **Referencias a Modelos:** Usa siempre **Lazy Strings** (`'app.Model'`) en las ForeignKeys.
2.  **Archivos:** Usa `io.BytesIO` para generar PDFs en memoria. Nunca uses `/tmp`.
3.  **Vistas:** Prefiero FBV (Function Based Views) limpias.

---

## 5. Identidad Visual - Constructora Rojoz

### A. Paleta de Colores Corporativa

La identidad visual se basa en tonos de **rojo quemado** (burnt sienna) como color corporativo principal, manteniendo elegancia y minimalismo.

#### Colores Principales
```css
--brand-rojoz: #A0372A;           /* Rojo corporativo principal */
--brand-rojoz-light: #C4574E;     /* Rojo quemado claro */
--brand-rojoz-dark: #7A2518;      /* Rojo oscuro */
--brand-ink: #1f2937;             /* Texto principal */
--brand-mist: #f9fafb;            /* Fondos claros */
--brand-warm: #fef7f5;            /* Fondo cálido */
```

#### Escala de Rojoz (Tailwind)
```javascript
'rojoz': {
    50: '#fef2f2',   // Muy claro
    100: '#fee2e2',
    200: '#fecaca',
    300: '#fca5a5',
    400: '#f87171',
    500: '#C4574E',  // Rojo quemado principal
    600: '#A0372A',  // Rojo corporativo
    700: '#7A2518',  // Rojo oscuro
    800: '#5a1a0f',
    900: '#3d100a',  // Muy oscuro
}
```

#### Gradientes Corporativos
```css
/* Gradiente de marca principal */
background: linear-gradient(135deg, #A0372A 0%, #C4574E 100%);

/* Gradiente de fondo suave */
background: linear-gradient(135deg, #fef7f5 0%, #ffffff 35%, #f9fafb 100%);

/* Gradiente sutil para fondos */
background: linear-gradient(135deg, rgba(160,55,42,0.03) 0%, rgba(196,87,78,0.06) 100%);
```

### B. Tipografía

- **Títulos principales**: Playfair Display (serif, elegante)
- **Texto general**: Gudea (sans-serif, legible)
- **Tracking**: `-0.01em` en h1, h2, h3

### C. Componentes UI

#### Botones
```css
/* Botón Primario - Gradiente corporativo */
.btn-primary {
    background: linear-gradient(135deg, var(--brand-rojoz) 0%, var(--brand-rojoz-light) 100%);
    color: white;
    box-shadow: 0 4px 14px rgba(160, 55, 42, 0.25), 0 2px 4px rgba(160, 55, 42, 0.15);
}

.btn-primary:hover {
    box-shadow: 0 8px 20px rgba(160, 55, 42, 0.35);
    transform: translateY(-2px);
}

/* Botón Secundario - Efecto shimmer */
.btn-neutral-elegant {
    background: #ffffff;
    border: 1.5px solid #e5e7eb;
}

.btn-neutral-elegant:hover {
    border-color: rgba(160, 55, 42, 0.4);
    background: linear-gradient(135deg, #fef7f5 0%, #ffffff 100%);
}
```

#### Cards
```css
.card {
    box-shadow: 0 1px 3px rgba(160, 55, 42, 0.08), 0 1px 2px rgba(0, 0, 0, 0.02);
    border: 1px solid rgba(160, 55, 42, 0.1);
    animation: fadeInUp 0.6s ease-out;
}

.card:hover {
    box-shadow: 0 8px 24px rgba(160, 55, 42, 0.15);
}
```

#### Navegación
```css
/* Links de navegación con hover corporativo */
.nav-link:hover {
    background: linear-gradient(to right, rgba(160, 55, 42, 0.12), rgba(196, 87, 78, 0.06));
}

.nav-link.active {
    border-left: 3px solid var(--brand-rojoz);
    background: linear-gradient(to right, rgba(160, 55, 42, 0.12), rgba(196, 87, 78, 0.06));
}
```

#### Tablas
```css
.table th {
    color: var(--brand-rojoz-dark);
    background: linear-gradient(to bottom, #fef7f5, #ffffff);
    border-bottom: 2px solid rgba(160, 55, 42, 0.15);
    text-transform: uppercase;
    font-size: 0.75rem;
    letter-spacing: 0.5px;
}

.table tr:hover {
    background: linear-gradient(to right, rgba(160, 55, 42, 0.03), rgba(196, 87, 78, 0.02));
}
```

#### Badges
```css
.badge-rojoz {
    background: linear-gradient(135deg, rgba(160,55,42,0.1) 0%, rgba(196,87,78,0.15) 100%);
    color: var(--brand-rojoz-dark);
    border: 1px solid rgba(160, 55, 42, 0.2);
}
```

### D. Efectos y Animaciones

#### Shimmer (Accent Bar)
```css
@keyframes shimmer {
    0% { left: -100%; }
    100% { left: 200%; }
}

.accent-bar::before {
    animation: shimmer 3s infinite;
}
```

#### Fade In Up (Cards)
```css
@keyframes fadeInUp {
    from {
        opacity: 0;
        transform: translateY(20px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}
```

### E. Elementos Decorativos

#### Sidebar Header
- Fondo con gradiente rojizo corporativo
- Círculos decorativos con blur
- Icono corporativo con backdrop-blur
- Logo "Rojoz" en blanco con Playfair Display

#### Scrollbar Personalizada
```css
::-webkit-scrollbar-thumb {
    background: linear-gradient(180deg, var(--brand-rojoz-light) 0%, var(--brand-rojoz) 100%);
    border-radius: 4px;
}
```

### F. Reglas de Uso

1. **NO usar líneas de acento automáticas** en cards o títulos al hacer hover
2. **Mantener minimalismo**: El rojo aparece de forma sutil y elegante
3. **Gradientes sutiles**: Usar opacidades bajas para fondos (0.03-0.06)
4. **Sombras con tinte rojizo**: Usar `rgba(160, 55, 42, opacity)` en lugar de grises puros
5. **Transiciones suaves**: `cubic-bezier(0.4, 0, 0.2, 1)` para movimientos naturales
6. **Hover states**: Siempre con color corporativo
7. **Focus en inputs**: Borde rojizo con ring sutil

### G. Complementos Cálidos

Para variedad visual sin perder coherencia:
- **Ámbar/Naranja**: `from-amber-600 to-orange-500` (para métricas financieras)
- **Esmeralda**: `text-emerald-600` (solo para indicadores positivos de crecimiento)

---
