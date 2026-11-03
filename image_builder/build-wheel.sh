#!/usr/bin/env bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
python_exec=${SYNAPSE_BUILD_PYTHON:-python3}
"$python_exec" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Python 3.12 to build the Synapse wheel"'
# Do not run a Next.js development server during the build.
npm --prefix synapse_ui ci --no-audit --no-fund
(cd synapse_ui && NEXT_TELEMETRY_DISABLED=1 npm run build)
"$python_exec" -m build --wheel --outdir "$repo/dist/image-builder"
