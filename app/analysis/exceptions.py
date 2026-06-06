from __future__ import annotations


class AnalysisError(Exception): ...


class RetryableError(AnalysisError): ...
class ProviderUnavailableError(RetryableError): ...
class RateLimitError(RetryableError): ...


class NonRetryableError(AnalysisError): ...
class AuthError(NonRetryableError): ...
class BadRequestError(NonRetryableError): ...
class ContentPolicyError(NonRetryableError): ...


class SchemaValidationError(NonRetryableError):
    def __init__(self, message: str, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response


class PoolExhaustedError(AnalysisError):
    """Raised by ModelPool when all members fail."""

    def __init__(self, message: str, attempts: list | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts or []
