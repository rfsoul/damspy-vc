#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
script_path="${repo_root}/src/serve_woym.py"
logs_root="${repo_root}/../DAMspy-core/src/DAMspy_logs"

if [[ ! -d "${logs_root}" ]]; then
  echo "Could not find the DAMSpy logs directory:" >&2
  echo "  ${logs_root}" >&2
  echo "Place DAMspy-core beside damspy-vc, or update this launcher for its location." >&2
  exit 1
fi

for python_command in python3 python3.11; do
  if ! command -v "${python_command}" >/dev/null 2>&1; then
    continue
  fi

  if ! "${python_command}" -c "import PIL" >/dev/null 2>&1; then
    echo "Skipping ${python_command} because Pillow is not available in that interpreter." >&2
    continue
  fi

  echo "Starting WOYM monitor with ${python_command}..."
  cd -- "${repo_root}"
  exec "${python_command}" "${script_path}" "$@"
done

echo "Could not start the WOYM monitor with Python 3 and Pillow installed." >&2
echo "Install Pillow for Python 3, or make a compatible Python interpreter available on PATH." >&2
exit 1
