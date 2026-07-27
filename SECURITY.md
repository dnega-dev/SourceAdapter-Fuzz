# Security policy

## Supported versions

This pre-1.0 project currently supports the latest released `0.1.x` version on
CPython 3.9 and newer. Security fixes may require upgrading to the newest patch.

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the project maintainers.
Include the affected version, operating system and Python version, a minimal
reproducer, impact, and any suggested mitigation. Do not include credentials,
private source data, or exploit unrelated public services in a report.

Maintainers should acknowledge a report within five business days, validate it
in an isolated localhost fixture, and coordinate a fix and disclosure timeline.
There is no bug bounty commitment.

## Security boundaries

- The fixture server binds only to `127.0.0.1`, `localhost`, or `::1`. It has no
  outbound behavior and should not be exposed as a production service.
- URL mode intentionally performs network I/O chosen by the caller. Applications
  accepting untrusted URLs must add their own SSRF policy, DNS/IP allowlist, and
  egress controls before constructing an `AcquisitionRequest`.
- Response bytes are held in memory. The MVP does not impose a maximum body size;
  production adapters should enforce one appropriate to their source contract.
- The client follows HTTP redirects up to a configured limit. Callers requiring
  same-origin redirects must enforce that policy in a custom adapter.
- Receipts contain URLs and selected response metadata. Avoid credentials in
  URLs or headers, and review receipts before sharing them.
- The in-memory cache is process-local and is not designed for sensitive data or
  cross-tenant isolation.
- JUnit, JSON, SARIF, and text are serializers only. Report viewers remain
  responsible for safely rendering untrusted source strings.

## Explicit non-goals

Source Adapter Fuzz does not provide CAPTCHA solving, browser impersonation,
credential harvesting, proxy rotation, rate-limit bypass, access-control bypass,
or any other stealth capability. HTTP 403 is terminal. HTTP 429 is surfaced with
its `Retry-After` value so the caller can comply with source policy.
