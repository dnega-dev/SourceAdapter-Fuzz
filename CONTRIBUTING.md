# Contributing

Thanks for improving acquisition reliability. Changes should remain small,
deterministic, standard-library-only at runtime, and safe to execute offline.

## Set up

Python 3.9 or newer is required. An editable install is optional:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
./ci/check.sh
```

The check script sets `PYTHONPATH=src`, runs all `unittest` tests, and compiles
source, tests, and examples. Tests must use localhost fixtures only; never rely
on a public URL, DNS, sleep longer than necessary, credentials, or order shared
with another test.

## Change requirements

1. Add or update tests for every behavioral change.
2. Keep Python 3.9 compatibility and avoid runtime dependencies.
3. Use a typed `ContinuationError` or `TerminalError`; do not key control flow
   off human-readable messages.
4. Update `docs/fault-model.md` for a new fixture or classification.
5. Ensure receipts explain selection and account for pending, failed, cancelled,
   and snipe-aborted attempts.
6. Update `CHANGELOG.md` for user-visible behavior.
7. Run `./ci/check.sh` before proposing the change.

## Fixture design

A fixture must be deterministic, bounded, localhost-only, and have a documented
expected outcome. Use an ephemeral port in tests. If a fault needs connection
teardown or delay, catch expected peer disconnects so test output stays clean.
Stateful fixtures must expose/reset state through `FixtureState`.

## Adapter behavior

Adapters consume `AcquisitionRequest`, return `AdapterResult`, and accept an
optional `threading.Event` cancellation signal. They should honor public source
terms and identify themselves honestly. Do not add browser spoofing, proxy
rotation, challenge solving, access-control bypass, or retry behavior that
ignores `Retry-After`.

## Style

Use four spaces, type hints on public APIs, docstrings for modules and public
objects, stable output schemas, and descriptive tests. Prefer explicit state
over hidden retries. No formatter or linter dependency is required for the MVP.

## License

By contributing, you agree that your contribution is licensed under Apache-2.0.
