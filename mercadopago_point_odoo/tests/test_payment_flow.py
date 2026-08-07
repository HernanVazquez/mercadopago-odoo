"""Accounting integration tests for explicit Point actions and posting barrier."""

from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.mercadopago_point_odoo.services.client import MercadoPagoNetworkError

from .common import MercadoPagoPointCommon


@tagged("post_install", "-at_install")
class TestMercadoPagoPointPaymentFlow(MercadoPagoPointCommon):

    def _created_response(self, attempt):
        return {
            "id": "ORDER-%s" % attempt.id,
            "external_reference": attempt.external_reference,
            "status": "created",
            "status_detail": "created",
            "transactions": {
                "payments": [{
                    "id": "PAYMENT-%s" % attempt.id,
                    "amount": attempt.requested_amount_text,
                    "status": "created",
                    "status_detail": "created",
                }],
            },
        }

    def _processed_response(self, attempt, paid_amount=None):
        return {
            "id": attempt.mp_order_id,
            "external_reference": attempt.external_reference,
            "status": "processed",
            "status_detail": "processed",
            "transactions": {
                "payments": [{
                    "id": attempt.mp_payment_id,
                    "amount": attempt.requested_amount_text,
                    "paid_amount": paid_amount or attempt.requested_amount_text,
                    "status": "processed",
                    "status_detail": "accredited",
                    "payment_method": {
                        "type": "credit_card",
                        "id": "visa",
                        "installments": 3,
                    },
                }],
            },
        }

    def test_method_is_detected_by_master_technical_code(self):
        payment = self._create_point_payment()
        self.assertEqual(self.point_lines_created_automatically, 0)
        self.assertTrue(payment.is_mercadopago_point)
        self.assertEqual(
            payment.payment_method_line_id.payment_method_id.code,
            "mercadopago_point",
        )

    def test_posting_is_blocked_without_verified_order(self):
        payment = self._create_point_payment()
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.create_order",
            autospec=True,
        ) as create_order, patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
        ) as get_order:
            with self.assertRaises(UserError):
                payment.action_post()
        create_order.assert_not_called()
        get_order.assert_not_called()

    def test_non_point_payment_keeps_standard_posting_behavior(self):
        payment = self.env["account.payment"].with_company(self.company).create({
            "company_id": self.company.id,
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": self.partner_a.id,
            "amount": 25.0,
            "currency_id": self.company.currency_id.id,
            "journal_id": self.journal.id,
            "payment_method_line_id": self.inbound_payment_method_line.id,
        })
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.create_order",
            autospec=True,
        ) as create_order, patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
        ) as get_order:
            payment.action_post()
        self.assertEqual(payment.state, "posted")
        create_order.assert_not_called()
        get_order.assert_not_called()

    def test_inactive_config_history_does_not_block_replacement(self):
        self.point_config.active = False
        replacement = self.env["mercadopago.point.config"].create({
            "name": "Replacement Virtual Point TEST",
            "company_id": self.company.id,
            "environment": "test",
            "access_token": "TEST-replacement-token-never-log",
            "terminal_id": " VIRTUAL_POINT_TEST_001 ",
            "timeout_seconds": 10,
        })
        self.assertEqual(replacement.terminal_id, "VIRTUAL_POINT_TEST_001")

    def test_active_config_scope_cannot_be_ambiguous(self):
        with self.assertRaises(ValidationError):
            self.env["mercadopago.point.config"].create({
                "name": "Ambiguous Virtual Point TEST",
                "company_id": self.company.id,
                "environment": "test",
                "access_token": "TEST-duplicate-token-never-log",
                "terminal_id": "VIRTUAL_POINT_TEST_001",
                "timeout_seconds": 10,
            })

    def test_terminal_failure_keeps_attempt_before_new_one(self):
        payment = self._create_point_payment(80.0)
        first_attempt = payment._mercadopago_point_prepare_attempt(
            self.point_config,
            "80.00",
        )
        failed_response = self._created_response(first_attempt)
        failed_response.update({"status": "failed", "status_detail": "rejected"})
        failed_response["transactions"]["payments"][0].update({
            "status": "failed",
            "status_detail": "rejected",
        })
        first_attempt.apply_api_response(failed_response, verified=True)

        second_attempt = payment._mercadopago_point_prepare_attempt(
            self.point_config,
            "80.00",
        )

        self.assertEqual(len(payment.mercadopago_point_order_ids), 2)
        self.assertEqual(first_attempt.attempt_number, 1)
        self.assertEqual(second_attempt.attempt_number, 2)
        self.assertNotEqual(first_attempt.idempotency_key, second_attempt.idempotency_key)

    def test_send_and_manual_get_enable_local_barrier(self):
        payment = self._create_point_payment(123.45)

        def create_order(_client, payload, idempotency_key):
            attempt = payment.mercadopago_point_order_ids
            self.assertEqual(payload["transactions"]["payments"][0]["amount"], "123.45")
            self.assertEqual(idempotency_key, attempt.idempotency_key)
            return self._created_response(attempt)

        with patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.create_order",
            autospec=True,
            side_effect=create_order,
        ):
            payment.action_mercadopago_point_send()

        attempt = payment.mercadopago_point_order_ids
        self.assertEqual(len(attempt), 1)
        self.assertEqual(attempt.requested_amount_text, "123.45")
        self.assertFalse(attempt.is_verified_success)
        with self.assertRaises(UserError):
            payment._mercadopago_point_validate_before_post()

        with patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.get_order",
            autospec=True,
            return_value=self._processed_response(attempt),
        ):
            payment.action_mercadopago_point_refresh()

        self.assertTrue(attempt.is_verified_success)
        self.assertEqual(attempt.paid_amount_text, "123.45")
        self.assertTrue(payment._mercadopago_point_validate_before_post())

    def test_paid_amount_difference_never_enables_posting(self):
        payment = self._create_point_payment(100.0)
        attempt = payment._mercadopago_point_prepare_attempt(
            self.point_config,
            "100.00",
        )
        attempt.write({
            "mp_order_id": "ORDER-DIFFERENT",
            "mp_payment_id": "PAYMENT-DIFFERENT",
        })
        attempt.apply_api_response(
            self._processed_response(attempt, paid_amount="99.99"),
            verified=True,
        )
        self.assertFalse(attempt.is_verified_success)
        with self.assertRaises(UserError):
            payment._mercadopago_point_validate_before_post()

    def test_timeout_reuses_attempt_and_idempotency_key(self):
        payment = self._create_point_payment(50.0)
        error = MercadoPagoNetworkError(
            "Unknown network result",
            code="network_error",
            uncertain=True,
        )
        with patch(
            "odoo.addons.mercadopago_point_odoo.models.account_payment."
            "MercadoPagoOrdersClient.create_order",
            autospec=True,
            side_effect=error,
        ):
            payment.action_mercadopago_point_send()
            attempt = payment.mercadopago_point_order_ids
            original_key = attempt.idempotency_key
            payment.action_mercadopago_point_send()

        self.assertEqual(len(payment.mercadopago_point_order_ids), 1)
        self.assertEqual(attempt.idempotency_key, original_key)
        self.assertEqual(attempt.state, "uncertain")
