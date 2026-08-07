"""Small, model-independent HTTP client for Mercado Pago Orders API."""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import re

import requests


API_BASE_URL = "https://api.mercadopago.com"
EXTERNAL_REFERENCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TECHNICAL_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
UNSAFE_CONTENT_TYPE_RE = re.compile(r"[^A-Za-z0-9!#$&^_.+\-;/= ]")
MAX_ERROR_MESSAGE_LENGTH = 512
MAX_CONTENT_TYPE_LENGTH = 128

# Values published for Argentina (MLA) by the Point Orders simulation schema.
SIMULATION_CREDIT_METHOD_IDS = (
    "amex", "argencard", "cabal", "cencosud", "cmr", "diners", "master",
    "naranja", "visa",
)
SIMULATION_DEBIT_METHOD_IDS = ("debcabal", "debmaster", "debvisa")
SIMULATION_REJECTION_DETAILS = (
    "bad_filled_card_data",
    "required_call_for_authorize",
    "card_disabled",
    "high_risk",
    "insufficient_amount",
    "invalid_installments",
    "max_attempts_exceeded",
    "rejected_other_reason",
    "processing_error",
)


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


def build_qr_order_payload(external_reference, amount_text, external_pos_id):
    """Build a fixed-amount hybrid QR payload controlled exclusively by Odoo.

    ``total_amount`` and the single transaction amount deliberately use the
    same exact value from ``account.payment``. The externally provisioned POS
    must have ``fixed_amount=true``; customers can never enter or alter it.
    """
    if not EXTERNAL_REFERENCE_RE.fullmatch(external_reference or ""):
        raise ValueError("Invalid external reference format.")
    if not (external_pos_id or "").strip():
        raise ValueError("External POS ID is required.")
    try:
        amount = Decimal(str(amount_text))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Invalid QR amount.") from error
    if amount <= 0 or amount.as_tuple().exponent != -2:
        raise ValueError("QR amount must be positive and have exactly two decimals.")
    return {
        "type": "qr",
        "total_amount": amount_text,
        "external_reference": external_reference,
        "config": {
            "qr": {
                "external_pos_id": external_pos_id.strip(),
                "mode": "hybrid",
            },
        },
        "transactions": {"payments": [{"amount": amount_text}]},
    }


