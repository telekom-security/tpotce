#!/usr/bin/env bash
set -Eeuo pipefail
unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="sensor_dashboard"
IMAGE=""
# No local installation settings belong in the disposable cluster.
export COMPOSE_ENV_FILES=/dev/null

usage() {
  cat <<EOF
Usage: $0 [--image KIBANA_IMAGE] [--timeout SEC] [--bind-ip IP] [--keep-artifacts]

Verify the T-Pot Sensors dashboard's runtime fields, version/start aggregations,
saved-object import and live data views against Elasticsearch and Kibana.
Uses local official images at the Dockerfile versions; never builds or pulls.
No ports are published. --bind-ip is accepted for runner compatibility.
All data and containers are temporary. Allow at least 240 seconds for startup.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
    --timeout) TEST_TIMEOUT="${2:?--timeout needs a value}"; shift 2 ;;
    --bind-ip) TEST_BIND_IP="${2:?--bind-ip needs a value}"; shift 2 ;;
    --keep-artifacts) TEST_KEEP_ARTIFACTS=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) test_die "Unknown option: $1" ;;
  esac
done

test_check_dependencies
test_validate_timeout
ES_VERSION="$(sed -n 's/^ARG ES_VER=//p' "${DOCKER_ROOT}/elk/elasticsearch/Dockerfile" | head -n 1)"
KB_VERSION="$(sed -n 's/^ARG KB_VER=//p' "${DOCKER_ROOT}/elk/kibana/Dockerfile" | head -n 1)"
ES_IMAGE="docker.elastic.co/elasticsearch/elasticsearch:${ES_VERSION}"
IMAGE="${IMAGE:-docker.elastic.co/kibana/kibana:${KB_VERSION}}"
test_require_image "${ES_IMAGE}" "docker pull ${ES_IMAGE}"
test_require_image "${IMAGE}" "docker pull ${IMAGE}"
test_prepare_harness "${TEST_NAME}"
test_enable_cleanup

cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  elasticsearch:
    image: "${ES_IMAGE}"
    environment:
      discovery.type: single-node
      cluster.name: tpot-dashboard-test
      xpack.security.enabled: "false"
      ingest.geoip.downloader.enabled: "false"
      node.store.allow_mmap: "false"
      ES_JAVA_OPTS: "-Xms512m -Xmx512m"
  kibana:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    environment:
      ELASTICSEARCH_HOSTS: '["http://elasticsearch:9200"]'
      SERVER_BASEPATH: ""
      XPACK_SECURITY_ENABLED: "false"
      TELEMETRY_ENABLED: "false"
      NEWSFEED_ENABLED: "false"
      XPACK_FLEET_ISAIRGAPPED: "true"
networks:
  default:
    internal: true
EOF

test_info "Starting isolated Elasticsearch and Kibana"
test_compose up -d >/dev/null
(( TEST_TIMEOUT >= 240 )) || TEST_TIMEOUT=240
python3 "${SCRIPT_DIR}/../lib/sensor_dashboard.py" \
  "${TEST_HARNESS_COMPOSE}" "${TEST_PROJECT_NAME}" "${TEST_TIMEOUT}"
test_ok "Sensor dashboard runtime fields and saved objects passed"
