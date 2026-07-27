"""Command-line interface for fixtures and conformance runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .fixtures import FixtureServer
from .reporting import render
from .scenarios import list_scenarios, run_scenarios, run_url


_FORMATS = ("text", "json", "junit", "sarif")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="source-adapter-fuzz",
        description="Conformance and fault injection for public-data acquisition adapters.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve-fixtures", help="run the deterministic localhost fixture server")
    serve.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost", "::1"))
    serve.add_argument("--port", type=int, default=8765)

    run = subparsers.add_parser("run", help="run built-in scenarios or acquire one URL")
    run.add_argument("url", nargs="?", help="optional URL; omit it to run the built-in fixture suite")
    run.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help="built-in scenario name (repeatable; implies built-in suite)",
    )
    run.add_argument("--timeout", type=float, default=2.0, help="per-attempt URL timeout in seconds")
    run.add_argument(
        "--expect-content-type",
        action="append",
        default=[],
        help="acceptable media type for URL mode (repeatable)",
    )
    run.add_argument("--format", choices=_FORMATS, default="text")
    run.add_argument("--output", help="write the report to this path instead of stdout")

    scenarios = subparsers.add_parser("scenarios", help="inspect built-in fault scenarios")
    scenario_sub = scenarios.add_subparsers(dest="scenario_command", required=True)
    scenario_list = scenario_sub.add_parser("list", help="list scenario names and descriptions")
    scenario_list.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve-fixtures":
        return _serve(args.host, args.port)
    if args.command == "scenarios":
        return _list(args.format)
    if args.command == "run":
        if args.url and args.scenarios:
            parser.error("URL mode and --scenario cannot be combined")
        try:
            if args.url:
                records = [
                    run_url(
                        args.url,
                        timeout=args.timeout,
                        expected_content_types=args.expect_content_type,
                    )
                ]
            else:
                records = run_scenarios(args.scenarios)
        except ValueError as exc:
            parser.error(str(exc))
        output = render(records, args.format)
        _write_output(output, args.output)
        return 0 if all(record.passed for record in records) else 1
    parser.error("unknown command")
    return 2


def _serve(host: str, port: int) -> int:
    fixtures = FixtureServer(host, port)
    fixtures.start()
    print("source-adapter-fuzz fixtures listening at {}".format(fixtures.base_url), flush=True)
    try:
        assert fixtures.thread is not None
        while fixtures.thread.is_alive():
            fixtures.thread.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        fixtures.stop()
    return 0


def _list(output_format: str) -> int:
    scenarios = list_scenarios()
    if output_format == "json":
        payload = {
            "schema": "source-adapter-fuzz/scenarios/v1",
            "scenarios": [
                {"name": scenario.name, "description": scenario.description, "path": scenario.path}
                for scenario in scenarios
            ],
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        width = max(len(scenario.name) for scenario in scenarios)
        for scenario in scenarios:
            sys.stdout.write("{:<{}}  {}\n".format(scenario.name, width, scenario.description))
    return 0


def _write_output(content: str, output: Optional[str]) -> None:
    if output:
        Path(output).write_text(content, encoding="utf-8")
    else:
        sys.stdout.write(content)


if __name__ == "__main__":
    raise SystemExit(main())
