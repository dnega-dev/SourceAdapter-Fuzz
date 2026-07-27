from __future__ import annotations

import threading
import time
import unittest
from typing import List, Optional

from source_adapter_fuzz import (
    AcquisitionEngine,
    AcquisitionFailed,
    AcquisitionRequest,
    AdapterResult,
    AttemptState,
    CancelledAttempt,
    ContinuationError,
    EmptyResponse,
    Forbidden,
    PartialBatchFailure,
    Strategy,
    TerminalError,
)


LOCAL_URL = "http://127.0.0.1/fake-record"


class FakeAdapter:
    def __init__(
        self,
        name: str,
        *,
        delay: float = 0.0,
        error: Optional[Exception] = None,
        body: bytes = b"ok",
        honor_cancellation: bool = True,
    ) -> None:
        self.name = name
        self.delay = delay
        self.error = error
        self.body = body
        self.honor_cancellation = honor_cancellation
        self.calls: List[str] = []

    def acquire(
        self,
        request: AcquisitionRequest,
        cancellation: Optional[threading.Event] = None,
    ) -> AdapterResult:
        self.calls.append(request.url)
        started = time.monotonic()
        while time.monotonic() - started < self.delay:
            if self.honor_cancellation and cancellation is not None and cancellation.is_set():
                raise CancelledAttempt("fake adapter observed cancellation")
            time.sleep(0.002)
        if self.error is not None:
            raise self.error
        return AdapterResult(
            status_code=200,
            url=request.url,
            headers={"content-type": "text/plain"},
            body=self.body,
            elapsed_ms=(time.monotonic() - started) * 1000.0,
            adapter=self.name,
            canonical_url=request.url,
        )


class UntypedFailureAdapter:
    name = "untyped"

    def acquire(self, request: AcquisitionRequest, cancellation=None) -> AdapterResult:
        raise RuntimeError("implementation leaked")


class InvalidReturnAdapter:
    name = "invalid-return"

    def acquire(self, request: AcquisitionRequest, cancellation=None):
        return "not a result"


