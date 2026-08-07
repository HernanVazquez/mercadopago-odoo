"""Accounting payment method used to identify Mercado Pago Point payments."""

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


MERCADOPAGO_POINT_METHOD_CODE = "mercadopago_point"
MERCADOPAGO_QR_METHOD_CODE = "mercadopago_qr"
MERCADOPAGO_METHOD_CODES = {
    MERCADOPAGO_POINT_METHOD_CODE,
    MERCADOPAGO_QR_METHOD_CODE,
}
_SKIP_AUTO_LINES_CONTEXT_KEY = "mercadopago_point_skip_auto_lines"


class AccountPaymentMethod(models.Model):
    _inherit = "account.payment.method"

    @api.model
    def _get_payment_method_information(self):
        """Make Point selectable on bank/cash journals without assigning it globally.

        Odoo 16 automatically creates a line on every eligible journal for methods
        declared as ``multi``.  During creation of our master method, ``create``
        adds a private context flag so the method is temporarily omitted here.
        Afterwards it is exposed normally and an administrator can enable it only
        on the intended journal from the standard Incoming Payments tab.
        """
        information = super()._get_payment_method_information()
        if not self.env.context.get(_SKIP_AUTO_LINES_CONTEXT_KEY):
            for method_code in MERCADOPAGO_METHOD_CODES:
                information[method_code] = {
                    "mode": "multi",
                    "domain": [("type", "in", ("bank", "cash"))],
                }
        return information

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("code") in MERCADOPAGO_METHOD_CODES for vals in vals_list):
            self = self.with_context(**{_SKIP_AUTO_LINES_CONTEXT_KEY: True})
        return super(AccountPaymentMethod, self).create(vals_list)


class AccountPaymentMethodLine(models.Model):
    _inherit = "account.payment.method.line"

    mercadopago_point_is_method = fields.Boolean(
        compute="_compute_mercadopago_point_is_method",
    )
    mercadopago_qr_is_method = fields.Boolean(
        compute="_compute_mercadopago_point_is_method",
    )
    mercadopago_point_config_type = fields.Selection(
        selection=[("point", "Point"), ("qr", "QR")],
        compute="_compute_mercadopago_point_is_method",
    )
    mercadopago_point_config_id = fields.Many2one(
        comodel_name="mercadopago.point.config",
        string="Mercado Pago Point Configuration",
        ondelete="restrict",
        check_company=True,
        copy=False,
        help="Backend credentials and device used by this Mercado Pago payment method line.",
    )

    @api.depends("payment_method_id", "payment_method_id.code")
    def _compute_mercadopago_point_is_method(self):
        for line in self:
            code = line.payment_method_id.code
            line.mercadopago_point_is_method = code == MERCADOPAGO_POINT_METHOD_CODE
            line.mercadopago_qr_is_method = code == MERCADOPAGO_QR_METHOD_CODE
            line.mercadopago_point_config_type = (
                "point" if line.mercadopago_point_is_method
                else "qr" if line.mercadopago_qr_is_method
                else False
            )

    @api.constrains("payment_method_id", "mercadopago_point_config_id", "journal_id")
    def _check_mercadopago_point_config(self):
        for line in self:
            is_point = line.mercadopago_point_is_method
            is_qr = line.mercadopago_qr_is_method
            is_mercadopago = is_point or is_qr
            config = line.mercadopago_point_config_id
            if is_mercadopago and not config:
                raise ValidationError(_(
                    "A Mercado Pago payment method line requires a backend configuration."
                ))
            if not is_mercadopago and config:
                raise ValidationError(_(
                    "A Mercado Pago Point configuration can only be assigned to the "
                    "Mercado Pago Point payment method."
                ))
            if not config:
                continue
            expected_type = "qr" if is_qr else "point"
            if config.integration_type != expected_type:
                raise ValidationError(_(
                    "The Mercado Pago configuration type must match the selected payment method."
                ))
            if line.journal_id.company_id != config.company_id:
                raise ValidationError(_(
                    "The Point configuration and the journal must belong to the same company."
                ))
            if not config.active:
                raise ValidationError(_("The selected Point configuration is inactive."))
            if config.environment != "test":
                raise ValidationError(_(
                    "Production Point configurations cannot be enabled in this implementation stage."
                ))
