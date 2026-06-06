#!/bin/bash
echo "Building and pushing multi-platform image to Docker Hub..."
docker buildx create --use
docker buildx build --platform linux/amd64,linux/arm64 -t caddismaster/budget-buddy:latest --push .
echo "Done! Image pushed to caddismaster/budget-buddy:latest"