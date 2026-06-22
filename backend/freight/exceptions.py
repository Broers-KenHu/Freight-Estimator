from __future__ import annotations

from typing import Any


class FreightError(Exception):
    code = "freight_error"

    def __init__(self, message: str = "", **context: Any):
        super().__init__(message)
        self.context = context


class QuoteValidationError(FreightError):
    code = "quote_validation_error"


class ChannelEligibilityError(FreightError):
    code = "channel_eligibility_error"


class RateCardSelectionError(FreightError):
    code = "rate_card_selection_error"


class CalculatorConfigurationError(FreightError):
    code = "calculator_configuration_error"


class CalculatorExecutionError(FreightError):
    code = "calculator_execution_error"


class ExternalCarrierError(FreightError):
    code = "external_carrier_error"


SENSITIVE_CONTEXT_KEYS = {
    "authorization",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "client_secret",
    "database_url",
    "connection_string",
    "dsn",
}


def freight_error_code(exc: Exception, fallback: str = "freight_error") -> str:
    return str(getattr(exc, "code", "") or fallback)


def safe_error_details(exc: Exception, fallback_code: str = "freight_error", **context: Any) -> dict[str, Any]:
    details = {
        "error_code": freight_error_code(exc, fallback_code),
        "message": str(exc),
        "exception_class": exc.__class__.__name__,
    }
    if isinstance(exc, FreightError) and exc.context:
        details.update({key: _safe_context_value(key, value) for key, value in exc.context.items()})
    details.update({key: _safe_context_value(key, value) for key, value in context.items()})
    return details


def _safe_context_value(key: str, value: Any) -> Any:
    if key.lower() in SENSITIVE_CONTEXT_KEYS:
        return "***redacted***"
    if isinstance(value, dict):
        return {nested_key: _safe_context_value(str(nested_key), nested_value) for nested_key, nested_value in value.items()}
    if isinstance(value, list):
        return [_safe_context_value(key, item) for item in value]
    return value
