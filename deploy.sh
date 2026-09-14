#!/bin/sh
# Deploy portalu sm-portal na ZimaOS (docker:cli z sockelim — ZimaOS nie ma docker compose)
set -e
cd /DATA/AppData/sm-portal
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /DATA/AppData/sm-portal:/DATA/AppData/sm-portal \
  -w /DATA/AppData/sm-portal \
  docker:cli docker compose up -d --build
echo "DEPLOY-DONE $(date)"