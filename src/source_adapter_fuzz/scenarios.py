"""Built-in conformance scenarios driven exclusively by localhost fixtures."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .adapters import HttpAdapter, Strategy
from .engine import AcquisitionEngine
from .fixtures import FixtureServer
from .models import AcquisitionRequest, AcquisitionRun
from .reporting import ConformanceRecord


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    path: str


SCENARIOS: Tuple[Scenario, ...] = (
    Scenario("empty-http-200", "Reject a successful status with an empty body.", "/empty-200"),
    Scenario("redirect", "Follow a finite same-origin redirect and record its chain.", "/redirect"),
    Scenario("redirect-loop", "Detect a cyclic redirect graph.", "/redirect-loop-a"),
    Scenario("http-403", "Treat explicit forbidden policy as terminal.", "/forbidden"),
    Scenario("http-429-retry-after", "Expose rate limiting and parsed Retry-After.", "/rate-limited"),
    Scenario("http-500", "Classify server failure as a continuation error.", "/server-error"),
    Scenario("slow-response", "Turn a deadline breach into a typed timeout.", "/slow?delay=0.20"),
    Scenario("content-type-switch", "Observe a resource switch from HTML to PDF.", "/content-switch"),
    Scenario("malformed-pdf", "Validate PDF signature and EOF markers.", "/malformed-pdf"),
    Scenario("truncated-body", "Detect a body shorter than Content-Length.", "/truncated"),
    Scenario("stale-etag-cache", "Reject conflicting 304 and cached validators.", "/stale-etag"),
    Scenario("javascript-empty-shell", "Reject markup containing no server-rendered record.", "/javascript-shell"),
    Scenario("duplicate-urls", "Normalize and de-duplicate equivalent URL spellings.", "/duplicate-urls"),
    Scenario("moved-canonical-url", "Record redirects and the final canonical URL.", "/moved"),
    Scenario("charset-problem", "Report bytes invalid for the declared charset.", "/charset-problem"),
    Scenario("network-exception", "Map a dropped connection to a network error.", "/network-exception"),
    Scenario("partial-batch-failure", "Preserve successes when one batch item fails.", "/partial-batch"),
)

_SCENARIO_BY_NAME = {scenario.name: scenario for scenario in SCENARIOS}


def list_scenarios() -> Tuple[Scenario, ...]:
    return SCENARIOS


def _error_code(run: AcquisitionRun) -> Optional[str]:
    for attempt in run.receipt.attempts:
        if attempt.error_code:
            return attempt.error_code
    return None


def run_scenarios(names: Optional[Sequence[str]] = None) -> List[ConformanceRecord]:
    selected = list(names) if names else [scenario.name for scenario in SCENARIOS]
    unknown = [name for name in selected if name not in _SCENARIO_BY_NAME]
    if unknown:
        raise ValueError("unknown scenario(s): {}".format(", ".join(unknown)))
    records: List[ConformanceRecord] = []
    with FixtureServer() as fixtures:
        adapter = HttpAdapter()
        engine = AcquisitionEngine([Strategy(adapter, score=100.0)])
        for name in selected:
            fixtures.state.reset()
            started = time.monotonic()
            try:
                passed, message, details = _run_one(name, fixtures, adapter, engine)
            except Exception as exc:
                passed = False
                message = "scenario raised unhandled {}: {}".format(type(exc).__name__, exc)
                details = {"exception": type(exc).__name__}
            records.append(
                ConformanceRecord(
                    name=name,
                    passed=passed,
                    message=message,
                    duration_ms=(time.monotonic() - started) * 1000.0,
                    details=details,
                )
            )
    return records


def run_url(url: str, *, timeout: float = 2.0, expected_content_types: Iterable[str] = ()) -> ConformanceRecord:
    adapter = HttpAdapter()
    engine = AcquisitionEngine([Strategy(adapter, score=100.0)])
    started = time.monotonic()
    run = engine.run(
        AcquisitionRequest(
            url,
            timeout=timeout,
            expected_content_types=tuple(expected_content_types),
        )
    )
    return ConformanceRecord(
        name="acquire-url",
        passed=run.ok,
        message=run.receipt.selection_reason,
        duration_ms=(time.monotonic() - started) * 1000.0,
        details=run.receipt.to_dict(),
    )


def _run_one(
    name: str,
    fixtures: FixtureServer,
    adapter: HttpAdapter,
    engine: AcquisitionEngine,
) -> Tuple[bool, str, Dict[str, object]]:
    base = fixtures.base_url

    if name == "empty-http-200":
        return _expect_error(engine.run(AcquisitionRequest(base + "/empty-200")), "empty_http_200")
    if name == "redirect":
        run = engine.run(AcquisitionRequest(base + "/redirect", expected_content_types=("application/json",)))
        passed = bool(run.ok and run.result and len(run.result.redirect_chain) == 1)
        return passed, "finite redirect followed" if passed else "redirect was not followed correctly", run.receipt.to_dict()
    if name == "redirect-loop":
        return _expect_error(engine.run(AcquisitionRequest(base + "/redirect-loop-a")), "redirect_loop")
    if name == "http-403":
        run = engine.run(AcquisitionRequest(base + "/forbidden"))
        passed, message, details = _expect_error(run, "http_403")
        passed = passed and bool(run.receipt.attempts and run.receipt.attempts[0].state.value == "terminal_error")
        return passed, message, details
    if name == "http-429-retry-after":
        run = engine.run(AcquisitionRequest(base + "/rate-limited"))
        passed, message, details = _expect_error(run, "http_429")
        passed = passed and run.receipt.attempts[0].retry_after == 2.0
        return passed, "rate limit and Retry-After classified" if passed else message, details
    if name == "http-500":
        return _expect_error(engine.run(AcquisitionRequest(base + "/server-error")), "http_server_error")
    if name == "slow-response":
        return _expect_error(
            engine.run(AcquisitionRequest(base + "/slow?delay=0.20", timeout=0.04)),
            "slow_response_timeout",
        )
    if name == "content-type-switch":
        first = engine.run(AcquisitionRequest(base + "/content-switch"))
        second = engine.run(AcquisitionRequest(base + "/content-switch"))
        first_type = first.result.content_type if first.result else None
        second_type = second.result.content_type if second.result else None
        passed = first_type == "text/html" and second_type == "application/pdf"
        return (
            passed,
            "content type switched HTML to PDF" if passed else "content type switch not observed",
            {"first": first.receipt.to_dict(), "second": second.receipt.to_dict()},
        )
    if name == "malformed-pdf":
        return _expect_error(engine.run(AcquisitionRequest(base + "/malformed-pdf")), "malformed_pdf")
    if name == "truncated-body":
        return _expect_error(engine.run(AcquisitionRequest(base + "/truncated")), "truncated_body")
    if name == "stale-etag-cache":
        first = engine.run(AcquisitionRequest(base + "/stale-etag"))
        second = engine.run(AcquisitionRequest(base + "/stale-etag"))
        passed = first.ok and _error_code(second) == "stale_etag_cache"
        return (
            passed,
            "conflicting cache validator rejected" if passed else "stale validator was not detected",
            {"first": first.receipt.to_dict(), "second": second.receipt.to_dict()},
        )
    if name == "javascript-empty-shell":
        return _expect_error(engine.run(AcquisitionRequest(base + "/javascript-shell")), "javascript_empty_shell")
    if name == "duplicate-urls":
        manifest = engine.run(AcquisitionRequest(base + "/duplicate-urls"))
        if not manifest.result:
            return False, "duplicate URL manifest could not be acquired", manifest.receipt.to_dict()
        urls = json.loads(manifest.result.body.decode("utf-8"))["urls"]
        before = fixtures.state.count("/ok")
        batch = engine.run_batch([AcquisitionRequest(url) for url in urls], deduplicate=True)
        fetched = fixtures.state.count("/ok") - before
        passed = len(batch.items) == 3 and len(batch.successes) == 3 and fetched == 2
        return passed, "three inputs collapsed to two acquisitions" if passed else "URL de-duplication failed", batch.to_dict()
    if name == "moved-canonical-url":
        run = engine.run(AcquisitionRequest(base + "/moved"))
        canonical = base + "/canonical"
        passed = bool(run.ok and run.result and run.result.canonical_url == canonical and run.result.redirect_chain)
        return passed, "moved resource canonicalized" if passed else "canonical URL not retained", run.receipt.to_dict()
    if name == "charset-problem":
        return _expect_error(engine.run(AcquisitionRequest(base + "/charset-problem")), "charset_problem")
    if name == "network-exception":
        return _expect_error(engine.run(AcquisitionRequest(base + "/network-exception")), "network_exception")
    if name == "partial-batch-failure":
        manifest = engine.run(AcquisitionRequest(base + "/partial-batch"))
        if not manifest.result:
            return False, "partial batch manifest could not be acquired", manifest.receipt.to_dict()
        urls = json.loads(manifest.result.body.decode("utf-8"))["urls"]
        batch = engine.run_batch([AcquisitionRequest(url) for url in urls])
        passed = batch.partial and len(batch.successes) == 2 and len(batch.failures) == 1
        return passed, "partial failure retained two successful items" if passed else "partial failure accounting wrong", batch.to_dict()
    raise AssertionError("unreachable scenario: {}".format(name))


def _expect_error(run: AcquisitionRun, code: str) -> Tuple[bool, str, Dict[str, object]]:
    actual = _error_code(run)
    passed = not run.ok and actual == code
    message = "classified expected {}".format(code) if passed else "expected {}, received {}".format(code, actual)
    return passed, message, run.receipt.to_dict()
