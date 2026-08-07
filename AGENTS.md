# Contexto del proyecto

- ERP: Odoo 16 Community.
- El desarrollo está destinado inicialmente a una única empresa y una única cuenta de Mercado Pago.
- La integración utilizará Mercado Pago Point con la API Orders. No utilizará la API legacy Payment Intent.
- El flujo de venta actual se realiza desde el módulo Ventas, sobre presupuestos y pedidos. No se utiliza Odoo POS.
- Actualmente existe un único Point Smart compartido por todos los usuarios.
- Odoo deberá enviar el importe a ese Point y recibir posteriormente el resultado del cobro.
- Se debe conservar `external_reference` para relacionar la Order de Mercado Pago con el documento de Odoo.
- Se deben almacenar los identificadores de Order y Payment devueltos por Mercado Pago.
- El resultado puede contener medio de pago, marca de tarjeta, cuotas, importe pagado y estado.
- Las comisiones, impuestos, retenciones y el neto acreditado se tratarán posteriormente mediante conciliación o reportes financieros. No asumir que esos datos vienen completos en la respuesta de la Order.

## Reglas de implementación

- Implementar Webhooks y validar criptográficamente su firma antes de procesarlos.
- Mantener las credenciales privadas exclusivamente en el backend.
- Nunca registrar Access Tokens ni secretos en logs.
- Implementar manejo de errores, timeouts e idempotencia en las llamadas a Mercado Pago.
- No crear automáticamente movimientos contables definitivos hasta que esté claramente definido el flujo contable existente de Odoo.
- No modificar módulos estándar de Odoo. Toda personalización debe estar contenida en módulos propios.
- Priorizar cambios pequeños, auditables y reversibles.

## Entornos

- Desarrollo: https://dev.metalurgicaeltalar.com.ar
- Producción: https://metalurgicaeltalar.com.ar
- Configuración de Odoo en el servidor: `/etc/odoo.conf`.
- Servicio: `odoo16.service`.
- Custom addons: `/opt/odoo16/custom-addons`.
- Base utilizada actualmente en el clon de desarrollo: `talar-v1`.

