#!/bin/bash
# The whole production update, in one command: ./update.sh
#
# Pulls the latest code, rebuilds (production copies code into the image at
# build time rather than reading it live, so a plain restart would not pick up
# anything new), and applies any new migrations. Run this from /opt/cemh on the
# droplet after every "merged to main" — nothing else needs typing by hand.
set -e

echo ""
echo "  Updating the clinic system..."
echo ""

echo "  [1/3] Pulling the latest code..."
git pull origin main

echo "  [2/3] Rebuilding and restarting..."
docker compose -f docker-compose.prod.yml up -d --build

echo "  [3/3] Applying any new database migrations..."
docker compose -f docker-compose.prod.yml exec -T web python manage.py migrate

echo ""
echo "  ============================================================"
echo "     Done. https://cemhcare.com should be running the update."
echo "  ============================================================"
echo ""
