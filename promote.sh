#!/bin/bash
# Release gate: point :latest at an already-built, already-smoke-tested version tag.
# This RETAGS the existing manifest (docker buildx imagetools) — no rebuild, no new
# bytes, so prod runs the exact multi-arch image you tested. Run ONLY after deploy.sh
# published the tag and you smoke-tested it via docker-compose.staging.yml.
set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "Usage: ./promote.sh vX.Y.Z" >&2
  echo "  Retags caddismaster/budget-buddy:<tag> -> :latest (no rebuild)." >&2
  exit 1
fi

echo "Promoting caddismaster/budget-buddy:${TAG} -> :latest (retag, no rebuild)..."
docker buildx imagetools create -t caddismaster/budget-buddy:latest "caddismaster/budget-buddy:${TAG}"

echo "Done! :latest now points at ${TAG}."
echo "Deploy on the Droplet:  cd ~/budget-buddy && docker compose pull && docker compose up -d"
