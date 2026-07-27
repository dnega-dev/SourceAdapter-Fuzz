from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from source_adapter_fuzz.cli import main
from source_adapter_fuzz.fixtures import FixtureServer
from source_adapter_fuzz.reporting import ConformanceRecord, render
from source_adapter_fuzz.scenarios import list_scenarios, run_scenarios, run_url


ROOT = Path(__file__).resolve().parent.parent


class ReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = [
            ConformanceRecord("passes", True, "all good", 1.25, {"value": 1}),
            ConformanceRecord("fails", False, "not good", 2.5, {"value": 2}),
        ]

    def test_text_report(self) -> None:
        output = render(self.records, "text")
        self.assertIn("PASS passes", output)
        self.assertIn("FAIL fails", output)
        self.assertIn("1/2 passed", output)

    def test_json_report(self) -> None:
        payload = json.loads(render(self.records, "json"))
        self.assertEqual(payload["schema"], "source-adapter-fuzz/report/v1")
        self.assertEqual(payload["summary"]["failed"], 1)
        self.assertEqual(len(payload["results"]), 2)

    def test_junit_report(self) -> None:
        root = ET.fromstring(render(self.records, "junit"))
        self.assertEqual(root.tag, "testsuite")
        self.assertEqual(root.attrib["tests"], "2")
        self.assertEqual(root.attrib["failures"], "1")
        self.assertEqual(len(root.findall("testcase/failure")), 1)

    def test_sarif_report(self) -> None:
        payload = json.loads(render(self.records, "sarif"))
        self.assertEqual(payload["version"], "2.1.0")
        results = payload["runs"][0]["results"]
        self.assertEqual(results[0]["level"], "note")
        self.assertEqual(results[1]["level"], "error")

    def test_unknown_report_format(self) -> None:
        with self.assertRaises(ValueError):
            render(self.records, "yaml")


class ScenarioAndCliTests(unittest.TestCase):
    def test_scenario_catalog_is_complete_and_unique(self) -> None:
        scenarios = list_scenarios()
        names = [scenario.name for scenario in scenarios]
        self.assertGreaterEqual(len(names), 17)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("partial-batch-failure", names)

    def test_selected_scenarios_pass(self) -> None:
        records = run_scenarios(["redirect", "http-429-retry-after", "truncated-body"])
        self.assertEqual(len(records), 3)
        self.assertTrue(all(record.passed for record in records))

    def test_complete_scenario_suite_passes(self) -> None:
        records = run_scenarios()
        self.assertEqual(len(records), len(list_scenarios()))
        self.assertTrue(all(record.passed for record in records))

    def test_unknown_scenario_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_scenarios(["does-not-exist"])

    def test_run_url_uses_local_fixture(self) -> None:
        with FixtureServer() as fixture:
            record = run_url(
                fixture.base_url + "/ok",
                expected_content_types=("application/json",),
            )
        self.assertTrue(record.passed)
        self.assertEqual(record.details["status_code"], 200)

    def test_cli_scenarios_list_text(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = main(["scenarios", "list"])
        self.assertEqual(status, 0)
        self.assertIn("redirect-loop", stream.getvalue())

    def test_cli_scenarios_list_json(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = main(["scenarios", "list", "--format", "json"])
        payload = json.loads(stream.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["schema"], "source-adapter-fuzz/scenarios/v1")

    def test_cli_run_selected_scenario_as_json(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = main(["run", "--scenario", "redirect", "--format", "json"])
        payload = json.loads(stream.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["summary"]["passed"], 1)

    def test_cli_run_url_failure_returns_one(self) -> None:
        with FixtureServer() as fixture:
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                status = main(["run", fixture.base_url + "/empty-200", "--format", "json"])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(stream.getvalue())["summary"]["failed"], 1)

    def test_cli_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=str(ROOT)) as directory:
            target = Path(directory) / "report.sarif"
            status = main(
                [
                    "run",
                    "--scenario",
                    "redirect",
                    "--format",
                    "sarif",
                    "--output",
                    str(target),
                ]
            )
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(payload["version"], "2.1.0")

    def test_cli_rejects_url_plus_scenario(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(["run", "http://127.0.0.1/", "--scenario", "redirect"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("cannot be combined", stderr.getvalue())

    def test_fixture_server_rejects_non_loopback_bind(self) -> None:
        with self.assertRaises(ValueError):
            FixtureServer("0.0.0.0")


if __name__ == "__main__":
    unittest.main()
