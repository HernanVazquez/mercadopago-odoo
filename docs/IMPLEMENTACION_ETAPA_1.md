# Implementación de la Etapa 1

## Estado

Esta etapa implementa el inicio y la consulta manual de cobros Mercado Pago Point desde `account.payment` en Odoo 16 Community. Utiliza API Orders y depende únicamente del módulo estándar `account`.

No incluye Webhooks, producción, reembolsos, comisiones, conciliación financiera, `account_payment_group` ni `talar_sale`.

## Regla funcional central: el importe lo controla Odoo

El importe enviado al Point siempre es exactamente `account.payment.amount`.

- El comprador no puede elegir ni modificar el importe en el terminal.
- El payload no admite propinas ni ingreso manual de monto.
- No existen tolerancias ni ajustes automáticos.
- Si se necesita un pago parcial, el usuario debe crear primero un `account.payment` por ese importe parcial y luego enviarlo al Point.
- Para habilitar la publicación contable, `paid_amount` debe coincidir exactamente con el string de dos decimales enviado por Odoo.
- Una transferencia en la que el cliente decide cuánto enviar pertenece a otra operatoria y queda fuera de esta integración.

El código rechaza importes que no puedan representarse exactamente con dos decimales. No los redondea. El string enviado, por ejemplo `123.45`, se conserva en `requested_amount_text`, y el devuelto se conserva en `paid_amount_text`; la comparación que habilita la publicación usa esos valores decimales exactos, sin margen de tolerancia.

## Componentes

### Método de pago

Se crea un `account.payment.method` entrante:

- nombre: `Mercado Pago Point`;
- código técnico: `mercadopago_point`.

La identificación usa `account.payment.payment_method_line_id.payment_method_id.code`. No depende del nombre del diario, de etiquetas visibles ni de campos de `talar_sale`.

El módulo no crea líneas del método en ningún diario. Odoo 16 normalmente autoasigna los métodos declarados como `multi`; el override de `account.payment.method.create()` omite exclusivamente esa autoasignación mientras se crea el registro maestro Point. Después el método queda disponible para selección manual en diarios bancarios o de caja.

### Configuración

`mercadopago.point.config` almacena:

- compañía;
- entorno `test` o `production`;
- Access Token privado;
- terminal ID;
- timeout HTTP;
- estado activo.

La combinación activa de compañía, entorno y terminal es única. Una configuración anterior puede desactivarse y conservarse para mantener la trazabilidad antes de crear su reemplazo activo. La arquitectura permite varias terminales: cada línea de método del diario referencia explícitamente la configuración que debe utilizar. No se elige una configuración por nombre de diario ni mediante una búsqueda ambigua de “la primera activa”.

El Access Token es un campo backend restringido a `base.group_system`, se presenta como contraseña y nunca se registra. El campo no implica cifrado en reposo; la seguridad de base de datos y backups sigue siendo necesaria.

Aunque el modelo permite registrar el entorno `production`, las líneas y acciones de red de esta etapa lo bloquean. Solo se admiten configuraciones TEST.

### Intentos de Order

Cada llamada lógica se conserva en `mercadopago.point.order`. La relación es:

```text
account.payment 1 ─────── 0..N mercadopago.point.order
```

Un intento anterior nunca se sobrescribe. Una tarjeta rechazada, cancelación, expiración o fallo definitivo puede dar lugar a un intento posterior. Todos quedan vinculados al mismo pago.

Cada intento guarda, entre otros datos:

- número de intento;
- `external_reference`;
- idempotency key;
- Order ID y Payment ID;
- estados y detalles de Order/pago;
- importe solicitado y pagado, tanto monetario como string exacto;
- medio de pago real, marca y cuotas;
- terminal utilizada;
- fechas de envío y verificación;
- resultado incierto y último error sanitizado.

Solo puede existir una Order verificada exitosa que habilite un `account.payment`. Si se detectaran varias Orders aprobadas para el mismo pago, la barrera bloquea la publicación para exigir revisión manual.

## Configuración en desarrollo

1. Instalar o actualizar `mercadopago_point_odoo`.
2. Ir a **Contabilidad → Configuración → Mercado Pago Point**.
3. Crear una configuración con:
   - compañía correspondiente;
   - entorno `Test`;
   - Access Token TEST;
   - ID de la terminal virtual TEST;
   - timeout entre 1 y 60 segundos.
4. Abrir el diario elegido por el administrador.
5. En **Pagos entrantes**, agregar manualmente `Mercado Pago Point`.
6. En la misma línea seleccionar la configuración/terminal Point creada.

El módulo no busca un diario llamado “Mercado Pago” y no modifica otros diarios.

