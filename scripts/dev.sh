#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:?port required}"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
