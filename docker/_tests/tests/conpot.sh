#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="conpot"
DEFAULT_IMAGE="dtagdevsec/conpot:24.04.2"
IMAGE=""
LOG_DIR=""
IDENTITY_DIR=""
RENDERED_DIR=""
PROBES=""
CPU_LIMIT="20"

# Deployed T-Pot templates, one container each.
TEMPLATES=(IEC104 guardian_ast ipmi kamstrup_382 beckhoff_cx building_automation hart_gateway iccp plc_modbus)

# Port key -> template, container port/protocol. Host ports are dynamic loopback
# ports unless set with --<key>-port.
PORT_KEYS=(iec104 dnp3 snmp guardian-ast ipmi kamstrup kamstrup-management ads ads-discovery opcua knx bacnet hartip iccp modbus)
declare -A PORT_TEMPLATE=(
  [iec104]=IEC104 [dnp3]=IEC104 [snmp]=IEC104
  [guardian-ast]=guardian_ast
  [ipmi]=ipmi
  [kamstrup]=kamstrup_382 [kamstrup-management]=kamstrup_382
  [ads]=beckhoff_cx [ads-discovery]=beckhoff_cx [opcua]=beckhoff_cx
  [knx]=building_automation [bacnet]=building_automation
  [hartip]=hart_gateway
  [iccp]=iccp
  [modbus]=plc_modbus
)
declare -A PORT_CONTAINER=(
  [iec104]=2404/tcp [dnp3]=20000/tcp [snmp]=161/udp
  [guardian-ast]=10001/tcp
  [ipmi]=623/udp
  [kamstrup]=1025/tcp [kamstrup-management]=50100/tcp
  [ads]=48898/tcp [ads-discovery]=48899/udp [opcua]=4840/tcp
  [knx]=3671/udp [bacnet]=47808/udp
  [hartip]=5094/tcp
  [iccp]=102/tcp
  [modbus]=502/tcp
)
declare -A HOST_PORT=()
declare -A MAPPED_PORT=()
CONPOT_CONTAINER_NAMES=()

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Conpot image with all deployed
T-Pot templates (${TEMPLATES[*]}).

Options:
  --image IMAGE          Image to test. Defaults to docker/conpot/docker-compose.yml.
  --<key>-port PORT      Host port for one service, default: dynamic loopback port.
                         Keys: ${PORT_KEYS[*]}
  --timeout SEC          Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP           Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts       Keep temporary compose file and logs for debugging.
  -h, --help             Show this help message.
EOF
}

parse_args() {
  local key=""
  local value=""

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
      --*-port|--*-port=*)
        key="${1#--}"
        if [[ "${key}" == *=* ]]; then
          value="${key#*=}"
          key="${key%%=*}"
          shift
        else
          [[ $# -ge 2 ]] || test_die "$1 requires an argument"
          value="$2"
          shift 2
        fi
        key="${key%-port}"
        [[ -n "${PORT_TEMPLATE[${key}]+x}" ]] || test_die "Unknown option: --${key}-port"
        HOST_PORT[${key}]="${value}"
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
  local key=""

  test_validate_timeout
  for key in "${!HOST_PORT[@]}"; do
    test_validate_port "${HOST_PORT[${key}]}"
  done
}

ensure_ports_free() {
  local key=""
  local port=""

  for key in "${!HOST_PORT[@]}"; do
    port="${HOST_PORT[${key}]}"
    if (( port < 1024 )); then
      test_info "Skipping user-space preflight for privileged port ${port}; Docker will validate the binding."
    elif [[ "${PORT_CONTAINER[${key}]}" == */udp ]]; then
      test_ensure_udp_port_free "${TEST_BIND_IP}" "${port}" || test_die "${TEST_BIND_IP}:${port}/udp is already in use. Try --${key}-port <free-port>."
    else
      test_ensure_port_free "${TEST_BIND_IP}" "${port}" || test_die "${TEST_BIND_IP}:${port} is already in use. Try --${key}-port <free-port>."
    fi
  done
}

container_name() {
  printf '%s-%s\n' "${TEST_PROJECT_NAME}" "${1,,}"
}

prepare_conpot_harness() {
  local template=""
  local key=""
  local cport=""

  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  IDENTITY_DIR="${TEST_TMP_ROOT}/identity"
  RENDERED_DIR="${TEST_TMP_ROOT}/rendered"
  PROBES="${TEST_TMP_ROOT}/probes.py"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"
  mkdir -p "${LOG_DIR}" "${IDENTITY_DIR}" "${RENDERED_DIR}"
  chmod 0777 "${LOG_DIR}" "${IDENTITY_DIR}"
  write_probes

  {
    # one network for all containers with ICC off, as in the T-Pot compose files
    printf 'networks:\n  conpot_local:\n    driver_opts:\n      com.docker.network.bridge.enable_icc: "false"\n\nservices:\n'
    for template in "${TEMPLATES[@]}"; do
      CONPOT_CONTAINER_NAMES+=("$(container_name "${template}")")
      cat <<EOF
  conpot_${template}:
    image: "${IMAGE}"
    container_name: "$(container_name "${template}")"
    restart: "no"
    read_only: true
    user: "2000:2000"
    environment:
      - CONPOT_CONFIG=/etc/conpot/conpot.cfg
      - CONPOT_JSON_LOG=/var/log/conpot/conpot_${template}.json
      - CONPOT_LOG=/var/log/conpot/conpot_${template}.log
      - CONPOT_TEMPLATE=${template}
      - CONPOT_TMP=/tmp/conpot
    tmpfs:
      - /tmp/conpot:uid=2000,gid=2000
    networks:
      - conpot_local
    volumes:
      - "${LOG_DIR}:/var/log/conpot"
      - "${IDENTITY_DIR}:/var/lib/conpot"
    ports:
EOF
      for key in "${PORT_KEYS[@]}"; do
        [[ "${PORT_TEMPLATE[${key}]}" == "${template}" ]] || continue
        cport="${PORT_CONTAINER[${key}]}"
        if [[ "${cport}" == */tcp ]]; then
          cport="${cport%/tcp}"
        fi
        printf '      - "%s:%s:%s"\n' "${TEST_BIND_IP}" "${HOST_PORT[${key}]:-}" "${cport}"
      done
      printf '\n'
    done
  } > "${TEST_HARNESS_COMPOSE}"
}

test_show_diagnostics() {
  local container=""
  local file=""

  printf '\n[diagnostics] Container states\n' >&2
  for container in "${CONPOT_CONTAINER_NAMES[@]}"; do
    printf '%s: ' "${container}" >&2
    docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} error={{.State.Error}}' "${container}" >&2 || true
  done

  printf '\n[diagnostics] Docker logs\n' >&2
  if [[ -n "${TEST_HARNESS_COMPOSE}" && -f "${TEST_HARNESS_COMPOSE}" ]]; then
    test_compose logs --no-color --tail=60 >&2 || true
  fi

  printf '\n[diagnostics] Test log artifacts\n' >&2
  if [[ -n "${LOG_DIR}" && -d "${LOG_DIR}" ]]; then
    while IFS= read -r file; do
      printf '\n--- %s ---\n' "${file}" >&2
      tail -n 40 "${file}" >&2 || true
    done < <(find "${LOG_DIR}" -maxdepth 1 -type f -print | sort)
  fi
}

