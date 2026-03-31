# Project Status

## Latest: Liquidación de comisiones por recaudo (finance)
Se implementó flujo completo para liquidar comisiones por avance real de pago.

### Regla implementada
- La venta debe estar aprobada.
- Base de avance: `valor_venta * 20%`.
- `% liquidación = recaudo_acumulado / base_20`.
- El porcentaje se limita a `100%`.
- Por asesor se calcula: total comisión, liquidable, liquidado y pendiente.

### Vistas y rutas
- Cola de ventas listas para liquidar: `commission_liquidation_queue`.
- Acción de liquidación por venta: `commission_liquidate_sale`.
- URLs:
  - `/finanzas/comisiones/liquidacion/`
  - `/finanzas/comisiones/liquidacion/<sale_id>/liquidar/`

### UI
- Listado de ventas para liquidación con métricas de avance y estado.
- Botón de liquidar solo dentro del detalle (no en listado general).
- Barra de progreso ajustada a la regla del 20%.

---

## Latest: Recaudos y aplicación al cronograma
Módulo de recaudos activo con aplicación automática a cuotas en orden:
**mora -> interés -> capital**.

### Incluye
- `PaymentMethod`, `PaymentReceipt`, `PaymentApplication`.
- Soporte PDF en storage privado.
- Hash SHA-256 para detectar duplicados.
- Cálculo de saldo a favor cuando hay excedente.
- Vistas operativas de recaudos y detalle de recibo.

---

## Latest: Seguridad y permisos (users)
Se endureció el control de acceso a esquema **fail-closed**.

### Cambios clave
- Middleware exige autenticación y permiso explícito para vistas protegidas.
- Validación de permisos por rol sin auto-allow implícito.
- Se incluyeron `ADMIN` y `DIRECTOR` en gestión UI de permisos.
- Comando de bootstrap:
  - `python manage.py seed_role_permissions`
  - `python manage.py seed_role_permissions --reset`

### Acceso por objeto
- En detalle de contrato, asesores no pueden ver ventas que no crearon, salvo que también tengan rol elevado.

---

## Latest: Storage privado MinIO (documentos/PDF)
Se ajustó entrega de archivos privados para usar URLs firmadas temporales.

### Ajustes
- `PrivateMediaStorage` forzado a no usar dominio custom directo para privados.
- Método `url()` ajustado para generar enlaces firmados válidos.
- Config adicional:
  - `AWS_S3_PRIVATE_CUSTOM_DOMAIN`
  - `AWS_S3_SIGNATURE_VERSION` (`s3v4`)
- En no-DEBUG, llaves AWS/MinIO obligatorias (falla temprana de configuración).

---

## Latest: Adjuntos de venta
- `SaleDocument.date` pasó a `auto_now_add=True`.
- Se removió el campo `date` del formulario y de la UI de carga.
- Migración aplicada para alinear modelo/formulario.

---

## Latest: UI contrato (sales)
- Cronograma + timeline en una sola fila (proporción visual aprox. 60/40, ajustada para legibilidad).
- Timeline con `max-height` y `overflow` para no extender la vista.
- Tabla de cronograma compactada: columna de cuota unificada (`CI1`, `CI2`, `FN1`, etc.).

---

## Calidad: pruebas
Suite de pruebas ampliada en módulos:
- `users`
- `inventory`
- `sales`
- `finance`
- `documents`

### Cobertura funcional agregada
- Autenticación y permisos por rol/superuser.
- Flujos de venta, aprobación y restricciones por propietario.
- Recaudos y aplicación financiera (incluyendo edge cases).
- Cola y liquidación de comisiones (idempotencia y límites).
- API/documentos y validaciones de parámetros.

---

## Estado de salida a producción
Listo para piloto controlado. Antes de go-live total:
- [ ] Ejecutar suite completa en CI con base de datos igual a producción.
- [ ] Verificar variables MinIO/AWS en entorno productivo.
- [ ] Correr `seed_role_permissions` en producción y validar matriz con negocio.
- [ ] Revisar observabilidad: logs de liquidación, recaudos y errores de storage.
- [ ] Prueba de humo end-to-end: venta -> aprobación -> recaudo -> liquidación -> descarga de adjuntos.
