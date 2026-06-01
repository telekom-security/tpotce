#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="dicompot"
DEFAULT_IMAGE="dtagdevsec/dicompot:24.04.1"
IMAGE=""
HOST_PORT=""
LOG_DIR=""
IMAGE_DIR=""
GET_DIR=""
JSON_LOG_FILE=""
MAPPED_PORT=""
DCMTK_SAMPLE_FILE=""
STUDY_INSTANCE_UID=""
SERIES_INSTANCE_UID=""
SOP_INSTANCE_UID=""
STORE_SCU_BIN=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Dicompot image.

This test requires DCMTK client tools on the host: echoscu, getscu,
dcmdump, and either setscu or storescu.

Options:
  --image IMAGE       Image to test. Defaults to docker/dicompot/docker-compose.yml.
  --host-port PORT    Host TCP port for DICOM. Default: dynamic free port.
  --timeout SEC       Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP        Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts    Keep temporary compose file and logs for debugging.
  -h, --help          Show this help message.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image)
        [[ $# -ge 2 ]] || test_die "--image requires an argument"
        IMAGE="$2"
        shift 2
        ;;
      --image=*)
        IMAGE="${1#*=}"
        shift
        ;;
      --host-port|--port)
        [[ $# -ge 2 ]] || test_die "$1 requires an argument"
        HOST_PORT="$2"
        shift 2
        ;;
      --host-port=*|--port=*)
        HOST_PORT="${1#*=}"
        shift
        ;;
      --timeout)
        [[ $# -ge 2 ]] || test_die "--timeout requires an argument"
        TEST_TIMEOUT="$2"
        shift 2
        ;;
      --timeout=*)
        TEST_TIMEOUT="${1#*=}"
        shift
        ;;
      --bind-ip)
        [[ $# -ge 2 ]] || test_die "--bind-ip requires an argument"
        TEST_BIND_IP="$2"
        shift 2
        ;;
      --bind-ip=*)
        TEST_BIND_IP="${1#*=}"
        shift
        ;;
      --keep-artifacts)
        TEST_KEEP_ARTIFACTS="true"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        test_die "Unknown option: $1"
        ;;
    esac
  done
}

validate_args() {
  test_validate_timeout
  if [[ -n "${HOST_PORT}" ]]; then
    test_validate_port "${HOST_PORT}"
  fi
}

ensure_tcp_port_free_for_docker() {
  local port="$1"

  if [[ -z "${port}" ]]; then
    return 0
  fi

  if (( port < 1024 )); then
    test_info "Skipping user-space preflight for privileged TCP port ${port}; Docker will validate the binding."
  else
    test_ensure_port_free "${TEST_BIND_IP}" "${port}" || test_die "${TEST_BIND_IP}:${port} is already in use. Try --host-port <free-port>."
  fi
}

resolve_store_scu_command() {
  if command -v setscu >/dev/null 2>&1; then
    STORE_SCU_BIN="setscu"
  elif command -v storescu >/dev/null 2>&1; then
    STORE_SCU_BIN="storescu"
  else
    test_die "Required command not found: setscu or storescu"
  fi
}

extract_dicom_uid() {
  local dcm_file="$1"
  local tag_name="$2"

  dcmdump +P "${tag_name}" "${dcm_file}" 2>/dev/null \
    | sed -n 's/.*\[\([^]]*\)\].*/\1/p' \
    | head -n 1
}

dcmtk_timeout() {
  if (( TEST_TIMEOUT < 5 )); then
    printf '%s\n' "${TEST_TIMEOUT}"
  else
    printf '5\n'
  fi
}

