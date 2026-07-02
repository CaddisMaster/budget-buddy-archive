#!/bin/bash
# Build + push an IMMUTABLE version-tagged image. Does NOT touch :latest — that's
# the staging gate: deploy.sh publishes vX.Y.Z, you smoke-test that exact image
# (docker-compose.staging.yml), then ./promote.sh vX.Y.Z moves :latest onto it.
set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "Usage: ./deploy.sh vX.Y.Z" >&2
  echo "  Builds + pushes caddismaster/budget-buddy:<tag>. Does NOT update :latest." >&2
  echo "  After smoke-testing the image, run ./promote.sh <tag> to release it." >&2
  exit 1
fi
if [ "$TAG" = "latest" ]; then
  echo "Refusing to build :latest directly — deploy a version tag, then ./promote.sh it." >&2
  exit 1
fi

echo "Building and pushing caddismaster/budget-buddy:${TAG} (immutable — NOT :latest)..."
docker buildx inspect budget-buddy-builder > /dev/null 2>&1 || docker buildx create --name budget-buddy-builder
docker buildx use budget-buddy-builder
docker buildx build --platform linux/amd64,linux/arm64 -t "caddismaster/budget-buddy:${TAG}" --push .

echo "Done! Pushed caddismaster/budget-buddy:${TAG}."
echo
echo "Next — smoke-test the EXACT image before releasing:"
echo "  TAG=${TAG} docker compose -p bb-staging -f docker-compose.staging.yml up --pull always"
echo "  # open http://localhost:5002 , register a throwaway user, exercise the new feature"
echo "  TAG=${TAG} docker compose -p bb-staging -f docker-compose.staging.yml down -v   # tear down"
echo "Then release:"
echo "  ./promote.sh ${TAG}"
