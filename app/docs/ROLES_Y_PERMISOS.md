# Roles y Permisos - Constructora Rojoz

## Definición de Roles

### 1. Gerente General
**Alcance:** Acceso total al sistema.

**Permisos:**
- ✅ Acceso completo a todos los módulos
- ✅ Administración de usuarios y roles
- ✅ Visualización de reportes globales
- ✅ Configuración del sistema

---

### 2. Gerente Comercial
**Alcance:** Acceso y aprobación de todo lo relacionado con el **proceso de ventas** (NO incluye procesos que apoyan ventas).

**Permisos de Ventas:**
- [ ] Crear, editar y eliminar contratos
- [ ] Aprobar contratos
- [ ] Ver historial de ventas
- [ ] Crear y modificar planes de pago
- [ ] Crear y aprobar comisiones de vendedores
- [ ] Generar PDFs de contratos
- [ ] Enviar contratos para firma digital
- [ ] Ver reportes de ventas

**Restricciones:**
- ❌ NO tiene acceso a inventario (apertura, modificación)
- ❌ NO tiene acceso a tesorería (pagos de clientes)
- ❌ NO tiene acceso a operaciones administrativas

---

### 3. Vendedor
**Alcance:** Operaciones básicas de ventas.

**Permisos:**
- [ ] Crear contratos de venta
- [ ] Ver contratos propios (los que él creó)
- [ ] Cotizar proyectos
- [ ] Ver inventario disponible (solo lectura)
- [ ] Ver sus propias comisiones

**Restricciones:**
- ❌ NO puede aprobar contratos
- ❌ NO puede crear planes de pago personalizados (usa plantillas)
- ❌ NO puede ver contratos de otros vendedores
- ❌ NO puede modificar comisiones
- ❌ NO tiene acceso a tesorería
- ❌ NO tiene acceso a operaciones

---

### 4. Tesorería
**Alcance:** Todo lo relacionado con flujo de dinero (entrada y salida).

**Permisos:**
- [ ] Ver bandeja de solicitudes de recibo
- [ ] Crear solicitudes de recibo
- [ ] Validar solicitudes de recibo
- [ ] Generar recibos desde solicitudes aprobadas
- [ ] Registrar pagos de clientes (recaudos)
- [ ] Ver cartera completa
- [ ] Generar reportes de cartera
- [ ] Realizar pagos a gestores/vendedores
- [ ] Ver historial de pagos
- [ ] Conciliar pagos
- [ ] Exportar reportes financieros

**Restricciones:**
- ❌ NO puede crear contratos
- ❌ NO puede modificar planes de pago
- ❌ NO puede crear comisiones (solo pagarlas)
- ❌ NO tiene acceso a inventario
- ❌ NO tiene acceso a operaciones

---

### 5. Operaciones
**Alcance:** Gestión administrativa del sistema, correcciones y configuraciones.

**Permisos:**
- [ ] Apertura de inventario (proyectos, casas)
- [ ] Modificar inventario (precios, estados)
- [ ] Realizar correcciones en contratos
- [ ] Realizar reversiones de operaciones
- [ ] Modificar acabados y opciones
- [ ] Gestionar tipos de casa
- [ ] Ver todos los módulos (solo lectura para ventas y finanzas)
- [ ] Generar reportes operativos

**Restricciones:**
- ❌ NO puede crear contratos
- ❌ NO puede registrar pagos de clientes
- ❌ NO puede crear comisiones

---

## Matriz de Permisos por Módulo

### Módulo: Inventario

| Acción | Gerente General | Gerente Comercial | Vendedor | Tesorería | Operaciones |
|--------|----------------|-------------------|----------|-----------|-------------|
| Ver proyectos | ✅ | ✅ (lectura) | ✅ (lectura) | ❌ | ✅ |
| Crear proyectos | ✅ | ❌ | ❌ | ❌ | ✅ |
| Modificar proyectos | ✅ | ❌ | ❌ | ❌ | ✅ |
| Abrir/cerrar casas | ✅ | ❌ | ❌ | ❌ | ✅ |
| Gestionar acabados | ✅ | ❌ | ❌ | ❌ | ✅ |

---

### Módulo: Ventas

| Acción | Gerente General | Gerente Comercial | Vendedor | Tesorería | Operaciones |
|--------|----------------|-------------------|----------|-----------|-------------|
| Crear contratos | ✅ | ✅ | ✅ | ❌ | ❌ |
| Aprobar contratos | ✅ | ✅ | ❌ | ❌ | ❌ |
| Ver todos los contratos | ✅ | ✅ | ❌ (solo propios) | ❌ | ✅ (lectura) |
| Crear planes de pago | ✅ | ✅ | ❌ | ❌ | ❌ |
| Crear comisiones | ✅ | ✅ | ❌ | ❌ | ❌ |
| Generar PDFs | ✅ | ✅ | ✅ | ❌ | ✅ |
| Firmas digitales | ✅ | ✅ | ✅ | ❌ | ❌ |

---

### Módulo: Finanzas

| Acción | Gerente General | Gerente Comercial | Vendedor | Tesorería | Operaciones |
|--------|----------------|-------------------|----------|-----------|-------------|
| Crear solicitudes de recibo | ✅ | ❌ | ✅ | ✅ | ❌ |
| Validar solicitudes de recibo | ✅ | ❌ | ❌ | ✅ | ❌ |
| Generar recibos desde solicitudes | ✅ | ❌ | ❌ | ✅ | ❌ |
| Ver cartera | ✅ | ✅ (lectura) | ❌ | ✅ | ✅ (lectura) |
| Registrar pagos clientes | ✅ | ❌ | ❌ | ✅ | ❌ |
| Ver comisiones | ✅ | ✅ | ✅ (propias) | ✅ | ❌ |
| Pagar comisiones | ✅ | ❌ | ❌ | ✅ | ❌ |
| Generar reportes | ✅ | ✅ (ventas) | ❌ | ✅ (financieros) | ✅ |

---

### Módulo: Usuarios

| Acción | Gerente General | Gerente Comercial | Vendedor | Tesorería | Operaciones |
|--------|----------------|-------------------|----------|-----------|-------------|
| Crear usuarios | ✅ | ❌ | ❌ | ❌ | ❌ |
| Asignar roles | ✅ | ❌ | ❌ | ❌ | ❌ |
| Ver usuarios | ✅ | ✅ (lectura) | ❌ | ❌ | ✅ (lectura) |
| Gestionar perfiles | ✅ | ❌ | ✅ (propio) | ✅ (propio) | ✅ (propio) |

---

## Implementación Técnica

### Django Groups y Permissions

```python
# Grupos a crear en Django
ROLES = [
    'Gerente General',
    'Gerente Comercial',
    'Vendedor',
    'Tesorería',
    'Operaciones',
]
```

### Decoradores Sugeridos

```python
# Ejemplos de uso
@role_required('Gerente General', 'Gerente Comercial')
def approve_contract(request, pk):
    pass

@role_required('Tesorería')
def register_payment(request):
    pass

@role_required('Operaciones')
def open_inventory(request):
    pass
```

---

## Notas de Implementación

1. **A medida que implementemos funcionalidades**, actualizar esta matriz con los permisos específicos.
2. Cada vista debe tener su decorador de permisos correspondiente.
3. Los permisos se validarán tanto en el backend (vistas) como en el frontend (ocultar botones/menús).
4. El sistema de permisos usará `django.contrib.auth.models.Permission` y `Group`.

---

## Changelog

- **2026-01-26**: Creación inicial del documento con 5 roles base.