wait_for_containers() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local container=""
  local state=""
  local all_running=""

  while (( SECONDS < deadline )); do
    all_running="true"
    for container in "${CONPOT_CONTAINER_NAMES[@]}"; do
      state="$(docker inspect -f '{{.State.Status}}' "${container}" 2>/dev/null || true)"
      case "${state}" in
        running) ;;
        exited|dead) return 1 ;;
        *) all_running="false" ;;
      esac
    done
    [[ "${all_running}" == "true" ]] && return 0
    sleep 1
  done

  return 1
}

assert_containers_running() {
  local container=""
  local state=""

  for container in "${CONPOT_CONTAINER_NAMES[@]}"; do
    state="$(docker inspect -f '{{.State.Status}}' "${container}" 2>/dev/null || true)"
    [[ "${state}" == "running" ]] || test_die "${container} is not running; state=${state:-unknown}"
  done
}

wait_for_log_line() {
  local log_file="$1"
  local pattern="$2"
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if [[ -f "${log_file}" ]] && grep -F -- "${pattern}" "${log_file}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  return 1
}

wait_for_listeners() {
  local template=""
  local name=""
  local -A listeners=(
    [IEC104]="IEC104Server DNP3Server"
    [guardian_ast]="GuardianASTServer"
    [ipmi]="IPMIServer"
    [kamstrup_382]="KamstrupServer KamstrupManagementServer"
    [beckhoff_cx]="AdsServer AdsDiscovery OPCUAServer"
    [building_automation]="KnxnetipServer BacnetServer"
    [hart_gateway]="HartipServer"
    [iccp]="ICCPServer"
    [plc_modbus]="ModbusServer"
  )

  for template in "${TEMPLATES[@]}"; do
    for name in ${listeners[${template}]}; do
      wait_for_log_line "${LOG_DIR}/conpot_${template}.log" "${name} listening on" || test_die "${name} listener entry was not found in conpot_${template}.log"
    done
  done
  # SNMP logs no generic listener line
  wait_for_log_line "${LOG_DIR}/conpot_IEC104.log" "SNMP server started on" || test_die "SNMP listener entry was not found in conpot_IEC104.log"
}

resolve_ports() {
  local key=""
  local mapping=""

  for key in "${PORT_KEYS[@]}"; do
    mapping="$(docker port "$(container_name "${PORT_TEMPLATE[${key}]}")" "${PORT_CONTAINER[${key}]}" 2>/dev/null | head -n 1 || true)"
    [[ -n "${mapping}" ]] || test_die "Could not resolve mapped host port for ${key} (${PORT_CONTAINER[${key}]})"
    MAPPED_PORT[${key}]="${mapping##*:}"
  done
}

