#!/usr/bin/env bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
python_exec=${SYNAPSE_BUILD_PYTHON:-python3}
"$python_exec" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Python 3.12 to build the Synapse wheel"'
tls_dir=$(mktemp -d)
trap 'rm -rf -- "$tls_dir"' EXIT
"$python_exec" image_builder/payload/tls_policy.py bundle --output "$tls_dir/ca-bundle.pem"
mkdir "$tls_dir/empty"
export SSL_CERT_FILE="$tls_dir/ca-bundle.pem" SSL_CERT_DIR="$tls_dir/empty"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE" CURL_CA_BUNDLE="$SSL_CERT_FILE" PIP_CERT="$SSL_CERT_FILE" GIT_SSL_CAINFO="$SSL_CERT_FILE" GIT_SSL_CAPATH="$tls_dir/empty"
"$python_exec" image_builder/payload/tls_policy.py bootstrap --python "$python_exec"
"$python_exec" image_builder/payload/tls_policy.py audit
bun image_builder/check_runtime.ts
# Do not run a Next.js development server during the build.
(cd synapse_ui && bun install --frozen-lockfile)
(cd synapse_ui && NEXT_TELEMETRY_DISABLED=1 bun run build)
"$python_exec" -m build --wheel --outdir "$repo/dist/image-builder"
