"""Explicit confirmation for the global Production operation switch."""

from odoo import fields, models, _
from odoo.exceptions import AccessError, UserError

from ..models.mercadopago_point_config import PRODUCTION_ENABLED_PARAMETER


class MercadoPagoProductionConfirmation(models.TransientModel):
    _name = "mercadopago.production.confirmation"
    _description = "Confirm Mercado Pago Production Status Change"

    previous_state = fields.Boolean(required=True, readonly=True)
    target_state = fields.Boolean(required=True, readonly=True)
    warning_message = fields.Text(compute="_compute_warning_message", readonly=True)

    def _compute_warning_message(self):
        for wizard in self:
            if wizard.target_state:
                wizard.warning_message = _(
                    "Se habilitarán nuevas operaciones reales contra Mercado Pago Production.\n\n"
                    "Cualquier configuración Production activa y asignada a un diario podrá "
                    "iniciar cobranzas reales inmediatamente. Verifique credenciales, terminales, "
                    "External POS y diarios antes de continuar."
                )
            else:
                wizard.warning_message = _(
                    "Se deshabilitará el inicio de nuevas operaciones contra Mercado Pago Production.\n\n"
                    "Las Orders productivas existentes no se borrarán ni cancelarán. Odoo podrá "
                    "seguir consultando su estado y completar su verificación. Mientras Production "
                    "permanezca deshabilitada, sólo las configuraciones TEST podrán iniciar operaciones."
                )

    def action_confirm(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Sólo los administradores pueden cambiar el estado de Mercado Pago Production."))
        # Serialize confirmations so two administrators cannot confirm the same
        # transition concurrently and create duplicate or misleading audit rows.
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            [PRODUCTION_ENABLED_PARAMETER],
        )
        current_state = self.env["mercadopago.point.config"]._production_enabled()
        if current_state != self.previous_state:
            raise UserError(_(
                "El estado de Mercado Pago Production cambió después de abrir esta confirmación. "
                "Ciérrela e intente nuevamente."
            ))
        if current_state == self.target_state:
            return {"type": "ir.actions.act_window_close"}
        self.env["ir.config_parameter"].sudo().set_param(
            PRODUCTION_ENABLED_PARAMETER,
            "True" if self.target_state else "False",
        )
        self.env["mercadopago.production.audit"].sudo().create({
            "user_id": self.env.user.id,
            "changed_at": fields.Datetime.now(),
            "previous_state": current_state,
            "new_state": self.target_state,
        })
        return {"type": "ir.actions.act_window_close"}