# Copy the rendered templates and identities out of the containers, the probes
# compare what goes over the wire with them.
collect_rendered() {
  local template=""
  local container=""

  for template in "${TEMPLATES[@]}"; do
    container="$(container_name "${template}")"
    rm -rf "${RENDERED_DIR:?}/${template}"
    # docker cp does not see tmpfs mounts
    docker exec "${container}" tar -C /tmp/conpot/templates -cf - "${template}" | tar -xf - -C "${RENDERED_DIR}" || test_die "Rendered template ${template} not found in ${container}"
    # templates without per-install values store no identity
    docker exec "${container}" sh -c "cat /var/lib/conpot/${template}.json 2>/dev/null || echo '{\"values\": {}}'" > "${RENDERED_DIR}/${template}.identity.json"
  done
}

probe() {
  local name="$1"
  local key="$2"
  shift 2

  python3 "${PROBES}" "${name}" "${TEST_BIND_IP}" "${MAPPED_PORT[${key}]}" "${TEST_TIMEOUT}" "${RENDERED_DIR}" "$@" || test_die "${name} probe failed"
}

run_opcua_probe() {
  local container=""
  local result=""

  container="$(container_name beckhoff_cx)"
  result="$(docker run --rm -i --network "container:${container}" --entrypoint python3 "${IMAGE}" - < "${TEST_TMP_ROOT}/opcua_client.py")" || test_die "OPC UA client failed"
  printf '%s\n' "${result}" > "${TEST_TMP_ROOT}/opcua_result.json"
  python3 "${PROBES}" opcua_check "${TEST_BIND_IP}" "${MAPPED_PORT[opcua]}" "${TEST_TIMEOUT}" "${RENDERED_DIR}" "${TEST_TMP_ROOT}/opcua_result.json" || test_die "OPC UA probe failed"
}

run_all_probes() {
  test_info "Running protocol probes"
  probe iec104 iec104
  probe dnp3 dnp3
  probe snmp snmp
  probe guardian_ast guardian-ast
  probe ipmi ipmi
  probe kamstrup_meter kamstrup
  probe kamstrup_management kamstrup-management
  probe ads ads
  probe ads_discovery ads-discovery
  probe opcua_tcp opcua
  run_opcua_probe
  probe knx knx
  probe bacnet bacnet
  probe hartip hartip
  probe iccp iccp
  probe modbus modbus
  test_ok "All protocol probes passed"
}

assert_identity_persistent() {
  local container=""
  local before=""
  local after=""
  local fresh=""

  container="$(container_name beckhoff_cx)"
  before="$(cat "${RENDERED_DIR}/beckhoff_cx.identity.json")"
  docker restart "${container}" >/dev/null
  wait_for_containers || test_die "${container} did not come back after restart"
  wait_for_log_line "${LOG_DIR}/conpot_beckhoff_cx.log" "OPCUAServer listening on" || test_die "${container} did not restart its listeners"
  after="$(docker exec "${container}" cat /var/lib/conpot/beckhoff_cx.json)"
  [[ "${before}" == "${after}" ]] || test_die "Identity of beckhoff_cx changed across a restart"
  # dynamic host ports change with the restart
  resolve_ports

  fresh="$(docker run --rm --entrypoint python3 "${IMAGE}" -c 'import json, sys; sys.path.insert(0, "/opt/tpot"); import tpot_templates as t; spec = t.load_spec("/opt/tpot/templates.toml"); print(json.dumps(t.resolve_identity(spec["templates"]["beckhoff_cx"], {})[0], sort_keys=True))')"
  python3 - "${after}" "${fresh}" <<'PY' || test_die "A fresh identity does not differ from the stored one"
import json
import sys

stored = json.loads(sys.argv[1])["values"]
fresh = json.loads(sys.argv[2])
same = [k for k in stored if stored[k] == fresh.get(k)]
if len(same) == len(stored):
    raise SystemExit(f"fresh identity equals stored identity: {same}")
print(f"stored identity kept across restart, fresh identity differs in {len(stored) - len(same)}/{len(stored)} values")
PY
  test_ok "Identity is persistent and generated per install"
}

assert_no_hang() {
  local container=""
  local cpu=""
  local key=""

  test_info "Abusing every TCP service (half-open, partial and oversized frames, RST)"
  for key in "${PORT_KEYS[@]}"; do
    [[ "${PORT_CONTAINER[${key}]}" == */tcp ]] || continue
    python3 "${PROBES}" abuse "${TEST_BIND_IP}" "${MAPPED_PORT[${key}]}" "${TEST_TIMEOUT}" "${RENDERED_DIR}" || test_die "abuse of ${key} failed"
  done
  sleep 10
  for _ in 1 2; do
    while read -r container cpu; do
      cpu="${cpu%\%}"
      python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) < float(sys.argv[2]) else 1)" "${cpu}" "${CPU_LIMIT}" || test_die "${container} uses ${cpu}% CPU after the abuse round"
    done < <(docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' "${CONPOT_CONTAINER_NAMES[@]}")
    sleep 3
  done
  test_ok "No container spins after the abuse round (CPU < ${CPU_LIMIT}%)"
}

assert_no_runtime_errors() {
  if grep -R -E "Traceback|NameError|handler crashed" "${LOG_DIR}" >/dev/null 2>&1; then
    grep -R -n -E -A3 "Traceback|NameError|handler crashed" "${LOG_DIR}" | head -n 20 >&2 || true
    test_die "Conpot runtime error found in log files"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "Traceback|NameError" >/dev/null 2>&1; then
    test_die "Conpot runtime error found in Docker logs"
  fi
}

