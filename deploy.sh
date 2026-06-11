#!/bin/bash
echo "Building and pushing multi-platform image to Docker Hub..."
docker buildx inspect budget-buddy-builder > /dev/null 2>&1 || docker buildx create --name budget-buddy-builder
docker buildx use budget-buddy-builder
docker buildx build --platform linux/amd64,linux/arm64 -t caddismaster/budget-buddy:latest --push .
echo "Done! Image pushed to caddismaster/budget-buddy:latest"