"""Minimal custom adapter participating in a scored waterfall."""

from __future__ import annotations

import threading
import time
from typing import Optional

from source_adapter_fuzz import (
    AcquisitionEngine,
    AcquisitionRequest,
    AdapterResult,
    ContinuationError,
    Strategy,
)


class StaticPublicDatasetAdapter:
    """Example only: model a source-specific adapter without network access."""

    name = "static-public-dataset"

    def acquire(
        self,
        request: AcquisitionRequest,
        cancellation: Optional[threading.Event] = None,
    ) -> AdapterResult:
        started = time.monotonic()
        if cancellation is not None and cancellation.is_set():
            raise ContinuationError("cancelled before static lookup", code="cancelled")
        if request.url != "dataset://example/record-42":
            raise ContinuationError("record is absent from this strategy", code="record_absent")
        body = b'{"id":42,"status":"published"}'
        return AdapterResult(
            status_code=200,
            url=request.url,
            headers={"content-type": "application/json"},
            body=body,
            elapsed_ms=(time.monotonic() - started) * 1000.0,
            adapter=self.name,
            canonical_url=request.url,
        )


def main() -> None:
    engine = AcquisitionEngine(
        [Strategy(StaticPublicDatasetAdapter(), score=100.0)],
    )
    run = engine.run(AcquisitionRequest("dataset://example/record-42"))
    print(run.result.body.decode("utf-8") if run.result else "failed")
    print(run.receipt.to_dict())


if __name__ == "__main__":
    main()
