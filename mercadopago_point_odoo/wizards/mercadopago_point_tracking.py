"""Real-time tracking modal and TEST-only simulation orchestration."""

from odoo import fields, models, _
from odoo.exceptions import UserError, ValidationError

from ..models.mercadopago_point_order import (
    FINAL_REMOTE_STATES,
    SIMULATABLE_REMOTE_STATES,
)
from ..services.client import (
    MercadoPagoClientError,
    SIMULATION_CREDIT_METHOD_IDS,
    SIMULATION_DEBIT_METHOD_IDS,
    SIMULATION_REJECTION_DETAILS,
    build_simulation_event_payload,
)


STATUS_LABELS = {
    "created": "Esperando pago",
    "at_terminal": "Esperando pago",
    "action_required": "Esperando acción en el Point",
    "processed": "Pago acreditado",
    "failed": "Pago rechazado",
    "canceled": "Operación cancelada",
    "expired": "Operación vencida",
    "refunded": "Pago reembolsado",
    "uncertain": "Resultado pendiente de verificación",
    "sent": "Enviando al Point",
    "error": "Error de integración",
}

METHOD_LABELS = {
    "amex": "American Express",
    "argencard": "Argencard",
    "cabal": "Cabal",
    "cencosud": "Cencosud",
    "cmr": "CMR",
    "debcabal": "Cabal Débito",
    "debmaster": "Mastercard Débito",
    "debvisa": "Visa Débito",
    "diners": "Diners Club",
    "master": "Mastercard",
    "naranja": "Naranja",
    "visa": "Visa",
}

REJECTION_LABELS = {
    "bad_filled_card_data": "Datos de tarjeta incorrectos",
    "required_call_for_authorize": "Se requiere autorización telefónica",
    "card_disabled": "Tarjeta deshabilitada",
    "high_risk": "Rechazado por riesgo alto",
    "insufficient_amount": "Fondos insuficientes",
    "invalid_installments": "Cantidad de cuotas inválida",
    "max_attempts_exceeded": "Máximo de intentos excedido",
    "rejected_other_reason": "Rechazado por otro motivo",
    "processing_error": "Error al procesar el pago",
}


class MercadoPagoPointTrackingWizard(models.TransientModel):
    _name = "mercadopago.point.tracking.wizard"
    _description = "Mercado Pago Point Payment Tracking"

    order_id = fields.Many2one(
        comodel_name="mercadopago.point.order",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    tracking_widget = fields.Char(default="tracking", readonly=True)

    def _snapshot(self):
        self.ensure_one()
        order = self.order_id
        payment = order.payment_id
        now = fields.Datetime.now()
        started = order.sent_at or order.create_date
        elapsed_seconds = 0
        if started:
            elapsed_seconds = max(0, int((now - started).total_seconds()))
        status = order.status or order.state
        verified_success = bool(order.is_verified_success)
        display_status = STATUS_LABELS.get(status, status or _("Unknown status"))
        if status == "processed" and not verified_success:
            display_status = _("Pago procesado; verificando acreditación")
        rejection_detail = order.payment_status_detail or order.status_detail
        return {
            "attempt_id": order.id,
            "attempt_number": order.attempt_number,
            "order_type": order.order_type,
            "requested_amount": order.requested_amount_text,
            "paid_amount": order.paid_amount_text or False,
            "currency": order.currency_id.name,
            "status": status,
            "status_detail": order.status_detail or False,
            "payment_status": order.payment_status or False,
            "payment_status_detail": order.payment_status_detail or False,
            "payment_method_type": order.payment_method_type or False,
            "payment_method_id": order.payment_method_id or False,
            "installments": order.installments or 0,
            "elapsed_seconds": elapsed_seconds,
            "display_status": display_status,
            "rejection_message": (
                REJECTION_LABELS.get(rejection_detail, rejection_detail)
                if status == "failed" else False
            ),
            "is_final": status in FINAL_REMOTE_STATES,
            "is_verified_success": verified_success,
            "can_simulate": bool(
                order.order_type == "point"
                and order.config_id.environment == "test"
                and payment.state == "draft"
                and order.mp_order_id
                and order.state in SIMULATABLE_REMOTE_STATES
            ),
            "can_cancel_qr": bool(
                order.order_type == "qr"
                and order.config_id.environment == "test"
                and payment.state == "draft"
                and order.mp_order_id
                and order.state == "created"
            ),
            "is_test": order.config_id.environment == "test",
            "simulation_options": {
                "credit_methods": [
                    {"value": value, "label": METHOD_LABELS[value]}
                    for value in SIMULATION_CREDIT_METHOD_IDS
                ],
                "debit_methods": [
                    {"value": value, "label": METHOD_LABELS[value]}
                    for value in SIMULATION_DEBIT_METHOD_IDS
                ],
                "rejection_details": [
                    {"value": value, "label": REJECTION_LABELS[value]}
                    for value in SIMULATION_REJECTION_DETAILS
                ],
            },
        }

    def get_tracking_snapshot(self):
        return self._snapshot()

    def poll_order_status(self):
        """Perform one GET. Errors preserve the last known business state."""
        self.ensure_one()
        snapshot = self._snapshot()
        if snapshot["is_final"]:
            return snapshot
        try:
            self.order_id.sudo()._refresh_from_api()
        except (MercadoPagoClientError, ValidationError, ValueError, UserError) as error:
            snapshot = self._snapshot()
            snapshot["poll_error"] = str(error)
            return snapshot
        return self._snapshot()

    def simulate_test_result(
        self,
        scenario,
        payment_method_type=None,
        payment_method_id=None,
        installments=None,
        status_detail=None,
    ):
        """POST the TEST event and immediately GET; never forge local success."""
        self.ensure_one()
        order = self.order_id
        # Explicit duplicate backend protection; the model service checks too.
        if order.config_id.environment != "test":
            raise UserError(_("Point result simulation is only available in TEST."))
        if order.order_type != "point":
            raise UserError(_("The TEST events endpoint is only available for Point Orders."))
        try:
            payload = build_simulation_event_payload(
                scenario,
                payment_method_type=payment_method_type,
                payment_method_id=payment_method_id,
                installments=installments,
                status_detail=status_detail,
            )
        except ValueError as error:
            raise UserError(str(error)) from error

        simulation_error = False
        try:
            order.sudo()._send_test_simulation_event(payload)
        except MercadoPagoClientError as error:
            # A timed-out POST may still have been accepted. GET below is always
            # attempted, and subsequent polling continues from the known state.
            simulation_error = str(error)

        try:
            order.sudo()._refresh_from_api()
        except (MercadoPagoClientError, ValidationError, ValueError, UserError) as error:
            snapshot = self._snapshot()
            snapshot["poll_error"] = str(error)
            if simulation_error:
                snapshot["simulation_error"] = simulation_error
            return snapshot

        snapshot = self._snapshot()
        if simulation_error:
            snapshot["simulation_error"] = simulation_error
        return snapshot

    def cancel_test_qr(self):
        """POST the official QR cancellation and immediately GET the Order."""
        self.ensure_one()
        try:
            cancel_error = self.order_id.sudo()._cancel_test_qr_and_refresh()
        except (MercadoPagoClientError, ValidationError, ValueError, UserError) as error:
            snapshot = self._snapshot()
            snapshot["poll_error"] = str(error)
            return snapshot
        snapshot = self._snapshot()
        if cancel_error:
            snapshot["cancel_error"] = cancel_error
        return snapshot
