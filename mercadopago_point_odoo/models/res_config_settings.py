"""Administrative Settings entry point for Mercado Pago Production calls."""

from odoo import fields, models, _
from odoo.exceptions import AccessError

from .mercadopago_point_config import PRODUCTION_ENABLED_PARAMETER


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mercadopago_production_enabled = fields.Boolean(
        string="Producción habilitada",
        compute="_compute_mercadopago_production_enabled",
        groups="base.group_system",
    )
    mercadopago_production_status = fields.Char(
        string="Estado de Producción",
        compute="_compute_mercadopago_production_enabled",
        groups="base.group_system",
    )

    def _compute_mercadopago_production_enabled(self):
        enabled = self.env["mercadopago.point.config"]._production_enabled()
        for settings in self:
            settings.mercadopago_production_enabled = enabled
            settings.mercadopago_production_status = _("Habilitado") if enabled else _("Deshabilitado")

    def _ensure_mercadopago_system_admin(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Sólo los administradores pueden cambiar el estado de Mercado Pago Production."))

    def _open_mercadopago_production_confirmation(self, target_state):
        self._ensure_mercadopago_system_admin()
        current_state = self.env["mercadopago.point.config"]._production_enabled()
        if current_state == target_state:
            return {"type": "ir.actions.act_window_close"}
        wizard = self.env["mercadopago.production.confirmation"].create({
            "previous_state": current_state,
            "target_state": target_state,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Confirmar cambio de Mercado Pago Production"),
            "res_model": "mercadopago.production.confirmation",
            "res_id": wizard.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "mercadopago_point_odoo.view_mercadopago_production_confirmation_form"
            ).id,
            "target": "new",
        }

    def action_enable_mercadopago_production(self):
        return self._open_mercadopago_production_confirmation(True)

    def action_disable_mercadopago_production(self):
        return self._open_mercadopago_production_confirmation(False)
