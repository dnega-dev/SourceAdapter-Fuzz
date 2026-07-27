"""Text, JSON, JUnit, and SARIF serializers for conformance records."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence


@dataclass
class ConformanceRecord:
    name: str
    passed: bool
    message: str
    duration_ms: float = 0.0
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "details": dict(self.details),
        }


def render(records: Sequence[ConformanceRecord], output_format: str) -> str:
    output_format = output_format.lower()
    if output_format == "text":
        return render_text(records)
    if output_format == "json":
        return render_json(records)
    if output_format == "junit":
        return render_junit(records)
    if output_format == "sarif":
        return render_sarif(records)
    raise ValueError("unsupported output format: {}".format(output_format))


def render_text(records: Sequence[ConformanceRecord]) -> str:
    lines: List[str] = []
    for record in records:
        lines.append(
            "{} {:<28} {:>8.2f} ms  {}".format(
                "PASS" if record.passed else "FAIL",
                record.name,
                record.duration_ms,
                record.message,
            )
        )
    passed = sum(record.passed for record in records)
    lines.append("Summary: {}/{} passed; {} failed".format(passed, len(records), len(records) - passed))
    return "\n".join(lines) + "\n"


def render_json(records: Sequence[ConformanceRecord]) -> str:
    passed = sum(record.passed for record in records)
    payload = {
        "schema": "source-adapter-fuzz/report/v1",
        "summary": {"total": len(records), "passed": passed, "failed": len(records) - passed},
        "results": [record.to_dict() for record in records],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_junit(records: Sequence[ConformanceRecord]) -> str:
    failures = sum(not record.passed for record in records)
    suite = ET.Element(
        "testsuite",
        {
            "name": "source-adapter-fuzz",
            "tests": str(len(records)),
            "failures": str(failures),
            "errors": "0",
            "time": "{:.6f}".format(sum(record.duration_ms for record in records) / 1000.0),
        },
    )
    for record in records:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "source_adapter_fuzz.conformance",
                "name": record.name,
                "time": "{:.6f}".format(record.duration_ms / 1000.0),
            },
        )
        if not record.passed:
            failure = ET.SubElement(case, "failure", {"message": record.message, "type": "ConformanceFailure"})
            failure.text = json.dumps(record.details, sort_keys=True)
        output = ET.SubElement(case, "system-out")
        output.text = record.message
    return ET.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"


def render_sarif(records: Sequence[ConformanceRecord]) -> str:
    rules = []
    results = []
    for record in records:
        rule_id = "SAF-" + record.name.upper().replace("_", "-").replace(" ", "-")
        rules.append(
            {
                "id": rule_id,
                "name": record.name.replace("-", "_").replace(" ", "_"),
                "shortDescription": {"text": "Source acquisition conformance: " + record.name},
            }
        )
        results.append(
            {
                "ruleId": rule_id,
                "level": "note" if record.passed else "error",
                "message": {"text": record.message},
                "properties": {
                    "passed": record.passed,
                    "durationMs": record.duration_ms,
                    "details": dict(record.details),
                },
            }
        )
    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "source-adapter-fuzz",
                        "informationUri": "https://example.invalid/source-adapter-fuzz",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