class EngineTests(unittest.TestCase):
    def request(self, suffix: str = "") -> AcquisitionRequest:
        return AcquisitionRequest(LOCAL_URL + suffix)

    def test_higher_score_runs_first(self) -> None:
        low = FakeAdapter("low")
        high = FakeAdapter("high")
        run = AcquisitionEngine(
            [Strategy(low, score=1), Strategy(high, score=10)]
        ).run(self.request())
        self.assertTrue(run.ok)
        self.assertEqual(run.receipt.selected_strategy, "high")
        self.assertEqual(low.calls, [])

    def test_continuation_falls_back(self) -> None:
        first = FakeAdapter("first", error=EmptyResponse("empty"))
        second = FakeAdapter("second", body=b"fallback")
        run = AcquisitionEngine(
            [Strategy(first, score=10), Strategy(second, score=5)]
        ).run(self.request())
        self.assertTrue(run.ok)
        self.assertEqual(run.result.body if run.result else None, b"fallback")
        self.assertEqual(run.receipt.attempts[0].state, AttemptState.CONTINUATION_ERROR)
        self.assertEqual(run.receipt.attempts[1].state, AttemptState.SUCCEEDED)

    def test_terminal_error_stops_fallback(self) -> None:
        first = FakeAdapter("first", error=Forbidden("policy denied"))
        second = FakeAdapter("second")
        run = AcquisitionEngine(
            [Strategy(first, score=10), Strategy(second, score=5)]
        ).run(self.request())
        self.assertFalse(run.ok)
        self.assertEqual(run.receipt.attempts[0].state, AttemptState.TERMINAL_ERROR)
        self.assertEqual(run.receipt.attempts[1].state, AttemptState.PENDING)
        self.assertEqual(second.calls, [])
        self.assertIn("fallback was not permitted", run.receipt.selection_reason)

    def test_declaration_order_breaks_score_tie(self) -> None:
        declared_first = FakeAdapter("declared-first")
        declared_second = FakeAdapter("declared-second")
        engine = AcquisitionEngine(
            [Strategy(declared_first, score=5), Strategy(declared_second, score=5)]
        )
        ordered = engine.ordered_strategies(self.request())
        self.assertEqual([item[0].name for item in ordered], ["declared-first", "declared-second"])

    def test_callable_score_is_request_specific(self) -> None:
        generic = FakeAdapter("generic")
        pdf = FakeAdapter("pdf")
        engine = AcquisitionEngine(
            [
                Strategy(generic, score=50),
                Strategy(pdf, score=lambda request: 100 if request.url.endswith(".pdf") else 1),
            ]
        )
        pdf_order = engine.ordered_strategies(self.request(".pdf"))
        text_order = engine.ordered_strategies(self.request(".txt"))
        self.assertEqual(pdf_order[0][0].name, "pdf")
        self.assertEqual(text_order[0][0].name, "generic")

    def test_strategy_label_is_used_in_receipt(self) -> None:
        adapter = FakeAdapter("implementation-name")
        run = AcquisitionEngine(
            [Strategy(adapter, score=1, label="declared-label")]
        ).run(self.request())
        self.assertEqual(run.receipt.selected_strategy, "declared-label")
        self.assertEqual(run.receipt.attempts[0].strategy, "declared-label")

    def test_empty_strategy_set_is_explained(self) -> None:
        run = AcquisitionEngine([]).run(self.request())
        self.assertFalse(run.ok)
        self.assertIn("No acquisition strategies", run.receipt.selection_reason)
        self.assertEqual(run.receipt.accounting["configured"], 0)

    def test_require_result_retains_receipt(self) -> None:
        adapter = FakeAdapter("failure", error=EmptyResponse("empty"))
        run = AcquisitionEngine([Strategy(adapter)]).run(self.request())
        with self.assertRaises(AcquisitionFailed) as raised:
            run.require_result()
        self.assertIs(raised.exception.receipt, run.receipt)

    def test_success_receipt_hashes_body(self) -> None:
        adapter = FakeAdapter("success", body=b"receipt bytes")
        run = AcquisitionEngine([Strategy(adapter)]).run(self.request())
        self.assertEqual(run.receipt.body_bytes, len(b"receipt bytes"))
        self.assertEqual(len(run.receipt.body_sha256 or ""), 64)
        self.assertEqual(run.receipt.accounting["succeeded"], 1)

    def test_untyped_exception_becomes_terminal_contract_error(self) -> None:
        fallback = FakeAdapter("fallback")
        run = AcquisitionEngine(
            [Strategy(UntypedFailureAdapter(), score=10), Strategy(fallback, score=1)]
        ).run(self.request())
        self.assertFalse(run.ok)
        self.assertEqual(run.receipt.attempts[0].error_code, "adapter_contract_error")
        self.assertEqual(fallback.calls, [])

    def test_invalid_adapter_return_becomes_contract_error(self) -> None:
        run = AcquisitionEngine([Strategy(InvalidReturnAdapter())]).run(self.request())
        self.assertFalse(run.ok)
        self.assertEqual(run.receipt.attempts[0].error_code, "adapter_contract_error")

    def test_staggered_later_strategy_can_win(self) -> None:
        slow = FakeAdapter("slow", delay=0.15)
        fast = FakeAdapter("fast", delay=0.005, body=b"winner")
        run = AcquisitionEngine(
            [Strategy(slow, score=100), Strategy(fast, score=50)],
            stagger_ms=10,
        ).run(self.request())
        self.assertTrue(run.ok)
        self.assertEqual(run.receipt.selected_strategy, "fast")
        self.assertEqual(run.receipt.attempts[0].state, AttemptState.CANCELLED)
        self.assertTrue(run.receipt.attempts[0].snipe_aborted)
        self.assertEqual(run.receipt.accounting["snipe_aborted"], 1)

    def test_staggered_terminal_cancels_active_without_snipe(self) -> None:
        slow = FakeAdapter("slow", delay=0.15)
        denied = FakeAdapter("denied", error=Forbidden("stop"))
        run = AcquisitionEngine(
            [Strategy(slow, score=100), Strategy(denied, score=50)],
            stagger_ms=5,
        ).run(self.request())
        self.assertFalse(run.ok)
        self.assertEqual(run.receipt.attempts[0].state, AttemptState.CANCELLED)
        self.assertFalse(run.receipt.attempts[0].snipe_aborted)
        self.assertEqual(run.receipt.attempts[1].state, AttemptState.TERMINAL_ERROR)

    def test_batch_deduplicates_normalized_urls(self) -> None:
        adapter = FakeAdapter("batch")
        engine = AcquisitionEngine([Strategy(adapter)])
        requests = [
            AcquisitionRequest("http://127.0.0.1/a?b=2&a=1"),
            AcquisitionRequest("http://127.0.0.1:80/a?a=1&b=2#fragment"),
            AcquisitionRequest("http://127.0.0.1/a?a=3"),
        ]
        batch = engine.run_batch(requests)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(len(batch.successes), 3)
        self.assertIs(batch.items[0].run, batch.items[1].run)

    def test_batch_can_disable_deduplication(self) -> None:
        adapter = FakeAdapter("batch")
        request = self.request()
        batch = AcquisitionEngine([Strategy(adapter)]).run_batch(
            [request, request], deduplicate=False
        )
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(len(batch.items), 2)

    def test_partial_batch_retains_success(self) -> None:
        class PathAdapter(FakeAdapter):
            def acquire(self, request: AcquisitionRequest, cancellation=None) -> AdapterResult:
                if request.url.endswith("/bad"):
                    raise ContinuationError("bad item", code="bad_item")
                return super().acquire(request, cancellation)

        engine = AcquisitionEngine([Strategy(PathAdapter("path"))])
        batch = engine.run_batch(
            [
                AcquisitionRequest("http://127.0.0.1/good"),
                AcquisitionRequest("http://127.0.0.1/bad"),
            ]
        )
        self.assertTrue(batch.partial)
        self.assertEqual(len(batch.successes), 1)
        self.assertEqual(len(batch.failures), 1)

    def test_partial_batch_can_raise_typed_error(self) -> None:
        class PathAdapter(FakeAdapter):
            def acquire(self, request: AcquisitionRequest, cancellation=None) -> AdapterResult:
                if request.url.endswith("/bad"):
                    raise ContinuationError("bad item", code="bad_item")
                return super().acquire(request, cancellation)

        engine = AcquisitionEngine([Strategy(PathAdapter("path"))])
        with self.assertRaises(PartialBatchFailure) as raised:
            engine.run_batch(
                [
                    AcquisitionRequest("http://127.0.0.1/good"),
                    AcquisitionRequest("http://127.0.0.1/bad"),
                ],
                raise_on_partial=True,
            )
        self.assertEqual(raised.exception.succeeded, 1)
        self.assertEqual(raised.exception.failed, 1)

    def test_negative_stagger_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AcquisitionEngine([], stagger_ms=-1)

    def test_request_validation(self) -> None:
        with self.assertRaises(ValueError):
            AcquisitionRequest("", timeout=1)
        with self.assertRaises(ValueError):
            AcquisitionRequest(LOCAL_URL, timeout=0)
        with self.assertRaises(ValueError):
            AcquisitionRequest(LOCAL_URL, method="POST")

    def test_error_serialization_has_disposition(self) -> None:
        continuation = ContinuationError("try next", code="next")
        terminal = TerminalError("stop", code="stop")
        self.assertEqual(continuation.as_dict()["disposition"], "continuation")
        self.assertEqual(terminal.as_dict()["disposition"], "terminal")


if __name__ == "__main__":
    unittest.main()
