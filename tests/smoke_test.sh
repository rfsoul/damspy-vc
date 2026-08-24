#!/usr/bin/env bash
set -euo pipefail

required_files=(
  "AGENTS.md"
  "README.md"
  "doc_map.md"
  "docs/agent_commands.md"
  "Makefile"
  "src/run_woym_monitor.sh"
)

for f in "${required_files[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "Missing required repository file: $f" >&2
    exit 1
  fi
done

bash -n src/run_woym_monitor.sh
python3 -c 'compile(open("src/serve_woym.py", encoding="utf-8").read(), "src/serve_woym.py", "exec")'
python3 - <<'PY'
import runpy

module = runpy.run_path("src/serve_woym.py", run_name="damspy_vc_smoke")
format_label = module["format_channel_or_frequency_label"]
assert format_label("40", 460_000_000) == "Ch 40"
assert format_label(None, 460_000_000) == "460 MHz"
assert format_label("", None) == "Ch ?"
PY

echo "Smoke checks passed."
