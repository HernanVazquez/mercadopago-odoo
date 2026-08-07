# Análisis técnico y arquitectura propuesta

## 1. Alcance y método

Este informe analiza las copias locales de `payment_mercado_pago`, `account_payment_group` y `talar_sale` con el único objetivo de diseñar una futura integración entre Odoo 16 Community y Mercado Pago Point mediante la API Orders. No se implementó funcionalidad ni se modificó ninguno de los módulos de referencia.

Las referencias fueron recibidas dentro de `references/mp_odoo_references.tar`. Las rutas citadas a continuación corresponden a los archivos contenidos en ese paquete, expresadas como `references/<módulo>/<archivo>` para facilitar su comparación con la instalación del servidor.

El informe usa tres categorías:

- **Comprobado en el código:** comportamiento que surge directamente de los archivos entregados.
- **Propuesta:** diseño recomendado para `mercadopago_point_odoo`; todavía no está implementado.
- **Pendiente de verificar:** aspecto que requiere el código fuente base de Odoo, inspección de la base `talar-v1`, credenciales de prueba o una decisión funcional/contable.

### 1.1 Limitaciones de la evidencia

El paquete no contiene los módulos estándar `account` y `payment`, ni un volcado de la configuración de la base. Por eso, se puede comprobar qué agregan los tres addons, pero no reconstruir solamente con ellos todos los campos, dominios y automatismos heredados de Odoo 16. En particular, la relación interna entre un `payment.provider` online y las líneas de métodos de pago de un diario debe contrastarse con el core exacto instalado y con los registros de `talar-v1`.

En Odoo contable, los nombres técnicos pertinentes son `account.payment.method` y `account.payment.method.line`. En este informe se usa “método” para el primero y “línea de método” para el segundo. No deben confundirse con `payment.provider` y `payment.transaction`, que pertenecen al framework de pagos online.

## 2. Análisis de `payment_mercado_pago`

### 2.1 Naturaleza del módulo

**Comprobado en el código.** Es el proveedor oficial de pagos online de Mercado Pago, no una integración de Point para cobros presenciales:

- El manifiesto lo denomina `Payment Provider: Mercado Pago`, depende únicamente de `payment` y carga vistas de proveedor, una plantilla de redirección y datos del proveedor (`references/payment_mercado_pago/__manifest__.py`, líneas 4-18).
- Su README declara un flujo con redirección y explica que combina Checkout Pro con Checkout API (`references/payment_mercado_pago/README.md`, líneas 5-31).
- La plantilla `redirect_form` genera un formulario cuyo destino es una URL devuelta por Mercado Pago (`references/payment_mercado_pago/views/payment_mercado_pago_templates.xml`, líneas 5-8).

Por lo tanto, reutilizar este flujo implicaría adoptar semántica de Checkout web que no corresponde al objetivo del proyecto.

### 2.2 `account.payment.method` y `account.payment.method.line`

**Comprobado en el código.** El addon entregado:

- no hereda ni crea código Python para `account.payment.method`;
- no hereda ni crea código Python para `account.payment.method.line`;
- no define en sus XML un registro de ninguno de esos modelos;
- no hereda `account.journal` ni `account.payment`.

El único registro de datos relacionado con la activación es `payment.payment_provider_mercado_pago`, modelo `payment.provider`, al que asigna `code = mercado_pago` y la plantilla de redirección (`references/payment_mercado_pago/data/payment_provider_data.xml`, líneas 4-7).

**Conclusión comprobada.** Este addon, por sí solo, no demuestra que “Mercado Pago” sea un método seleccionable manualmente en Pagos entrantes de un diario ni define qué ocurre al elegirlo en un `account.payment`. Si aparece allí en la base de desarrollo, el origen puede estar en el módulo estándar `payment`, en datos base cargados para el proveedor o en otra personalización/configuración no incluida.

**Pendiente de verificar.** En `talar-v1` se debe inspeccionar:

1. los registros `account.payment.method` con código o nombre Mercado Pago;
2. las `account.payment.method.line` del diario Mercado Pago;
3. su eventual relación con un `payment.provider`;
4. los campos y dominios exactos de Odoo 16 usados por el formulario real de `account.payment`.

### 2.3 `payment.provider`

**Comprobado en el código.** La clase `Paymentprovider` hereda `payment.provider` y:

- agrega `mercado_pago` a la selección `code` (`references/payment_mercado_pago/models/payment_provider.py`, clase `Paymentprovider`, líneas 18-23);
- agrega `mercado_pago_access_token`, obligatorio para ese proveedor y visible solamente para `base.group_system` (`payment_provider.py`, líneas 24-28);
- filtra proveedores compatibles según una lista estática de monedas (`_get_compatible_providers`, líneas 32-41; lista en `references/payment_mercado_pago/const.py`, líneas 3-28);
- centraliza solicitudes HTTP en `_mercado_pago_make_request` (`payment_provider.py`, líneas 43-82).

La vista muestra el Access Token como campo de contraseña cuando `code == mercado_pago` (`references/payment_mercado_pago/views/payment_provider_views.xml`, líneas 4-18). Esto restringe su exposición en la interfaz, pero el campo `Char` no implica cifrado en reposo.

La función HTTP usa `Authorization: Bearer <token>` y timeout de 10 segundos (`payment_provider.py`, líneas 55-63). Para POST ejecuta `raise_for_status()`, registra endpoint y payload ante error y transforma errores de conexión/timeout en `ValidationError` (líneas 64-81).

Observaciones relevantes:

