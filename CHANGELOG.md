# Changelog

All notable changes are documented here. The format follows Keep a Changelog,
and the project intends to use Semantic Versioning after the initial MVP.

## [Unreleased]

### Planned

- Adapter contract test helpers for third-party implementations.
- Optional bounded response-size policy.

## [0.1.0] - 2025-01-01

### Added

- Zero-runtime-dependency Python 3.9+ package and CLI.
- `SourceAdapter` protocol, reference `HttpAdapter`, typed continuation and
  terminal acquisition errors.
- Score-ordered sequential and staggered waterfall execution.
- Cancellation and snipe-abort accounting with acquisition receipts.
- Conservative normalized URL cache keys, in-memory validators, and duplicate
  request de-duplication.
- Deterministic localhost fixtures covering empty 200, redirects and loops, 403,
  429 with `Retry-After`, 500, slow responses, content switches, malformed PDF,
  truncation, stale ETag, JavaScript shells, duplicate URLs, canonical moves,
  charset faults, network disconnects, and partial batches.
- `serve-fixtures`, `run`, and `scenarios list` CLI commands.
- Text, JSON, JUnit XML, and SARIF 2.1.0 reports.
- Standard-library `unittest` suite, examples, fault model documentation, and
  portable CI check script.

[Unreleased]: https://example.invalid/source-adapter-fuzz/compare/v0.1.0...HEAD
[0.1.0]: https://example.invalid/source-adapter-fuzz/releases/tag/v0.1.0
