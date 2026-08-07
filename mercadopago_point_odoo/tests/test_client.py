"""Unit tests for the model-independent Orders API client."""

from unittest.mock import Mock

import requests

from odoo.tests.common import TransactionCase

from odoo.addons.mercadopago_point_odoo.services.client import (
    MercadoPagoAPIError,
    MercadoPagoNetworkError,
    MercadoPagoOrdersClient,
    build_point_order_payload,
    build_simulation_event_payload,
)


class TestMercadoPagoOrdersClient(TransactionCase):

    def test_simulation_payloads_only_expose_documented_combinations(self):
        self.assertEqual(
            build_simulation_event_payload(
                "approved", "credit_card", "visa", 3, "accredited"
            ),
            {
                "status": "processed",
                "payment_method_type": "credit_card",
                "payment_method_id": "visa",
                "installments": 3,
                "status_detail": "accredited",
            },
        )
        self.assertEqual(
            build_simulation_event_payload(
                "rejected", "qr", status_detail="high_risk"
            ),
            {
                "status": "failed",
                "payment_method_type": "qr",
                "status_detail": "high_risk",
            },
        )
        self.assertEqual(
            build_simulation_event_payload("canceled"),
            {"status": "canceled"},
        )
        with self.assertRaises(ValueError):
            build_simulation_event_payload(
                "approved", "debit_card", "visa", status_detail="accredited"
            )

    def test_simulate_event_accepts_official_no_content_response(self):
        session = Mock()
        response = Mock(status_code=204)
        session.request.return_value = response
        client = MercadoPagoOrdersClient("TEST-secret-token", session=session)

        result = client.simulate_order_event("ORDER-1", {"status": "canceled"})

        self.assertIsNone(result)
        response.json.assert_not_called()
        call = session.request.call_args
        self.assertEqual(
            call.args[:2],
            ("POST", "https://api.mercadopago.com/v1/orders/ORDER-1/events"),
        )
        self.assertNotIn("TEST-secret-token", str(result))

    def test_simulation_api_error_never_exposes_token(self):
        session = Mock()
        response = Mock(status_code=400)
        response.json.return_value = {
            "error": "bad_request_TEST-secret-token",
            "message": "Rejected TEST-secret-token",
        }
        session.request.return_value = response
        client = MercadoPagoOrdersClient("TEST-secret-token", session=session)

        with self.assertRaises(MercadoPagoAPIError) as caught:
            client.simulate_order_event("ORDER-1", {"status": "canceled"})

        self.assertNotIn("TEST-secret-token", str(caught.exception))
        self.assertNotIn("TEST-secret-token", caught.exception.code)

    def test_payload_amount_is_fixed_by_odoo(self):
        payload = build_point_order_payload(
            "odoo-ap-test",
            "123.45",
            "VIRTUAL_POINT_TEST_001",
        )
        self.assertEqual(payload["transactions"]["payments"], [{"amount": "123.45"}])
        self.assertNotIn("tip_amount", str(payload))
        self.assertNotIn("manual", str(payload).lower())
        self.assertEqual(
            payload["config"],
            {"point": {"terminal_id": "VIRTUAL_POINT_TEST_001"}},
        )

    def test_payload_never_rounds_an_amount(self):
        with self.assertRaises(ValueError):
            build_point_order_payload(
                "odoo-ap-test",
                "123.456",
                "VIRTUAL_POINT_TEST_001",
            )

    def test_create_order_sends_idempotency_and_timeout(self):
        session = Mock()
        response = Mock(status_code=201)
        response.json.return_value = {"id": "ORDER-1"}
        session.request.return_value = response
        client = MercadoPagoOrdersClient("TEST-secret-token", timeout=7, session=session)

        result = client.create_order({"type": "point"}, "fixed-key")

        self.assertEqual(result, {"id": "ORDER-1"})
        call = session.request.call_args
        self.assertEqual(call.args[:2], ("POST", "https://api.mercadopago.com/v1/orders"))
        self.assertEqual(call.kwargs["timeout"], 7)
        self.assertEqual(call.kwargs["headers"]["X-Idempotency-Key"], "fixed-key")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer TEST-secret-token")

    def test_post_timeout_is_uncertain(self):
        session = Mock()
        session.request.side_effect = requests.exceptions.Timeout()
        client = MercadoPagoOrdersClient("TEST-secret-token", session=session)

        with self.assertRaises(MercadoPagoNetworkError) as caught:
            client.create_order({"type": "point"}, "fixed-key")

        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn("TEST-secret-token", str(caught.exception))

    def test_post_conflict_is_uncertain(self):
        session = Mock()
        response = Mock(status_code=409)
        response.json.return_value = {
            "error": "conflict",
            "message": "The idempotent request may already exist",
        }
        session.request.return_value = response
        client = MercadoPagoOrdersClient("TEST-secret-token", session=session)

        with self.assertRaises(MercadoPagoAPIError) as caught:
            client.create_order({"type": "point"}, "fixed-key")

        self.assertTrue(caught.exception.uncertain)

    def test_post_http_timeout_is_uncertain(self):
        session = Mock()
        response = Mock(status_code=408)
        response.json.return_value = {"error": "request_timeout"}
        session.request.return_value = response
        client = MercadoPagoOrdersClient("TEST-secret-token", session=session)

        with self.assertRaises(MercadoPagoAPIError) as caught:
            client.create_order({"type": "point"}, "fixed-key")

        self.assertTrue(caught.exception.uncertain)

    def test_post_server_error_with_invalid_json_is_uncertain(self):
        session = Mock()
        response = Mock(status_code=502)
        response.json.side_effect = ValueError("invalid response")
        session.request.return_value = response
        client = MercadoPagoOrdersClient("TEST-secret-token", session=session)

        with self.assertRaises(MercadoPagoAPIError) as caught:
            client.create_order({"type": "point"}, "fixed-key")

        self.assertTrue(caught.exception.uncertain)

    def test_api_error_never_exposes_token(self):
        session = Mock()
        response = Mock(status_code=400)
        response.json.return_value = {
            "error": "bad_request_TEST-secret-token",
            "message": "Rejected TEST-secret-token",
        }
        session.request.return_value = response
        client = MercadoPagoOrdersClient("TEST-secret-token", session=session)

        with self.assertRaises(MercadoPagoAPIError) as caught:
            client.create_order({"type": "point"}, "fixed-key")

        self.assertNotIn("TEST-secret-token", str(caught.exception))
        self.assertNotIn("TEST-secret-token", caught.exception.code)
        self.assertIn("***", str(caught.exception))
