#!/bin/sh
# Everything that has to happen before the clinic system can be used.
#
# Run on every container start, so nobody has to type a command. Each step is
# safe to repeat: migrate does nothing when there is nothing to apply, and
# bootstrap refuses to touch a database that already has patients in it.
set -e

echo ""
echo "  Starting the clinic system..."
echo ""

echo "  [1/3] Preparing the database..."
python manage.py migrate --no-input

echo "  [2/3] Checking the administrator account..."
python manage.py bootstrap

echo "  [3/3] Ready."
echo ""
echo "  ============================================================"
echo "     Open this address in your browser:"
echo ""
echo "         http://localhost:8000"
echo ""
echo "     Sign in with     vrushali     clinicdemo2026"
echo "  ============================================================"
echo ""

exec "$@"
