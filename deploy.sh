#!/bin/bash
echo "Building and pushing image to Docker Hub..."
docker build -t caddismaster/budget-buddy:latest .
docker push caddismaster/budget-buddy:latest
echo "Done! Image pushed to caddismaster/budget-buddy:latest"