- El header con el token no se registra explícitamente.
- Sí se registra el payload completo ante un error POST (`payment_provider.py`, líneas 67-69), lo que puede exponer datos personales aunque no el token.
- La rama GET no llama a `raise_for_status()` antes de intentar devolver JSON (`payment_provider.py`, líneas 60-63 y 82); una respuesta HTTP de error puede no quedar tratada correctamente.
- No se observa un header de idempotencia en este helper.
- `neutralize.sql` borra el token al neutralizar una base (`references/payment_mercado_pago/data/neutralize.sql`, líneas 1-3), una idea útil para clones de producción.

### 2.4 `payment.transaction`

**Comprobado en el código.** La clase `PaymentTransaction` hereda `payment.transaction` (`references/payment_mercado_pago/models/payment_transaction.py`, líneas 18-19) y materializa el flujo de Checkout:

1. `_get_specific_rendering_values()` llama a `/checkout/preferences` y obtiene `init_point` o `sandbox_init_point` para redirigir al comprador (líneas 21-48).
2. `_mercado_pago_prepare_preference_request_payload()` arma URLs de retorno y Webhook, utiliza `self.reference` como `external_reference`, envía un ítem por el monto de la transacción e impone una cuota (líneas 50-90).
3. `_get_tx_from_notification_data()` busca la transacción por `external_reference` y `provider_code = mercado_pago` cuando el `super()` no encontró una única transacción (líneas 92-115).
4. `_process_notification_data()` toma el `payment_id`, lo guarda en `provider_reference`, consulta `/v1/payments/{id}` y traduce el estado remoto a `_set_pending()`, `_set_done()`, `_set_canceled()` o `_set_error()` (líneas 117-157).

El mapeo considera pendientes `pending`, `in_process` e `in_mediation`; terminados `approved` y `refunded`; cancelados `cancelled` y `null` (`references/payment_mercado_pago/const.py`, líneas 30-36). Para Point Orders no debe copiarse este mapeo: `refunded` requiere tratamiento propio y no equivale a un cobro disponible.

Los tests confirman que una respuesta verificada con `status = approved` lleva la transacción a `done` (`references/payment_mercado_pago/tests/test_payment_transaction.py`, método `test_processing_notification_data_confirms_transaction`, líneas 60-69).

### 2.5 `account.payment`

**Comprobado en el código.** `payment_mercado_pago` no hereda `account.payment` ni crea uno explícitamente. Cualquier creación contable posterior al estado `done` pertenecería a la lógica heredada del módulo estándar `payment`, que no está en las referencias.

**Pendiente de verificar.** No debe suponerse que el `account.payment` eventualmente creado por el framework online sea equivalente al pago que el usuario edita dentro de un Recibo de Cliente. Son puntos de entrada y ciclos de vida diferentes.

### 2.6 Webhooks, estados y errores

**Comprobado en el código.** `MercadoPagoController` publica:

- `GET /payment/mercado_pago/return`, que registra los datos recibidos y delega a `payment.transaction._handle_notification_data()` (`references/payment_mercado_pago/controllers/main.py`, líneas 14-34);
- `POST /payment/mercado_pago/webhook/<reference>`, público y sin CSRF (`main.py`, líneas 36-64).

El Webhook procesa solamente `payment.created` y `payment.updated`, extrae el ID y delega al framework de transacciones. Ante `ValidationError`, registra la excepción y responde igualmente vacío para reconocer la notificación (líneas 50-64). Los tests comprueban la delegación, pero no una validación criptográfica (`references/payment_mercado_pago/tests/test_processing_flows.py`, líneas 29-40).

No aparece validación de `x-signature`, `x-request-id`, timestamp ni secreto. Tampoco se observa protección explícita contra replay. Por esa razón, el controlador no es reutilizable como implementación de seguridad para el nuevo proyecto.

### 2.7 Qué reutilizar conceptualmente

**Propuesta.** Son reutilizables como patrones, no como flujo ni como clases a heredar:

- credencial privada accesible solo desde backend y grupos administrativos;
- helper HTTP centralizado con timeout y traducción de errores;
- `external_reference` como correlación estable;
- verificación del recurso mediante GET luego de recibir una notificación;
- máquina de estados interna explícita;
- tests de payload, correlación, notificaciones y estados;
- neutralización de credenciales en clones.

No se recomienda reutilizar:

- `/checkout/preferences`, redirecciones o plantillas web;
- `payment.transaction` como entidad principal de Point;
- el código de proveedor `mercado_pago`;
- el endpoint `/payment/mercado_pago/webhook/...`;
- el mapeo de estados de Payments/Checkout;
- el Webhook sin firma;
- la suposición de que `refunded` es un pago terminado favorablemente.

## 3. Análisis de `account_payment_group`

### 3.1 Modelo y creación del Recibo de Cliente

**Comprobado en el código.** `AccountPaymentGroup` crea el modelo `account.payment.group`, hereda `mail.thread` y define estados `draft`, `confirmed`, `posted` y `cancel` (`references/account_payment_group/models/account_payment_group.py`, clase `AccountPaymentGroup`, líneas 22-27 y 166-179).

El grupo contiene:

- cliente/proveedor, compañía, moneda, fecha, memo y talonario (`account_payment_group.py`, líneas 28-119);
- apuntes seleccionados para pagar en `to_pay_move_line_ids` y su vista auxiliar `debt_move_line_ids` (líneas 189-224);
- líneas de pago en `payment_ids`, One2many hacia `account.payment.payment_group_id` (líneas 250-261);
- totales, diferencia y apuntes conciliados calculados (por ejemplo, `_compute_payments_amount`, líneas 574-578, y `_compute_matched_move_line_ids`, líneas 530-553).

La acción de menú de Recibos de Clientes abre este modelo con `default_partner_type = customer` (`references/account_payment_group/views/account_payment_group_view.xml`, líneas 210-218 y 257-258).

