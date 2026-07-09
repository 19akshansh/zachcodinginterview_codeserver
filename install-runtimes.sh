#!/usr/bin/env bash
# Run this once after `docker-compose up -d` has the api container healthy.
# Installs only Python and Node so you don't waste disk/RAM on unused languages.

set -e

PISTON_CLI_IMAGE="ghcr.io/engineer-man/piston"
API_URL="http://localhost:2000"

echo "Waiting for Piston API to be reachable at $API_URL ..."
until curl -sf "$API_URL/api/v2/runtimes" > /dev/null; do
  sleep 2
done

echo "Installing Python 3..."
curl -s -X POST "$API_URL/api/v2/packages" \
  -H "Content-Type: application/json" \
  -d '{"language": "python", "version": "3.12.0"}'

echo "Installing Node.js..."
curl -s -X POST "$API_URL/api/v2/packages" \
  -H "Content-Type: application/json" \
  -d '{"language": "node", "version": "20.11.1"}'

echo ""
echo "Done. Verify with:"
echo "  curl $API_URL/api/v2/runtimes"
