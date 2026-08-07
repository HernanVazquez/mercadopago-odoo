"""Mercado Pago Point actions and accounting-posting safety barrier."""

from decimal import Decimal, InvalidOperation
import uuid

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .account_payment_method import MERCADOPAGO_POINT_METHOD_CODE
from .mercadopago_point_order import _decimal_equal
from ..services.client import (
    MercadoPagoClientError,
    MercadoPagoOrdersClient,
    build_point_order_payload,
)


PENDING_OR_UNCERTAIN_STATES = {"draft", "sent", "uncertain", "created", "at_terminal", "action_required"}


def _format_exact_point_amount(amount):
    """Return the exact two-decimal amount sent to Point, without rounding.

    The Point amount always comes from ``account.payment.amount``. If Odoo's
    value cannot be represented exactly with the two decimals required by the
    Orders API, the operation is rejected instead of rounded or tolerated.
    """
    try:
        decimal_amount = Decimal(str(amount))
        two_decimal_amount = decimal_amount.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise UserError(_("The Odoo payment amount is invalid for Point.")) from error
    if decimal_amount <= 0:
        raise UserError(_("The Odoo payment amount sent to Point must be positive."))
    if decimal_amount != two_decimal_amount:
        raise UserError(_(
            "The Odoo payment amount must have at most two decimals. It will not be rounded for Point."
        ))
    return format(two_decimal_amount, ".2f")