def build_simulation_event_payload(
    scenario,
    payment_method_type=None,
    payment_method_id=None,
    installments=None,
    status_detail=None,
):
    """Build only combinations allowed by the official TEST event schema.

    This payload never changes local Order state. It is sent to Mercado Pago's
    TEST-only event endpoint and must be followed by a GET of the Order.
    """
    if scenario == "canceled":
        if any((payment_method_type, payment_method_id, installments, status_detail)):
            raise ValueError("Canceled simulation only accepts the canceled status.")
        return {"status": "canceled"}
    if scenario not in {"approved", "rejected"}:
        raise ValueError("Invalid Point simulation scenario.")
    if payment_method_type not in {"credit_card", "debit_card", "qr"}:
        raise ValueError("Invalid Point simulation payment method type.")

    payload = {
        "status": "processed" if scenario == "approved" else "failed",
        "payment_method_type": payment_method_type,
    }
    if payment_method_type == "credit_card":
        if payment_method_id not in SIMULATION_CREDIT_METHOD_IDS:
            raise ValueError("Invalid credit card payment method ID for Argentina.")
        try:
            installment_decimal = Decimal(str(installments))
            installment_count = int(installment_decimal)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValueError("Credit card installments must be a positive integer.") from error
        if installment_count < 1 or installment_decimal != installment_count:
            raise ValueError("Credit card installments must be a positive integer.")
        payload.update({
            "payment_method_id": payment_method_id,
            "installments": installment_count,
        })
    elif payment_method_type == "debit_card":
        if payment_method_id not in SIMULATION_DEBIT_METHOD_IDS:
            raise ValueError("Invalid debit card payment method ID for Argentina.")
        payload["payment_method_id"] = payment_method_id
    elif payment_method_id or installments:
        raise ValueError("QR simulation does not accept a card brand or installments.")

    if scenario == "approved":
        if status_detail not in (None, False, "accredited"):
            raise ValueError("The only valid approved status detail is accredited.")
        payload["status_detail"] = "accredited"
    else:
        if status_detail not in SIMULATION_REJECTION_DETAILS:
            raise ValueError("Invalid Point simulation rejection detail.")
        payload["status_detail"] = status_detail
    return payload


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

    def _sanitize_message(self, value):
        """Return one bounded line without ever exposing the configured token."""
        message = self._sanitize(value).replace("\r", " ").replace("\n", " ")
        return message[:MAX_ERROR_MESSAGE_LENGTH]

    def _safe_technical_code(self, value, status_code):
        """Accept only bounded scalar API codes made of technical characters."""
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            candidate = self._sanitize(str(value)).strip()
            if TECHNICAL_ERROR_CODE_RE.fullmatch(candidate):
                return candidate
        return "http_%s" % status_code

    def _extract_api_error(self, response_data, status_code):
        """Extract supported Mercado Pago error shapes without retaining the body."""
        code_value = None
        for key in ("errorKey", "code", "error"):
            value = response_data.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                code_value = value
                break

        nested_error = None
        errors = response_data.get("errors")
        if isinstance(errors, list):
            for item in errors:
                if not isinstance(item, dict):
                    continue
                nested_code = next((
                    item.get(key)
                    for key in ("errorKey", "code", "error")
                    if isinstance(item.get(key), (str, int))
                    and not isinstance(item.get(key), bool)
                ), None)
                nested_message = item.get("message")
                if nested_code is not None or isinstance(nested_message, str):
                    nested_error = item
                    if code_value is None:
                        code_value = nested_code
                    break

        error_code = self._safe_technical_code(code_value, status_code)
        message_value = response_data.get("message")
        if not isinstance(message_value, str) and nested_error:
            message_value = nested_error.get("message")
        if not isinstance(message_value, str) or not message_value.strip():
            message_value = "Mercado Pago rejected the request."
        return error_code, self._sanitize_message(message_value)

    def _safe_response_metadata(self, response):
        """Format an allowlisted response summary; never include body or request data."""
        try:
            status_code = int(response.status_code)
        except (TypeError, ValueError):
            status_code = 0

        headers = response.headers if isinstance(response.headers, Mapping) else {}
        raw_content_type = headers.get("Content-Type") or headers.get("content-type") or ""
        content_type = self._sanitize(str(raw_content_type))
        content_type = UNSAFE_CONTENT_TYPE_RE.sub("?", content_type)
        content_type = content_type[:MAX_CONTENT_TYPE_LENGTH] or "not_provided"

        raw_content_length = headers.get("Content-Length") or headers.get("content-length")
        declared_length = None
        if isinstance(raw_content_length, (str, int)) and not isinstance(raw_content_length, bool):
            candidate = str(raw_content_length).strip()
            if candidate.isdigit() and len(candidate) <= 20:
                declared_length = int(candidate)

        content = response.content
        received_bytes = len(content) if isinstance(content, (bytes, bytearray)) else 0
        parts = [
            "HTTP %s" % status_code,
            "Content-Type=%s" % content_type,
        ]
        if declared_length is not None:
            parts.append("Content-Length=%s" % declared_length)
        parts.append("received_bytes=%s" % received_bytes)
        return "; ".join(parts)

    def _request(
        self,
        method,
        endpoint,
        expected_status,
        payload=None,
        idempotency_key=None,
        expect_json=True,
    ):
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
                uncertain=method.upper() == "POST",
            ) from error
        except requests.exceptions.RequestException as error:
            raise MercadoPagoNetworkError(
                self._sanitize("Mercado Pago request could not be completed."),
                code="request_error",
                uncertain=method == "POST",
            ) from error

        if response.status_code == expected_status and not expect_json:
            return None

        try:
            response_data = response.json()
        except ValueError as error:
            raise MercadoPagoAPIError(
                self._sanitize_message(
                    "Mercado Pago returned an invalid JSON response (%s)."
                    % self._safe_response_metadata(response)
                ),
                code="invalid_json",
                # Every POST in this client mutates remote state. Once an HTTP
                # response cannot be interpreted, its remote outcome is unknown
                # regardless of the status returned by an API or intermediary.
                uncertain=method == "POST",
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
            error_code, error_message = self._extract_api_error(
                response_data, response.status_code
            )
            raise MercadoPagoAPIError(
                error_message,
                code=error_code,
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

    def simulate_order_event(self, order_id, payload):
        if not order_id:
            raise ValueError("Order ID is required.")
        return self._request(
            "POST",
            "/v1/orders/%s/events" % order_id,
            expected_status=204,
            payload=payload,
            expect_json=False,
        )

    def cancel_order(self, order_id, idempotency_key):
        """Cancel a created Order; callers must always follow this with GET."""
        if not order_id:
            raise ValueError("Order ID is required.")
        if not idempotency_key:
            raise ValueError("Cancellation idempotency key is required.")
        return self._request(
            "POST",
            "/v1/orders/%s/cancel" % order_id,
            expected_status=200,
            idempotency_key=idempotency_key,
        )