prepare_dicompot_harness() {
  local image_source_dir="${DOCKER_ROOT}/${TEST_NAME}/dist/dcm_pts/images"
  local first_image=""

  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  IMAGE_DIR="${TEST_TMP_ROOT}/images"
  GET_DIR="${TEST_TMP_ROOT}/get"
  JSON_LOG_FILE="${LOG_DIR}/dicompot.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${IMAGE_DIR}" "${GET_DIR}"
  chmod 0777 "${LOG_DIR}" "${IMAGE_DIR}" "${GET_DIR}"

  [[ -d "${image_source_dir}" ]] || test_die "Dicompot sample image directory not found: ${image_source_dir}"
  first_image="$(find "${image_source_dir}" -type f -name '*.dcm' -print -quit)"
  [[ -n "${first_image}" ]] || test_die "No DICOM sample images found in ${image_source_dir}"
  cp -a "${image_source_dir}/." "${IMAGE_DIR}/"
  chmod -R a+rX "${IMAGE_DIR}"

  DCMTK_SAMPLE_FILE="$(find "${IMAGE_DIR}" -type f -name '*.dcm' | sort | head -n 1)"
  [[ -n "${DCMTK_SAMPLE_FILE}" ]] || test_die "No copied DICOM sample image found in ${IMAGE_DIR}"
  STUDY_INSTANCE_UID="$(extract_dicom_uid "${DCMTK_SAMPLE_FILE}" "StudyInstanceUID")"
  [[ -n "${STUDY_INSTANCE_UID}" ]] || test_die "Could not read StudyInstanceUID from ${DCMTK_SAMPLE_FILE}"
  SERIES_INSTANCE_UID="$(extract_dicom_uid "${DCMTK_SAMPLE_FILE}" "SeriesInstanceUID")"
  [[ -n "${SERIES_INSTANCE_UID}" ]] || test_die "Could not read SeriesInstanceUID from ${DCMTK_SAMPLE_FILE}"
  SOP_INSTANCE_UID="$(extract_dicom_uid "${DCMTK_SAMPLE_FILE}" "SOPInstanceUID")"
  [[ -n "${SOP_INSTANCE_UID}" ]] || test_die "Could not read SOPInstanceUID from ${DCMTK_SAMPLE_FILE}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  dicompot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${TEST_BIND_IP}:${HOST_PORT}:11112"
    volumes:
      - "${LOG_DIR}:/var/log/dicompot"
      - "${IMAGE_DIR}:/opt/dicompot/images"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_dicom_cecho_probe() {
  local calling_ae="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_PORT}" "${calling_ae}" "${TEST_TIMEOUT}" <<'PY'
import socket
import struct
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
calling_ae = sys.argv[3]
timeout = int(sys.argv[4])

APPLICATION_CONTEXT_UID = "1.2.840.10008.3.1.1.1"
VERIFICATION_SOP_CLASS_UID = "1.2.840.10008.1.1"
IMPLICIT_VR_LITTLE_ENDIAN = "1.2.840.10008.1.2"
EXPLICIT_VR_LITTLE_ENDIAN = "1.2.840.10008.1.2.1"
IMPLEMENTATION_CLASS_UID = "1.2.826.0.1.3680043.10.543.1"
IMPLEMENTATION_VERSION_NAME = "TPOTSMOKE"


class ProbeError(Exception):
    pass


def recv_exact(sock, size, deadline):
    chunks = []
    remaining = size
    while remaining > 0:
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            raise ProbeError(f"Timed out while waiting for {size} bytes")
        sock.settimeout(min(remaining_time, 1.0))
        try:
            chunk = sock.recv(remaining)
        except socket.timeout:
            continue
        if not chunk:
            raise ProbeError("Connection closed by peer")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_pdu(sock, deadline):
    header = recv_exact(sock, 6, deadline)
    pdu_type = header[0]
    pdu_length = struct.unpack(">I", header[2:6])[0]
    if pdu_length > 16 * 1024 * 1024:
        raise ProbeError(f"Refusing oversized DICOM PDU: {pdu_length} bytes")
    return pdu_type, recv_exact(sock, pdu_length, deadline)


def pdu(pdu_type, payload):
    return bytes([pdu_type, 0]) + struct.pack(">I", len(payload)) + payload


def pdu_item(item_type, payload):
    return bytes([item_type, 0]) + struct.pack(">H", len(payload)) + payload


def uid_item(item_type, uid):
    return pdu_item(item_type, uid.encode("ascii"))


def ae_title(value):
    raw = value.encode("ascii")
    if not raw:
        raise ProbeError("AE title must not be empty")
    if len(raw) > 16:
        raise ProbeError(f"AE title is longer than 16 bytes: {value}")
    return raw.ljust(16, b" ")


def build_associate_rq():
    presentation_context = (
        b"\x01\x00\x00\x00"
        + uid_item(0x30, VERIFICATION_SOP_CLASS_UID)
        + uid_item(0x40, IMPLICIT_VR_LITTLE_ENDIAN)
        + uid_item(0x40, EXPLICIT_VR_LITTLE_ENDIAN)
    )
    user_information = (
        pdu_item(0x51, struct.pack(">I", 16384))
        + uid_item(0x52, IMPLEMENTATION_CLASS_UID)
        + pdu_item(0x55, IMPLEMENTATION_VERSION_NAME.encode("ascii"))
    )
    payload = (
        struct.pack(">H", 1)
        + b"\x00\x00"
        + ae_title("DICOMPOT")
        + ae_title(calling_ae)
        + (b"\x00" * 32)
        + uid_item(0x10, APPLICATION_CONTEXT_UID)
        + pdu_item(0x20, presentation_context)
        + pdu_item(0x50, user_information)
    )
    return pdu(0x01, payload)


def parse_associate_accept(payload):
    if len(payload) < 68:
        raise ProbeError(f"A-ASSOCIATE-AC payload is too short: {len(payload)} bytes")

    accepted_contexts = []
    rejected_contexts = []
    offset = 68
    while offset + 4 <= len(payload):
        item_type = payload[offset]
        item_length = struct.unpack(">H", payload[offset + 2:offset + 4])[0]
        offset += 4
        body = payload[offset:offset + item_length]
        offset += item_length

        if item_type == 0x21:
            if len(body) < 4:
                raise ProbeError("Presentation Context AC item is too short")
            context_id = body[0]
            result_reason = body[2]
            if result_reason == 0:
                accepted_contexts.append(context_id)
            else:
                rejected_contexts.append(f"id={context_id} result={result_reason}")

    if not accepted_contexts:
        details = f" ({', '.join(rejected_contexts)})" if rejected_contexts else ""
        raise ProbeError(f"No accepted DICOM presentation context{details}")

    return accepted_contexts[0]


def encode_ui_value(uid):
    value = uid.encode("ascii")
    if len(value) % 2:
        value += b"\x00"
    return value


def command_element(group, element, value):
    return struct.pack("<HHI", group, element, len(value)) + value


def command_us(element, value):
    return command_element(0x0000, element, struct.pack("<H", value))


def command_ul(element, value):
    return command_element(0x0000, element, struct.pack("<I", value))


def build_cecho_rq():
    command_without_group_length = b"".join([
        command_element(0x0000, 0x0002, encode_ui_value(VERIFICATION_SOP_CLASS_UID)),
        command_us(0x0100, 0x0030),
        command_us(0x0110, 1),
        command_us(0x0800, 0x0101),
    ])
    return command_ul(0x0000, len(command_without_group_length)) + command_without_group_length


def build_p_data_tf(presentation_context_id, command_set):
    pdv = bytes([presentation_context_id, 0x03]) + command_set
    return pdu(0x04, struct.pack(">I", len(pdv)) + pdv)


def parse_command_set(data):
    elements = {}
    offset = 0
    while offset + 8 <= len(data):
        group, element, length = struct.unpack("<HHI", data[offset:offset + 8])
        offset += 8
        value = data[offset:offset + length]
        offset += length
        elements[(group, element)] = value

    def get_us(tag):
        value = elements.get(tag)
        if value is None or len(value) < 2:
            return None
        return struct.unpack("<H", value[:2])[0]

    return {
        "command_field": get_us((0x0000, 0x0100)),
        "message_id_responded_to": get_us((0x0000, 0x0120)),
        "status": get_us((0x0000, 0x0900)),
    }


def associate_rejection(payload):
    if len(payload) >= 4:
        return f"result={payload[1]} source={payload[2]} reason={payload[3]}"
    return f"payload={payload.hex()}"


deadline = time.monotonic() + timeout
command_fragments = []

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(build_associate_rq())
        pdu_type, payload = read_pdu(sock, deadline)
        if pdu_type == 0x03:
            raise ProbeError(f"DICOM association rejected: {associate_rejection(payload)}")
        if pdu_type != 0x02:
            raise ProbeError(f"Expected A-ASSOCIATE-AC PDU, got type 0x{pdu_type:02x}")

        presentation_context_id = parse_associate_accept(payload)
        sock.sendall(build_p_data_tf(presentation_context_id, build_cecho_rq()))

        while time.monotonic() < deadline:
            pdu_type, payload = read_pdu(sock, deadline)
            if pdu_type == 0x07:
                raise ProbeError("DICOM association aborted before C-ECHO response")
            if pdu_type != 0x04:
                continue

            offset = 0
            while offset + 4 <= len(payload):
                item_length = struct.unpack(">I", payload[offset:offset + 4])[0]
                offset += 4
                item = payload[offset:offset + item_length]
                offset += item_length
                if len(item) < 2:
                    raise ProbeError("P-DATA-TF item is too short")

                control_header = item[1]
                fragment = item[2:]
                if control_header & 0x02:
                    command_fragments.append(fragment)
                    if control_header & 0x01:
                        command = parse_command_set(b"".join(command_fragments))
                        if command["command_field"] != 0x8030:
                            raise ProbeError(
                                f"Expected C-ECHO-RSP command 0x8030, got {command['command_field']!r}"
                            )
                        if command["message_id_responded_to"] != 1:
                            raise ProbeError(
                                "Expected C-ECHO-RSP for message id 1, "
                                f"got {command['message_id_responded_to']!r}"
                            )
                        if command["status"] != 0:
                            raise ProbeError(f"Expected C-ECHO success status 0x0000, got {command['status']!r}")

                        sock.sendall(pdu(0x05, b"\x00\x00\x00\x00"))
                        try:
                            sock.settimeout(1)
                            release_type, _ = read_pdu(sock, time.monotonic() + 1)
                            if release_type != 0x06:
                                print(f"DICOM C-ECHO succeeded; release response was PDU 0x{release_type:02x}")
                            else:
                                print("DICOM C-ECHO succeeded; association released cleanly")
                        except Exception:
                            print("DICOM C-ECHO succeeded; release response was not received before close")
                        sys.exit(0)

    raise ProbeError("No C-ECHO response received")
except (OSError, ProbeError) as exc:
    print(f"DICOM C-ECHO probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_dicom_cecho_probe_with_retries() {
  local calling_ae="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_dicom_cecho_probe "${calling_ae}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

run_echoscu_probe() {
  local calling_ae="$1"
  local timeout

  timeout="$(dcmtk_timeout)"
  echoscu -v -aec DICOMPOT -aet "${calling_ae}" -to "${timeout}" -ts "${timeout}" -ta "${timeout}" -td "${timeout}" "${TEST_BIND_IP}" "${MAPPED_PORT}"
}

run_getscu_probe() {
  local calling_ae="$1"
  local timeout
  local received_count

  timeout="$(dcmtk_timeout)"
  getscu -v -S -aec DICOMPOT -aet "${calling_ae}" -to "${timeout}" -ta "${timeout}" -td "${timeout}" \
    -od "${GET_DIR}" \
    -k QueryRetrieveLevel=IMAGE \
    -k StudyInstanceUID="${STUDY_INSTANCE_UID}" \
    -k SeriesInstanceUID="${SERIES_INSTANCE_UID}" \
    -k SOPInstanceUID="${SOP_INSTANCE_UID}" \
    "${TEST_BIND_IP}" "${MAPPED_PORT}"

  received_count="$(find "${GET_DIR}" -type f | wc -l)"
  if (( received_count < 1 )); then
    printf 'getscu completed without storing retrieved DICOM files in %s\n' "${GET_DIR}" >&2
    return 1
  fi

  printf 'getscu retrieved %s DICOM file(s) into %s\n' "${received_count}" "${GET_DIR}"
}

run_store_scu_probe() {
  local calling_ae="$1"
  local timeout
  local output=""
  local status=0

  timeout="$(dcmtk_timeout)"
  output="$("${STORE_SCU_BIN}" -v -aec DICOMPOT -aet "${calling_ae}" -to "${timeout}" -ts "${timeout}" -ta "${timeout}" -td "${timeout}" \
    "${TEST_BIND_IP}" "${MAPPED_PORT}" "${DCMTK_SAMPLE_FILE}" 2>&1)" || status=$?

  printf '%s\n' "${output}"
  if printf '%s\n' "${output}" | grep -F "Received Store Response" >/dev/null 2>&1; then
    return 0
  fi

  return "${status}"
}

run_dcmtk_probe_with_retries() {
  local description="$1"
  shift
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$("$@" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  printf '%s probe did not succeed before timeout\n' "${description}" >&2
  return 1
}

wait_for_dicompot_log_event() {
  local calling_ae="$1"
  local operation="$2"

  python3 - "${JSON_LOG_FILE}" "${calling_ae}" "${operation}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

log_file = Path(sys.argv[1])
calling_ae = sys.argv[2].lower()
operation = sys.argv[3].lower()
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
operation_markers = {
    "echo": ("c-echo", "cecho", "echo", "verification"),
    "get": ("c-get", "cget", "get", "retrieve"),
    "store": ("c-store", "cstore", "store"),
}
markers = operation_markers.get(operation, (operation,)) + (calling_ae,)
last_error = None


def contains_marker(value):
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in markers)
    if isinstance(value, dict):
        return any(contains_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_marker(item) for item in value)
    return False


while time.monotonic() < deadline:
    if not log_file.exists():
        last_error = f"{log_file} does not exist yet"
        time.sleep(1)
        continue

    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        last_error = f"Could not read {log_file}: {exc}"
        time.sleep(1)
        continue

    if not lines:
        last_error = f"{log_file} is empty"
        time.sleep(1)
        continue

    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            if contains_marker(stripped):
                print(f"Dicompot log text found in {log_file}:{line_number}")
                sys.exit(0)
            last_error = f"{log_file}:{line_number}: line is not JSON and does not contain C-ECHO markers"
            continue

        if isinstance(event, dict) and contains_marker(event):
            print(f"Dicompot JSON event found in {log_file}:{line_number}")
            sys.exit(0)

        last_error = f"No C-ECHO marker found in {log_file}:{line_number}"

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
print(f"No Dicompot {operation} event found in {log_file}", file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  if grep -R -E "panic:|fatal error|Traceback|NameError|Exception|permission denied|level\":\"fatal|level\":\"panic" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Dicompot runtime error found in dicompot.log"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "panic:|fatal error|Traceback|NameError|Exception|permission denied|level\":\"fatal|level\":\"panic" >/dev/null 2>&1; then
    test_die "Dicompot runtime error found in Docker logs"
  fi
}

main() {
  parse_args "$@"
  validate_args
  test_check_dependencies
  test_require_command echoscu
  test_require_command getscu
  test_require_command dcmdump
  resolve_store_scu_command

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_info "Using store SCU command: ${STORE_SCU_BIN}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"

  ensure_tcp_port_free_for_docker "${HOST_PORT}"

  prepare_dicompot_harness
  test_enable_cleanup

  test_info "Starting isolated Dicompot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Dicompot container did not stay running"
  test_ok "Container is running"

  MAPPED_PORT="$(test_get_mapped_port "${TEST_NAME}" "11112")" || test_die "Could not resolve mapped host port for 11112/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_PORT} maps to container port 11112/tcp"

  local echo_ae="TPOTECHO$(( $$ % 100000000 ))"
  local get_ae="TPOTGET$(( $$ % 1000000000 ))"
  local store_ae="TPOTSTORE$(( $$ % 1000000 ))"
  echo_ae="${echo_ae:0:16}"
  get_ae="${get_ae:0:16}"
  store_ae="${store_ae:0:16}"

  test_info "Running echoscu C-ECHO probe with calling AE: ${echo_ae}"
  run_dcmtk_probe_with_retries "echoscu" run_echoscu_probe "${echo_ae}" || test_die "echoscu C-ECHO probe failed on ${TEST_BIND_IP}:${MAPPED_PORT}"
  test_wait_for_container || test_die "Dicompot container stopped after echoscu probe"
  test_ok "echoscu C-ECHO probe succeeded"

  test_info "Waiting for echoscu event in dicompot.log"
  wait_for_dicompot_log_event "${echo_ae}" "echo" || test_die "echoscu event was not found in dicompot.log"
  test_ok "echoscu event was written to dicompot.log"

  test_info "Running getscu C-GET probe for SOPInstanceUID: ${SOP_INSTANCE_UID}"
  run_dcmtk_probe_with_retries "getscu" run_getscu_probe "${get_ae}" || test_die "getscu C-GET probe failed on ${TEST_BIND_IP}:${MAPPED_PORT}"
  test_wait_for_container || test_die "Dicompot container stopped after getscu probe"
  test_ok "getscu C-GET probe succeeded"

  test_info "Waiting for getscu event in dicompot.log"
  wait_for_dicompot_log_event "${get_ae}" "get" || test_die "getscu event was not found in dicompot.log"
  test_ok "getscu event was written to dicompot.log"

  test_info "Running ${STORE_SCU_BIN} C-STORE probe with sample file: ${DCMTK_SAMPLE_FILE}"
  run_dcmtk_probe_with_retries "${STORE_SCU_BIN}" run_store_scu_probe "${store_ae}" || test_die "${STORE_SCU_BIN} C-STORE probe failed on ${TEST_BIND_IP}:${MAPPED_PORT}"
  test_wait_for_container || test_die "Dicompot container stopped after ${STORE_SCU_BIN} probe"
  test_ok "${STORE_SCU_BIN} C-STORE probe succeeded"

  test_info "Waiting for ${STORE_SCU_BIN} event in dicompot.log"
  wait_for_dicompot_log_event "${store_ae}" "store" || test_die "${STORE_SCU_BIN} event was not found in dicompot.log"
  test_ok "${STORE_SCU_BIN} event was written to dicompot.log"

  assert_no_runtime_errors
  test_ok "No Dicompot runtime errors found in logs"

  test_ok "Dicompot post-build smoke test completed successfully"
}

main "$@"
