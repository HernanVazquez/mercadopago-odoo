"""Production switch, confirmation, audit, and TEST/PROD policy tests."""

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from ..models.mercadopago_point_config import PRODUCTION_ENABLED_PARAMETER
from .common import MercadoPagoPointCommon


@tagged("post_install", "-at_install")
class TestMercadoPagoProductionControls(MercadoPagoPointCommon):

    def setUp(self):
        super().setUp()
        self.parameter = self.env["ir.config_parameter"].sudo()
        self.parameter.set_param(PRODUCTION_ENABLED_PARAMETER, "False")
        self.production_config = self.env["mercadopago.point.config"].with_company(
            self.company
        ).create({
            "name": "Virtual Point PROD",
            "company_id": self.company.id,
            "environment": "production",
            "integration_type": "point",
            "access_token": "PROD-secret-never-log",
            "terminal_id": "VIRTUAL_POINT_PROD_001",
            "timeout_seconds": 10,
        })

    def _created_response(self, attempt):
        return {
            "id": "ORDER-%s" % attempt.id,
            "type": attempt.order_type,
            "external_reference": attempt.external_reference,
            "status": "created",
            "transactions": {"payments": [{
                "id": "PAYMENT-%s" % attempt.id,
                "amount": attempt.requested_amount_text,
                "status": "created",
                "status_detail": "created",
            }]},
        }

    def _use_production_point(self):
        self.point_method_line.mercadopago_point_config_id = self.production_config
        return self._create_point_payment()

    def test_production_enabled_default_is_false(self):
        self.parameter.search([
            ("key", "=", PRODUCTION_ENABLED_PARAMETER),
        ]).unlink()
        self.assertFalse(self.env["mercadopago.point.config"]._production_enabled())

    def test_test_send_allowed_while_production_disabled(self):
        payment = self._create_point_payment()
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.create_order",
            autospec=True,
            side_effect=lambda _client, _payload, _key: self._created_response(
                payment.mercadopago_point_order_ids
            ),
        ) as create_order:
            payment.action_mercadopago_point_send()
        create_order.assert_called_once()

    def test_production_send_blocked_before_attempt_and_http(self):
        payment = self._use_production_point()
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.create_order",
            autospec=True,
        ) as create_order:
            with self.assertRaises(UserError):
                payment.action_mercadopago_point_send()
        self.assertFalse(payment.mercadopago_point_order_ids)
        create_order.assert_not_called()

    def test_production_send_allowed_after_confirmation(self):
        self.parameter.set_param(PRODUCTION_ENABLED_PARAMETER, "True")
        payment = self._use_production_point()
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.create_order",
            autospec=True,
            side_effect=lambda _client, _payload, _key: self._created_response(
                payment.mercadopago_point_order_ids
            ),
        ) as create_order:
            payment.action_mercadopago_point_send()
        create_order.assert_called_once()
        self.assertEqual(payment.mercadopago_point_order_ids.config_id, self.production_config)

    def test_existing_production_order_get_allowed_while_disabled(self):
        payment = self._use_production_point()
        attempt = payment._mercadopago_point_prepare_attempt(
            self.production_config, "100.00", "point"
        )
        attempt.apply_api_response(self._created_response(attempt))
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
            return_value=self._created_response(attempt),
        ) as get_order:
            payment.action_mercadopago_point_refresh()
        get_order.assert_called_once()

    def test_uncertain_production_attempt_reuses_idempotency_while_disabled(self):
        payment = self._use_production_point()
        attempt = payment._mercadopago_point_prepare_attempt(
            self.production_config, "100.00", "point"
        )
        attempt.mark_request_sent()
        attempt.mark_error("network_error", "Unknown result", uncertain=True)
        original_key = attempt.idempotency_key
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.create_order",
            autospec=True,
            return_value=self._created_response(attempt),
        ) as create_order:
            payment.action_mercadopago_point_send()
        create_order.assert_called_once()
        self.assertEqual(len(payment.mercadopago_point_order_ids), 1)
        self.assertEqual(attempt.idempotency_key, original_key)

    def test_existing_production_order_poll_allowed_while_disabled(self):
        payment = self._use_production_point()
        attempt = payment._mercadopago_point_prepare_attempt(
            self.production_config, "100.00", "point"
        )
        attempt.apply_api_response(self._created_response(attempt))
        wizard = self.env["mercadopago.point.tracking.wizard"].create({"order_id": attempt.id})
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
            return_value=self._created_response(attempt),
        ) as get_order:
            wizard.poll_order_status()
        get_order.assert_called_once()

    def test_production_test_helpers_remain_unavailable(self):
        payment = self._use_production_point()
        attempt = payment._mercadopago_point_prepare_attempt(
            self.production_config, "100.00", "point"
        )
        attempt.apply_api_response(self._created_response(attempt))
        wizard = self.env["mercadopago.point.tracking.wizard"].create({"order_id": attempt.id})
        for enabled in (False, True):
            self.parameter.set_param(PRODUCTION_ENABLED_PARAMETER, str(enabled))
            snapshot = wizard.get_tracking_snapshot()
            self.assertFalse(snapshot["can_simulate"])
            self.assertFalse(snapshot["can_cancel_qr"])
            self.assertFalse(snapshot["is_test"])
            with patch(
                "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
                "MercadoPagoOrdersClient.simulate_order_event",
                autospec=True,
            ) as simulate:
                with self.assertRaises(UserError):
                    wizard.simulate_test_result("canceled")
            simulate.assert_not_called()

    def test_production_configs_match_only_their_method_and_company(self):
        self.point_method_line.mercadopago_point_config_id = self.production_config
        self.assertEqual(self.point_method_line.mercadopago_point_config_id, self.production_config)
        production_qr = self.env["mercadopago.point.config"].create({
            "name": "QR PROD",
            "company_id": self.company.id,
            "environment": "production",
            "integration_type": "qr",
            "access_token": "PROD-qr-secret-never-log",
            "external_pos_id": "QR_POS_PROD_001",
            "qr_mode": "hybrid",
        })
        self.qr_method_line.mercadopago_point_config_id = production_qr
        self.assertEqual(self.qr_method_line.mercadopago_point_config_id, production_qr)
        with self.assertRaises(ValidationError):
            self.qr_method_line.mercadopago_point_config_id = self.production_config
        with self.assertRaises(ValidationError):
            self.point_method_line.mercadopago_point_config_id = production_qr

        other_company = self.env["res.company"].create({"name": "Other MP company"})
        other_config = self.production_config.copy({
            "name": "Other company PROD",
            "company_id": other_company.id,
            "terminal_id": "OTHER_COMPANY_PROD",
        })
        with self.assertRaises((UserError, ValidationError)):
            self.point_method_line.mercadopago_point_config_id = other_config
        self.production_config.active = False
        with self.assertRaises(ValidationError):
            self.point_method_line.mercadopago_point_config_id = self.production_config

    def test_confirmation_required_and_changes_are_audited(self):
        settings = self.env["res.config.settings"].create({})
        action = settings.action_enable_mercadopago_production()
        self.assertFalse(self.env["mercadopago.point.config"]._production_enabled())
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        wizard.action_confirm()
        self.assertTrue(self.env["mercadopago.point.config"]._production_enabled())
        enabled_audit = self.env["mercadopago.production.audit"].search([], limit=1)
        self.assertEqual(enabled_audit.user_id, self.env.user)
        self.assertFalse(enabled_audit.previous_state)
        self.assertTrue(enabled_audit.new_state)
        self.assertTrue(enabled_audit.changed_at)

        action = settings.action_disable_mercadopago_production()
        self.assertTrue(self.env["mercadopago.point.config"]._production_enabled())
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        wizard.action_confirm()
        disabled_audit = self.env["mercadopago.production.audit"].search([], limit=1)
        self.assertFalse(self.env["mercadopago.point.config"]._production_enabled())
        self.assertTrue(disabled_audit.previous_state)
        self.assertFalse(disabled_audit.new_state)

    def test_canceling_confirmation_does_not_change_parameter(self):
        settings = self.env["res.config.settings"].create({})
        action = settings.action_enable_mercadopago_production()
        self.env[action["res_model"]].browse(action["res_id"]).unlink()
        self.assertFalse(self.env["mercadopago.point.config"]._production_enabled())
        self.assertFalse(self.env["mercadopago.production.audit"].search([]))

    def test_non_admin_cannot_open_or_confirm_change(self):
        group_user = self.env.ref("base.group_user")
        normal_user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Mercado Pago normal user",
            "login": "mp-normal-user",
            "groups_id": [(6, 0, [group_user.id])],
        })
        settings = self.env["res.config.settings"].create({}).with_user(normal_user)
        with self.assertRaises(AccessError):
            settings.action_enable_mercadopago_production()
        wizard = self.env["mercadopago.production.confirmation"].sudo().create({
            "previous_state": False,
            "target_state": True,
        })
        with self.assertRaises(AccessError):
            wizard.with_user(normal_user).action_confirm()
        self.assertFalse(self.env["mercadopago.point.config"]._production_enabled())

    def test_journal_selector_keeps_scope_without_test_filter(self):
        view = self.env.ref(
            "mercadopago_point_odoo.view_account_journal_form_mercadopago_point"
        )
        arch = view.arch_db
        self.assertNotIn("('environment', '=', 'test')", arch)
        self.assertIn("('company_id', '=', parent.company_id)", arch)
        self.assertIn("('active', '=', True)", arch)
        self.assertIn(
            "('integration_type', '=', mercadopago_point_config_type)", arch
        )
