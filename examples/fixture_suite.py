"""Run every built-in localhost conformance scenario as JSON."""

from source_adapter_fuzz import render, run_scenarios


if __name__ == "__main__":
    print(render(run_scenarios(), "json"), end="")