Las credenciales y el identificador de la terminal virtual no se incluyen en el repositorio. Deben obtenerse y configurarse en el entorno de desarrollo autorizado. La referencia oficial para crear Orders Point es: <https://www.mercadopago.com.ar/developers/es/reference/in-person-payments/point/orders/create-order/post>.

## Flujo operativo

### Enviar al Point

1. Crear y guardar un `account.payment` entrante en ARS.
2. Elegir el diario y la línea `Mercado Pago Point`.
3. Definir el importe exacto en Odoo.
4. Pulsar **Enviar al Point**.

Odoo crea un intento local antes de la llamada y ejecuta:

```text
POST https://api.mercadopago.com/v1/orders
```

El payload contiene solamente:

- tipo `point`;
- `external_reference`;
- una transacción de pago por el importe exacto;
- terminal ID.

Se envía `X-Idempotency-Key` y el Access Token únicamente en headers backend.

La respuesta POST guarda IDs y estado, pero nunca marca el intento como verificado para publicación.

### Resultado incierto

Si el POST termina en timeout, error de conexión, HTTP 408, HTTP 409, HTTP 5xx o respuesta exitosa inconsistente, puede haber una Order remota aunque Odoo no haya recibido su ID.

En ese caso:

- el intento queda `uncertain`;
- se conserva la misma `external_reference`;
- se conserva la misma idempotency key;
- no se permite crear inmediatamente otro intento;
- pulsar nuevamente **Enviar al Point** reenvía el mismo payload con la misma key para recuperar el resultado idempotente.

No se permite cambiar importe, moneda, terminal o configuración mientras se intenta recuperar ese resultado.

### Consultar estado

Cuando existe Order ID, **Consultar estado** ejecuta explícitamente:

```text
GET https://api.mercadopago.com/v1/orders/{id}
```

La consulta valida `external_reference`, Order ID, que exista una única transacción y que el importe de la transacción coincida con el enviado.

Una Order habilita la barrera contable solamente si el GET confirma simultáneamente:

- Order `processed`;
- pago `processed`;
- detalle del pago `accredited`;
- Order ID y Payment ID presentes;
- referencia externa coincidente;
- `paid_amount` exactamente igual al importe enviado.

Consultar no publica el pago.

### Publicar el pago

`account.payment.action_post()` es únicamente una barrera local. No realiza llamadas HTTP.

Para pagos que no son Point, delega directamente al comportamiento estándar. Para Point exige exactamente una Order verificada y vuelve a comprobar moneda e importe exacto antes de llamar al `super()` de Odoo.

## Manejo de errores y seguridad

- El cliente HTTP vive en `services/client.py` y no conoce reglas contables.
- Todas las llamadas tienen timeout.
- Los errores se convierten en mensajes sanitizados.
- No existe ningún logger de headers, token o payload sensible.
- Incluso si Mercado Pago devolviera accidentalmente el token dentro de un mensaje, el cliente lo reemplaza antes de persistir o mostrar el error.
- Los intentos enviados no pueden eliminarse.
- Configuraciones e intentos son de solo lectura para usuarios contables; solo los administradores gestionan configuraciones y las acciones internas actualizan intentos con privilegios controlados.
- Se aplican reglas multiempresa a configuración y Orders.
- Los IDs, estados y errores no generan asientos adicionales.

## Pruebas incluidas

- payload de importe fijo;
- headers, timeout e idempotencia;
- sanitización del Access Token;
- timeout de red y HTTP 408/409/5xx POST como resultado incierto;
- respuesta JSON inválida de un POST potencialmente procesado como resultado incierto;
- detección mediante `payment_method_id.code`;
- ausencia de autoasignación del método a diarios;
- unicidad de configuración activa por compañía, entorno y terminal;
- reemplazo de configuraciones desactivadas sin perder historial;
- bloqueo contable sin GET exitoso;
- confirmación de que `action_post()` no realiza llamadas HTTP;
- publicación estándar sin cambios para pagos que no son Point;
- envío exacto y consulta manual;
- rechazo de diferencias en `paid_amount`;
- conservación de intentos fallidos antes de crear el siguiente;
- reutilización del intento y la misma idempotency key después de timeout.

Los tests HTTP usan mocks y no consumen credenciales ni llaman a Mercado Pago.

## Limitaciones de la Etapa 1

- solo ARS;
- solo entorno TEST;
- actualización manual, sin Webhook;
- sin cancelación remota ni reembolsos;
- sin automatización de publicación;
- sin conciliación de comisiones, impuestos, retenciones o neto;
- sin puentes con `account_payment_group`;
- sin integración con `talar_sale`.