Desde una factura, la segunda definición —y por lo tanto la efectiva dentro de esa clase Python— de `action_account_invoice_payment_group()` abre un grupo y pasa `default_partner_id`, los apuntes abiertos mediante `to_pay_move_line_ids`, `pop_up` y compañía (`references/account_payment_group/models/account_move.py`, líneas 180-206). La vista reemplaza el botón estándar de registro de pago para llamar esa acción (`references/account_payment_group/views/account_move_view.xml`, líneas 4-18).

### 3.2 Creación y edición de `account.payment`

**Comprobado en el código.** `AccountPayment` hereda `account.payment` y agrega `payment_group_id` con borrado en cascada desde el grupo (`references/account_payment_group/models/account_payment.py`, clase `AccountPayment`, líneas 11-19), además de campos de importes firmados y conversión.

La pestaña “Lineas de pagos” edita el One2many `payment_ids`. El contexto precarga tipo inbound/outbound, fecha, partner, importe de diferencia y `payment_group = True` (`references/account_payment_group/views/account_payment_group_view.xml`, líneas 164-169). La vista de árbol muestra `journal_id`, `payment_method_description`, importes y estado; el método técnico está comentado en esa grilla (`references/account_payment_group/views/account_payment_view.xml`, líneas 33-55).

El botón de detalle abre el formulario estándar heredado de `account.payment` mediante `show_details()` (`references/account_payment_group/models/account_payment.py`, líneas 388-403). Por consiguiente, la selección efectiva de diario y línea de método depende principalmente del formulario y de los onchanges estándar de Odoo, con modificaciones de los addons instalados.

`get_journals_domain()` agrega la compañía del grupo y `_onchange_payment_type()` limpia el diario cuando el contexto indica que se viene desde un grupo (`account_payment.py`, líneas 205-220). El override de `create()` normaliza `payment_type_copy`, elimina `destination_journal_id`, llama a `super()` y, en ciertos contextos especiales, crea y publica automáticamente un grupo (`account_payment.py`, líneas 304-369).

### 3.3 Diario y método de pago

**Comprobado en el código.** En la creación interactiva ordinaria del Recibo no hay lógica propia que elija Mercado Pago ni otro método: se utiliza la selección estándar de `account.payment`.

El código contiene una mezcla de convenciones de distintas generaciones:

- usa `payment_method_line_id.code` al validar cheques (`account_payment.py`, líneas 91-118);
- usa campos `inbound_payment_method_ids` y `payment_method_id` en `pay_now()` (`references/account_payment_group/models/account_move.py`, líneas 120-146);
- la vista agrupa por `payment_method_id` y muestra `payment_method_description` (`account_payment_view.xml`, líneas 33-55 y 209-230).

**Conclusión.** Para un desarrollo nuevo en Odoo 16 debe considerarse canónico `payment_method_line_id`, pero la compatibilidad real de esta copia requiere validación en `talar-v1`. No conviene copiar el uso legacy de `payment_method_id` sin estudiar los fields efectivos.

### 3.4 Confirmación y publicación

**Comprobado en el código.** El grupo implementa dos modalidades:

- En modalidad simple, el botón “Validar” llama directamente a `post()` desde borrador.
- En doble validación, `confirm()` cambia el grupo a `confirmed`, y luego “Validar” llama a `post()` (`account_payment_group_view.xml`, líneas 68-81; `account_payment_group.py`, métodos `confirm` y `post`, líneas 750-827).

`post()`:

1. asigna numeración del talonario si corresponde;
2. exige al menos una línea de pago;
3. valida que importe a pagar e importe pagado coincidan en doble validación;
4. ejecuta `action_post()` sobre cada `account.payment` todavía en borrador;
5. toma los apuntes por cobrar/pagar de los pagos;
6. los reconcilia con `to_pay_move_line_ids`;
7. marca el grupo `posted` (`account_payment_group.py`, líneas 758-827).

Luego realiza un `self.env.cr.commit()` explícito y actualiza por SQL el `payment_state` de ciertas facturas (`account_payment_group.py`, líneas 832-837). Este comportamiento es técnicamente sensible: corta la atomicidad de la operación y evita los mecanismos normales del ORM para la actualización final.

### 3.5 Vínculo con facturas y conciliación

**Comprobado en el código.** `_get_to_pay_move_lines_domain()` selecciona apuntes publicados, conciliables, no conciliados, de la cuenta por cobrar/pagar del partner y de la compañía (`account_payment_group.py`, líneas 640-668). `add_all()` carga esos apuntes (líneas 670-673).

Al publicar, se reconcilian los apuntes receivable/payable generados por los pagos con los apuntes seleccionados del grupo mediante `(counterpart_aml + to_pay_move_line_ids).reconcile()` (`account_payment_group.py`, líneas 815-825). `_compute_matched_move_line_ids()` reconstruye después los documentos alcanzados consultando `account.partial.reconcile` (`account_payment_group.py`, líneas 530-553). `AccountMove._compute_payment_groups()` obtiene los grupos desde los pagos asociados a los apuntes (`references/account_payment_group/models/account_move.py`, líneas 24-39).

### 3.6 Puntos de extensión

**Propuesta basada en el código.** Los puntos menos invasivos para la integración son:

- `account.payment.action_post()`: barrera general que impida publicar un pago Point sin una Order aprobada. Es llamado tanto desde el formulario estándar como desde `account.payment.group.post()`.
- Métodos nuevos y explícitos sobre `account.payment`, por ejemplo “Iniciar cobro Point”, “Actualizar estado” y “Cancelar Order”; evitan cambiar el significado de `action_post()`.
- Vistas heredadas de `account.payment` para mostrar configuración y estado Point. Al editar una línea desde el Recibo, el módulo ya abre ese formulario.
- `account.payment.action_cancel()` / `action_draft()` solamente para validar coherencia local; no deberían disparar por sí solos un reembolso remoto.
- `account.payment.group.post()` solo si se necesita un mensaje agregado o una acción masiva específica. El guard en `account.payment.action_post()` ya evita que el grupo continúe hacia conciliación.

