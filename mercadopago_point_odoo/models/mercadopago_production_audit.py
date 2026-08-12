"""Immutable audit trail for the global Mercado Pago Production switch."""

from odoo import fields, models, _
from odoo.exceptions import UserError


class MercadoPagoProductionAudit(models.Model):
    _name = "mercadopago.production.audit"
    _description = "Mercado Pago Production Status Change"
    _order = "changed_at desc, id desc"

    user_id = fields.Many2one(
        comodel_name="res.users",
        required=True,
        readonly=True,
        ondelete="restrict",
    )
    changed_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    previous_state = fields.Boolean(required=True, readonly=True)
    new_state = fields.Boolean(required=True, readonly=True)

    def write(self, vals):
        raise UserError(_("Mercado Pago Production audit entries cannot be modified."))

    def unlink(self):
        raise UserError(_("Mercado Pago Production audit entries cannot be deleted."))
