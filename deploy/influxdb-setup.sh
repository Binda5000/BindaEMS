#!/usr/bin/env bash
# Richtet die InfluxDB-1.x-Datenbank für BindaEMS ein: Datenbank, Retention Policies und
# Continuous Queries. Das Skript ist idempotent und darf beliebig oft laufen.
#
# Aufruf:
#   INFLUX_URL=http://influx.lan:8086 INFLUX_ADMIN_USER=admin INFLUX_ADMIN_PASSWORD=… \
#     ./influxdb-setup.sh
set -euo pipefail

: "${INFLUX_URL:?INFLUX_URL fehlt, z. B. http://influx.lan:8086}"
: "${INFLUX_ADMIN_USER:?INFLUX_ADMIN_USER fehlt}"
: "${INFLUX_ADMIN_PASSWORD:?INFLUX_ADMIN_PASSWORD fehlt}"

DB="bindaems"

# Sendet eine InfluxQL-Anweisung und gibt die JSON-Antwort aus.
query() {
  curl --silent --show-error --fail-with-body \
    --user "${INFLUX_ADMIN_USER}:${INFLUX_ADMIN_PASSWORD}" \
    --data-urlencode "q=$1" \
    "${INFLUX_URL%/}/query"
}

# Führt eine Anweisung aus und bricht ab, wenn InfluxDB einen Fehler meldet.
run() {
  local response
  response="$(query "$1")"
  if [[ "$response" == *'"error"'* ]]; then
    echo "Fehler bei: $1" >&2
    echo "$response" >&2
    return 1
  fi
  echo "ok: $1"
}

# Legt eine Retention Policy an oder passt eine vorhandene an.
# $1 Name, $2 Dauer, $3 "DEFAULT" oder leer
retention_policy() {
  local settings="ON \"$DB\" DURATION $2 REPLICATION 1${3:+ $3}"
  local response
  response="$(query "CREATE RETENTION POLICY \"$1\" $settings")"
  if [[ "$response" == *'already exists'* ]]; then
    run "ALTER RETENTION POLICY \"$1\" $settings"
  elif [[ "$response" == *'"error"'* ]]; then
    echo "Fehler bei Retention Policy $1: $response" >&2
    return 1
  else
    echo "ok: CREATE RETENTION POLICY \"$1\" $settings"
  fi
}

# Ersetzt eine Continuous Query. $1 Name, $2 RESAMPLE-Klausel, $3 SELECT-Anweisung
continuous_query() {
  query "DROP CONTINUOUS QUERY \"$1\" ON \"$DB\"" >/dev/null || true
  run "CREATE CONTINUOUS QUERY \"$1\" ON \"$DB\" $2 BEGIN $3 END"
}

RAW="\"$DB\".\"raw\""
LONG="\"$DB\".\"long\""

run "CREATE DATABASE \"$DB\""
retention_policy raw 90d DEFAULT
retention_policy long INF ""

# RESAMPLE: verspätet geschriebene Punkte (Schreibtakt, Viertelstunden-Abschluss) werden
# beim nächsten Lauf noch erfasst.
continuous_query cq_power_1m "RESAMPLE FOR 2m" \
  "SELECT mean(p_w) AS p_w, mean(i_a) AS i_a, mean(u_v) AS u_v INTO $LONG.\"power\" FROM $RAW.\"power\" GROUP BY time(1m), *"
continuous_query cq_soc_1m "RESAMPLE FOR 2m" \
  "SELECT mean(pct) AS pct INTO $LONG.\"soc\" FROM $RAW.\"soc\" GROUP BY time(1m), *"
continuous_query cq_energy_1m "RESAMPLE FOR 2m" \
  "SELECT last(kwh) AS kwh INTO $LONG.\"energy\" FROM $RAW.\"energy\" GROUP BY time(1m), *"
continuous_query cq_flows_15m "RESAMPLE EVERY 15m FOR 30m" \
  "SELECT sum(wh) AS wh INTO $LONG.\"flows\" FROM $RAW.\"flows\" GROUP BY time(15m), *"

echo "InfluxDB-Einrichtung abgeschlossen."
