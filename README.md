# Source Adapter Fuzz

Source Adapter Fuzz is a zero-runtime-dependency Python 3.9+ conformance and
fault-injection toolkit for public-data acquisition strategies. It provides a
small adapter protocol, a scored/staggered waterfall engine, deterministic
localhost fixtures, typed failure semantics, and audit-ready acquisition
receipts.

The toolkit is deliberately about **reliability, not evasion**. It does not
rotate identities, bypass access controls, solve challenges, spoof browsers, or
hide traffic. A `403` is terminal and `429 Retry-After` is reported to the
caller; neither condition triggers stealth behavior.

## What the MVP covers

The fixture suite deterministically simulates:

- empty HTTP 200 responses;
- finite redirects and redirect loops;
- HTTP 403, HTTP 429 with `Retry-After`, and HTTP 500;
- slow responses and network disconnects;
- HTML-to-PDF content-type changes;
- malformed PDF bytes and truncated bodies;
- normal and conflicting/stale ETag revalidation;
- JavaScript-only empty shells;
- duplicate URL spellings and normalized cache keys;
- moved resources with a canonical URL;
- invalid declared charset data; and
- batches with both successful and failed items.

The acquisition engine adds score-based ordering, sequential or staggered
waterfalls, continuation-versus-terminal errors, cancellation and
*snipe-abort* accounting, batch de-duplication, and receipts that explain why a
strategy was selected or why fallback stopped.

## Quick start

No third-party runtime packages are required.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
source-adapter-fuzz scenarios list
source-adapter-fuzz run
```

Without installing, run from a checkout:

```sh
PYTHONPATH=src python -m source_adapter_fuzz run --format text
```

### Fixture server

```sh
source-adapter-fuzz serve-fixtures --host 127.0.0.1 --port 8765
curl http://127.0.0.1:8765/ok
```

Port `0` selects an ephemeral port. The server rejects non-loopback bind names.
It makes no outbound requests.

### Reports

`run` supports `text`, `json`, `junit`, and `sarif`:

```sh
source-adapter-fuzz run --format json --output report.json
source-adapter-fuzz run --scenario redirect-loop --format junit
source-adapter-fuzz run http://127.0.0.1:8765/pdf \
  --expect-content-type application/pdf --format sarif
```

Exit status is `0` when every record passes, `1` for a conformance failure, and
`2` for CLI usage errors.

## Adapter protocol

An adapter has a stable name and one method. It returns `AdapterResult` or raises
an `AcquisitionError` subtype:

```python
from typing import Optional
import threading

from source_adapter_fuzz import AcquisitionRequest, AdapterResult

class MyAdapter:
    name = "my-public-api"

    def acquire(
        self,
        request: AcquisitionRequest,
        cancellation: Optional[threading.Event] = None,
    ) -> AdapterResult:
        # Fetch through the public, documented interface. Honor cancellation.
        ...
```

`ContinuationError` says another declared strategy may be tried. `TerminalError`
says fallback must stop. Unknown exceptions or non-`AdapterResult` return values
are converted to terminal `adapter_contract_error` outcomes.

## Waterfall and receipts

```python
from source_adapter_fuzz import AcquisitionEngine, AcquisitionRequest, HttpAdapter, Strategy

engine = AcquisitionEngine(
    [
        Strategy(HttpAdapter(), score=100, label="official-http"),
        Strategy(another_adapter, score=lambda req: 80 if req.url.endswith(".pdf") else 40),
    ],
    stagger_ms=75,
)
run = engine.run(AcquisitionRequest("https://data.example.gov/record.pdf"))
print(run.receipt.to_dict())
```

Scores are evaluated per request and sorted descending; declaration order breaks
ties. With a positive stagger, a fallback starts only after the previous launch
has not completed within that interval. The first valid result wins. In-flight
losers receive a cancellation event and are marked as `snipe_aborted`. A
terminal error cancels active fallbacks and prevents new ones.

Receipts include the normalized cache key, strategy rank and score, every state
transition, typed error code, `Retry-After`, selected strategy, canonical URL,
content hash and size, timing, and cancellation accounting. Receipt timestamps
and durations are observational; fixture behavior and classifications remain
deterministic.

## Cache semantics

`normalize_cache_key()` lower-cases scheme/host, removes fragments and default
ports, removes dot segments, normalizes unreserved percent escapes, and sorts
query components while retaining duplicate components. `MemoryCache` stores
validators. A fresh entry can be returned directly; otherwise `ETag` and
`Last-Modified` are revalidated. A conflicting validator or explicit stale
fixture marker becomes `stale_etag_cache` rather than silently serving bytes.

## Batch behavior

`run_batch()` preserves input order and all item receipts. Equivalent normalized
URLs share one run by default. Successful items remain available if another
item fails; `BatchResult.partial` makes that state explicit. Set
`raise_on_partial=True` to receive a typed `PartialBatchFailure` after accounting
is complete.

## Development

```sh
./ci/check.sh
```

The test suite uses `unittest`, binds fixtures only to an ephemeral localhost
port, and requires no network access. See [docs/fault-model.md](docs/fault-model.md)
for expected classifications and [CONTRIBUTING.md](CONTRIBUTING.md) for change
requirements.

## Limitations

This MVP is a transport/conformance harness, not a crawler or production cache.
It intentionally does not execute JavaScript, parse full PDF object graphs,
persist cache data, retry automatically, or implement authentication. Adapters
own source-specific policy while the engine enforces declared control flow.

## License

Apache License 2.0. See [LICENSE](LICENSE).
