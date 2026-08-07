"""Persistent and auditable Mercado Pago Order attempts linked to payments."""

from decimal import Decimal, InvalidOperation
import uuid

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from ..services.client import MercadoPagoClientError, MercadoPagoOrdersClient


POINT_ORDER_STATES = [
    ("draft", "Draft"),
    ("sent", "Sent"),
    ("uncertain", "Uncertain Result"),
    ("created", "Created"),
    ("at_terminal", "At Terminal"),
    ("action_required", "Action Required"),
    ("processed", "Processed"),
    ("failed", "Failed"),
    ("expired", "Expired"),
    ("canceled", "Canceled"),
    ("refunded", "Refunded"),
    ("error", "Error"),
]

KNOWN_REMOTE_STATES = {
    "created",
    "at_terminal",
    "action_required",
    "processed",
    "failed",
    "expired",
    "canceled",
    "refunded",
}
FINAL_REMOTE_STATES = {"processed", "failed", "expired", "canceled", "refunded"}
SIMULATABLE_REMOTE_STATES = {"created", "at_terminal", "action_required"}


def _decimal_equal(left, right):
    """Compare API decimal strings exactly, with no business tolerance."""
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


class MercadoPagoPointOrder(models.Model):
    _name = "mercadopago.point.order"
    _description = "Mercado Pago Order Attempt"
    _order = "payment_id, attempt_number desc, id desc"
    _rec_name = "external_reference"
    _check_company_auto = True

    payment_id = fields.Many2one(
        comodel_name="account.payment",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="payment_id.company_id",
        store=True,
        index=True,
    )
    config_id = fields.Many2one(
        comodel_name="mercadopago.point.config",
        required=True,
        ondelete="restrict",
        check_company=True,
    )
    order_type = fields.Selection(
        selection=[("point", "Point"), ("qr", "QR")],
        required=True,
        default="point",
        readonly=True,
        index=True,
    )
    attempt_number = fields.Integer(required=True, readonly=True)
    external_reference = fields.Char(required=True, readonly=True, index=True, copy=False)
    idempotency_key = fields.Char(required=True, readonly=True, index=True, copy=False)
    cancel_idempotency_key = fields.Char(
        readonly=True,
        copy=False,
        groups="base.group_system",
    )
    mp_order_id = fields.Char(string="Mercado Pago Order ID", readonly=True, index=True, copy=False)
    mp_payment_id = fields.Char(string="Mercado Pago Payment ID", readonly=True, index=True, copy=False)

    state = fields.Selection(POINT_ORDER_STATES, required=True, default="draft", readonly=True, index=True)
    status = fields.Char(string="Order Status", readonly=True, index=True)
    status_detail = fields.Char(string="Order Status Detail", readonly=True)
    payment_status = fields.Char(readonly=True, index=True)
    payment_status_detail = fields.Char(readonly=True)

    currency_id = fields.Many2one(comodel_name="res.currency", required=True, readonly=True)
    requested_amount = fields.Monetary(currency_field="currency_id", required=True, readonly=True)
    requested_amount_text = fields.Char(
        required=True,
        readonly=True,
        help="Exact two-decimal amount string sent by Odoo to Point.",
    )
    paid_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    paid_amount_text = fields.Char(
        readonly=True,
        help="Exact amount string reported as paid by Mercado Pago.",
    )
    payment_method_type = fields.Char(string="Actual Payment Method", readonly=True)
    payment_method_id = fields.Char(string="Card Brand / Payment Method ID", readonly=True)
    installments = fields.Integer(readonly=True)
    terminal_id = fields.Char(readonly=True)
    external_pos_id = fields.Char(string="External POS ID", readonly=True)
    qr_mode = fields.Selection(
        selection=[("hybrid", "Hybrid")],
        string="QR Mode",
        readonly=True,
    )
    qr_data = fields.Text(
        string="QR Data",
        readonly=True,
        copy=False,
        groups="base.group_system",
        help="QR payload returned by Mercado Pago. It is stored but not rendered in Stage 2.5.",
    )

    sent_at = fields.Datetime(readonly=True)
    last_sync_at = fields.Datetime(readonly=True)
    verified_at = fields.Datetime(readonly=True)
    reference_verified = fields.Boolean(readonly=True)
    network_result_uncertain = fields.Boolean(readonly=True)
    error_code = fields.Char(readonly=True)
    error_message = fields.Text(readonly=True)
    is_verified_success = fields.Boolean(
        compute="_compute_is_verified_success",
        store=True,
        string="Verified Successful Payment",
    )

    _sql_constraints = [
        (
            "payment_attempt_unique",
            "unique(payment_id, attempt_number)",
            "The attempt number must be unique per Odoo payment.",
        ),
        (
            "external_reference_unique",
            "unique(external_reference)",
            "The Mercado Pago external reference must be unique.",
        ),
        (
            "idempotency_key_unique",
            "unique(idempotency_key)",
            "The Mercado Pago idempotency key must be unique.",
        ),
        (
            "cancel_idempotency_key_unique",
            "unique(cancel_idempotency_key)",
            "The Mercado Pago cancellation idempotency key must be unique.",
        ),
        (
            "mp_order_id_unique",
            "unique(mp_order_id)",
            "The Mercado Pago Order ID must be unique.",
        ),
        (
            "mp_payment_id_unique",
            "unique(mp_payment_id)",
            "The Mercado Pago Payment ID must be unique.",
        ),
        (
            "requested_amount_positive",
            "CHECK(requested_amount > 0)",
            "The requested Point amount must be positive.",
        ),
    ]

    @api.depends(
        "verified_at",
        "reference_verified",
        "status",
        "payment_status",
        "payment_status_detail",
        "requested_amount_text",
        "paid_amount_text",
        "mp_order_id",
        "mp_payment_id",
    )
    def _compute_is_verified_success(self):
        for order in self:
            order.is_verified_success = bool(
                order.verified_at
                and order.reference_verified
                and order.status == "processed"
                and order.payment_status == "processed"
                and order.payment_status_detail == "accredited"
                and order.mp_order_id
                and order.mp_payment_id
                and _decimal_equal(order.requested_amount_text, order.paid_amount_text)
            )

    @api.constrains("config_id", "payment_id", "order_type", "terminal_id", "external_pos_id", "qr_mode")
    def _check_config_company(self):
        for order in self:
            if order.config_id.company_id != order.payment_id.company_id:
                raise ValidationError(_(
                    "The Point configuration and the Odoo payment must belong to the same company."
                ))
            if order.config_id.integration_type != order.order_type:
                raise ValidationError(_(
                    "The attempt type must match its Mercado Pago configuration type."
                ))
            if order.order_type == "point" and not (order.terminal_id or "").strip():
                raise ValidationError(_("A Point attempt requires a Terminal ID."))
            if order.order_type == "qr":
                if not (order.external_pos_id or "").strip():
                    raise ValidationError(_("A QR attempt requires an External POS ID."))
                if order.qr_mode != "hybrid":
                    raise ValidationError(_("Stage 2.5 only supports hybrid QR attempts."))

    def _extract_payment_values(self, response_data):
        payments = response_data.get("transactions", {}).get("payments", [])
        if not isinstance(payments, list) or len(payments) != 1:
            raise ValidationError(_("The Point Order response must contain exactly one payment."))
        payment_data = payments[0]
        if not isinstance(payment_data, dict):
            raise ValidationError(_("Mercado Pago returned an invalid payment structure."))
        remote_amount = payment_data.get("amount")
        if remote_amount is not None and not _decimal_equal(
            self.requested_amount_text, remote_amount
        ):
            raise ValidationError(_(
                "Mercado Pago returned a requested amount different from the amount sent by Odoo."
            ))
        payment_method = payment_data.get("payment_method") or {}
        if not isinstance(payment_method, dict):
            raise ValidationError(_("Mercado Pago returned an invalid payment method structure."))
        paid_amount = payment_data.get("paid_amount")
        try:
            paid_amount_value = (
                float(Decimal(str(paid_amount))) if paid_amount is not None else 0.0
            )
            installments = int(payment_method.get("installments") or 0)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValidationError(_(
                "Mercado Pago returned invalid paid amount or installments data."
            )) from error
        return {
            "mp_payment_id": str(payment_data.get("id") or "") or False,
            "payment_status": payment_data.get("status") or False,
            "payment_status_detail": payment_data.get("status_detail") or False,
            "paid_amount": paid_amount_value,
            "paid_amount_text": str(paid_amount) if paid_amount is not None else False,
            "payment_method_type": payment_method.get("type") or False,
            "payment_method_id": payment_method.get("id") or False,
            "installments": installments,
        }

    def apply_api_response(self, response_data, verified=False):
        """Apply a sanitized Orders API response to this immutable attempt.

        ``verified`` must only be true for a successful explicit GET.  A POST
        response can populate IDs and status, but it can never authorize posting
        the accounting payment.
        """
        self.ensure_one()
        if not isinstance(response_data, dict):
            raise ValidationError(_("Mercado Pago returned an invalid JSON object."))
        remote_reference = response_data.get("external_reference")
        if remote_reference != self.external_reference:
            raise ValidationError(_("Mercado Pago returned an unexpected external reference."))
        remote_order_id = str(response_data.get("id") or "")
        if not remote_order_id:
            raise ValidationError(_("Mercado Pago returned an Order without an ID."))
        if self.mp_order_id and self.mp_order_id != remote_order_id:
            raise ValidationError(_("Mercado Pago returned an unexpected Order ID."))
        remote_type = response_data.get("type")
        if remote_type != self.order_type:
            raise ValidationError(_("Mercado Pago returned an unexpected Order type."))

        type_response = response_data.get("type_response") or {}
        if not isinstance(type_response, dict):
            raise ValidationError(_("Mercado Pago returned an invalid type response."))
        if self.order_type == "qr":
            remote_total = response_data.get("total_amount")
            if not _decimal_equal(self.requested_amount_text, remote_total):
                raise ValidationError(_(
                    "Mercado Pago returned a QR total amount different from the amount sent by Odoo."
                ))
            remote_config = response_data.get("config") or {}
            if not isinstance(remote_config, dict):
                raise ValidationError(_("Mercado Pago returned an invalid Order configuration."))
            remote_qr_config = remote_config.get("qr") or {}
            if not isinstance(remote_qr_config, dict):
                raise ValidationError(_("Mercado Pago returned an invalid QR configuration."))
            remote_external_pos = remote_qr_config.get("external_pos_id")
            if remote_external_pos and remote_external_pos != self.external_pos_id:
                raise ValidationError(_("Mercado Pago returned an unexpected External POS ID."))
            remote_mode = remote_qr_config.get("mode")
            if remote_mode and remote_mode != self.qr_mode:
                raise ValidationError(_("Mercado Pago returned an unexpected QR mode."))

        remote_status = response_data.get("status") or False
        values = {
            "mp_order_id": remote_order_id,
            "status": remote_status,
            "status_detail": response_data.get("status_detail") or False,
            "state": remote_status if remote_status in KNOWN_REMOTE_STATES else "error",
            "last_sync_at": fields.Datetime.now(),
            "network_result_uncertain": False,
            "error_code": False,
            "error_message": False,
        }
        remote_qr_data = type_response.get("qr_data")
        if remote_qr_data:
            if not isinstance(remote_qr_data, str):
                raise ValidationError(_("Mercado Pago returned invalid QR data."))
            values["qr_data"] = remote_qr_data
        payment_values = self._extract_payment_values(response_data)
        if (
            self.mp_payment_id
            and payment_values["mp_payment_id"]
            and self.mp_payment_id != payment_values["mp_payment_id"]
        ):
            raise ValidationError(_("Mercado Pago returned an unexpected Payment ID."))
        values.update(payment_values)
        if verified:
            values.update({
                "verified_at": fields.Datetime.now(),
                "reference_verified": True,
            })
        self.write(values)
        return self

    def _mercadopago_point_client(self):
        """Build a backend-only client without returning or logging credentials."""
        self.ensure_one()
        secure_config = self.config_id.sudo()
        return MercadoPagoOrdersClient(
            secure_config.access_token,
            timeout=secure_config.timeout_seconds,
        )

    def _refresh_from_api(self):
        """GET and verify this attempt without changing any accounting state."""
        self.ensure_one()
        if self.config_id.environment != "test":
            raise UserError(_("Production is disabled in the current implementation stage."))
        if not self.mp_order_id:
            raise UserError(_("The Point attempt does not have a Mercado Pago Order ID."))
        response_data = self._mercadopago_point_client().get_order(self.mp_order_id)
        return self.apply_api_response(response_data, verified=True)

    def _send_test_simulation_event(self, payload):
        """Send an official TEST event; never write a simulated final state locally."""
        self.ensure_one()
        if self.order_type != "point":
            raise UserError(_("The TEST events endpoint is only available for Point Orders."))
        if self.config_id.environment != "test":
            raise UserError(_("Point result simulation is only available in TEST."))
        if self.payment_id.state != "draft":
            raise UserError(_("Only draft Odoo payments can be simulated."))
        if not self.payment_id.is_mercadopago_point:
            raise UserError(_("The selected payment method is not Mercado Pago Point."))
        if not self.mp_order_id:
            raise UserError(_("Create the Mercado Pago Order before simulating its result."))
        if self.state not in SIMULATABLE_REMOTE_STATES:
            raise UserError(_("This Point Order no longer allows a TEST transition."))
        self._mercadopago_point_client().simulate_order_event(self.mp_order_id, payload)
        return True

    def _cancel_test_qr_and_refresh(self):
        """Cancel a TEST QR remotely and always recover its state with GET."""
        self.ensure_one()
        if self.order_type != "qr":
            raise UserError(_("This action is only available for QR Orders."))
        if self.config_id.environment != "test":
            raise UserError(_("QR cancellation is only available in TEST."))
        if self.payment_id.state != "draft":
            raise UserError(_("Only Orders for draft Odoo payments can be canceled."))
        if not self.payment_id.is_mercadopago_qr:
            raise UserError(_("The selected payment method is not Mercado Pago QR."))
        if not self.mp_order_id or self.state != "created":
            raise UserError(_("This QR Order can no longer be canceled."))
        client = self._mercadopago_point_client()
        if not self.cancel_idempotency_key:
            self.write({"cancel_idempotency_key": str(uuid.uuid4())})
        cancel_error = False
        try:
            client.cancel_order(self.mp_order_id, self.cancel_idempotency_key)
        except MercadoPagoClientError as error:
            cancel_error = str(error)
        # A mutating POST may have succeeded even if its response was lost.
        # GET is mandatory and is the only source of the new local state.
        self._refresh_from_api()
        return cancel_error

    def action_open_tracking(self):
        self.ensure_one()
        wizard = self.env["mercadopago.point.tracking.wizard"].create({
            "order_id": self.id,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Mercado Pago QR" if self.order_type == "qr" else "Mercado Pago Point"),
            "res_model": "mercadopago.point.tracking.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "mercadopago_point_odoo.view_mercadopago_point_tracking_wizard_form"
            ).id,
            "target": "new",
        }

    def mark_request_sent(self):
        self.ensure_one()
        self.write({"state": "sent", "sent_at": fields.Datetime.now()})

    def mark_error(self, code, message, uncertain=False):
        self.ensure_one()
        self.write({
            "state": "uncertain" if uncertain else "error",
            "network_result_uncertain": uncertain,
            "error_code": code or False,
            "error_message": message or False,
            "last_sync_at": fields.Datetime.now(),
        })

    def unlink(self):
        protected = self.filtered(lambda order: order.sent_at or order.mp_order_id)
        if protected:
            raise UserError(_(
                "Point attempts that were sent or have a Mercado Pago Order ID cannot be deleted."
            ))
        return super().unlink()
