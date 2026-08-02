#!/bin/sh
# Shuts the clinic system down. Double-click this file.
# Patient records are kept — they live in a Docker volume, not in the
# containers this stops.
cd "$(dirname "$0")" || exit 1

echo ""
echo "  Shutting down the clinic system..."
echo ""

docker compose down

echo ""
echo "  Stopped. All patient records have been kept."
echo "  Double-click START-CLINIC.command when you need it again."
echo ""
read -r _ 2>/dev/null
