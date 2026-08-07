"""Small, model-independent HTTP client for Mercado Pago Point Orders API."""

from decimal import Decimal, InvalidOperation
import re

import requests


API_BASE_URL = "https://api.mercadopago.com"
EXTERNAL_REFERENCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _is_uncertain_post_status(method, status_code):
    """Return whether an HTTP result can hide a completed remote POST."""
    return method == "POST" and (
        status_code == 408
        or status_code == 409
        or status_code >= 500
    )


class MercadoPagoClientError(Exception):
    """Base exception whose message is guaranteed not to expose the token."""

    def __init__(self, message, code=None, uncertain=False):
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain


class MercadoPagoNetworkError(MercadoPagoClientError):
    """Network failure. A POST result is uncertain and must keep its key."""


class MercadoPagoAPIError(MercadoPagoClientError):
    """HTTP or response-contract failure."""


def build_point_order_payload(external_reference, amount_text, terminal_id):
    """Build a fixed-amount Point payload controlled exclusively by Odoo.

    There is deliberately no option for tips, terminal-entered amounts, partial
    capture, or a user-selected amount. A partial payment must first be modeled
    as a separate ``account.payment`` with that exact amount.
    """
    if not EXTERNAL_REFERENCE_RE.fullmatch(external_reference or ""):
        raise ValueError("Invalid external reference format.")
    if not (terminal_id or "").strip():
        raise ValueError("Terminal ID is required.")
    try:
        amount = Decimal(str(amount_text))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Invalid Point amount.") from error
    if amount <= 0 or amount.as_tuple().exponent != -2:
        raise ValueError("Point amount must be positive and have exactly two decimals.")
    return {
        "type": "point",
        "external_reference": external_reference,
        "transactions": {"payments": [{"amount": amount_text}]},
        "config": {"point": {"terminal_id": terminal_id.strip()}},
    }


class MercadoPagoOrdersClient:
    """HTTP client that never logs or exposes the Access Token."""

    def __init__(self, access_token, timeout=10, session=None):
        if not access_token:
            raise ValueError("Access Token is required.")
        self._access_token = access_token
        self.timeout = timeout
        self.session = session or requests.Session()

    def _sanitize(self, value):
        message = str(value or "Mercado Pago request failed.")
        return message.replace(self._access_token, "***")

    def _request(self, method, endpoint, expected_status, payload=None, idempotency_key=None):
        headers = {
            "Authorization": "Bearer %s" % self._access_token,
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        try:
            response = self.session.request(
                method,
                "%s%s" % (API_BASE_URL, endpoint),
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as error:
            raise MercadoPagoNetworkError(
                self._sanitize("Could not confirm the Mercado Pago network response."),
                code="network_error",
                uncertain=method == "POST",
            ) from error
        except requests.exceptions.RequestException as error:
            raise MercadoPagoNetworkError(
                self._sanitize("Mercado Pago request could not be completed."),
                code="request_error",
                uncertain=method == "POST",
            ) from error

        try:
            response_data = response.json()
        except ValueError as error:
            raise MercadoPagoAPIError(
                "Mercado Pago returned an invalid JSON response.",
                code="invalid_json",
                uncertain=(
                    method == "POST" and 200 <= response.status_code < 300
                ) or _is_uncertain_post_status(method, response.status_code),
            ) from error

        if not isinstance(response_data, dict):
            raise MercadoPagoAPIError(
                "Mercado Pago returned an unexpected response structure.",
                code="invalid_response",
                uncertain=(
                    method == "POST"
                    and (
                        200 <= response.status_code < 300
                        or _is_uncertain_post_status(method, response.status_code)
                    )
                ),
            )
        if response.status_code != expected_status:
            error_code = response_data.get("error") or response_data.get("code") or response.status_code
            error_message = response_data.get("message") or "Mercado Pago rejected the request."
            raise MercadoPagoAPIError(
                self._sanitize(error_message),
                code=self._sanitize(str(error_code)),
                # A POST conflict can mean the key/reference reached Mercado
                # Pago even though Odoo has not recovered the Order ID yet.
                # Keep the same attempt/key instead of risking a duplicate.
                uncertain=_is_uncertain_post_status(method, response.status_code),
            )
        return response_data

    def create_order(self, payload, idempotency_key):
        if not idempotency_key:
            raise ValueError("Idempotency key is required.")
        return self._request(
            "POST",
            "/v1/orders",
            expected_status=201,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def get_order(self, order_id):
        if not order_id:
            raise ValueError("Order ID is required.")
        return self._request(
            "GET",
            "/v1/orders/%s" % order_id,
            expected_status=200,
        )
