"""Typed acquisition failures.

Continuation errors mean another declared strategy may be tried.  Terminal errors
mean the request itself is invalid or policy has said to stop.  The distinction is
explicit so adapters never need to communicate control flow through strings.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Mapping, Optional


class ErrorDisposition(str, Enum):
    """How the waterfall should react to an error."""

    CONTINUE = "continuation"
    TERMINAL = "terminal"


class AcquisitionError(Exception):
    """Base class for expected, typed acquisition failures."""

    default_code = "acquisition_error"
    default_disposition = ErrorDisposition.TERMINAL

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        disposition: Optional[ErrorDisposition] = None,
        retry_after: Optional[float] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.disposition = disposition or self.default_disposition
        self.retry_after = retry_after
        self.details = dict(details or {})

    @property
    def is_continuation(self) -> bool:
        return self.disposition is ErrorDisposition.CONTINUE

    @property
    def is_terminal(self) -> bool:
        return self.disposition is ErrorDisposition.TERMINAL

    def as_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "disposition": self.disposition.value,
        }
        if self.retry_after is not None:
            data["retry_after"] = self.retry_after
        if self.details:
            data["details"] = self.details
        return data


class ContinuationError(AcquisitionError):
    default_disposition = ErrorDisposition.CONTINUE


class TerminalError(AcquisitionError):
    default_disposition = ErrorDisposition.TERMINAL


class NetworkFailure(ContinuationError):
    default_code = "network_exception"


class ResponseTimeout(ContinuationError):
    default_code = "slow_response_timeout"


class EmptyResponse(ContinuationError):
    default_code = "empty_http_200"


class RedirectLoop(ContinuationError):
    default_code = "redirect_loop"


class TooManyRedirects(ContinuationError):
    default_code = "too_many_redirects"


class RateLimited(ContinuationError):
    default_code = "http_429"


class ServerFailure(ContinuationError):
    default_code = "http_server_error"


class UnexpectedContentType(ContinuationError):
    default_code = "unexpected_content_type"


class MalformedPDF(ContinuationError):
    default_code = "malformed_pdf"


class TruncatedBody(ContinuationError):
    default_code = "truncated_body"


class StaleCache(ContinuationError):
    default_code = "stale_etag_cache"


class JavaScriptShell(ContinuationError):
    default_code = "javascript_empty_shell"


class CharsetFailure(ContinuationError):
    default_code = "charset_problem"


class CancelledAttempt(ContinuationError):
    default_code = "cancelled"


class Forbidden(TerminalError):
    default_code = "http_403"


class ClientFailure(TerminalError):
    default_code = "http_client_error"


class InvalidRequest(TerminalError):
    default_code = "invalid_request"


class AdapterContractError(TerminalError):
    default_code = "adapter_contract_error"


class PartialBatchFailure(ContinuationError):
    """Raised on request when a batch has both successes and failures."""

    default_code = "partial_batch_failure"

    def __init__(self, message: str, *, succeeded: int, failed: int) -> None:
        super().__init__(message, details={"succeeded": succeeded, "failed": failed})
        self.succeeded = succeeded
        self.failed = failed


class AcquisitionFailed(TerminalError):
    """Raised by ``AcquisitionRun.require_result`` while retaining its receipt."""

    default_code = "all_strategies_failed"

    def __init__(self, message: str, receipt: Any) -> None:
        super().__init__(message)
        self.receipt = receipt
