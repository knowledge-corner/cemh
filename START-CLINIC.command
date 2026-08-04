#!/bin/sh
# ====================================================================
#  Starts the clinic system. Double-click this file (macOS or Linux).
#
#  The .command extension is what makes Finder run it on a double-click
#  rather than opening it in a text editor.
# ====================================================================
cd "$(dirname "$0")" || exit 1

echo ""
echo "  ================================================"
echo "     Centre for Endocrine & Metabolic Health"
echo "     Starting the clinic system"
echo "  ================================================"
echo ""

if ! command -v docker >/dev/null 2>&1; then
  echo "  Docker is not installed on this computer."
  echo ""
  echo "  Install Docker Desktop from:"
  echo "      https://www.docker.com/products/docker-desktop/"
  echo ""
  echo "  Then double-click this file again."
  echo ""
  read -r _ 2>/dev/null
  exit 1
fi

# Straight after the Mac is switched on, Docker Desktop needs half a minute or
# so before it will answer. Giving up immediately made this file useless at
# login, which is exactly when it is most wanted — so nudge Docker awake, wait,
# and only complain if it really is not coming.
if ! docker info >/dev/null 2>&1; then
  open -a Docker >/dev/null 2>&1 || true
  echo "  Waiting for Docker Desktop to be ready..."
  tries=0
  until docker info >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 40 ]; then
      echo ""
      echo "  Docker Desktop did not become ready after two minutes."
      echo ""
      echo "  1. Open Docker Desktop from Applications"
      echo "  2. Wait for the whale icon in the menu bar to settle"
      echo "  3. Double-click this file again"
      echo ""
      read -r _ 2>/dev/null
      exit 1
    fi
    sleep 3
  done
fi

echo "  Starting... the first time takes a few minutes."
echo "  Later starts take about ten seconds."
echo ""

if ! docker compose up -d --build; then
  echo ""
  echo "  Something went wrong starting up."
  echo "  Send this whole window to whoever supports the system."
  echo ""
  read -r _ 2>/dev/null
  exit 1
fi

echo ""
echo "  Waiting for the clinic system to be ready..."
tries=0
while [ "$tries" -lt 60 ]; do
  if curl -fs -o /dev/null http://localhost:8000/ 2>/dev/null; then
    echo "  Ready."
    break
  fi
  tries=$((tries + 1))
  sleep 2
done

# open on macOS, xdg-open on Linux — whichever exists.
(open http://localhost:8000/ 2>/dev/null || xdg-open http://localhost:8000/ 2>/dev/null) &

echo ""
echo "  ================================================"
echo "     The clinic system is running."
echo ""
echo "     Address:   http://localhost:8000"
echo ""
echo "     Reception: reception / clinicdemo2026"
echo "     Doctor:    vrushali / clinicdemo2026"
echo ""
echo "     Leave it running. To shut it down, double-click"
echo "     STOP-CLINIC.command"
echo "  ================================================"
echo ""
echo "  You can close this window."
echo ""
read -r _ 2>/dev/null
