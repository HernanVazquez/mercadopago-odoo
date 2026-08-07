# Etapa 2: seguimiento en tiempo real y simulador TEST

## Alcance implementado

Después de que **Enviar al Point** crea correctamente una Order, Odoo abre un
modal de seguimiento sobre el intento persistente `mercadopago.point.order`.
El modal consulta el backend de Odoo, y solamente el backend ejecuta el GET a
Mercado Pago. Empieza cada 2,5 segundos, reduce la frecuencia a los 30 y 90
segundos y nunca superpone solicitudes.

El seguimiento se detiene al cerrar el modal, después de tres errores
consecutivos o cuando la Order informa `processed`, `failed`, `canceled`,
`expired` o `refunded`. Un error de red conserva el último estado conocido y
habilita una consulta manual; nunca se interpreta como rechazo.

Un resultado `processed` sólo se muestra como **Pago acreditado** cuando el GET
también verificó `payment.status = processed`, `payment.status_detail =
accredited`, referencia, identificadores e igualdad exacta entre el importe
enviado por Odoo y `paid_amount`. El modal no publica `account.payment` ni
valida recibos.

## Simulador exclusivo de TEST

La herramienta aparece dentro del mismo modal solamente si la configuración
es TEST, el pago está en borrador y la Order admite una transición. El backend
repite todas esas validaciones.

El flujo es siempre:

1. Odoo envía `POST /v1/orders/{order_id}/events` a Mercado Pago TEST.
2. Mercado Pago procesa el evento.
3. Odoo ejecuta inmediatamente `GET /v1/orders/{order_id}`.
4. Sólo la respuesta del GET actualiza el intento local.

Las opciones se limitan al esquema oficial vigente para Argentina: aprobado
(`processed/accredited`), rechazado (`failed` con un `status_detail`
documentado) y cancelado (`canceled`). Crédito y débito restringen las marcas
a sus identificadores MLA; QR no envía marca ni cuotas. Para crédito, cuotas
es un entero positivo, que es la restricción publicada por el esquema.

Referencia oficial consultada:

- <https://www.mercadopago.com.ar/developers/es/reference/in-person-payments/point/orders/simulate-order/post>

## Seguridad y contabilidad

- El navegador nunca recibe el Access Token ni llama directamente a Mercado
  Pago.
- El cliente HTTP no registra tokens ni headers y sanitiza errores.
- No se modificó `action_post()`: continúa siendo una barrera local y sin HTTP.
- No se publica automáticamente el pago, no se concilia y no se agregó ningún
  puente con `account_payment_group` ni `talar_sale`.
- No se implementaron Webhooks ni producción.

## Repetir la suite en DEV

Desde el servidor, con el módulo copiado o actualizado bajo
`/opt/odoo16/custom-addons`:

```bash
sudo systemctl stop odoo16.service
sudo -u odoo16 /opt/odoo16/odoo-bin \
  -c /etc/odoo.conf \
  -d talar-v1 \
  -u mercadopago_point_odoo \
  --test-enable \
  --test-tags /mercadopago_point_odoo \
  --stop-after-init
sudo systemctl start odoo16.service
```

Los tests JavaScript están incorporados al bundle estándar de Odoo 16
`web.qunit_suite_tests`. Con DEV iniciado y en modo assets, se pueden repetir
desde:

```text
https://dev.metalurgicaeltalar.com.ar/web/tests?module=mercadopago_point_odoo
```

La suite Python reemplaza todas las llamadas HTTP con mocks y crea su propia
compañía, cuentas, diario y pagos de prueba; no depende de datos comerciales.
