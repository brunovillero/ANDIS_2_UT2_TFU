#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Docker Desktop puede instalar el CLI en el directorio del usuario.
export PATH="$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
export BASE_URL="${BASE_URL:-http://localhost:${PORT:-8080}}"
docker compose up -d --build --wait
python3 demo_scaling.py
python3 tests/test_api.py
python3 demo_resilience.py