`account.payment.group.post()` es monolítico y no expone hooks intermedios antes/después de publicar cada pago o antes de conciliar. Una herencia profunda sería frágil y duplicaría código de terceros. `confirm()` solo cambia el estado y tampoco es el lugar adecuado para afirmar que el dinero fue cobrado.

**Pendiente de decidir.** Si `mercadopago_point_odoo` debe depender directamente de `account_payment_group` o si conviene separar un addon base (`account.payment` + Point) y un addon puente opcional para Recibos. La segunda opción ofrece mayor independencia; la primera reduce módulos y coincide con el flujo actual.

## 4. Análisis de `talar_sale`

### 4.1 Alcance de este análisis

`talar_sale` es un desarrollo de terceros. Las conclusiones de esta sección son descriptivas. No se propone modificarlo, heredarlo ni agregarlo como dependencia.

### 4.2 Wizard “Agregar Pago” y opciones numeradas

**Comprobado en el código.** El botón `add_installment()` de `sale.order` abre `sale.order.installment.wizard` para pedidos en estado `sale` o `done` y calcula el monto residual (`references/talar_sale/models.py`, clase `SaleOrder`, líneas 128-145). El wizard solicita:

- `installment_id`, relación a `account.journal.installment`;
- monto residual;
- monto a asignar (`references/talar_sale/wizard/wizard_model.py`, clase `SaleOrderInstallmentWizard`, líneas 46-65).

Al confirmar, crea un `sale.order.installment` con pedido, opción y monto. La vista impide crear opciones al vuelo (`references/talar_sale/wizard/wizard_view.xml`, líneas 4-24).

`account.journal.installment` contiene `journal_id`, cantidad de cuotas (`installments`) y porcentaje de recargo (`percent`). Su `name_get()` devuelve `"<nombre del diario> - <cantidad de cuotas>"` (`references/talar_sale/models.py`, clase `AccountJournalInstallment`, líneas 442-454).

**Conclusión comprobada.** Opciones como “Mercado Pago - 1” y “Mercado Pago - 2” no representan terminales, cuentas ni credenciales. Representan registros configurados de cuotas para un diario cuyo nombre es “Mercado Pago”; el número es el campo `installments`.

### 4.3 Cuotas y recargos

**Comprobado en el código.** `sale.order.installment` guarda opción, porcentaje y monto (`models.py`, líneas 413-440). La configuración de cuotas se edita dentro del formulario del diario y permite cantidad de cuotas y recargo; el diario también guarda un producto para cargos financieros (`references/talar_sale/sale_view.xml`, líneas 209-228; `models.py`, líneas 107-111).

`btn_add_installments()` exige que la suma de montos coincida con el total, salvo cuenta corriente. Para opciones con recargo, elimina recargos previos y crea líneas de venta con el producto configurado y un precio calculado sobre el importe sin impuestos (`models.py`, líneas 165-195).

### 4.4 Creación de `account.payment` y vínculo con `sale.order`

**Comprobado en el código.** `talar_sale` agrega `order_id` a `account.payment` (`models.py`, líneas 102-105) y `payment_ids` a `sale.order` por esa relación inversa (`models.py`, líneas 351-369).

`btn_add_payments()` recorre las opciones elegidas y crea un `account.payment` por cada una con:

- partner del pedido;
- diario tomado de `installment.installment_id.journal_id`;
- `payment_type = inbound`;
- monto de la opción;
- moneda, fecha y referencia;
- `order_id` del pedido (`models.py`, líneas 197-223).

Inmediatamente llama `payment_id.action_post()` (línea 223). No envía explícitamente `payment_method_id` ni `payment_method_line_id`; por ello depende del default/onchange/core para completar el método. Tampoco espera confirmación externa antes de considerar el pago publicado.

Antes de ese bucle, el método cancela el pedido, lo vuelve a borrador, recalcula recargos y lo confirma nuevamente (`models.py`, líneas 199-207). Además asigna `sale.order.payment_state = paid` antes de crear, facturar y conciliar (`líneas 205-208`).

### 4.5 Factura y conciliación posterior

**Comprobado en el código.** Después de publicar los pagos, `btn_add_payments()`:

1. crea la factura con `_create_invoices()`;
2. asigna el diario de facturación y aplica reglas particulares de impuestos para el diario código `0099`;
3. publica la factura;
4. la guarda en `sale.order.invoice_id`;
5. reúne la línea por cobrar de la factura y las líneas por cobrar de todos los pagos;
6. ejecuta `aml_obj.reconcile()` (`models.py`, líneas 226-249).

El vínculo pago-pedido es directo mediante `account.payment.order_id`; el vínculo pedido-factura usa tanto el mecanismo estándar de ventas como el campo adicional `invoice_id` (`models.py`, líneas 240 y 351-364).

### 4.6 Modificaciones de modelos estándar relevantes

**Comprobado en el código.** En materia de pagos y ventas, `talar_sale` modifica:

- `account.payment`: `order_id` y `andres_payment_date` (`models.py`, líneas 102-105 y 554-564);
- `account.journal`: opciones de cuotas y producto de recargo (`líneas 107-111`);
- `sale.order`: wizard, generación/publicación de pagos y factura, estados propios, cuotas, vínculo a pagos/factura y un override de `action_confirm()` (`líneas 113-373`);
- `sale.order.line`: línea de recargo y campos de cantidades/longitudes (`líneas 376-410`);
- `account.move` y `account.move.line`: orden de compra y campos de cantidades/longitudes (`líneas 497-538`).

