#!/bin/bash
# Fluent Bit of a SENSOR: the environment of fluent-bit.yaml, then Fluent Bit.

# MY_EXTIP, MY_INTIP, MY_HOSTNAME, MY_VERSION of tpotinit (updateip.sh); without the file the fields stay out
if [ -f /data/tpot/etc/compose/elk_environment ]; then
  set -o allexport
  # shellcheck disable=SC1091
  source /data/tpot/etc/compose/elk_environment
  set +o allexport
fi

# The start of this process for the heartbeat (a restart of the HIVE's view of the sensor)
TPOT_FLUENTBIT_START=$(date -u +%Y-%m-%dT%H:%M:%SZ) || {
  echo "Cannot determine the Fluent Bit start time." >&2
  exit 1
}

LS_SSL_VERIFICATION="${LS_SSL_VERIFICATION:-full}"
case "$LS_SSL_VERIFICATION" in
  full) FLB_TLS_VERIFY="on" ;;
  none) FLB_TLS_VERIFY="off" ;;
  *)
    echo "LS_SSL_VERIFICATION is '$LS_SSL_VERIFICATION'; it takes full (check the certificate of the HIVE) or none. Set it in ~/tpotce/.env." >&2
    exit 1
    ;;
esac

TPOT_HIVE_PORT="${TPOT_HIVE_PORT:-64294}"
FLB_STORAGE_LIMIT="${FLB_STORAGE_LIMIT:-512M}"

if [ -z "${TPOT_HIVE_IP:-}" ]; then
  echo "TPOT_HIVE_IP is empty; a SENSOR needs the address of its HIVE in ~/tpotce/.env (tpot sensors add on the HIVE sets it)." >&2
  exit 1
fi
if [ -z "${TPOT_HIVE_USER:-}" ]; then
  echo "TPOT_HIVE_USER is empty; a SENSOR needs its access to the HIVE in ~/tpotce/.env (tpot sensors add on the HIVE sets it)." >&2
  exit 1
fi

if [ -z "${MY_VERSION:-}" ]; then
  echo "Warning: sensor heartbeat version is unknown; update tpotinit to provide it in elk_environment." >&2
  MY_VERSION=""
fi

export TPOT_FLUENTBIT_START FLB_TLS_VERIFY TPOT_HIVE_IP TPOT_HIVE_PORT TPOT_HIVE_USER FLB_STORAGE_LIMIT MY_VERSION

# Task 14: the E2 guard (fuPROBE) goes here, before Fluent Bit starts

exec fluent-bit -c /etc/fluent-bit/fluent-bit.yaml