validate_json_events() {
  python3 - "${LOG_DIR}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

log_dir = Path(sys.argv[1])
deadline = time.monotonic() + int(sys.argv[2])

# template -> list of (description, predicate)
EXPECT = {
    "IEC104": [
        ("IEC104 connection", lambda e: e["protocol"] == "IEC104" and e["event_type"] == "NEW_CONNECTION"),
        ("DNP3 connection", lambda e: e["protocol"] == "dnp3" and e["event_type"] == "NEW_CONNECTION"),
    ],
    "guardian_ast": [
        ("Guardian AST I20100", lambda e: e["protocol"] == "guardian_ast" and e["event_type"] == "AST I20100"),
    ],
    "ipmi": [
        ("IPMI auth capabilities", lambda e: e["protocol"] == "ipmi" and e["event_type"] == "GET_CHANNEL_AUTH_CAPABILITIES" and e.get("response")),
    ],
    "kamstrup_382": [
        ("Kamstrup meter", lambda e: e["protocol"] == "kamstrup_protocol" and e["dst_port"] == 1025),
        ("Kamstrup management", lambda e: e["protocol"] == "kamstrup_management_protocol" and e["dst_port"] == 50100),
    ],
    "beckhoff_cx": [
        ("ADS request", lambda e: e["protocol"] == "ads" and e["event_type"] == "REQUEST"),
        ("OPC UA request", lambda e: e["protocol"] == "opcua" and e["event_type"] == "REQUEST"),
    ],
    "building_automation": [
        ("KNXnet/IP request", lambda e: e["protocol"] == "knxnetip" and e["event_type"] == "REQUEST"),
        ("BACnet connection", lambda e: e["protocol"] == "bacnet" and e["event_type"] == "NEW_CONNECTION"),
    ],
    "hart_gateway": [
        ("HART-IP request", lambda e: e["protocol"] == "hartip" and e["event_type"] == "REQUEST"),
    ],
    "iccp": [
        ("ICCP request", lambda e: e["protocol"] == "iccp" and e["event_type"] == "REQUEST"),
    ],
    "plc_modbus": [
        ("Modbus connection", lambda e: e["protocol"] == "modbus" and e["event_type"] == "NEW_CONNECTION"),
    ],
}
REQUIRED = ("schema_version", "sensorid", "session_id", "protocol", "session_time", "event_time", "src_ip", "dst_port", "event_type", "template")

missing = {}
while True:
    missing = {}
    for template, checks in EXPECT.items():
        path = log_dir / f"conpot_{template}.json"
        events = []
        if path.exists():
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    sys.exit(f"{path}:{number}: invalid JSON: {exc}")
                absent = [k for k in REQUIRED if k not in event]
                if absent or event["schema_version"] != 1 or event["template"] != template:
                    sys.exit(f"{path}:{number}: not a v1 event of template {template}: missing={absent} {line[:200]}")
                events.append(event)
        for description, check in checks:
            if not any(check(e) for e in events):
                missing.setdefault(template, []).append(description)
    if not missing or time.monotonic() > deadline:
        break
    time.sleep(1)

if missing:
    sys.exit(f"JSON events not found: {missing}")
print("All expected v1 JSON events found")
PY
}

