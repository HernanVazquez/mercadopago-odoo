"""Backend-only configuration for Mercado Pago Point and QR Orders."""

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from psycopg2 import IntegrityError


ACTIVE_TERMINAL_INDEX = "mercadopago_point_config_active_terminal_unique"
ACTIVE_EXTERNAL_POS_INDEX = "mercadopago_point_config_active_external_pos_unique"
PRODUCTION_ENABLED_PARAMETER = "mercadopago_point_odoo.production_enabled"


class MercadoPagoPointConfig(models.Model):
    _name = "mercadopago.point.config"
    _description = "Mercado Pago Orders Configuration"
    _order = "company_id, environment, integration_type, terminal_id, external_pos_id, id"
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
    integration_type = fields.Selection(
        selection=[("point", "Point"), ("qr", "QR")],
        required=True,
        default="point",
        index=True,
    )
    access_token = fields.Char(
        string="Access Token",
        required=True,
        copy=False,
        groups="base.group_system",
        help="Private backend credential for Mercado Pago. It must never be logged.",
    )
    terminal_id = fields.Char(
        string="Terminal ID",
        copy=False,
        index=True,
    )
    external_pos_id = fields.Char(
        string="External POS ID",
        copy=False,
        index=True,
        help="External ID of the externally provisioned fixed-amount QR cash register.",
    )
    qr_mode = fields.Selection(
        selection=[("hybrid", "Hybrid")],
        string="QR Mode",
        default="hybrid",
        copy=False,
        help="Stage 2.5 supports the official hybrid QR mode only.",
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
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                mercadopago_point_config_active_external_pos_unique
            ON mercadopago_point_config (company_id, environment, external_pos_id)
            WHERE active AND integration_type = 'qr'
        """)

    @api.model
    def _production_enabled(self):
        """Return the global switch with a safe false default."""
        value = self.env["ir.config_parameter"].sudo().get_param(
            PRODUCTION_ENABLED_PARAMETER, default="False"
        )
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _ensure_new_production_order_allowed(self, existing_recovery=False):
        """Authorize starting a new remote Order; GET operations are unaffected."""
        self.ensure_one()
        if (
            self.environment == "production"
            and not existing_recovery
            and not self._production_enabled()
        ):
            raise UserError(_(
                "Las nuevas operaciones de Mercado Pago Production están deshabilitadas. "
                "Un administrador debe habilitarlas en Ajustes antes de iniciar una cobranza real."
            ))
        return True

    @staticmethod
    def _normalize_identifiers(vals):
        vals = dict(vals)
        if vals.get("integration_type") == "point":
            vals["external_pos_id"] = False
        elif vals.get("integration_type") == "qr":
            vals["terminal_id"] = False
        for field_name in ("terminal_id", "external_pos_id"):
            if field_name in vals and vals[field_name]:
                vals[field_name] = vals[field_name].strip()
        return vals

    @staticmethod
    def _unique_error(error):
        constraint_name = error.diag.constraint_name
        if constraint_name == ACTIVE_TERMINAL_INDEX:
            return _(
                "Only one active Point configuration is allowed for the same "
                "company, environment, and terminal."
            )
        if constraint_name == ACTIVE_EXTERNAL_POS_INDEX:
            return _(
                "Only one active QR configuration is allowed for the same "
                "company, environment, and external POS ID."
            )
        return False

    @api.model_create_multi
    def create(self, vals_list):
        normalized_vals_list = []
        for vals in vals_list:
            normalized_vals_list.append(self._normalize_identifiers(vals))
        try:
            with self.env.cr.savepoint():
                return super().create(normalized_vals_list)
        except IntegrityError as error:
            message = self._unique_error(error)
            if not message:
                raise
            raise ValidationError(message) from error

    def write(self, vals):
        vals = self._normalize_identifiers(vals)
        try:
            with self.env.cr.savepoint():
                return super().write(vals)
        except IntegrityError as error:
            message = self._unique_error(error)
            if not message:
                raise
            raise ValidationError(message) from error

    @api.constrains("company_id", "environment", "terminal_id", "integration_type", "active")
    def _check_unique_active_terminal(self):
        for config in self.filtered(lambda record: record.active and record.integration_type == "point"):
            duplicate = self.search_count([
                ("id", "!=", config.id),
                ("company_id", "=", config.company_id.id),
                ("environment", "=", config.environment),
                ("integration_type", "=", "point"),
                ("terminal_id", "=", config.terminal_id),
                ("active", "=", True),
            ])
            if duplicate:
                raise ValidationError(_(
                    "Only one active Point configuration is allowed for the same "
                    "company, environment, and terminal."
                ))

    @api.constrains("company_id", "environment", "external_pos_id", "integration_type", "active")
    def _check_unique_active_external_pos(self):
        for config in self.filtered(
            lambda record: record.active and record.integration_type == "qr"
        ):
            duplicate = self.search_count([
                ("id", "!=", config.id),
                ("company_id", "=", config.company_id.id),
                ("environment", "=", config.environment),
                ("integration_type", "=", "qr"),
                ("external_pos_id", "=", config.external_pos_id),
                ("active", "=", True),
            ])
            if duplicate:
                raise ValidationError(_(
                    "Only one active QR configuration is allowed for the same "
                    "company, environment, and external POS ID."
                ))

    @api.constrains("timeout_seconds")
    def _check_timeout_seconds(self):
        for config in self:
            if not 1 <= config.timeout_seconds <= 60:
                raise ValidationError(_("The HTTP timeout must be between 1 and 60 seconds."))

    @api.constrains(
        "integration_type", "terminal_id", "external_pos_id", "qr_mode", "access_token"
    )
    def _check_required_values_not_blank(self):
        for config in self:
            if config.integration_type == "point" and not (config.terminal_id or "").strip():
                raise ValidationError(_("Terminal ID cannot be blank."))
            if config.integration_type == "qr":
                if not (config.external_pos_id or "").strip():
                    raise ValidationError(_("External POS ID cannot be blank for QR."))
                if config.qr_mode != "hybrid":
                    raise ValidationError(_("Stage 2.5 only supports hybrid QR mode."))
            if not (config.sudo().access_token or "").strip():
                raise ValidationError(_("Access Token cannot be blank."))