class AccountPayment(models.Model):
    _inherit = "account.payment"

    mercadopago_point_order_ids = fields.One2many(
        comodel_name="mercadopago.point.order",
        inverse_name="payment_id",
        string="Mercado Pago Point Attempts",
        copy=False,
        readonly=True,
    )
    mercadopago_point_order_count = fields.Integer(
        compute="_compute_mercadopago_point_display",
    )
    mercadopago_point_current_order_id = fields.Many2one(
        comodel_name="mercadopago.point.order",
        compute="_compute_mercadopago_point_display",
        string="Current Point Attempt",
    )
    mercadopago_point_state = fields.Char(
        compute="_compute_mercadopago_point_display",
        string="Point Status",
    )
    is_mercadopago_point = fields.Boolean(
        compute="_compute_is_mercadopago_point",
    )
    mercadopago_point_can_refresh = fields.Boolean(
        compute="_compute_mercadopago_point_display",
    )

    @api.depends("payment_method_line_id", "payment_method_line_id.payment_method_id.code", "payment_type")
    def _compute_is_mercadopago_point(self):
        for payment in self:
            # The technical source of truth is account.payment.method.code.
            # Labels and journal names are deliberately ignored.
            payment.is_mercadopago_point = bool(
                payment.payment_type == "inbound"
                and payment.payment_method_line_id.payment_method_id.code
                == MERCADOPAGO_POINT_METHOD_CODE
            )

    @api.depends(
        "mercadopago_point_order_ids",
        "mercadopago_point_order_ids.state",
        "mercadopago_point_order_ids.status",
        "mercadopago_point_order_ids.mp_order_id",
    )
    def _compute_mercadopago_point_display(self):
        for payment in self:
            attempts = payment.mercadopago_point_order_ids.sorted(
                key=lambda order: (order.attempt_number, order.id), reverse=True
            )
            current = attempts[:1]
            payment.mercadopago_point_order_count = len(attempts)
            payment.mercadopago_point_current_order_id = current
            payment.mercadopago_point_state = (
                (current.status or current.state) if current else False
            )
            payment.mercadopago_point_can_refresh = bool(current and current.mp_order_id)

    def _mercadopago_point_get_config(self):
        self.ensure_one()
        config = self.payment_method_line_id.mercadopago_point_config_id
        if not config:
            raise UserError(_(
                "The selected Mercado Pago Point payment method line has no backend configuration."
            ))
        if config.company_id != self.company_id:
            raise UserError(_("The Point configuration belongs to another company."))
        if not config.active:
            raise UserError(_("The Point configuration is inactive."))
        if config.environment != "test":
            raise UserError(_(
                "Production is disabled in Stage 1. Select a TEST Point configuration."
            ))
        return config

    def _mercadopago_point_validate_send(self):
        self.ensure_one()
        if not self.id:
            raise UserError(_("Save the Odoo payment before sending it to Point."))
        if self.state != "draft":
            raise UserError(_("Only draft Odoo payments can be sent to Point."))
        if not self.is_mercadopago_point:
            raise UserError(_("The selected payment method is not Mercado Pago Point."))
        if self.currency_id.name != "ARS":
            raise UserError(_("Stage 1 only supports Point payments in Argentine pesos (ARS)."))
        amount_text = _format_exact_point_amount(self.amount)
        return self._mercadopago_point_get_config(), amount_text

    def _mercadopago_point_prepare_attempt(self, config, amount_text):
        self.ensure_one()
        attempts = self.mercadopago_point_order_ids.sorted(
            key=lambda order: (order.attempt_number, order.id), reverse=True
        )
        latest = attempts[:1]
        if latest and latest.state in {"sent", "uncertain"} and not latest.mp_order_id:
            if (
                latest.requested_amount_text != amount_text
                or latest.currency_id != self.currency_id
                or latest.config_id != config
                or latest.terminal_id != config.terminal_id
            ):
                raise UserError(_(
                    "The last Point request has an uncertain result. Restore its original amount "
                    "and configuration before recovering it with the same idempotency key."
                ))
            return latest.sudo()
        if latest and latest.state in PENDING_OR_UNCERTAIN_STATES:
            raise UserError(_(
                "A Point attempt is still pending. Consult its status before starting another attempt."
            ))
        if self.mercadopago_point_order_ids.filtered("is_verified_success"):
            raise UserError(_("This Odoo payment already has a verified successful Point Order."))

        next_attempt = max(self.mercadopago_point_order_ids.mapped("attempt_number") or [0]) + 1
        return self.env["mercadopago.point.order"].sudo().create({
            "payment_id": self.id,
            "config_id": config.id,
            "attempt_number": next_attempt,
            # Human-auditable link to the Odoo payment plus random uniqueness.
            "external_reference": "odoo-ap-%s-%s" % (self.id, uuid.uuid4().hex),
            "idempotency_key": str(uuid.uuid4()),
            "currency_id": self.currency_id.id,
            "requested_amount": self.amount,
            "requested_amount_text": amount_text,
            "terminal_id": config.terminal_id,
        })

    @staticmethod
    def _mercadopago_point_notification(title, message, notification_type="info", sticky=False):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": notification_type,
                "sticky": sticky,
            },
        }

    def action_mercadopago_point_send(self):
        """Explicitly create/recover a fixed-amount Point Order.

        The exact amount is read from this ``account.payment``. The terminal can
        only let the customer choose the available payment method; it cannot
        choose, edit, tip, or tolerate a different amount.
        """
        self.ensure_one()
        config, amount_text = self._mercadopago_point_validate_send()
        attempt = self._mercadopago_point_prepare_attempt(config, amount_text)
        payload = build_point_order_payload(
            attempt.external_reference,
            attempt.requested_amount_text,
            attempt.terminal_id,
        )
        attempt.mark_request_sent()
        secure_config = config.sudo()
        client = MercadoPagoOrdersClient(
            secure_config.access_token,
            timeout=secure_config.timeout_seconds,
        )
        try:
            response_data = client.create_order(payload, attempt.idempotency_key)
            attempt.apply_api_response(response_data, verified=False)
        except MercadoPagoClientError as error:
            attempt.mark_error(error.code, str(error), uncertain=error.uncertain)
            return self._mercadopago_point_notification(
                _("Mercado Pago Point"),
                _(
                    "The request result is uncertain; retry Enviar al Point to recover it with "
                    "the same idempotency key."
                ) if error.uncertain else str(error),
                notification_type="warning" if error.uncertain else "danger",
                sticky=True,
            )
        except (ValidationError, ValueError) as error:
            # A successful POST may already have created a remote Order even if
            # its response is inconsistent. Preserve the key and block new attempts.
            attempt.mark_error("invalid_create_response", str(error), uncertain=True)
            return self._mercadopago_point_notification(
                _("Mercado Pago Point"),
                _(
                    "Mercado Pago may have created the Order but returned inconsistent data. "
                    "The same attempt and idempotency key were preserved for recovery."
                ),
                notification_type="warning",
                sticky=True,
            )
        return self._mercadopago_point_notification(
            _("Mercado Pago Point"),
            _("The exact Odoo amount %s ARS was sent to the configured Point terminal.") % amount_text,
            notification_type="success",
        )

    def action_mercadopago_point_refresh(self):
        """Explicitly query the latest remote Order; never post accounting entries."""
        self.ensure_one()
        if not self.is_mercadopago_point:
            raise UserError(_("The selected payment method is not Mercado Pago Point."))
        attempt = self.mercadopago_point_current_order_id
        if not attempt:
            raise UserError(_("This payment has no Point attempts."))
        attempt = attempt.sudo()
        if not attempt.mp_order_id:
            raise UserError(_(
                "The last request has no recoverable Order ID. Use Enviar al Point again; "
                "it will reuse the same idempotency key."
            ))
        config = attempt.config_id
        if config.environment != "test":
            raise UserError(_("Production is disabled in Stage 1."))
        secure_config = config.sudo()
        client = MercadoPagoOrdersClient(
            secure_config.access_token,
            timeout=secure_config.timeout_seconds,
        )
        try:
            response_data = client.get_order(attempt.mp_order_id)
            attempt.apply_api_response(response_data, verified=True)
        except MercadoPagoClientError as error:
            attempt.write({
                "error_code": error.code or False,
                "error_message": str(error),
                "last_sync_at": fields.Datetime.now(),
            })
            return self._mercadopago_point_notification(
                _("Mercado Pago Point"),
                str(error),
                notification_type="warning",
                sticky=True,
            )
        except (ValidationError, ValueError) as error:
            attempt.write({
                "error_code": "invalid_get_response",
                "error_message": str(error),
                "last_sync_at": fields.Datetime.now(),
            })
            return self._mercadopago_point_notification(
                _("Mercado Pago Point"),
                str(error),
                notification_type="danger",
                sticky=True,
            )
        return self._mercadopago_point_notification(
            _("Mercado Pago Point"),
            _("Point Order status updated: %s") % (attempt.status or attempt.state),
            notification_type="success" if attempt.is_verified_success else "info",
        )

    def action_open_mercadopago_point_orders(self):
        self.ensure_one()
        action = self.env.ref(
            "mercadopago_point_odoo.action_mercadopago_point_order"
        ).read()[0]
        action["domain"] = [("payment_id", "=", self.id)]
        action["context"] = {"create": False, "default_payment_id": self.id}
        return action

    def _mercadopago_point_validate_before_post(self):
        """Pure local barrier. This method must never perform network calls."""
        for payment in self.filtered("is_mercadopago_point"):
            expected_amount = _format_exact_point_amount(payment.amount)
            successful = payment.mercadopago_point_order_ids.filtered("is_verified_success")
            if len(successful) != 1:
                raise UserError(_(
                    "A Point payment can only be posted with exactly one verified "
                    "processed/accredited Order."
                ))
            order = successful[0]
            if order.currency_id != payment.currency_id or order.requested_amount_text != expected_amount:
                raise UserError(_(
                    "The verified Point Order amount or currency does not match the Odoo payment."
                ))
            if not _decimal_equal(order.paid_amount_text, expected_amount):
                raise UserError(_(
                    "Mercado Pago paid_amount must exactly match the amount sent by Odoo."
                ))
        return True

    def action_post(self):
        # Safety barrier only. All network I/O lives in the explicit Point actions.
        self._mercadopago_point_validate_before_post()
        return super().action_post()