write_probes() {
  cat > "${TEST_TMP_ROOT}/opcua_client.py" <<'PY'
import asyncio
import json

from asyncua import Client, ua


async def main():
    url = "opc.tcp://127.0.0.1:4840"
    client = Client(url, timeout=10)
    endpoints = await client.connect_and_get_server_endpoints()
    result = {
        "endpoints": [e.EndpointUrl for e in endpoints],
        "application_name": endpoints[0].Server.ApplicationName.Text,
        "application_uri": endpoints[0].Server.ApplicationUri,
        "product_uri": endpoints[0].Server.ProductUri,
    }
    client = Client(endpoints[0].EndpointUrl.replace(endpoints[0].EndpointUrl.split("/")[2], "127.0.0.1:4840"), timeout=10)
    async with client:
        build = await client.get_node(ua.ObjectIds.Server_ServerStatus_BuildInfo).read_value()
        result["build_info"] = {
            "product_uri": build.ProductUri,
            "manufacturer_name": build.ManufacturerName,
            "product_name": build.ProductName,
            "software_version": build.SoftwareVersion,
        }
        result["namespaces"] = await client.get_namespace_array()
        objects = await client.nodes.objects.get_children()
        result["objects"] = [(await n.read_browse_name()).Name for n in objects]
    print(json.dumps(result))


asyncio.run(main())
PY

  cat > "${PROBES}" <<'PY'
"""Conpot protocol probes. Each probe compares the answer with the rendered
template and identity and rejects known Conpot / upstream default strings."""

import json
import socket
import struct
import sys
import time
import tomllib
from pathlib import Path

DENY = [
    b"conpot", b"technodrome", b"mouser", b"statoil", b"evilpowerprovider",
    b"163.172.189.137", b"freeopcua", b"compagnie generale",
]


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def guard(name, data):
    low = data.lower() if isinstance(data, bytes) else str(data).lower().encode()
    for word in DENY:
        if word in low:
            fail(f"{name}: response contains fingerprint '{word.decode()}': {data[:200]!r}")


def load(rendered, template, proto):
    with open(Path(rendered) / template / f"{proto}.toml", "rb") as f:
        return tomllib.load(f)


def identity(rendered, template):
    return json.loads((Path(rendered) / f"{template}.identity.json").read_text())["values"]


def recv_until(sock, predicate, timeout):
    deadline = time.monotonic() + timeout
    data = b""
    sock.settimeout(1)
    while time.monotonic() < deadline and not predicate(data):
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            break
        data += chunk
    return data


def udp(host, port, payload, timeout):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(min(timeout, 5))
        for _ in range(3):
            sock.sendto(payload, (host, port))
            try:
                return sock.recvfrom(4096)[0]
            except socket.timeout:
                continue
    fail(f"no UDP response from {host}:{port}")


def tcp(host, port, payload, timeout, predicate=lambda d: len(d) > 0):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        if payload:
            sock.sendall(payload)
        return recv_until(sock, predicate, timeout)


def dnp3_crc(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA6BC if crc & 1 else crc >> 1
    return struct.pack("<H", ~crc & 0xFFFF)


def ber_tlv(tag, value):
    assert len(value) < 0x80
    return bytes([tag, len(value)]) + value


# --------------------------------------------------------------------------


def probe_iec104(host, port, timeout, rendered):
    # STARTDT act -> STARTDT con
    data = tcp(host, port, bytes.fromhex("680407000000"), timeout)
    if not data.startswith(bytes.fromhex("68040b000000")):
        fail(f"IEC104: expected STARTDT con, got {data[:16].hex()}")
    print("IEC104 STARTDT confirmed")


def probe_dnp3(host, port, timeout, rendered):
    address = load(rendered, "IEC104", "dnp3")["dnp3"]["outstation_address"]
    # link layer: request link status (PRM=1, DIR=1, FC=9), master 0 -> outstation
    header = bytes([0x05, 0x64, 0x05, 0xC9]) + struct.pack("<HH", address, 0)
    data = tcp(host, port, header + dnp3_crc(header), timeout, lambda d: len(d) >= 10)
    if len(data) < 10 or data[:2] != b"\x05\x64" or data[3] & 0x0F != 0x0B:
        fail(f"DNP3: expected link status response, got {data[:16].hex()}")
    if struct.unpack("<H", data[6:8])[0] != address:
        fail(f"DNP3: response source is not outstation {address}")
    guard("DNP3", data)
    print(f"DNP3 link status from outstation {address}")


def probe_snmp(host, port, timeout, rendered):
    db = load(rendered, "IEC104", "template")["core"]["databus"]["key_value_mappings"]
    oids = {
        "sysDescr": "1.3.6.1.2.1.1.1.0",
        "sysContact": "1.3.6.1.2.1.1.4.0",
        "sysLocation": "1.3.6.1.2.1.1.6.0",
    }

    def oid_bytes(oid):
        parts = [int(p) for p in oid.split(".")]
        out = bytes([parts[0] * 40 + parts[1]])
        for p in parts[2:]:
            enc = [p & 0x7F]
            p >>= 7
            while p:
                enc.insert(0, 0x80 | (p & 0x7F))
                p >>= 7
            out += bytes(enc)
        return out

    answers = {}
    for name, oid in oids.items():
        varbind = ber_tlv(0x30, ber_tlv(0x30, ber_tlv(0x06, oid_bytes(oid)) + b"\x05\x00"))
        pdu = ber_tlv(0xA0, ber_tlv(0x02, b"\x01") + b"\x02\x01\x00\x02\x01\x00" + varbind)
        msg = ber_tlv(0x30, b"\x02\x01\x01" + ber_tlv(0x04, b"public") + pdu)
        answers[name] = udp(host, port, msg, timeout)
        guard(f"SNMP {name}", answers[name])
    for name, key in (("sysDescr", "SystemDescription"), ("sysContact", "sysContact"), ("sysLocation", "sysLocation")):
        if db[key].encode() not in answers[name]:
            fail(f"SNMP: {name} '{db[key]}' not in response {answers[name][-60:]!r}")
    # Upstream registers the IP-/TCP-/UDP-MIB table columns as scalar instances, which
    # pysnmp 7 does not serve, so ipAdEntAddr etc. cannot be checked here.
    print(f"SNMP sysContact '{db['sysContact']}', sysLocation '{db['sysLocation']}'")


def probe_guardian_ast(host, port, timeout, rendered):
    data = tcp(host, port, b"\x01I20100\n", timeout, lambda d: b"I20100" in d and b"\x03" in d)
    if b"I20100" not in data or b"IN-TANK" not in data:
        fail(f"Guardian AST: expected inventory response, got {data[:120]!r}")
    for value in (b"AVIA", b"ADBLUE"):
        if value not in data:
            fail(f"Guardian AST: T-Pot value {value!r} missing in response")
    guard("Guardian AST", data)
    print("Guardian AST inventory with T-Pot station values")


def probe_ipmi(host, port, timeout, rendered):
    # RMCP/IPMI v1.5 Get Channel Authentication Capabilities
    payload = bytes.fromhex("0600ff07 0000000000000000 0009 2018c88100388e04b5".replace(" ", ""))
    data = udp(host, port, payload, timeout)
    if not data.startswith(bytes.fromhex("0600ff07")) or b"\x38" not in data:
        fail(f"IPMI: expected auth capabilities response, got {data[:32].hex()}")
    guard("IPMI", data)
    print(f"IPMI response {len(data)} bytes")


def probe_kamstrup_meter(host, port, timeout, rendered):
    with socket.create_connection((host, port), timeout=timeout):
        pass
    print("Kamstrup meter TCP connection accepted")


def probe_kamstrup_management(host, port, timeout, rendered):
    ident = identity(rendered, "kamstrup_382")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        banner = recv_until(sock, lambda d: b"Welcome" in d, timeout)
        if b"Welcome" not in banner:
            fail(f"Kamstrup management: expected banner, got {banner[:120]!r}")
        sock.sendall(b"!GC\r\n")
        config = recv_until(sock, lambda d: ident["LAN_IP"].encode() in d and b"\r\n\r\n" in d, timeout)
    for value in (ident["LAN_IP"], ident["MAC"], "pwr_ctrl_mgmt01.int.local"):
        if value.encode().lower() not in config.lower():
            fail(f"Kamstrup management: '{value}' missing in !GC output {config[:300]!r}")
    guard("Kamstrup management", banner + config)
    print("Kamstrup management config shows identity and T-Pot values")


def probe_ads(host, port, timeout, rendered):
    ads = load(rendered, "beckhoff_cx", "ads")["ads"]
    target = bytes(int(p) for p in ads["ams_net_id"].split("."))
    source = bytes([10, 0, 0, 2, 1, 1])
    header = struct.pack("<6sH6sHHHIII", target, ads["ams_port"], source, 32905, 1, 4, 0, 0, 1)
    frame = struct.pack("<HI", 0, len(header)) + header
    data = tcp(host, port, frame, timeout, lambda d: len(d) >= 6 + 32 + 24)
    if len(data) < 6 + 32 + 24:
        fail(f"ADS: short ReadDeviceInfo response {data.hex()}")
    payload = data[6 + 32:]
    result, major, minor, build = struct.unpack("<IBBH", payload[:8])
    name = payload[8:24].rstrip(b"\x00").decode("latin-1")
    if result != 0 or name != ads["device_name"] or (major, minor, build) != (ads["version_major"], ads["version_minor"], ads["version_build"]):
        fail(f"ADS: unexpected device info result={result} name={name!r} version={major}.{minor}.{build}")
    guard("ADS", data)
    print(f"ADS device info '{name}' {major}.{minor}.{build}")


def probe_ads_discovery(host, port, timeout, rendered):
    ads = load(rendered, "beckhoff_cx", "ads")["ads"]
    request = struct.pack("<III6sHI", 0x71146603, 0, 1, bytes([10, 0, 0, 2, 1, 1]), 10000, 0)
    data = udp(host, port, request, timeout)
    if data[:4] != struct.pack("<I", 0x71146603):
        fail(f"ADS discovery: bad magic {data[:8].hex()}")
    if ads["hostname"].encode() not in data or bytes(int(p) for p in ads["ams_net_id"].split(".")) not in data:
        fail(f"ADS discovery: hostname/NetId missing in {data!r}")
    guard("ADS discovery", data)
    print(f"ADS discovery hostname {ads['hostname']}, NetId {ads['ams_net_id']}")


def probe_opcua_tcp(host, port, timeout, rendered):
    url = f"opc.tcp://{host}:{port}".encode()
    body = struct.pack("<IIIII", 0, 65536, 65536, 0, 0) + struct.pack("<i", len(url)) + url
    hello = b"HELF" + struct.pack("<I", 8 + len(body)) + body
    data = tcp(host, port, hello, timeout, lambda d: len(d) >= 8)
    if not data.startswith(b"ACKF"):
        fail(f"OPC UA: expected ACK, got {data[:16]!r}")
    print("OPC UA HEL/ACK through the mapped port")


def probe_opcua_check(host, port, timeout, rendered, result_file):
    opcua = load(rendered, "beckhoff_cx", "opcua")["opcua"]
    result = json.loads(Path(result_file).read_text())
    guard("OPC UA", json.dumps(result))
    if result["application_name"] != opcua["server_name"]:
        fail(f"OPC UA: ApplicationName {result['application_name']!r} != {opcua['server_name']!r}")
    if result["build_info"]["product_name"] != opcua["product_name"]:
        fail(f"OPC UA: BuildInfo {result['build_info']}")
    for url in result["endpoints"]:
        if "0.0.0.0" in url or opcua["endpoint_host"] not in url:
            fail(f"OPC UA: endpoint {url} does not advertise {opcua['endpoint_host']}")
    if opcua["folder_name"] not in result["objects"]:
        fail(f"OPC UA: folder {opcua['folder_name']} missing in {result['objects']}")
    print(f"OPC UA '{result['application_name']}' at {result['endpoints'][0]}")


def probe_knx(host, port, timeout, rendered):
    knx = load(rendered, "building_automation", "knxnetip")["knxnetip"]
    hpai = bytes([8, 1]) + b"\x00" * 6
    search = udp(host, port, bytes.fromhex("06100201000e") + hpai, timeout)
    if search[2:4] != b"\x02\x02":
        fail(f"KNX: expected SEARCH_RESPONSE, got {search[:8].hex()}")
    advertised = socket.inet_ntoa(search[8:12])
    if advertised != knx["advertise_ip"]:
        fail(f"KNX: SEARCH_RESPONSE advertises {advertised}, expected {knx['advertise_ip']}")
    desc = udp(host, port, bytes.fromhex("06100203000e") + hpai, timeout)
    if desc[2:4] != b"\x02\x04":
        fail(f"KNX: expected DESCRIPTION_RESPONSE, got {desc[:8].hex()}")
    name = desc[6 + 24:6 + 54].rstrip(b"\x00").decode("latin-1")
    if name != knx["friendly_name"][:30]:
        fail(f"KNX: friendly name {name!r} != {knx['friendly_name']!r}")
    guard("KNX", search + desc)
    print(f"KNX '{name}' advertises {advertised}")


def probe_bacnet(host, port, timeout, rendered):
    bacnet = load(rendered, "building_automation", "bacnet")["bacnet"]["device_info"]
    whois = bytes.fromhex("810a000c0120ffff00ff1008")
    iam = udp(host, port, whois, timeout)
    if b"\x10\x00\xc4" not in iam:
        fail(f"BACnet: expected I-Am, got {iam.hex()}")
    apdu = iam[iam.index(b"\x10\x00\xc4") + 3:]
    instance = struct.unpack(">I", apdu[:4])[0] & 0x3FFFFF
    vendor = apdu[-1] if apdu[-2] == 0x21 else struct.unpack(">H", apdu[-2:])[0]
    if instance != bacnet["device_identifier"] or vendor != bacnet["vendor_identifier"]:
        fail(f"BACnet: I-Am device {instance} vendor {vendor}, expected {bacnet['device_identifier']}/{bacnet['vendor_identifier']}")
    answers = b""
    for prop in (121, 70):  # vendor-name, model-name
        objid = struct.pack(">I", (8 << 22) | instance)
        rp = bytes.fromhex("0104") + bytes.fromhex("0005010c") + b"\x0c" + objid + bytes([0x19, prop])
        answers += udp(host, port, bytes.fromhex("810a") + struct.pack(">H", 4 + len(rp)) + rp, timeout)
    for value in (bacnet["vendor_name"], bacnet["model_name"]):
        if value.encode() not in answers:
            fail(f"BACnet: '{value}' not in ReadProperty answers {answers!r}")
    guard("BACnet", iam + answers)
    print(f"BACnet device {instance}, vendor {vendor} '{bacnet['vendor_name']}'")


def probe_hartip(host, port, timeout, rendered):
    hart = load(rendered, "hart_gateway", "hartip")["hartip"]

    def pdu(msg_id, seq, body):
        return struct.pack("!BBBBHH", 1, 0, msg_id, 0, seq, 8 + len(body)) + body

    def token(cmd):
        frame = bytes([0x02, 0x80, cmd, 0])
        check = 0
        for b in frame:
            check ^= b
        return frame + bytes([check])

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(pdu(0, 1, b"\x01\x00\x00\x75\x30"))
        init = recv_until(sock, lambda d: len(d) >= 13, timeout)
        if len(init) < 8 or init[2] != 0 or init[1] != 1:
            fail(f"HART-IP: session initiate failed {init.hex()}")
        sock.sendall(pdu(3, 2, token(0)))
        cmd0 = recv_until(sock, lambda d: len(d) >= 8 and len(d) >= struct.unpack("!H", d[6:8])[0], timeout)
        sock.sendall(pdu(3, 3, token(20)))
        cmd20 = recv_until(sock, lambda d: len(d) >= 8 and len(d) >= struct.unpack("!H", d[6:8])[0], timeout)
    data0 = cmd0[8 + 6:]
    manufacturer = struct.unpack("!H", data0[17:19])[0]
    if manufacturer != hart["manufacturer_id"] or struct.unpack("!H", data0[1:3])[0] != hart["expanded_device_type"]:
        fail(f"HART-IP: command 0 manufacturer {manufacturer} / device type mismatch: {data0.hex()}")
    if hart["long_tag"].encode() not in cmd20:
        fail(f"HART-IP: long tag {hart['long_tag']!r} not in {cmd20!r}")
    guard("HART-IP", cmd0 + cmd20)
    print(f"HART-IP manufacturer {manufacturer}, long tag '{hart['long_tag']}'")


ICCP_CR = bytes.fromhex("0300000b06e00000000100")
ICCP_INITIATE = bytes.fromhex(
    "030000c502f0800dbc0506130100160102140200023302000134020002c1a63181a3a003800101a2819b80020780"
    "810400000001820400000002a423300f0201010604520100013004060251013010020103060528ca220201300406"
    "025101880206006160305e020101a059605780020780a107060528ca220101a20406022902a303020102a6040602"
    "2901a703020101be32283006025101020103a027a82580027d00810114820114830104a416800101810305fb0082"
    "0c036e1d000000000064000198"
)


def probe_iccp(host, port, timeout, rendered):
    iccp = load(rendered, "iccp", "iccp")["iccp"]
    identify = ber_tlv(0xA0, ber_tlv(0x02, b"\x01") + b"\x82\x00")
    presentation = ber_tlv(0x61, ber_tlv(0x30, ber_tlv(0x02, b"\x03") + ber_tlv(0xA0, identify)))
    user = b"\x01\x00\x01\x00" + presentation
    dt = b"\x02\xf0\x80" + user
    tpkt = struct.pack("!BBH", 3, 0, 4 + len(dt)) + dt

    def full_tpkt(d):
        return len(d) >= 4 and len(d) >= struct.unpack("!H", d[2:4])[0]

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(ICCP_CR)
        cc = recv_until(sock, full_tpkt, timeout)
        if len(cc) < 6 or cc[5] != 0xD0:
            fail(f"ICCP: expected COTP CC, got {cc.hex()}")
        sock.sendall(ICCP_INITIATE)
        accept = recv_until(sock, full_tpkt, timeout)
        sock.sendall(tpkt)
        answer = recv_until(sock, lambda d: full_tpkt(d) and iccp["vendor"].encode() in d, timeout)
    for value in (iccp["vendor"], iccp["model"], iccp["revision"]):
        if value.encode() not in answer:
            fail(f"ICCP: identify response misses '{value}': {answer!r}")
    guard("ICCP", cc + accept + answer)
    print(f"ICCP identify '{iccp['vendor']}' '{iccp['model']}' '{iccp['revision']}'")


def probe_modbus(host, port, timeout, rendered):
    modbus = load(rendered, "plc_modbus", "modbus")["modbus"]
    unit = modbus["slaves"][0]["id"]
    # FC43 / MEI 14 read device identification, basic
    request = struct.pack(">HHHB", 1, 0, 5, unit) + bytes([0x2B, 0x0E, 0x01, 0x00])
    data = tcp(host, port, request, timeout, lambda d: len(d) >= 9)
    if len(data) < 9 or data[7] != 0x2B:
        fail(f"Modbus: expected device identification, got {data.hex()}")
    for key in ("VendorName", "ProductCode", "MajorMinorRevision"):
        if modbus["device_info"][key].encode() not in data:
            fail(f"Modbus: {key} '{modbus['device_info'][key]}' missing in {data!r}")
    guard("Modbus", data)
    print(f"Modbus device identification '{modbus['device_info']['VendorName']}' '{modbus['device_info']['ProductCode']}'")


def probe_abuse(host, port, timeout, rendered):
    """Half-open connections, partial frames, oversized length headers, RST."""
    junk = [
        b"",
        b"\x68\xff",
        bytes.fromhex("0001000000ff01") + b"\x03",
        b"\x05\x64\xff\xc4\x01\x00\x00\x00",
        b"\x00\x00\xff\xff\xff\x7f",
        b"\x03\x00\xff\xff\x02",
        b"\x01\x00\x03\x00\x00\x01\xff\xff",
        b"HELF\xff\xff\xff\x7f",
        b"\x00" * 4096,
    ]
    held = []
    for payload in junk:
        for linger in (False, True):
            try:
                sock = socket.create_connection((host, port), timeout=timeout)
            except OSError as exc:
                fail(f"abuse: connect to {port} failed: {exc}")
            if payload:
                try:
                    sock.sendall(payload)
                except OSError:
                    pass
            if linger:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                sock.close()
            else:
                held.append(sock)
    time.sleep(2)
    for sock in held:
        sock.close()
    print(f"abused port {port}")


def main():
    name, host, port, timeout, rendered, *extra = sys.argv[1:]
    func = globals().get(f"probe_{name}")
    if func is None:
        fail(f"unknown probe {name}")
    func(host, int(port), int(timeout), rendered, *extra)


main()
PY
}

main() {
  parse_args "$@"
  validate_args
  test_check_dependencies

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build conpot_default"

  ensure_ports_free
  prepare_conpot_harness
  test_enable_cleanup

  test_info "Starting isolated Conpot containers: ${TEMPLATES[*]}"
  test_compose up -d --no-build >/dev/null

  wait_for_containers || test_die "One or more Conpot containers did not stay running"
  test_ok "All Conpot containers are running"

  test_info "Waiting for Conpot listener entries"
  wait_for_listeners
  test_ok "All Conpot listener entries were found"

  resolve_ports
  collect_rendered
  test_ok "Rendered templates and identities collected"

  run_all_probes
  assert_containers_running

  assert_identity_persistent

  assert_no_hang
  run_all_probes
  assert_containers_running

  assert_no_runtime_errors
  test_ok "No Conpot runtime errors found in logs"

  test_info "Validating Conpot JSON events"
  validate_json_events || test_die "Conpot JSON event validation failed"
  test_ok "Conpot JSON events use event schema v1"

  test_ok "Conpot post-build smoke test completed successfully"
}

main "$@"