También modifica stock, productos, lista de precios, partners, equipos de venta y transportistas, fuera del alcance de la integración Point.

### 4.7 Consecuencias para compatibilidad futura

**Propuesta.** `mercadopago_point_odoo` debe detectar Point exclusivamente por el código de la `account.payment.method.line`, nunca por el nombre del diario ni por `account.journal.installment`. Así, “Mercado Pago - 1” seguirá significando una configuración comercial de cuotas de `talar_sale`, no una orden remota.

Existe, sin embargo, un riesgo operativo: `talar_sale` no fija línea de método y publica el pago inmediatamente. Si el diario Mercado Pago queda configurado con Point como único/default entrante, ese pago podría recibir la línea Point y el guard propuesto impediría `action_post()`. Es preferible bloquear antes que registrar un cobro inexistente, pero ese escenario debe probarse antes de habilitar el método en el mismo diario.

Alternativas a decidir:

- usar un diario separado “Mercado Pago Point” para el nuevo flujo;
- conservar una línea manual además de Point en el diario existente y verificar cuál toma `talar_sale`;
- configurar Point en el mismo diario solo después de una prueba de regresión completa.

No se debe adaptar `btn_add_payments()`, `sale.order.installment` ni `order_id` desde el nuevo módulo en esta etapa.

## 5. Arquitectura propuesta para `mercadopago_point_odoo`

### 5.1 Principios

**Propuesta.** La integración debe:

1. tener como origen contable a `account.payment`;
2. representar cada intento remoto con un modelo propio;
3. mantener separado el estado remoto de la Order y el estado contable del pago;
4. no publicar ni conciliar hasta verificar un resultado favorable;
5. no depender de `talar_sale` ni de sus campos;
6. convivir con Checkout usando códigos, modelos, rutas y credenciales propios;
7. ser idempotente y auditable;
8. tratar el Webhook como señal para consultar/verificar el recurso, no como verdad suficiente sin firma;
9. no crear movimientos contables adicionales para comisiones, impuestos, retenciones o neto acreditado.

