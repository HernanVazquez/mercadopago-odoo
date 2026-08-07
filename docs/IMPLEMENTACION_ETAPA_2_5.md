# Etapa 2.5: Mercado Pago QR con Orders API

## Alcance

Esta etapa agrega cobros QR TEST independientes de Point sobre el modelo de
intentos existente. No modifica conciliación, recibos, Ventas, Webhooks ni
producción.

## Configuración

El administrador crea una configuración de tipo **QR** con Access Token TEST y
el `external_pos_id` de una caja provisionada externamente con
`fixed_amount=true`. El modo inicial es exclusivamente `hybrid`.

La instalación crea el método maestro entrante `Mercado Pago QR`, código
`mercadopago_qr`, pero no lo asigna a ningún diario. Debe habilitarse
manualmente y asociarse a una configuración QR de la misma compañía.

## Regla de importe

Odoo es la única fuente del importe. El payload envía exactamente el mismo
valor de dos decimales en `total_amount` y
`transactions.payments[0].amount`. No existen propinas, tolerancias, redondeos
ni ingreso manual en la caja. Un pago parcial requiere primero un
`account.payment` por ese importe parcial.

## Flujo QR

1. El usuario guarda un pago entrante en borrador con el método QR.
2. **Cobrar con QR** crea una Order `type=qr`, `mode=hybrid` y abre el modal.
3. El comprador escanea el QR estático de la caja TEST.
4. El polling consulta `GET /v1/orders/{id}` y actualiza el intento.
5. La publicación sigue siendo manual y `action_post()` sólo evalúa datos
   locales verificados; nunca hace HTTP.

Si Mercado Pago devuelve `type_response.qr_data`, se conserva para evolución
futura, pero no se representa gráficamente en esta etapa. Se omite
`expiration_time` y se usa el valor predeterminado de Mercado Pago.

## Pruebas TEST

Las aprobaciones y rechazos QR se realizan mediante un comprador TEST que
escanea la caja y utiliza medios de prueba oficiales. El endpoint `/events`
permanece restringido a Orders Point.

Una Order QR pendiente puede cancelarse desde el modal TEST. Odoo ejecuta
`POST /v1/orders/{order_id}/cancel` y, incluso si el resultado del POST es
incierto, ejecuta obligatoriamente un GET. Sólo la respuesta consultada puede
actualizar el estado local. La cancelación conserva una clave de idempotencia
propia, distinta de la utilizada para crear la Order.

## Separación y seguridad

Cada intento conserva `order_type`, terminal o caja, referencia, idempotencia,
IDs y resultado. Una Order Point nunca valida un pago QR ni una Order QR valida
un pago Point. Los Access Tokens permanecen en backend y no se registran ni se
devuelven al navegador.
