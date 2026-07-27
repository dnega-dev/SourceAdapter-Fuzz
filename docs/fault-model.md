# Fault model

This document defines the observable contract of the MVP fixture suite and
reference HTTP adapter. Fixtures are test doubles, not assertions that every
real source should use identical policy. Source-specific adapters may refine the
policy, but must preserve explicit continuation/terminal semantics.

## Dispositions

A **continuation error** says the current strategy did not produce acceptable
bytes and another configured strategy may run. A **terminal error** says fallback
must stop because continuing would violate request validity or policy. Both are
`AcquisitionError` values with stable machine-readable `code`, a human message,
and optional details.

| Fixture | Trigger | Expected code/result | Disposition |
| --- | --- | --- | --- |
| `/empty-200` | `200` and zero bytes | `empty_http_200` | continuation |
| `/redirect` | `302` to `/ok` | valid JSON, one redirect in receipt | success |
| `/redirect-loop-a` | A → B → A | `redirect_loop` | continuation |
| `/forbidden` | `403` | `http_403` | **terminal** |
| `/rate-limited` | `429`, `Retry-After: 2` | `http_429`, `retry_after=2.0` | continuation |
| `/server-error` | `500` | `http_server_error` | continuation |
| `/slow?delay=.20` | deadline below delay | `slow_response_timeout` | continuation |
| `/content-switch` | odd hit HTML, even hit PDF | two valid results with different media types | success |
| `/malformed-pdf` | PDF media type, invalid bytes | `malformed_pdf` | continuation |
| `/truncated` | declared 100 bytes, sends 5 | `truncated_body` | continuation |
| `/etag` | validator revalidation | cached result on `304` | success |
| `/stale-etag` | conflicting `304` validator | `stale_etag_cache` | continuation |
| `/javascript-shell` | empty app mount plus script | `javascript_empty_shell` | continuation |
| `/duplicate-urls` | equivalent query order/fragment | 3 items, 2 acquisitions | success |
| `/moved` | `301` to page declaring canonical | final canonical URL retained | success |
| `/charset-problem` | invalid UTF-8 under UTF-8 header | `charset_problem` | continuation |
| `/network-exception` | close before status line | `network_exception` | continuation |
| `/partial-batch` | success, 500, success URLs | two successes and one retained failure | partial |

The fixture server also provides `/ok`, `/pdf`, `/canonical`,
`/charset-latin1`, `/javascript-shell-marker`, and `/health` as supporting
controls. It tracks per-path hit counts and can reset them between scenarios.

## HTTP validation order

The reference adapter applies faults in this order:

1. validate the URL shape and cancellation state;
2. normalize the GET/HEAD cache key and prepare validators;
3. perform one request at a time, manually following bounded redirects;
4. classify status (`304`, redirects, 403, 429, 5xx, other 4xx);
5. verify `Content-Length` where present;
6. reject an empty non-HEAD body unless explicitly allowed;
7. enforce expected media types, if any;
8. validate minimal PDF framing (`%PDF-` and trailing `%%EOF`);
9. decode textual bytes using the declared charset (UTF-8 by default);
10. identify a deterministic JavaScript empty-shell signature; and
11. derive `Content-Location`/HTML canonical URL and cache validators.

This ordering matters. For example, malformed PDF bytes produce
`malformed_pdf`, not a generic content error, and a 403 does not inspect or
interpret its body.

## Redirects

Redirects are followed explicitly for statuses 301, 302, 303, 307, and 308. The
normalized destination is added to a visited set before the next request. A
repeated key is `redirect_loop`; exceeding `max_redirects` is
`too_many_redirects`. Relative destinations are resolved against the current
URL. Redirect history stores destinations in request order.

The reference implementation permits cross-origin redirects because real
public-data endpoints may move across hosts. Deployments needing stricter SSRF
or trust-domain policy should wrap or replace `HttpAdapter`.

## Cache and stale validators

The normalized key includes the method and normalized absolute URL. URL
normalization:

- lower-cases scheme and IDNA host;
- removes default ports and fragments;
- normalizes percent escapes of unreserved characters;
- removes `.` and `..` path segments; and
- sorts raw query components while preserving duplicate components.

A positive `cache_max_age` can return a fresh entry without transport. A zero
value revalidates with `If-None-Match` and/or `If-Modified-Since`. A normal `304`
returns cached bytes with `from_cache=True`. A `304` without an entry, a
conflicting response ETag, or the explicit fixture stale marker is
`stale_etag_cache`; stale bytes are not returned.

## JavaScript shell signal

This toolkit does not execute JavaScript. It identifies only deterministic
empty-shell evidence: the fixture marker, or a near-empty `#app`/`#root` mount
combined with a script. It does not classify a normal HTML page merely because
scripts are present. An adapter can disable the check when an empty mount is a
valid source representation.

## Waterfall state model

Each configured attempt starts `pending`, then follows one of these transitions:

```text
pending -> running -> succeeded
                   -> continuation_error
                   -> terminal_error
                   -> cancelled
```

Strategies are sorted by descending per-request score; declaration order is the
stable tie-breaker. In sequential mode, a continuation error advances to the
next pending strategy. A terminal error stops immediately.

In staggered mode, strategy N+1 launches after `stagger_ms` if active attempts
have not produced a result. The first valid `AdapterResult` is selected. The
engine signals cancellation to other active adapters and records those attempts
as `cancelled` plus `snipe_aborted=true`. If a terminal error arrives first,
active fallbacks are cancelled but not counted as snipe-aborted. Pending
strategies remain `pending` and contribute to `not_launched` accounting.

Adapters should inspect the provided `threading.Event` before transport and
between body chunks. Python cannot forcibly stop arbitrary adapter code, so
receipts record cancellation intent immediately; a non-cooperative adapter may
finish in its background worker after `run()` returns.

## Receipts

A receipt is emitted for success and failure. It contains:

- original URL and normalized cache key;
- start time and total elapsed milliseconds;
- configured strategy rank, evaluated score, state, launch offset and duration;
- status, stable error code, reason, and parsed retry delay per attempt;
- selected strategy and an English selection explanation;
- final/canonical URL, media type, byte count and SHA-256 on success; and
- counts for configured, launched, successful, continuation, terminal,
  cancelled, snipe-aborted, and not-launched attempts.

Bodies are not embedded in receipts. URLs may still contain sensitive query
values; callers should sanitize them before long-term retention.

## Partial batches and duplicates

Batch execution preserves one `BatchItem` for every input index. With
`deduplicate=True`, requests sharing method plus normalized URL reuse the same
`AcquisitionRun`; they do not make another transport request. A batch is
`partial` only when at least one item succeeds and at least one fails. The
successful results remain accessible. `raise_on_partial=True` raises
`PartialBatchFailure` only after all item accounting exists.

## Determinism boundaries

Fixture response classes, bodies, status codes, validators, and hit-driven
switches are deterministic. Ephemeral ports, wall-clock timestamps, scheduling,
and measured durations are intentionally variable. Stagger tests should assert
states and accounting rather than exact millisecond values.
