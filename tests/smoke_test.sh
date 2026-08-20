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

echo "Smoke checks passed."
