"""Backend-only configuration for Mercado Pago Point Orders."""

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from psycopg2 import IntegrityError


ACTIVE_TERMINAL_INDEX = "mercadopago_point_config_active_terminal_unique"


class MercadoPagoPointConfig(models.Model):
    _name = "mercadopago.point.config"
    _description = "Mercado Pago Point Configuration"
    _order = "company_id, environment, terminal_id, id"
    _check_company_auto = True

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    environment = fields.Selection(
        selection=[("test", "Test"), ("production", "Production")],
        required=True,
        default="test",
        index=True,
    )
    access_token = fields.Char(
        string="Access Token",
        required=True,
        copy=False,
        groups="base.group_system",
        help="Private TEST credential. It is used only in backend requests and must never be logged.",
    )
    terminal_id = fields.Char(
        string="Terminal ID",
        required=True,
        copy=False,
        index=True,
    )
    timeout_seconds = fields.Integer(
        string="HTTP Timeout (seconds)",
        required=True,
        default=10,
    )

    def init(self):
        """Enforce non-ambiguous active configurations without losing history.

        A partial PostgreSQL index allows a deactivated configuration to remain
        referenced by old attempts while guaranteeing that concurrent writes
        cannot create two active configurations for the same terminal scope.
        """
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                mercadopago_point_config_active_terminal_unique
            ON mercadopago_point_config (company_id, environment, terminal_id)
            WHERE active
        """)

    @api.model_create_multi
    def create(self, vals_list):
        normalized_vals_list = []
        for vals in vals_list:
            vals = dict(vals)
            if "terminal_id" in vals and vals["terminal_id"]:
                vals["terminal_id"] = vals["terminal_id"].strip()
            normalized_vals_list.append(vals)
        try:
            with self.env.cr.savepoint():
                return super().create(normalized_vals_list)
        except IntegrityError as error:
            if error.diag.constraint_name != ACTIVE_TERMINAL_INDEX:
                raise
            raise ValidationError(_(
                "Only one active Point configuration is allowed for the same "
                "company, environment, and terminal."
            )) from error

    def write(self, vals):
        vals = dict(vals)
        if "terminal_id" in vals and vals["terminal_id"]:
            vals["terminal_id"] = vals["terminal_id"].strip()
        try:
            with self.env.cr.savepoint():
                return super().write(vals)
        except IntegrityError as error:
            if error.diag.constraint_name != ACTIVE_TERMINAL_INDEX:
                raise
            raise ValidationError(_(
                "Only one active Point configuration is allowed for the same "
                "company, environment, and terminal."
            )) from error

    @api.constrains("company_id", "environment", "terminal_id", "active")
    def _check_unique_active_terminal(self):
        for config in self.filtered("active"):
            duplicate = self.search_count([
                ("id", "!=", config.id),
                ("company_id", "=", config.company_id.id),
                ("environment", "=", config.environment),
                ("terminal_id", "=", config.terminal_id),
                ("active", "=", True),
            ])
            if duplicate:
                raise ValidationError(_(
                    "Only one active Point configuration is allowed for the same "
                    "company, environment, and terminal."
                ))

    @api.constrains("timeout_seconds")
    def _check_timeout_seconds(self):
        for config in self:
            if not 1 <= config.timeout_seconds <= 60:
                raise ValidationError(_("The HTTP timeout must be between 1 and 60 seconds."))

    @api.constrains("terminal_id", "access_token")
    def _check_required_values_not_blank(self):
        for config in self:
            if not (config.terminal_id or "").strip():
                raise ValidationError(_("Terminal ID cannot be blank."))
            if not (config.sudo().access_token or "").strip():
                raise ValidationError(_("Access Token cannot be blank."))
