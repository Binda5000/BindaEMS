#!/usr/bin/env bash
# Installiert, aktualisiert oder entfernt den BindaEMS-Watchdog auf einem Victron Cerbo GX.
#
# Aufruf:
#   ./install.sh <ziel> <config.ini>     z. B. ./install.sh root@cerbo.lan config.ini
#   ./install.sh --uninstall <ziel>
#
# Auf dem Gerät ändert das Skript nur /data/bindaems-watchdog/, den Link
# /service/bindaems-watchdog und seinen Block in /data/rc.local. Ein erneuter Aufruf ersetzt
# Programm, config.ini und Block; state.json bleibt erhalten.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_DIR="/data/bindaems-watchdog"
SERVICE_LINK="/service/bindaems-watchdog"
RC_LOCAL="/data/rc.local"
VICTRON_DIR="/opt/victronenergy"
STAGE="" # lokales Verzeichnis für die Übertragung, am Ende entfernt

cleanup() {
  if [[ -n $STAGE ]]; then
    rm -rf -- "$STAGE"
  fi
}
trap cleanup EXIT

usage() {
  cat >&2 <<'EOF'
Aufruf: ./install.sh <ziel> <config.ini>
       ./install.sh --uninstall <ziel>

Installiert oder aktualisiert den BindaEMS-Watchdog per ssh auf dem Cerbo
(z. B. ./install.sh root@cerbo.lan config.ini) oder entfernt ihn wieder.
EOF
  exit 2
}

# Die Skripte für das Gerät sind POSIX-sh (Venus OS hat BusyBox). Sie bekommen die Pfade als
# Argumente: Programmverzeichnis, Dienst-Link, rc.local, Victron-Verzeichnis.
remote_common() {
  cat <<'EOF'
set -eu
# shellcheck disable=SC2034 # nicht jedes Skript braucht jeden Pfad
dir=$1 link=$2 rc=$3 victron=$4
begin='# bindaems-watchdog begin'
end='# bindaems-watchdog end'
tmp="$dir/rc.local.tmp"

fail() {
  echo "Fehler: $*" >&2
  exit 1
}

# Gibt rc.local ohne den Block aus. Mit einer Zeile in $1 steht an seiner Stelle der neue Block,
# sonst am Ende. Ein Block ohne Endmarke ergibt den Status 3: Dann bleibt rc.local, wie es ist.
edit_rc() {
  awk -v b="$begin" -v e="$end" -v line="$1" '
    !skip && $0 == b {
      skip = 1
      if (line != "" && !done) print b "\n" line "\n" e
      done = 1
      next
    }
    skip && $0 == e { skip = 0; next }
    !skip { print }
    END {
      if (skip) exit 3
      if (line != "" && !done) print b "\n" line "\n" e
    }
  ' "$rc"
}

# Bricht ab, weil der Block in rc.local keine Endmarke hat.
rc_broken() {
  fail "Der Block in $rc hat keine Endmarke; bitte von Hand bereinigen."
}

# Prüft rc.local vor jeder Änderung: Mit einem Block ohne Endmarke bricht das Skript ab, bevor es
# etwas anfasst.
check_rc() {
  if [ -f "$rc" ] && ! edit_rc "" >/dev/null; then
    rc_broken
  fi
}

# Schreibt rc.local aus $tmp neu (atomar, ausführbar).
replace_rc() {
  chmod 0755 "$tmp"
  mv -f "$tmp" "$rc"
}
EOF
}

# Prüft rc.local und das Gerät und legt das Programmverzeichnis an.
remote_check() {
  cat <<'EOF'
check_rc
command -v python3 >/dev/null 2>&1 || fail "python3 fehlt auf dem Gerät."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' ||
  fail "Der Watchdog braucht Python 3.8 oder neuer."
[ -d "$victron" ] || fail "$victron fehlt – das Ziel ist kein Gerät mit Venus OS."
[ -f "$victron/dbus-systemcalc-py/ext/velib_python/vedbus.py" ] ||
  fail "velib_python fehlt unter $victron/dbus-systemcalc-py/ext/velib_python."
python3 -c 'import configparser, dataclasses, json, contextlib, dbus; from gi.repository import GLib' ||
  fail "Python-Module fehlen (configparser, dataclasses, json, contextlib, dbus, GLib)."
mkdir -p "$dir"
echo "Gerät geeignet."
EOF
}