La API oficial actual exige `X-Idempotency-Key`, `external_reference`, un único pago por Order Point y `terminal_id`. También devuelve ID de Order e ID de la transacción de pago. Estos requisitos respaldan un registro persistente por intento. Véase la [referencia oficial de creación de Orders Point](https://www.mercadopago.com.ar/developers/es/reference/in-person-payments/point/orders/create-order/post).

### 5.2 Dependencias y capas

**Propuesta.** Hay dos opciones válidas:

**Opción A — un solo addon orientado al entorno actual**

- `mercadopago_point_odoo` dependería de `account` y `account_payment_group`.
- Toda la integración sigue siendo independiente de `talar_sale`.
- Es más simple de desplegar, pero acopla el módulo a este proveedor de Recibos.

**Opción B — núcleo y puente opcional (recomendada para mayor independencia)**

- `mercadopago_point_odoo`: depende de `account`; define configuración, método, Orders, cliente API, Webhook y guard de `account.payment`.
- un addon puente futuro, por ejemplo `mercadopago_point_payment_group`: depende del núcleo y de `account_payment_group`; agrega presentación/acciones específicas del Recibo si resultan necesarias.

El guard genérico en `account.payment.action_post()` ya protege el flujo de Recibos porque `account.payment.group.post()` invoca ese método. Por ello, el puente puede ser mínimo o incluso innecesario en la primera versión.

### 5.3 Configuración y credenciales

**Propuesta.** Crear un modelo backend `mercadopago.point.config` con, al menos:

| Dato | Ubicación propuesta | Motivo |
|---|---|---|
| Empresa | `company_id` | Preparar multiempresa aunque inicialmente haya una sola. |
| Cuenta/aplicación | nombre y campos identificadores no secretos | Auditabilidad. |
| Access Token | campo restringido o referencia a secreto externo | No mezclar con Checkout y limitar lectura. |
| Secreto de Webhook | campo restringido o referencia a secreto externo | Validación HMAC. |
| Terminal | `terminal_id` | La Order se asigna al Point específico. |
| Activa / entorno | campos explícitos | Evitar enviar pruebas a producción. |
| Timeout | configuración con límites seguros | No dejar requests indefinidos. |

Una sola configuración activa por compañía es suficiente inicialmente. La Order debe copiar como snapshot el `terminal_id` utilizado, para que cambiar la configuración no altere el historial.

El Access Token de `payment.provider.mercado_pago_access_token` no debería reutilizarse automáticamente:

- pertenece al proveedor Checkout `code = mercado_pago`;
- acoplaría Point a la instalación/activación del proveedor web;
- puede ser otra aplicación o credencial;
- aumentaría el riesgo de conflictos y permisos cruzados.

Si negocio confirma que ambas integraciones usan exactamente la misma aplicación/cuenta, podría ofrecerse una migración o selección explícita, nunca una lectura implícita.

**Pendiente de decidir.** Almacenamiento de secretos:

- **preferido:** secret manager/variable de entorno y en Odoo solo una referencia;
- **alternativa operativa:** campos en base con acceso limitado a un grupo administrativo específico. El widget `password` y `groups` reducen exposición visual, pero no cifran la base.

Debe existir una estrategia de neutralización para clones de producción equivalente conceptualmente a `payment_mercado_pago/data/neutralize.sql`.

### 5.4 Método entrante “Mercado Pago Point”

**Propuesta.** Definir un `account.payment.method` propio con:

- nombre: `Mercado Pago Point`;
- código técnico único: `mercadopago_point`;
- tipo: entrante.

El diario Mercado Pago tendrá una `account.payment.method.line` para ese método. La línea debería vincularse a `mercadopago.point.config`, o resolver la configuración activa por `journal_id.company_id`. El vínculo explícito en la línea es más claro si en el futuro existen varias terminales/cuentas.

No usar `code = mercado_pago`, porque ese identificador ya representa Checkout en `payment.provider`. No crear ni activar un `payment.provider` para Point y no presentar Point como método web en portales/e-commerce.

**Pendiente de verificar.** Forma exacta de declarar/habilitar una `account.payment.method.line` personalizada en el core Odoo 16 instalado y efectos de tener varias líneas entrantes en el diario actual.

### 5.5 Modelo de Order y relación con `account.payment`

**Propuesta.** Crear `mercadopago.point.order` como entidad de integración y auditoría. Relación:

```text
account.payment 1 ─────── 0..N mercadopago.point.order
                              └── una Order/intento remoto
```

La cardinalidad 1:N es intencional: una Order puede expirar, fallar o cancelarse y el operador puede iniciar un nuevo intento para el mismo pago. Los IDs remotos no deben sobrescribirse perdiendo historia.

Campos recomendados:

| Campo lógico | Modelo/campo propuesto |
|---|---|
| Pago Odoo | `payment_id` Many2one requerido a `account.payment`, con borrado restringido si existe actividad remota. |
| Configuración | `config_id`. |
| Número de intento | `attempt_number`, único por pago. |
| Referencia externa | `external_reference`, única, inmutable, sin PII y máximo 64 caracteres. |
| Idempotencia de creación | `idempotency_key`, única e inmutable. |
| Idempotencia de cancelación | `cancel_idempotency_key`, distinta de la de creación. |
| Order ID | `mp_order_id`, único e indexado. |
| Payment ID | `mp_payment_id`, indexado; único si el contrato real lo garantiza. |
| Estado Order | `status` y `status_detail`. |
| Estado de pago remoto | `payment_status` y `payment_status_detail`. |
| Importe solicitado | `amount` y `currency_id`, snapshot del pago. |
| Importe pagado | `paid_amount`. |
| Medio real | `payment_method_type`. |
| Marca | `payment_method_id` (por ejemplo, identificador de marca devuelto por MP). |
| Cuotas | `installments`. |
| Terminal usada | `terminal_id`, snapshot. |
| Fechas | creación, envío, última sincronización, finalización. |
| Error | código, mensaje sanitizado, categoría recuperable/no recuperable. |

En `account.payment` se agregarían un One2many a Orders, un puntero calculado/validado al intento vigente y campos relacionados de solo lectura para facilitar la interfaz. Los datos autoritativos permanecen en `mercadopago.point.order`.

No almacenar respuestas completas indiscriminadamente. Si se conserva JSON para auditoría/diagnóstico, debe sanitizarse, limitarse en tamaño, restringirse por ACL y nunca contener headers, token, secreto ni datos de tarjeta sensibles.

La documentación actual de Point indica que el resultado consultado puede incluir `paid_amount`, `payment_method.type`, `payment_method.id` e `installments`, y recomienda guardar los IDs de Order y Payment. Véase [procesamiento de pagos Point](https://www.mercadopago.com.ar/developers/es/docs/mp-point/payment-processing).

### 5.6 `external_reference`

**Propuesta.** Debe ser:

- generada en backend;
- única por intento de Order;
- estable ante reintentos técnicos del mismo intento;
- sin nombre, email, CUIT u otro dato personal;
- independiente del nombre editable del Recibo;
- válida en clones y entre compañías/bases.

Formato posible: `odoo-ap-<identificador-instalación>-<payment-id>-<intento>`, respetando los 64 caracteres y caracteres permitidos. El identificador de instalación y su comportamiento al clonar la base requieren definición. La referencia debe persistirse antes de enviar la solicitud.

### 5.7 Flujo propuesto

**Propuesta.** Flujo inicial, deliberadamente conservador:

1. El usuario crea/edita un `account.payment` en borrador.
2. Selecciona Diario Mercado Pago y línea `Mercado Pago Point`.
3. Guarda la línea y ejecuta “Iniciar cobro Point”.
4. Odoo crea un intento local con `external_reference` e idempotency key estables.
5. El servicio API envía `POST /v1/orders` con timeout, token backend, terminal, monto e idempotencia.
6. Guarda `mp_order_id`, `mp_payment_id` y estado inicial.
7. La terminal procesa el cobro.
8. El Webhook firmado señala un cambio; Odoo valida firma y consulta `GET /v1/orders/{id}` para obtener el estado autoritativo.
9. Si la Order queda `processed`, Odoo muestra el pago como remoto aprobado.
10. En la primera versión, el usuario valida el Recibo; `account.payment.group.post()` llama `account.payment.action_post()`, el guard confirma que la Order está aprobada y recién entonces Odoo publica y concilia.

No se recomienda que `action_post()` cree la Order y simplemente retorne dejando el pago en borrador: el caller espera semántica sincrónica y `account.payment.group.post()` continuaría con conciliación/estado. Tampoco conviene lanzar una excepción después de crear una Order remota, porque el rollback local puede perder la correlación.

El método de inicio debe capturar errores, guardar el intento y devolver feedback sin provocar rollback de la auditoría. Para el pequeño intervalo entre respuesta remota y commit local, la clave debe ser determinista para que el mismo intento pueda recuperarse con seguridad. Antes de implementar se debe decidir si se acepta este patrón sincrónico o se usa una cola/outbox transaccional.

### 5.8 Estados

**Propuesta.** Guardar el valor remoto sin reinterpretarlo y, opcionalmente, calcular una categoría interna:

| Status de Order actual | Categoría interna sugerida | ¿Permite publicar? |
|---|---|---|
| `created` | pendiente | No |
| `at_terminal` | pendiente en terminal | No |
| `action_required` | requiere acción | No |
| `processed` | aprobado | Sí, sujeto a validar importe y pago asociado |
| `failed` | fallido | No |
| `expired` | expirado | No |
| `canceled` | cancelado | No |
| `refunded` | reembolsado | No; requiere tratamiento contable separado |

Estos estados corresponden a la documentación actual de migración de Payment Intent a Orders, que además confirma que `processed` y `failed` contienen el resultado en la propia Order y que el tópico cambia a `orders`: [migración oficial a Orders](https://www.mercadopago.com.ar/developers/es/docs/mp-point/migrate-payment-intent-to-orders).

Para autorizar `action_post()` no alcanza con `status == processed`: deben coincidir `external_reference`, configuración/cuenta, moneda, importe solicitado/pagado y Payment ID esperado. Las reglas ante propina, pago parcial o diferencia de importe todavía deben definirse.

### 5.9 Webhook

**Propuesta.** Crear una ruta propia, por ejemplo `/mercadopago/point/orders/webhook`, con `auth = public`, POST y CSRF deshabilitado solamente porque es una integración externa. Antes de usar `sudo()` o modificar registros debe:

1. obtener `x-signature`, `x-request-id`, timestamp y `data.id` en la forma exacta definida por Mercado Pago;
2. reconstruir el template firmado;
3. validar HMAC-SHA256 con comparación constante;
4. aplicar tolerancia temporal/replay según la documentación y pruebas;
5. identificar la configuración/cuenta correcta;
6. deduplicar eventos;
7. consultar la Order por API y validar correlación;
8. actualizar solamente el registro de integración;
9. responder 200/201 dentro del plazo esperado en duplicados válidos.

Una firma inválida debe responder 401/403 y no reconocerse como válida. Un evento válido duplicado debe ser idempotente. No registrar body/headers completos sin sanitización.

La documentación oficial actual especifica la firma `x-signature`, `x-request-id`, `data.id`, HMAC y respuesta 200/201: [Webhooks de Mercado Pago](https://www.mercadopago.com.ar/developers/en/docs/your-integrations/notifications/webhooks).

No reutilizar la ruta de Checkout. Los tópicos y contratos son distintos, y la documentación actual indica que Orders Point usa el tópico Order (`orders`), no el tópico legacy `point_integration_wh`.

### 5.10 Idempotencia y concurrencia

**Propuesta.** Reglas mínimas:

- una clave de creación por intento; todo retry de la misma operación usa la misma clave y el mismo payload;
- un intento nuevo después de fallo definitivo usa nueva referencia y nueva clave;
- cancelación usa otra clave persistida;
- constraints SQL sobre referencia, idempotency key e IDs remotos;
- bloqueo de fila al procesar dos Webhooks/reintentos concurrentes;
- no crear otro intento mientras el vigente esté en `created`, `at_terminal` o `action_required`;
- comparar payload normalizado antes de reutilizar una clave;
- recuperación por GET cuando el POST tenga resultado incierto por timeout.

### 5.11 Cancelación, reembolso y errores

**Propuesta.** Separar acciones:

- **Cancelar Order:** acción explícita sobre un intento pendiente. La API actual permite cancelar por API solamente en `created`; en `at_terminal` debe cancelarse desde la terminal. Véase [cancelación oficial de Order Point](https://www.mercadopago.com.ar/developers/es/reference/in-person-payments/point/orders/cancel-order/post).
- **Cancelar `account.payment`:** acción contable local. No debe interpretarse automáticamente como cancelación/reembolso remoto.
- **Reembolsar:** proceso futuro explícito, con permisos, trazabilidad y definición contable. `refunded` nunca debe tratarse como cobro aprobado disponible.

Errores propuestos:

- de validación local: no llamar a API;
- HTTP 4xx: registrar código/mensaje sanitizado y marcar recuperable o definitivo según contrato;
- timeout/conexión: estado “resultado incierto”, conservar clave y consultar/reintentar;
- HTTP 5xx: retry acotado con backoff y misma idempotency key;
- respuesta inválida: no publicar, conservar evidencia sanitizada;
- error de firma: rechazar sin procesar;
- inconsistencia de importe/referencia/cuenta: bloquear y alertar para revisión manual.

Nunca registrar Access Token, secreto, header Authorization ni material de firma. Tampoco imprimir payloads con PII como hace el módulo Checkout en algunos niveles de log.

### 5.12 Momento contable

**Propuesta inicial.** El Webhook aprobado actualiza la Order, pero no publica automáticamente `account.payment`. El operador valida el Recibo después de ver el resultado. Esto respeta la regla del proyecto de no definir movimientos contables definitivos antes de comprender el flujo actual.

Una automatización posterior podría llamar `action_post()` con un contexto interno y controlado, pero solo después de definir:

- quién/qué fecha publica;
- qué pasa si el Recibo tiene varias líneas y solo Point está aprobado;
- cómo manejar fallos de conciliación después de haber cobrado;
- qué hacer con diferencias entre `amount` y `paid_amount`;
- cómo revertir y reembolsar;
- impacto del `commit()` y SQL explícitos de `account_payment_group.post()`.

No se crearán movimientos separados por comisiones, impuestos, retenciones o neto. Esos datos se resolverán mediante conciliación/reportes financieros posteriores.

### 5.13 Evitar conflictos con `payment_mercado_pago`

**Propuesta.** Medidas concretas:

- código de método `mercadopago_point`, nunca `mercado_pago`;
- no agregar `mercado_pago` a `payment.provider.code`;
- no heredar la lógica específica de `payment.transaction` de Checkout;
- modelos y campos prefijados `mercadopago_point_` o bajo `mercadopago.point.*`;
- XML IDs propios;
- ruta Webhook propia;
- configuración y credenciales propias;
- no reutilizar `provider_reference` para Order ID/Payment ID;
- no interceptar transacciones online cuyo `provider_code == mercado_pago`;
- tests con ambos addons instalados.

Pueden coexistir en el mismo diario solo después de comprobar el comportamiento del core. Semánticamente es más claro que Checkout permanezca como proveedor online y Point como línea contable entrante.

### 5.14 Estructura futura sugerida

**Propuesta; no implementada.** Posible distribución interna:

```text
mercadopago_point_odoo/
├── models/
│   ├── account_payment.py
│   ├── account_payment_method.py
│   ├── mercado_pago_point_config.py
│   └── mercado_pago_point_order.py
├── controllers/
│   └── webhook.py
├── services/
│   ├── client.py
│   └── webhook_signature.py
├── data/
│   └── account_payment_method_data.xml
├── security/
│   ├── ir.model.access.csv
│   └── security.xml
├── views/
│   ├── account_payment_views.xml
│   └── mercado_pago_point_config_views.xml
└── tests/
    ├── test_account_payment_flow.py
    ├── test_api_client.py
    ├── test_idempotency.py
    ├── test_payment_group_flow.py
    └── test_webhook.py
```

Odoo no crea automáticamente paquetes Python en directorios arbitrarios; antes de usar `services/` debe confirmarse el patrón de imports y empaquetado del addon.

## 6. Pruebas necesarias antes y durante el desarrollo

### 6.1 Verificación del entorno real

1. Confirmar commit/edición exacta de Odoo 16 y de los addons instalados.
2. Inspeccionar en `talar-v1` el diario Mercado Pago, líneas entrantes, provider y defaults.
3. Crear manualmente un pago en un Recibo y registrar qué campos técnicos cambian al elegir diario/método.
4. Confirmar si el formulario usa `payment_method_line_id` y cómo se calcula `payment_method_id`.
5. Probar un Recibo de una y varias líneas hasta antes de publicar en un clon descartable.
6. Verificar el comportamiento real del `commit()` y del SQL de `account_payment_group.post()`.
7. Probar `talar_sale.btn_add_payments()` con el diario actual y varias líneas de método, sin modificar el addon.

### 6.2 Integración Point

1. Crear Order de prueba y verificar IDs, estados y payload real.
2. Timeout antes/después de creación y retry con la misma clave.
3. Webhook válido, inválido, tardío, duplicado y fuera de orden.
4. Estados `processed`, `failed`, `expired`, `canceled`, `action_required` y `refunded`.
5. Cancelación en `created` y tratamiento de `at_terminal`.
6. Diferencias de importe, moneda, Payment ID o `external_reference`.
7. Dos usuarios intentando cobrar el mismo pago.
8. Convivencia con `payment_mercado_pago` habilitado.
9. Clon neutralizado sin credenciales ni riesgo de apuntar a producción.

## 7. Decisiones necesarias antes de implementar

1. **Dependencias:** ¿un único addon dependiente de `account_payment_group` o núcleo `account` más puente opcional?
2. **Momento de publicación:** ¿primera versión con validación manual del Recibo después de `processed`, o publicación automática por Webhook?
3. **Diario:** ¿usar el diario Mercado Pago existente o crear “Mercado Pago Point” para aislar el flujo y evitar que `talar_sale` tome el método por defecto?
4. **Secretos:** ¿secret manager/variables de entorno o campos restringidos en la base de Odoo?
5. **Configuración:** confirmar Access Token/aplicación, `terminal_id`, modo PDV y separación de credenciales de desarrollo/producción.
6. **Intentos:** confirmar que un mismo `account.payment` puede conservar varios intentos y cuál será la acción operativa para reintentar.
7. **Importes:** política ante propina, importe pagado distinto, pago parcial, moneda inesperada o múltiples datos de pago.
8. **Cancelaciones/reembolsos:** responsabilidades del operador y alcance de la primera versión.
9. **Recibos con múltiples líneas:** decidir si se valida el grupo solo cuando todas las líneas Point estén aprobadas y cómo presentar estados mixtos.
10. **Referencia externa:** formato y componente único por instalación/base para evitar colisiones entre desarrollo y producción.
11. **Ejecución API:** request sincrónico con recuperación idempotente o cola/outbox transaccional.
12. **Cuotas:** si Odoo solo registra las cuotas efectivamente devueltas por Point o si además permitirá sugerir un tipo/default al crear la Order.

## 8. Conclusión

El flujo oficial `payment_mercado_pago` aporta patrones útiles de encapsulación HTTP, credenciales backend, correlación y verificación del recurso, pero su arquitectura de Checkout web no corresponde a Point Orders. `account_payment_group` ofrece el punto de integración contable adecuado porque edita `account.payment`, los publica desde `post()` y solo después concilia. `talar_sale` representa las cuotas como configuraciones comerciales del diario y publica pagos de inmediato, sin método explícito ni confirmación externa; debe permanecer independiente y obliga a probar cuidadosamente el diario compartido.

La arquitectura recomendada sitúa la configuración y cada intento de Order en modelos propios, identifica Point mediante una `account.payment.method.line` con código exclusivo, valida Webhooks firmados, conserva idempotencia e impide `action_post()` hasta verificar una Order `processed`. El Webhook no debería crear contabilidad definitiva en la primera versión. Esta separación reduce conflictos con Checkout, mantiene trazabilidad y permite evolucionar el flujo contable sin acoplarse a `talar_sale`.
