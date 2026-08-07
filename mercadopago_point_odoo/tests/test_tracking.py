"""Tests for TEST simulation and real-time tracking orchestration."""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MercadoPagoPointCommon


@tagged("post_install", "-at_install")
class TestMercadoPagoPointTracking(MercadoPagoPointCommon):

    def _response(self, attempt, status="created", detail=None, method=None):
        payment_status = status
        payment_detail = detail or status
        payment = {
            "id": "PAYMENT-%s" % attempt.id,
            "amount": attempt.requested_amount_text,
            "status": payment_status,
            "status_detail": payment_detail,
        }
        if status == "processed":
            payment["paid_amount"] = attempt.requested_amount_text
        if method:
            payment["payment_method"] = method
        return {
            "id": "ORDER-%s" % attempt.id,
            "type": attempt.order_type,
            "external_reference": attempt.external_reference,
            "status": status,
            "status_detail": detail or status,
            "transactions": {"payments": [payment]},
            **({
                "total_amount": attempt.requested_amount_text,
                "config": {"qr": {
                    "external_pos_id": attempt.external_pos_id,
                    "mode": attempt.qr_mode,
                }},
                "type_response": {"qr_data": "000201-tracking-qr"},
            } if attempt.order_type == "qr" else {}),
        }

    def _wizard(self, amount=100.0):
        payment = self._create_point_payment(amount)
        attempt = payment._mercadopago_point_prepare_attempt(
            self.point_config, "%.2f" % amount
        )
        attempt.apply_api_response(self._response(attempt), verified=False)
        wizard = self.env["mercadopago.point.tracking.wizard"].create({
            "order_id": attempt.id,
        })
        return payment, attempt, wizard

    def test_approved_simulation_posts_event_then_gets_and_updates_result(self):
        _payment, attempt, wizard = self._wizard(123.45)
        response = self._response(
            attempt,
            status="processed",
            detail="accredited",
            method={"type": "credit_card", "id": "visa", "installments": 3},
        )
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.simulate_order_event",
            autospec=True,
        ) as simulate, patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
            return_value=response,
        ) as get_order:
            snapshot = wizard.simulate_test_result(
                "approved", "credit_card", "visa", 3, "accredited"
            )

        simulate.assert_called_once_with(
            simulate.call_args.args[0],
            attempt.mp_order_id,
            {
                "status": "processed",
                "payment_method_type": "credit_card",
                "payment_method_id": "visa",
                "installments": 3,
                "status_detail": "accredited",
            },
        )
        get_order.assert_called_once()
        self.assertTrue(snapshot["is_verified_success"])
        self.assertEqual(attempt.paid_amount_text, "123.45")
        self.assertEqual(attempt.payment_method_type, "credit_card")
        self.assertEqual(attempt.payment_method_id, "visa")
        self.assertEqual(attempt.installments, 3)
        self.assertEqual(attempt.payment_id.state, "draft")

    def test_rejected_simulation_is_recovered_by_get(self):
        _payment, attempt, wizard = self._wizard()
        response = self._response(attempt, "failed", "insufficient_amount")
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.simulate_order_event",
            autospec=True,
        ), patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
            return_value=response,
        ) as get_order:
            snapshot = wizard.simulate_test_result(
                "rejected", "debit_card", "debvisa", False, "insufficient_amount"
            )

        get_order.assert_called_once()
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["display_status"], "Pago rechazado")
        self.assertFalse(attempt.is_verified_success)

    def test_canceled_simulation_uses_status_only_and_gets_order(self):
        _payment, attempt, wizard = self._wizard()
        response = self._response(attempt, "canceled")
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.simulate_order_event",
            autospec=True,
        ) as simulate, patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
            return_value=response,
        ) as get_order:
            snapshot = wizard.simulate_test_result("canceled")

        self.assertEqual(simulate.call_args.args[2], {"status": "canceled"})
        get_order.assert_called_once()
        self.assertEqual(snapshot["status"], "canceled")

    def test_simulation_is_blocked_twice_in_production(self):
        _payment, attempt, wizard = self._wizard()
        production = self.env["mercadopago.point.config"].create({
            "name": "Production blocked in tests",
            "company_id": self.company.id,
            "environment": "production",
            "access_token": "APP-production-secret-never-log",
            "terminal_id": "PRODUCTION_BLOCKED_001",
            "timeout_seconds": 10,
        })
        attempt.config_id = production
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.simulate_order_event",
            autospec=True,
        ) as simulate:
            with self.assertRaises(UserError):
                wizard.simulate_test_result("canceled")
            with self.assertRaises(UserError):
                attempt.sudo()._send_test_simulation_event({"status": "canceled"})
        simulate.assert_not_called()

    def test_final_snapshot_prevents_another_poll_get(self):
        _payment, attempt, wizard = self._wizard()
        attempt.apply_api_response(
            self._response(attempt, "failed", "processing_error"), verified=True
        )
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
        ) as get_order:
            snapshot = wizard.poll_order_status()
        self.assertTrue(snapshot["is_final"])
        get_order.assert_not_called()

    def test_tracking_snapshot_never_exposes_access_token(self):
        _payment, _attempt, wizard = self._wizard()
        snapshot = wizard.get_tracking_snapshot()
        self.assertNotIn(self.point_config.sudo().access_token, str(snapshot))

    def test_qr_never_uses_point_events_and_cancel_always_gets(self):
        payment = self._create_qr_payment()
        attempt = payment._mercadopago_point_prepare_attempt(
            self.qr_config, "100.00", "qr"
        )
        attempt.apply_api_response(self._response(attempt), verified=False)
        wizard = self.env["mercadopago.point.tracking.wizard"].create({
            "order_id": attempt.id,
        })
        initial = wizard.get_tracking_snapshot()
        self.assertFalse(initial["can_simulate"])
        self.assertTrue(initial["can_cancel_qr"])

        canceled = self._response(attempt, "canceled")
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.simulate_order_event",
            autospec=True,
        ) as simulate, patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.cancel_order",
            autospec=True,
            return_value={"id": attempt.mp_order_id, "status": "canceled"},
        ) as cancel, patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
            return_value=canceled,
        ) as get_order:
            with self.assertRaises(UserError):
                wizard.simulate_test_result("canceled")
            snapshot = wizard.cancel_test_qr()

        simulate.assert_not_called()
        cancel.assert_called_once()
        self.assertEqual(cancel.call_args.args[1], attempt.mp_order_id)
        self.assertEqual(cancel.call_args.args[2], attempt.cancel_idempotency_key)
        get_order.assert_called_once()
        self.assertEqual(snapshot["status"], "canceled")
        self.assertFalse(snapshot["can_cancel_qr"])

    def test_qr_cancel_get_runs_after_uncertain_post(self):
        payment = self._create_qr_payment()
        attempt = payment._mercadopago_point_prepare_attempt(
            self.qr_config, "100.00", "qr"
        )
        attempt.apply_api_response(self._response(attempt), verified=False)
        wizard = self.env["mercadopago.point.tracking.wizard"].create({
            "order_id": attempt.id,
        })
        from odoo.addons.mercadopago_point_odoo.services.client import MercadoPagoNetworkError
        error = MercadoPagoNetworkError("Unknown cancellation result", uncertain=True)
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.cancel_order",
            autospec=True,
            side_effect=error,
        ), patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
            return_value=self._response(attempt, "canceled"),
        ) as get_order:
            snapshot = wizard.cancel_test_qr()
        get_order.assert_called_once()
        self.assertEqual(snapshot["status"], "canceled")
        self.assertIn("Unknown cancellation result", snapshot["cancel_error"])
        self.assertTrue(attempt.cancel_idempotency_key)

    def test_qr_cancel_is_blocked_in_production_without_http(self):
        payment = self._create_qr_payment()
        attempt = payment._mercadopago_point_prepare_attempt(
            self.qr_config, "100.00", "qr"
        )
        attempt.apply_api_response(self._response(attempt), verified=False)
        production = self.env["mercadopago.point.config"].create({
            "name": "QR production blocked",
            "company_id": self.company.id,
            "environment": "production",
            "integration_type": "qr",
            "access_token": "APP-production-qr-secret-never-log",
            "external_pos_id": "QR_PRODUCTION_BLOCKED",
            "qr_mode": "hybrid",
        })
        attempt.config_id = production
        wizard = self.env["mercadopago.point.tracking.wizard"].create({
            "order_id": attempt.id,
        })
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.cancel_order",
            autospec=True,
        ) as cancel, patch(
            "odoo.addons.mercadopago_point_odoo.models.mercadopago_point_order."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
        ) as get_order:
            snapshot = wizard.cancel_test_qr()
        cancel.assert_not_called()
        get_order.assert_not_called()
        self.assertIn("TEST", snapshot["poll_error"])
