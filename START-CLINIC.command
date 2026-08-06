#!/bin/sh
# ====================================================================
#  Starts the clinic system. Double-click this file (macOS or Linux).
#
#  The .command extension is what makes Finder run it on a double-click
#  rather than opening it in a text editor.
#
#  Everything lives inside main(), and the only thing after it is the call.
#  That is not tidiness: this script updates itself with "git pull", and sh
#  reads a script incrementally as it runs. Rewriting the file underneath a
#  running shell makes it resume at the wrong byte offset and execute
#  nonsense. Wrapping the body in a function forces sh to read the whole
#  file before a single line of it runs, so by the time the pull happens
#  there is nothing left to re-read.
# ====================================================================

# Bring the checkout up to date, so that starting Docker and double-clicking
# this file is the whole job — no separate "git pull" to remember, and no
# chance of running last month's code against this month's database.
update_to_latest() {
  # Fail fast rather than hang. Nobody is watching this window at 8am, and a
  # git sitting at a password prompt or on a dead network never opens the
  # clinic at all.
  GIT_TERMINAL_PROMPT=0
  GIT_HTTP_LOW_SPEED_LIMIT=1000
  GIT_HTTP_LOW_SPEED_TIME=20
  export GIT_TERMINAL_PROMPT GIT_HTTP_LOW_SPEED_LIMIT GIT_HTTP_LOW_SPEED_TIME

  command -v git >/dev/null 2>&1 || return 0
  [ -e .git ] || return 0

  echo "  Checking for updates..."

  # --ff-only, never a merge. This computer only ever receives changes, so a
  # pull that cannot simply move forward is a situation for a human, not
  # something to resolve automatically at half past eight in the morning. It
  # refuses, says so, and the clinic still opens on the version already here.
  if ! git pull --ff-only; then
    echo ""
    echo "  Could not fetch updates - carrying on with the version already"
    echo "  on this computer. The clinic system will still work. If this"
    echo "  keeps happening, send this window to whoever supports it."
  fi
  echo ""
}

main() {
  cd "$(dirname "$0")" || exit 1

  echo ""
  echo "  ================================================"
  echo "     Centre for Endocrine & Metabolic Health"
  echo "     Starting the clinic system"
  echo "  ================================================"
  echo ""

  update_to_latest

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

  # --build matters after an update: a pull that changed the Dockerfile or the
  # requirements needs the image rebuilt, and without it the new code would run
  # against the old dependencies.
  if ! docker compose up -d --build; then
    echo ""
    echo "  Something went wrong starting up."
    echo "  Send this whole window to whoever supports the system."
    echo ""
    read -r _ 2>/dev/null
    exit 1
  fi

  echo ""
  # The container applies migrations when it starts, but the web server reloads
  # changed code without restarting the container. So after a git pull the new
  # code is live while the database is still the old shape, and pages die with
  # "column ... does not exist". Applying them here means pulling and
  # double-clicking is always enough. It does nothing when there is nothing to do.
  echo "  Checking the database is up to date..."
  tries=0
  until docker compose exec -T web python manage.py migrate --no-input >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 5 ]; then
      echo "  Could not update the database. The system may still work; if a page"
      echo "  shows an error about a missing column, send this window on."
      break
    fi
    sleep 3
  done

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

  # The address a phone on the same Wi-Fi should use. Printed rather than
  # left to be looked up, because it is not fixed: the router hands out a
  # new one every so often, and "it worked last week" is the commonest
  # reason the phone stops loading.
  #
  # Asked of whichever interface carries the default route, so Docker's own
  # virtual adapters — which no phone can reach — are never offered in its
  # place. Optional: if none of it works, the banner is simply shorter.
  lan_ip=""
  if command -v route >/dev/null 2>&1 && command -v ipconfig >/dev/null 2>&1; then
    iface=$(route -n get default 2>/dev/null | awk '/interface:/ {print $2}')
    [ -n "$iface" ] && lan_ip=$(ipconfig getifaddr "$iface" 2>/dev/null)
  fi
  if [ -z "$lan_ip" ]; then
    lan_ip=$(hostname -I 2>/dev/null | awk '{print $1}')      # Linux
  fi

  echo ""
  echo "  ================================================"
  echo "     The clinic system is running."
  echo ""
  echo "     Address:   http://localhost:8000"
  if [ -n "$lan_ip" ]; then
    echo ""
    echo "     From a phone on the same Wi-Fi:"
    echo "        http://${lan_ip}:8000"
  fi
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
}

main "$@"
