#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

python3 -m unittest discover -s "$ROOT/tests" -p 'test_*.py' -v
python3 -m compileall -q "$ROOT/src" "$ROOT/tests" "$ROOT/examples"
