# Mercado Pago Point y QR para Odoo 16

Integración entre Odoo 16 Community y Mercado Pago Point/QR mediante la API Orders. Permite configurar dispositivos TEST, habilitar por separado los métodos entrantes `Mercado Pago Point` y `Mercado Pago QR`, enviar explícitamente el importe exacto de un `account.payment`, seguir la Order y bloquear la publicación contable hasta verificar un resultado `processed/accredited` del mismo canal e importe.

El importe siempre lo determina Odoo. El terminal no puede recibir propinas, permitir carga manual, aplicar tolerancias ni modificar el monto. Un pago parcial requiere crear primero una línea `account.payment` por ese importe parcial.

Esta etapa depende únicamente de `account`. No implementa Webhooks, producción, reembolsos, comisiones, integración con `account_payment_group` ni integración con `talar_sale`.

Documentación:

- [Análisis técnico y arquitectura](docs/ANALISIS_ARQUITECTURA.md)
- [Implementación de la Etapa 1](docs/IMPLEMENTACION_ETAPA_1.md)
