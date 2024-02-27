#!/usr/bin/env sh

####################################################################
# docker-autoheal: https://github.com/willfarrell/docker-autoheal
####################################################################

set -e
# shellcheck disable=2039
set -o pipefail

DOCKER_SOCK=${DOCKER_SOCK:-/var/run/docker.sock}
UNIX_SOCK=""
CURL_TIMEOUT=${CURL_TIMEOUT:-30}
WEBHOOK_URL=${WEBHOOK_URL:-""}
WEBHOOK_JSON_KEY=${WEBHOOK_JSON_KEY:-"text"}
APPRISE_URL=${APPRISE_URL:-""}

# only use unix domain socket if no TCP endpoint is defined
case "${DOCKER_SOCK}" in
  "tcp://"*) HTTP_ENDPOINT="$(echo ${DOCKER_SOCK} | sed 's#tcp://#http://#')"
             ;;
  "tcps://"*) HTTP_ENDPOINT="$(echo ${DOCKER_SOCK} | sed 's#tcps://#https://#')"
             CA="--cacert /certs/ca.pem"
             CLIENT_KEY="--key /certs/client-key.pem"
             CLIENT_CERT="--cert /certs/client-cert.pem"
             ;;
  *)         HTTP_ENDPOINT="http://localhost"
             UNIX_SOCK="--unix-socket ${DOCKER_SOCK}"
             ;;
esac

# AUTOHEAL_CONTAINER_LABEL=${AUTOHEAL_CONTAINER_LABEL:-autoheal}
AUTOHEAL_CONTAINER_LABEL=${AUTOHEAL_CONTAINER_LABEL:-all}
AUTOHEAL_START_PERIOD=${AUTOHEAL_START_PERIOD:-0}
AUTOHEAL_INTERVAL=${AUTOHEAL_INTERVAL:-5}
AUTOHEAL_DEFAULT_STOP_TIMEOUT=${AUTOHEAL_DEFAULT_STOP_TIMEOUT:-10}

docker_curl() {
  curl --max-time "${CURL_TIMEOUT}" --no-buffer -s \
  ${CA} ${CLIENT_KEY} ${CLIENT_CERT} \
  ${UNIX_SOCK} \
  "$@"
}

# shellcheck disable=2039
get_container_info() {
  local label_filter
  local url

  # Set container selector
  if [ "$AUTOHEAL_CONTAINER_LABEL" = "all" ]
  then
    label_filter=""
  else
    label_filter=",\"label\":\[\"${AUTOHEAL_CONTAINER_LABEL}=true\"\]"
  fi
  url="${HTTP_ENDPOINT}/containers/json?filters=\{\"health\":\[\"unhealthy\"\]${label_filter}\}"
  docker_curl "$url"
}

# shellcheck disable=2039
restart_container() {
  local container_id="$1"
  local timeout="$2"

  docker_curl -f -X POST "${HTTP_ENDPOINT}/containers/${container_id}/restart?t=${timeout}"
}

notify_webhook() {
  local text="$@"

  if [ -n "$WEBHOOK_URL" ]
  then
    # execute webhook requests as background process to prevent healer from blocking
    curl -s -X POST -H "Content-type: application/json" -d "$(generate_webhook_payload $text)"  $WEBHOOK_URL
  fi

  if [ -n "$APPRISE_URL" ]
  then
    # execute webhook requests as background process to prevent healer from blocking
    curl -s -X POST -H "Content-type: application/json" -d "$(generate_apprise_payload $text)"  $APPRISE_URL
  fi
}

notify_post_restart_script() {
  if [ -n "$POST_RESTART_SCRIPT" ]
  then
    # execute post restart script as background process to prevent healer from blocking
    $POST_RESTART_SCRIPT "$@" &
  fi
}

# https://towardsdatascience.com/proper-ways-to-pass-environment-variables-in-json-for-curl-post-f797d2698bf3
generate_webhook_payload() {
  local text="$@"
  cat <<EOF
{
  "$WEBHOOK_JSON_KEY":"$text"
}
EOF
}

generate_apprise_payload() {
  local text="$@"
  cat <<EOF
{
  "title":"Autoheal",
  "body":"$text"
}
EOF
}

# SIGTERM-handler
term_handler() {
  exit 143  # 128 + 15 -- SIGTERM
}

# shellcheck disable=2039
trap 'kill $$; term_handler' SIGTERM

if [ "$1" = "autoheal" ]
then
  if [ -n "$UNIX_SOCK" ] && ! [ -S "$DOCKER_SOCK" ]
  then
    echo "unix socket is currently not available" >&2
    exit 1
  fi
  # Delayed startup
  if [ "$AUTOHEAL_START_PERIOD" -gt 0 ]
  then
  echo "Monitoring containers for unhealthy status in $AUTOHEAL_START_PERIOD second(s)"
    sleep "$AUTOHEAL_START_PERIOD" &
    wait $!
  fi

  while true
  do
    STOP_TIMEOUT=".Labels[\"autoheal.stop.timeout\"] // $AUTOHEAL_DEFAULT_STOP_TIMEOUT"
    get_container_info | \
      jq -r ".[] | select(.Labels[\"autoheal\"] != \"False\") | foreach . as \$CONTAINER([];[]; \$CONTAINER | .Id, .Names[0], .State, ${STOP_TIMEOUT})" | \
      while read -r CONTAINER_ID && read -r CONTAINER_NAME && read -r CONTAINER_STATE && read -r TIMEOUT
    do
      # shellcheck disable=2039
      CONTAINER_SHORT_ID=${CONTAINER_ID:0:12}
      DATE=$(date +%d-%m-%Y" "%H:%M:%S)

      if [ "$CONTAINER_NAME" = "null" ]
      then
        echo "$DATE Container name of (${CONTAINER_SHORT_ID}) is null, which implies container does not exist - don't restart" >&2
      elif [ "$CONTAINER_STATE" = "restarting" ]
      then
        echo "$DATE Container $CONTAINER_NAME (${CONTAINER_SHORT_ID}) found to be restarting - don't restart"
      else
        echo "$DATE Container $CONTAINER_NAME (${CONTAINER_SHORT_ID}) found to be unhealthy - Restarting container now with ${TIMEOUT}s timeout"
        if ! restart_container "$CONTAINER_ID" "$TIMEOUT"
        then
          echo "$DATE Restarting container $CONTAINER_SHORT_ID failed" >&2
          notify_webhook "Container ${CONTAINER_NAME:1} (${CONTAINER_SHORT_ID}) found to be unhealthy. Failed to restart the container!" &
        else
          notify_webhook "Container ${CONTAINER_NAME:1} (${CONTAINER_SHORT_ID}) found to be unhealthy. Successfully restarted the container!" &
        fi
        notify_post_restart_script "$CONTAINER_NAME" "$CONTAINER_SHORT_ID" "$CONTAINER_STATE" "$TIMEOUT" &
      fi
    done
    sleep "$AUTOHEAL_INTERVAL" &
    wait $!
  done

else
  exec "$@"
fi