# Setzt Rechte, den Block in rc.local und den Link, startet den Dienst neu und zeigt seinen Stand.
remote_activate() {
  cat <<'EOF'
chmod 0644 "$dir/config.ini"
chmod 0755 "$dir/bindaems-watchdog.py" "$dir/service/run" "$dir/service/log/run"

line="ln -sfn $dir/service $link"
if [ -s "$rc" ]; then
  edit_rc "$line" >"$tmp" || {
    rm -f "$tmp"
    rc_broken
  }
else
  printf '#!/bin/sh\n%s\n%s\n%s\n' "$begin" "$line" "$end" >"$tmp"
fi
replace_rc
if grep -Eq '^[[:space:]]*exit([[:space:]]|$)' "$rc"; then
  echo "Achtung: $rc enthält eine exit-Zeile. Steht sie vor dem Block, startet der Watchdog nach einem Neustart nicht." >&2
fi

if [ -e "$link" ] || [ -L "$link" ]; then existed=1; else existed=0; fi
ln -sfn "$dir/service" "$link"
if [ "$existed" = 1 ]; then
  svc -t "$link"
fi

echo "Warte auf den Dienst …"
sleep 5
tries=0
until version=$(dbus -y com.victronenergy.bindaems /Version GetValue </dev/null 2>/dev/null); do
  tries=$((tries + 1))
  if [ "$tries" -ge 15 ]; then
    echo "Fehler: Der Watchdog meldet sich nicht auf D-Bus. Die letzten Zeilen des Logs:" >&2
    tail -n 20 /var/log/bindaems-watchdog/current >&2 || true
    exit 1
  fi
  sleep 1
done
state=$(dbus -y com.victronenergy.bindaems /State GetValue </dev/null)
echo "Version: $version"
echo "Zustand: $state (0 wartet, 1 scharf, 2 ausgelöst, 3 deaktiviert)"
EOF
}

# Hält den Dienst an, entfernt Link, Block und Programmverzeichnis.
remote_uninstall() {
  cat <<'EOF'
check_rc
if [ -e "$link" ] || [ -L "$link" ]; then
  svc -d "$link" || true
  rm -f "$link"
fi
if [ -d "$dir/service" ]; then
  # supervise und multilog beenden; ohne den Link startet svscan sie nicht neu.
  svc -dx "$dir/service" "$dir/service/log" >/dev/null 2>&1 || true
fi
if [ -f "$rc" ] && grep -qxF "$begin" "$rc"; then
  mkdir -p "$dir"
  edit_rc "" >"$tmp" || {
    rm -f "$tmp"
    rc_broken
  }
  replace_rc
fi
rm -rf "$dir"
echo "Watchdog entfernt."
EOF
}

# Führt die gemeinsamen Teile und das genannte Skript auf dem Gerät aus. $1 Ziel, $2 Skript.
on_device() {
  { remote_common; "$2"; } |
    ssh "$1" sh -s -- "$REMOTE_DIR" "$SERVICE_LINK" "$RC_LOCAL" "$VICTRON_DIR"
}

install_watchdog() {
  local target=$1 config=$2
  if [[ ! -f "$config" ]]; then
    echo "Fehler: $config nicht gefunden." >&2
    exit 2
  fi
  echo "Prüfe $target …"
  on_device "$target" remote_check
  echo "Übertrage die Dateien nach $target:$REMOTE_DIR …"
  STAGE="$(mktemp -d)"
  cp -- "$config" "$STAGE/config.ini"
  # tar über ssh statt scp: Moderne scp-Clients nutzen SFTP, das Dropbear auf Venus OS nicht immer
  # anbietet. tar behält die Ausführrechte; Bytecode bleibt draußen, und COPYFILE_DISABLE verhindert
  # die ._-Dateien von macOS. -o: Besitzer nicht vom Rechner übernehmen, die Dateien gehören root.
  COPYFILE_DISABLE=1 tar -c -f - --exclude=__pycache__ --exclude='*.pyc' \
    -C "$HERE" bindaems-watchdog.py bindaems_watchdog service -C "$STAGE" config.ini |
    ssh "$target" tar -C "$REMOTE_DIR" -x -o -f -
  echo "Richte den Dienst ein …"
  on_device "$target" remote_activate
}

uninstall_watchdog() {
  echo "Entferne den Watchdog von $1 …"
  on_device "$1" remote_uninstall
}

if [[ $# -eq 2 && $1 == "--uninstall" && -n $2 && $2 != -* ]]; then
  uninstall_watchdog "$2"
elif [[ $# -eq 2 && -n $1 && $1 != -* ]]; then
  install_watchdog "$1" "$2"
else
  usage
fi
