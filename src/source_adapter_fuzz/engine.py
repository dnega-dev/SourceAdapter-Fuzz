"""Scored, staggered waterfall acquisition engine."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Union

from .adapters import Strategy
from .cache import normalize_cache_key
from .errors import (
    AcquisitionError,
    AdapterContractError,
    CancelledAttempt,
    ErrorDisposition,
    PartialBatchFailure,
)
from .models import (
    AcquisitionReceipt,
    AcquisitionRequest,
    AcquisitionRun,
    AdapterResult,
    AttemptReceipt,
    AttemptState,
    BatchItem,
    BatchResult,
)


@dataclass
class _Outcome:
    result: Optional[AdapterResult]
    error: Optional[AcquisitionError]
    elapsed_ms: float


class AcquisitionEngine:
    """Order strategies by score and execute a sequential or staggered waterfall.

    A stagger starts the next strategy if the previous strategy has not produced a
    result by ``stagger_ms``. The first valid result wins. Any in-flight losers are
    accounted as *snipe-aborted* and receive the shared cancellation signal.
    """

    def __init__(self, strategies: Sequence[Strategy], *, stagger_ms: float = 0.0) -> None:
        if stagger_ms < 0:
            raise ValueError("stagger_ms cannot be negative")
        self.strategies = tuple(strategies)
        self.stagger_ms = float(stagger_ms)

    def ordered_strategies(self, request: AcquisitionRequest) -> List[Tuple[Strategy, float]]:
        scored: List[Tuple[int, Strategy, float]] = []
        for index, strategy in enumerate(self.strategies):
            scored.append((index, strategy, strategy.score_for(request)))
        scored.sort(key=lambda item: (-item[2], item[0]))
        return [(strategy, score) for _, strategy, score in scored]

    def run(self, request: AcquisitionRequest) -> AcquisitionRun:
        try:
            cache_key = normalize_cache_key(request.url)
        except ValueError:
            cache_key = request.url
        receipt = AcquisitionReceipt.new(request.url, cache_key)
        ordered = self.ordered_strategies(request)
        receipt.attempts = [
            AttemptReceipt(strategy=strategy.name, score=score, rank=rank + 1)
            for rank, (strategy, score) in enumerate(ordered)
        ]
        started = time.monotonic()
        if not ordered:
            receipt.selection_reason = "No acquisition strategies were configured."
            self._finish(receipt, started)
            return AcquisitionRun(None, receipt)
        if self.stagger_ms <= 0 or len(ordered) == 1:
            result = self._run_sequential(request, ordered, receipt)
        else:
            result = self._run_staggered(request, ordered, receipt)
        if result is not None:
            self._record_success(receipt, result)
        elif not receipt.selection_reason or receipt.selection_reason == "No strategy produced a valid result.":
            errors = [a for a in receipt.attempts if a.error_code]
            receipt.selection_reason = "All {} launched strategies failed; no valid response selected.".format(len(errors))
        self._finish(receipt, started)
        return AcquisitionRun(result, receipt)

    def run_batch(
        self,
        requests: Sequence[AcquisitionRequest],
        *,
        deduplicate: bool = True,
        raise_on_partial: bool = False,
    ) -> BatchResult:
        memo: Dict[str, AcquisitionRun] = {}
        items: List[BatchItem] = []
        for index, request in enumerate(requests):
            try:
                key = "{} {}".format(request.method, normalize_cache_key(request.url))
            except ValueError:
                key = "{} {}".format(request.method, request.url)
            if deduplicate and key in memo:
                run = memo[key]
            else:
                run = self.run(request)
                if deduplicate:
                    memo[key] = run
            items.append(BatchItem(index=index, request=request, run=run))
        result = BatchResult(tuple(items))
        if raise_on_partial and result.partial:
            raise PartialBatchFailure(
                "batch completed with both successes and failures",
                succeeded=len(result.successes),
                failed=len(result.failures),
            )
        return result

    def _run_sequential(
        self,
        request: AcquisitionRequest,
        ordered: Sequence[Tuple[Strategy, float]],
        receipt: AcquisitionReceipt,
    ) -> Optional[AdapterResult]:
        cancellation = threading.Event()
        run_started = time.monotonic()
        for index, (strategy, _) in enumerate(ordered):
            attempt = receipt.attempts[index]
            attempt.state = AttemptState.RUNNING
            attempt.launched_at_ms = (time.monotonic() - run_started) * 1000.0
            outcome = self._invoke(strategy, request, cancellation)
            self._apply_outcome(attempt, outcome)
            if outcome.result is not None:
                receipt.selection_reason = (
                    "Selected strategy '{}' at rank {} (score {:.3f}); it was the first valid response."
                ).format(strategy.name, index + 1, attempt.score)
                return outcome.result
            assert outcome.error is not None
            if outcome.error.is_terminal:
                receipt.selection_reason = (
                    "Stopped at terminal error '{}' from strategy '{}'; fallback was not permitted."
                ).format(outcome.error.code, strategy.name)
                return None
        return None

    def _run_staggered(
        self,
        request: AcquisitionRequest,
        ordered: Sequence[Tuple[Strategy, float]],
        receipt: AcquisitionReceipt,
    ) -> Optional[AdapterResult]:
        cancellation = threading.Event()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(ordered))
        futures: Dict[concurrent.futures.Future, int] = {}
        run_started = time.monotonic()
        next_index = 0
        last_launch = run_started
        stop = False

        def launch(index: int) -> None:
            nonlocal last_launch
            strategy = ordered[index][0]
            attempt = receipt.attempts[index]
            attempt.state = AttemptState.RUNNING
            attempt.launched_at_ms = (time.monotonic() - run_started) * 1000.0
            future = executor.submit(self._invoke, strategy, request, cancellation)
            futures[future] = index
            last_launch = time.monotonic()

        launch(0)
        next_index = 1
        try:
            while futures and not stop:
                timeout: Optional[float]
                if next_index < len(ordered):
                    deadline = last_launch + self.stagger_ms / 1000.0
                    timeout = max(0.0, deadline - time.monotonic())
                else:
                    timeout = None
                done, _ = concurrent.futures.wait(
                    tuple(futures), timeout=timeout, return_when=concurrent.futures.FIRST_COMPLETED
                )
                if not done:
                    launch(next_index)
                    next_index += 1
                    continue
                for future in sorted(done, key=lambda item: futures[item]):
                    index = futures.pop(future)
                    outcome = future.result()
                    attempt = receipt.attempts[index]
                    self._apply_outcome(attempt, outcome)
                    if outcome.result is not None:
                        receipt.selection_reason = (
                            "Selected strategy '{}' at rank {} (score {:.3f}); it won the staggered waterfall."
                        ).format(ordered[index][0].name, index + 1, attempt.score)
                        cancellation.set()
                        self._cancel_inflight(futures, receipt, snipe=True)
                        executor.shutdown(wait=False, cancel_futures=True)
                        return outcome.result
                    assert outcome.error is not None
                    if outcome.error.is_terminal:
                        receipt.selection_reason = (
                            "Stopped at terminal error '{}' from strategy '{}'; active fallbacks were cancelled."
                        ).format(outcome.error.code, ordered[index][0].name)
                        cancellation.set()
                        self._cancel_inflight(futures, receipt, snipe=False)
                        stop = True
                        break
                if stop:
                    break
                # A fast continuation failure should not force the next strategy to wait.
                if not futures and next_index < len(ordered):
                    launch(next_index)
                    next_index += 1
                elif next_index < len(ordered) and time.monotonic() - last_launch >= self.stagger_ms / 1000.0:
                    launch(next_index)
                    next_index += 1
        finally:
            if stop:
                executor.shutdown(wait=False, cancel_futures=True)
            elif not cancellation.is_set():
                executor.shutdown(wait=True)
        return None

    @staticmethod
    def _invoke(
        strategy: Strategy,
        request: AcquisitionRequest,
        cancellation: threading.Event,
    ) -> _Outcome:
        started = time.monotonic()
        try:
            result = strategy.adapter.acquire(request, cancellation)
            if not isinstance(result, AdapterResult):
                raise AdapterContractError(
                    "adapter '{}' returned {} instead of AdapterResult".format(
                        strategy.name, type(result).__name__
                    )
                )
            if not result.adapter:
                result.adapter = strategy.name
            return _Outcome(result, None, (time.monotonic() - started) * 1000.0)
        except AcquisitionError as exc:
            return _Outcome(None, exc, (time.monotonic() - started) * 1000.0)
        except Exception as exc:  # Third-party adapters must not leak implementation exceptions.
            error = AdapterContractError(
                "adapter '{}' raised untyped {}: {}".format(
                    strategy.name, type(exc).__name__, exc
                )
            )
            return _Outcome(None, error, (time.monotonic() - started) * 1000.0)

    @staticmethod
    def _apply_outcome(attempt: AttemptReceipt, outcome: _Outcome) -> None:
        attempt.elapsed_ms = outcome.elapsed_ms
        if outcome.result is not None:
            attempt.state = AttemptState.SUCCEEDED
            attempt.status_code = outcome.result.status_code
            return
        assert outcome.error is not None
        attempt.error_code = outcome.error.code
        attempt.reason = outcome.error.message
        attempt.retry_after = outcome.error.retry_after
        if isinstance(outcome.error, CancelledAttempt):
            attempt.state = AttemptState.CANCELLED
            attempt.cancelled = True
        elif outcome.error.disposition is ErrorDisposition.CONTINUE:
            attempt.state = AttemptState.CONTINUATION_ERROR
        else:
            attempt.state = AttemptState.TERMINAL_ERROR

    @staticmethod
    def _cancel_inflight(
        futures: Dict[concurrent.futures.Future, int],
        receipt: AcquisitionReceipt,
        *,
        snipe: bool,
    ) -> None:
        for future, index in list(futures.items()):
            future.cancel()
            attempt = receipt.attempts[index]
            attempt.state = AttemptState.CANCELLED
            attempt.cancelled = True
            attempt.snipe_aborted = snipe
            attempt.error_code = "snipe_aborted" if snipe else "cancelled"
            attempt.reason = (
                "A later strategy supplied the selected result."
                if snipe
                else "A terminal failure stopped the waterfall."
            )

    @staticmethod
    def _record_success(receipt: AcquisitionReceipt, result: AdapterResult) -> None:
        receipt.outcome = "success"
        successful = next(
            (attempt for attempt in receipt.attempts if attempt.state is AttemptState.SUCCEEDED),
            None,
        )
        receipt.selected_strategy = successful.strategy if successful else result.adapter
        receipt.canonical_url = result.canonical_url or result.url
        receipt.status_code = result.status_code
        receipt.content_type = result.content_type
        receipt.body_sha256 = result.body_sha256
        receipt.body_bytes = len(result.body)

    @staticmethod
    def _finish(receipt: AcquisitionReceipt, started: float) -> None:
        receipt.elapsed_ms = (time.monotonic() - started) * 1000.0
        states = [attempt.state for attempt in receipt.attempts]
        receipt.accounting = {
            "configured": len(receipt.attempts),
            "launched": sum(state is not AttemptState.PENDING for state in states),
            "succeeded": sum(state is AttemptState.SUCCEEDED for state in states),
            "continuation_errors": sum(state is AttemptState.CONTINUATION_ERROR for state in states),
            "terminal_errors": sum(state is AttemptState.TERMINAL_ERROR for state in states),
            "cancelled": sum(state is AttemptState.CANCELLED for state in states),
            "snipe_aborted": sum(attempt.snipe_aborted for attempt in receipt.attempts),
            "not_launched": sum(state is AttemptState.PENDING for state in states),
        }
