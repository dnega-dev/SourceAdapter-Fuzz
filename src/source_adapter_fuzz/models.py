"""Public data models for requests, results, attempts, and receipts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import AcquisitionFailed


class AttemptState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    CONTINUATION_ERROR = "continuation_error"
    TERMINAL_ERROR = "terminal_error"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AcquisitionRequest:
    """A strategy-neutral acquisition request.

    ``timeout`` is per adapter attempt. ``cache_max_age`` of zero means that a
    cached validator is revalidated rather than returned as fresh.
    """

    url: str
    method: str = "GET"
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float = 2.0
    expected_content_types: Tuple[str, ...] = ()
    allow_empty: bool = False
    validate_pdf: bool = True
    detect_javascript_shell: bool = True
    use_cache: bool = True
    cache_max_age: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("url must be a non-empty string")
        if self.method.upper() not in {"GET", "HEAD"}:
            raise ValueError("only GET and HEAD are supported by the reference adapter")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.cache_max_age < 0:
            raise ValueError("cache_max_age cannot be negative")
        object.__setattr__(self, "method", self.method.upper())
        object.__setattr__(self, "expected_content_types", tuple(self.expected_content_types))


@dataclass
class AdapterResult:
    """Successful bytes returned by an adapter."""

    status_code: int
    url: str
    headers: Dict[str, str]
    body: bytes
    elapsed_ms: float
    adapter: str
    from_cache: bool = False
    canonical_url: Optional[str] = None
    redirect_chain: Tuple[str, ...] = ()

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    @property
    def body_sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


@dataclass
class AttemptReceipt:
    """One strategy's state transition summary."""

    strategy: str
    score: float
    rank: int
    state: AttemptState = AttemptState.PENDING
    launched_at_ms: Optional[float] = None
    elapsed_ms: Optional[float] = None
    status_code: Optional[int] = None
    error_code: Optional[str] = None
    reason: Optional[str] = None
    retry_after: Optional[float] = None
    cancelled: bool = False
    snipe_aborted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "score": self.score,
            "rank": self.rank,
            "state": self.state.value,
            "launched_at_ms": self.launched_at_ms,
            "elapsed_ms": self.elapsed_ms,
            "status_code": self.status_code,
            "error_code": self.error_code,
            "reason": self.reason,
            "retry_after": self.retry_after,
            "cancelled": self.cancelled,
            "snipe_aborted": self.snipe_aborted,
        }


@dataclass
class AcquisitionReceipt:
    """Audit record explaining strategy selection and waterfall accounting."""

    request_url: str
    normalized_cache_key: str
    started_at: str
    outcome: str = "failed"
    selected_strategy: Optional[str] = None
    selection_reason: str = "No strategy produced a valid result."
    canonical_url: Optional[str] = None
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    body_sha256: Optional[str] = None
    body_bytes: Optional[int] = None
    elapsed_ms: float = 0.0
    attempts: List[AttemptReceipt] = field(default_factory=list)
    accounting: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def new(cls, request_url: str, normalized_cache_key: str) -> "AcquisitionReceipt":
        return cls(
            request_url=request_url,
            normalized_cache_key=normalized_cache_key,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_url": self.request_url,
            "normalized_cache_key": self.normalized_cache_key,
            "started_at": self.started_at,
            "outcome": self.outcome,
            "selected_strategy": self.selected_strategy,
            "selection_reason": self.selection_reason,
            "canonical_url": self.canonical_url,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "body_sha256": self.body_sha256,
            "body_bytes": self.body_bytes,
            "elapsed_ms": self.elapsed_ms,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "accounting": dict(self.accounting),
        }


@dataclass
class AcquisitionRun:
    result: Optional[AdapterResult]
    receipt: AcquisitionReceipt

    @property
    def ok(self) -> bool:
        return self.result is not None

    def require_result(self) -> AdapterResult:
        if self.result is None:
            raise AcquisitionFailed(self.receipt.selection_reason, self.receipt)
        return self.result


@dataclass
class BatchItem:
    index: int
    request: AcquisitionRequest
    run: AcquisitionRun

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "url": self.request.url,
            "ok": self.run.ok,
            "receipt": self.run.receipt.to_dict(),
        }


@dataclass
class BatchResult:
    items: Sequence[BatchItem]

    @property
    def successes(self) -> List[BatchItem]:
        return [item for item in self.items if item.run.ok]

    @property
    def failures(self) -> List[BatchItem]:
        return [item for item in self.items if not item.run.ok]

    @property
    def partial(self) -> bool:
        return bool(self.successes and self.failures)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": len(self.items),
            "succeeded": len(self.successes),
            "failed": len(self.failures),
            "partial": self.partial,
            "items": [item.to_dict() for item in self.items],
        }
