#!/usr/bin/env bash
# Build the frontend and deploy the whole stack with CDK.
set -euo pipefail
cd "$(dirname "$0")"

(cd frontend && npm ci && npm run build)
(cd infra && { [ -d .venv ] || python3 -m venv .venv; } && .venv/bin/pip install -q -r requirements.txt && cdk deploy --require-approval broadening "$@")
