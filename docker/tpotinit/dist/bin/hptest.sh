#!/bin/bash
# hptest.sh [-B <file>] [-y] [--nmap] [--tools-only] [host]: checks the honeypots
# of ~/tpotce/docker-compose.yml. Where the T-Pot Manager runs, this is
# `tpot check honeypots` (every honeypot from its port to the Attack Map, the test
# events removed from Elasticsearch afterwards); it asks first, -y confirms.
# Otherwise, for another host, with --nmap or --tools-only, the probes of old: a
# few service specific requests on host (default: the address of this host),
# then nmap over the published ports the checks probe (the probe table leaves
# some out, `tpot check honeypots --dry-run` names them). They are real attacks
# as far as T-Pot can tell and show up in Kibana. ewsposter of this host sends
# nothing while they run (ews_hold.sh), and one check runs at a time.

# an exported CDPATH turns a cd into a search that prints the folder it found
unset CDPATH
myHERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
# the look of the T-Pot scripts (installer/lib/ui.sh of the checkout this lies in,
# or of ~/tpotce), plain text where there is none (i.e. inside the tpotinit image)
# shellcheck source=installer/lib/ui.sh
if ! source "${myHERE}/../../../../installer/lib/ui.sh" 2>/dev/null && \
   ! source "${HOME}/tpotce/installer/lib/ui.sh" 2>/dev/null;
  then
# >>> plain fallback
    fuUI_INIT () { return 0; }
    fuUI_BANNER () { local myLINE; echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE//$'\n'/$'\n'### }"; done; echo; }
    fuUI_INFO () { local myTEXT="$*"; echo "### ${myTEXT//$'\n'/$'\n'### }"; }
    fuUI_OK () { echo "### [OK] - $*"; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_ERROR () { echo "### [ERROR] - $*" >&2; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
    fuMARK () { [ "${TPOT_MARKS:-}" = "1" ] && echo "@@tpot $*"; return 0; }
# <<< plain fallback
fi
fuUI_INIT
# the hold of ewsposter and the lock of the checks
# shellcheck source=docker/tpotinit/dist/bin/ews_hold.sh
if ! source "${myHERE}/ews_hold.sh";
  then
    fuUI_ERROR "${myHERE}/ews_hold.sh is missing, the probes do not run without the hold of ewsposter."
    exit 1
fi

myHOST=""
myTOOLS_ONLY=""
myNMAP=""
myYES=""
myBECOME_FILE=""
myTPOTCE="${TPOTCE_DIR:-$HOME/tpotce}"
myDOCKERCOMPOSEYML="${myTPOTCE}/docker-compose.yml"
myTIMEOUT=180
myMEDPOTPACKET="
MSH|^~\&|ADT1|MCM|LABADT|MCM|198808181126|SECURITY|ADT^A01|MSG00001-|P|2.6
EVN|A01|198808181123
PID|||PATID1234^5^M11^^AN||JONES^WILLIAM^A^III||19610615|M||2106-3|677 DELAWARE AVENUE^^EVERETT^MA^02149|GL|(919)379-1212|(919)271-3434~(919)277-3114||S||PATID12345001^2^M10^^ACSN|123456789|9-87654^NC
NK1|1|JONES^BARBARA^K|SPO|||||20011105
NK1|1|JONES^MICHAEL^A|FTH
PV1|1|I|2000^2012^01||||004777^LEBAUER^SIDNEY^J.|||SUR||-||ADM|A0
AL1|1||^PENICILLIN||CODE16~CODE17~CODE18
AL1|2||^CAT DANDER||CODE257
DG1|001|I9|1550|MAL NEO LIVER, PRIMARY|19880501103005|F
PR1|2234|M11|111^CODE151|COMMON PROCEDURES|198809081123
ROL|45^RECORDER^ROLE MASTER LIST|AD|RO|KATE^SMITH^ELLEN|199505011201
GT1|1122|1519|BILL^GATES^A
IN1|001|A357|1234|BCMD|||||132987
IN2|ID1551001|SSN12345678
ROL|45^RECORDER^ROLE MASTER LIST|AD|RO|KATE^ELLEN|199505011201"
# containers whose state the probes change, restarted afterwards (if they run here)
myRESTART="adbhoney conpot_guardian_ast conpot_kamstrup_382 dionaea"
# the ports of the honeypots that get no probe ("T:2575,U:623"): their ewsposter module is not held
myLEFTOUT=""

function fuTOOLS_FOR {   # $1 = os-release file -> "<package manager> <packages>"
	local myID="" myLIKE=""
	# shellcheck source=/dev/null
	myID=$(. "$1" 2>/dev/null; echo "${ID}")
	# shellcheck source=/dev/null
	myLIKE=$(. "$1" 2>/dev/null; echo "${ID_LIKE}")
	case " ${myID} ${myLIKE} " in
	  *" debian "*|*" ubuntu "*|*" raspbian "*) echo "apt nmap ncat dcmtk" ;;
	  *" fedora "*)
	    # DCMTK is in Fedora, but not in the base repositories of RHEL and its rebuilds
	    if [ "${myID}" = "fedora" ];
	      then echo "dnf nmap nmap-ncat dcmtk"
	      else echo "dnf nmap nmap-ncat"
	    fi ;;
	  *" rhel "*) echo "dnf nmap nmap-ncat" ;;
	  # openSUSE dropped nmap and ncat, its netcat sends the service requests
	  *" suse "*|*" opensuse "*|*" opensuse-tumbleweed "*) echo "zypper netcat-openbsd dcmtk" ;;
	esac
}

function fuCOMMAND_OF {   # package -> the command it brings
	case "$1" in
	  ncat|nmap-ncat) echo ncat ;;
	  netcat-openbsd) echo nc ;;
	  dcmtk) echo findscu ;;
	  *) echo "$1" ;;
	esac
}

# docker compose config --format json on stdin -> "T:22,...", "U:69,..." and the seconds the
# scanned honeypots may still write lines after the last probe; $1 the services left out. What
# the probe table of tpot check leaves alone is left out here too: the ports it never sends to or
# only knocks at (by the port inside the container), whole honeypots (the LLM honeypots, ddospot);
# the block is generated from tpotctl/checks/probes.py
function fuPORTS {
	python3 -c '
import json, sys
# >>> probe table >>>
# generated from tpotctl/checks/probes.py by python3 -m tpotctl.checks.probes --hptest
skipped = {"beelzebub": "*", "conpot_IEC104": [["20000", "tcp"]], "conpot_beckhoff_cx": [["48898", "tcp"]], "conpot_kamstrup_382": [["1025", "tcp"]], "conpot_plc_modbus": [["502", "tcp"]], "ddospot": "*", "dionaea": [["20", "tcp"], ["42", "tcp"], ["69", "udp"], ["81", "tcp"]], "galah": "*"}
late = {"ciscoasa": 60, "ddospot": 60}
# <<< probe table <<<
config = json.load(sys.stdin)
left_out = set(sys.argv[1].split())
tcp, udp = set(), set()
wait = 0
for name, service in (config.get("services") or {}).items():
    leave = skipped.get(name) or []
    if name in left_out or leave == "*":
        continue
    scanned = False
    for port in service.get("ports") or []:
        if [str(port.get("target") or ""), port.get("protocol") or "tcp"] in leave:
            continue
        if port.get("host_ip") in ("127.0.0.1", "::1"):
            continue
        published = str(port.get("published") or port.get("target") or "")
        # 64290 - 64309 are the ports of T-Pot itself (SSH, web UI, sensor ingest)
        if not published.isdigit() or 64290 <= int(published) <= 64309:
            continue
        (udp if port.get("protocol") == "udp" else tcp).add(int(published))
        scanned = True
    if scanned:
        wait = max(wait, late.get(name, 0))
print(",".join(f"T:{p}" for p in sorted(tcp)))
print(",".join(f"U:{p}" for p in sorted(udp)))
print(wait)
' "${1:-}"
}

function fuNOT_HELD {   # docker compose config --format json on stdin -> the services whose ewsposter module is not held
	local myNAME=""
	for myNAME in $(python3 -c 'import json, sys; print(" ".join(sorted((json.load(sys.stdin).get("services") or {}))))');
	  do
	    fuEWS_HOLDS "${myNAME}" || printf '%s ' "${myNAME}"
	  done
}

function fuPROBE_TO {   # protocol (T or U) port -> 0 unless the port belongs to a service left out
	case ",${myLEFTOUT}," in
	  *",$1:$2,"*) return 1 ;;
	esac
	return 0
}

function fuIKEPORTS {   # compose JSON -> Ciscoasa published port and NAT-T marker flag
    python3 -c '
import json, sys
service = (json.load(sys.stdin).get("services") or {}).get("ciscoasa", {})
for port in service.get("ports") or []:
    if port.get("protocol") != "udp" or port.get("host_ip") in ("127.0.0.1", "::1"):
        continue
    target = port.get("target")
    published = str(port.get("published") or target or "")
    if target in (500, 4500) and published.isdigit():
        print(published, int(target == 4500))
'
}

function fuIKEPROBE {   # host published-port NAT-T-flag
    python3 - "$1" "$2" "$3" <<'PYPROBE'
import socket, struct, sys
host, port, natt = sys.argv[1:]
sa = bytes.fromhex("00000001000000010000002001010001000000180101000080010005800200028003000180040002")
packet = b"tpotprob" + b"\0" * 8 + bytes([1, 0x10, 2, 0]) + struct.pack("!II", 0, 32 + len(sa)) + struct.pack("!BBH", 0, 0, 4 + len(sa)) + sa
marker = b"\0" * 4 if natt == "1" else b""
try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.sendto(marker + packet, (host, int(port)))
        response, _ = sock.recvfrom(65535)
    if not response.startswith(marker + b"tpotprob"):
        sys.exit(1)
except OSError:
    sys.exit(1)
PYPROBE
}

function fuHAS {   # command
	command -v "$1" >/dev/null 2>&1
}

function fuOWN_ADDRESS {   # host -> 0 when it is an address of this host (or a name of it)
	local myADDR=""
	case "$1" in
	  localhost|127.*|::1|"$(hostname 2>/dev/null)") return 0 ;;
	esac
	for myADDR in $(ip -o addr show 2>/dev/null | awk '{print $4}');
	  do
	    [ "${myADDR%/*}" = "$1" ] && return 0
	  done
	return 1
}

# the scans in the background: their own pids (timeout, or sudo for the UDP scan), so a signal
# reaches the scanner itself and a wait really waits for it
myPIDS=()
mySUDO_PIDS=()

function fuSTOP_PROBES {   # the probes still running (Ctrl+C, TERM) end before the hold does
	local myPID="" myJOBS="" myWAIT=0
	myJOBS=$(jobs -p)
	# shellcheck disable=SC2086
	kill ${myJOBS} "${myPIDS[@]}" 2>/dev/null
	# the UDP scan runs as root: when sudo did not end by the TERM above, root ends it (sudo passes
	# it on to timeout, timeout to nmap), with the credentials of the sudo -v before the scan
	for myPID in "${mySUDO_PIDS[@]}";
	  do
	    myWAIT=0
	    while kill -0 "${myPID}" 2>/dev/null && [ "${myWAIT}" -lt 10 ];
	      do
	        sleep 0.1
	        myWAIT=$(( myWAIT + 1 ))
	      done
	    kill -0 "${myPID}" 2>/dev/null && sudo -n kill -TERM "${myPID}" 2>/dev/null
	  done
	# every probe and scan, however long it takes to end (the scans at most myTIMEOUT)
	wait
}

function fuCHECKDEPS {
	local myTOOLS="" myMANAGER="" myPACKAGE="" myMISSING=()
	myTOOLS=$(fuTOOLS_FOR /etc/os-release)
	if [ -z "${myTOOLS}" ];
	  then
	    fuUI_WARN "Unknown distribution, install nmap, ncat (or nc) and dcmtk yourself."
	    return
	fi
	myMANAGER="${myTOOLS%% *}"
	for myPACKAGE in ${myTOOLS#* };
	  do
	    fuHAS "$(fuCOMMAND_OF "${myPACKAGE}")" || myMISSING+=("${myPACKAGE}")
	  done
	[ ${#myMISSING[@]} -eq 0 ] && { fuUI_OK "${myTOOLS#* } are there."; return; }
	fuUI_INFO "Installing ${myMISSING[*]} ..."
	case "${myMANAGER}" in
	  apt) sudo apt-get update -qq >/dev/null 2>&1
	       sudo apt-get install -y -qq "${myMISSING[@]}" >/dev/null 2>&1 ;;
	  dnf) sudo dnf -y -q install "${myMISSING[@]}" >/dev/null 2>&1 ;;
	  zypper) sudo zypper -n -q install "${myMISSING[@]}" >/dev/null 2>&1 ;;
	esac
	for myPACKAGE in "${myMISSING[@]}";
	  do
	    if fuHAS "$(fuCOMMAND_OF "${myPACKAGE}")";
	      then fuUI_OK "${myPACKAGE} installed."
	      else fuUI_WARN "${myPACKAGE} could not be installed, its probes are left out."
	    fi
	  done
}

function fuUSAGE {
	cat <<EOF
Usage: $0 [-B <file>] [-y] [--nmap] [--tools-only] [host]

Checks the honeypots of ${myDOCKERCOMPOSEYML}. Where the T-Pot Manager
runs, this is tpot check honeypots: every honeypot of this host from its port
to the Attack Map, the test events removed from Elasticsearch afterwards.
For another host, with --nmap or --tools-only, or without the T-Pot Manager:
service requests, then nmap over the published ports the checks probe on host
(default: the address of this host; the probe table leaves some out,
tpot check honeypots --dry-run names them); those probes are real attacks as
far as T-Pot can tell.
ewsposter of this host sends nothing during a check, one check runs at a time.

  -B <file>      Read the sudo password from a file
  -y             Start the check without asking (tpot check honeypots asks)
  --nmap         The service requests and the nmap scan, not tpot check
  --tools-only   Only install nmap, ncat and the DICOM tools, then end
  -h             Show this help message
EOF
	exit "${1:-1}"
}

# sourced by the tests for its functions
[[ "${BASH_SOURCE[0]}" != "${0}" ]] && return 0

while [ $# -gt 0 ];
  do
    case "$1" in
      -B) [ $# -ge 2 ] || { fuUI_ERROR "-B needs a file."; fuUSAGE; }
          myBECOME_FILE="$2"; shift 2 ;;
      --tools-only) myTOOLS_ONLY="1"; shift ;;
      --nmap) myNMAP="1"; shift ;;
      -y|--yes) myYES="1"; shift ;;
      -h|--help) fuUSAGE 0 ;;
      -*) fuUI_ERROR "Unknown option $1."; fuUSAGE ;;
      *) myHOST="$1"; shift ;;
    esac
  done

if [ -n "${myBECOME_FILE}" ];
  then
    [ -r "${myBECOME_FILE}" ] || { fuUI_ERROR "Cannot read the sudo password from ${myBECOME_FILE}."; exit 1; }
    myBECOME_FILE=$(cd "$(dirname "${myBECOME_FILE}")" && pwd)/$(basename "${myBECOME_FILE}")
    sudo () { command sudo -S -p "" -v < "${myBECOME_FILE}" >/dev/null 2>&1; command sudo "$@"; }
fi

# the check of this host is tpot check honeypots of the T-Pot Manager where it runs (as genuser.sh
# hands over to tpot users add); another host, --nmap and --tools-only stay here
myTPOT="${myTPOTCE}/tpot"
function fuTPOT_READY {
	[ -x "${myTPOT}" ] && "${myTPOT}" setup >/dev/null 2>&1
}
if [ -z "${myNMAP}" ] && [ -z "${myTOOLS_ONLY}" ] && { [ -z "${myHOST}" ] || fuOWN_ADDRESS "${myHOST}"; } && fuTPOT_READY;
  then
    myARGS=(check honeypots)
    [ -n "${myBECOME_FILE}" ] && myARGS+=(--become-file "${myBECOME_FILE}")
    [ -n "${myYES}" ] && myARGS+=(--yes)
    exec "${myTPOT}" "${myARGS[@]}"
fi

fuUI_BANNER "Honeypot probe" "Probes the honeypots of this T-Pot: service requests, then nmap over the ports the checks probe."

fuMARK phase tools Checking the tools
fuCHECKDEPS
[ -n "${myTOOLS_ONLY}" ] && exit 0

if [ -z "${myHOST}" ];
  then
    myHOST=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}')
fi
if [ -z "${myHOST}" ];
  then
    fuUI_ERROR "No address of this host found, name the host to probe."
    fuUSAGE
fi
if ! myPORTS=$(docker compose -f "${myDOCKERCOMPOSEYML}" config --format json 2>/dev/null | fuPORTS);
  then
    fuUI_ERROR "Cannot read the ports of ${myDOCKERCOMPOSEYML} (docker compose config)."
    exit 1
fi
myTCPPORTS=$(echo "${myPORTS}" | sed -n 1p)
myUDPPORTS=$(echo "${myPORTS}" | sed -n 2p)

# ewsposter of this host sends nothing from here on, until the last probe is logged
fuMARK phase hold Holding ewsposter
fuEWS_HOLD_TRAPS fuSTOP_PROBES
fuEWS_HOLD_BEGIN "$(fuEWS_DATA_PATH "${myTPOTCE}")" "${myDOCKERCOMPOSEYML}" || { fuMARK fail hold; exit 3; }
if ! fuOWN_ADDRESS "${myHOST}";
  then
    fuUI_WARN "${myHOST} is no address of this host: if it is another T-Pot, its ewsposter is not held and reports the probes."
  elif [ -n "${myEWS_HOLD_FAILED}" ];
    then
      # the honeypots whose ewsposter module is not held get no probe: their ports are left out
      myCONFIG=$(docker compose -f "${myDOCKERCOMPOSEYML}" config --format json 2>/dev/null)
      myNOTHELD=$(echo "${myCONFIG}" | fuNOT_HELD)
      if [ -n "${myNOTHELD}" ];
        then
          myALLPORTS="${myTCPPORTS},${myUDPPORTS}"
          myPORTS=$(echo "${myCONFIG}" | fuPORTS "${myNOTHELD}")
          myTCPPORTS=$(echo "${myPORTS}" | sed -n 1p)
          myUDPPORTS=$(echo "${myPORTS}" | sed -n 2p)
          myLEFTOUT=$(python3 -c 'import sys; keep = set(sys.argv[2].split(",")); print(",".join(p for p in sys.argv[1].split(",") if p and p not in keep))' "${myALLPORTS}" "${myTCPPORTS},${myUDPPORTS}")
          fuUI_WARN "No probe for ${myNOTHELD% }: ewsposter cannot hold its module (its idle pass fails; docker logs ewsposter)."
      fi
fi
# a honeypot that writes its lines late (ddospot, ciscoasa: a minute after the probe): the hold
# (of the ports really scanned) lasts that long after the last scan, plus a margin
myLATE=$(echo "${myPORTS}" | sed -n 3p)
case "${myLATE}" in
  ''|*[!0-9]*|0) ;;
  *) [ $(( myLATE + 10 )) -gt "${myEWS_HOLD_SETTLE}" ] && myEWS_HOLD_SETTLE=$(( myLATE + 10 )) ;;
esac

fuMARK phase probe Probing some services on "${myHOST}"
fuUI_INFO "Probing some services on ${myHOST} ..."
myNC=""
fuHAS nc && myNC="nc"
fuHAS ncat && myNC="ncat"
if [ -n "${myNC}" ];
  then
    fuPROBE_TO T 2575 && { echo "$myMEDPOTPACKET" | timeout 5 "${myNC}" "$myHOST" 2575 >/dev/null 2>&1 & }
    fuPROBE_TO T 10001 && { echo "I20100" | timeout 3 "${myNC}" "$myHOST" 10001 >/dev/null 2>&1 & }
    fuPROBE_TO T 3299 && { timeout 3 "${myNC}" "$myHOST" 3299 </dev/null >/dev/null 2>&1 & }
fi
if fuPROBE_TO T 9200;
  then
    curl -s -m 5 -XGET "http://$myHOST:9200/logstash-*/_search" >/dev/null 2>&1 &
    curl -s -m 5 -XPOST -H "Content-Type: application/json" -d '{"name":"test","email":"test@test.com"}' "http://$myHOST:9200/test" >/dev/null 2>&1 &
fi
if fuHAS findscu && fuPROBE_TO T 11112;
  then
    timeout 10 findscu -P -k PatientName="*" "$myHOST" 11112 >/dev/null 2>&1 &
    timeout 10 getscu -P -k PatientName="*" "$myHOST" 11112 >/dev/null 2>&1 &
fi
myIKEPORTS=$(docker compose -f "${myDOCKERCOMPOSEYML}" config --format json 2>/dev/null | fuIKEPORTS)
while read -r myIKEPORT myNATT;
  do
    [ -n "${myIKEPORT}" ] || continue
    fuPROBE_TO U "${myIKEPORT}" || continue
    if fuIKEPROBE "$myHOST" "$myIKEPORT" "$myNATT";
      then fuUI_OK "Ciscoasa IKE answered on ${myIKEPORT}/udp."
      else fuUI_WARN "Ciscoasa IKE did not answer on ${myIKEPORT}/udp."
    fi
  done <<< "${myIKEPORTS}"
wait
fuUI_OK "Service requests sent."

fuMARK phase scan Scanning the published ports
if fuHAS nmap;
  then
    fuUI_INFO "Scanning the UDP / TCP ports of ${myDOCKERCOMPOSEYML} (up to ${myTIMEOUT}s) ..."
    if [ -n "${myTCPPORTS}" ];
      then
        timeout --foreground "${myTIMEOUT}" nmap -sV -sC -v -p "${myTCPPORTS}" "$myHOST" &
        myPIDS+=("$!")
    fi
    # a UDP scan needs root: sudo itself in the background, so the job is sudo and no subshell
    # around it; with -B it reads the password from the file, otherwise sudo -v first (in the
    # foreground) and sudo -n for the scan
    if [ -n "${myUDPPORTS}" ];
      then
        if [ -n "${myBECOME_FILE}" ];
          then
            command sudo -S -p "" timeout --foreground "${myTIMEOUT}" nmap -sU -sV -sC -v -p "${myUDPPORTS}" "$myHOST" < "${myBECOME_FILE}" &
            myPIDS+=("$!")
            mySUDO_PIDS+=("$!")
        elif ! sudo -v;
          then
            fuUI_WARN "No sudo, no UDP scan."
        elif ! sudo -n true 2>/dev/null;
          then
            # sudo -v recorded nothing (i.e. timestamp_timeout=0): the scan below could not start
            fuUI_WARN "UDP scan skipped: sudo needs a password again."
            fuMARK warn scan UDP scan skipped: sudo needs a password again
          else
            command sudo -n timeout --foreground "${myTIMEOUT}" nmap -sU -sV -sC -v -p "${myUDPPORTS}" "$myHOST" &
            myPIDS+=("$!")
            mySUDO_PIDS+=("$!")
        fi
    fi
    [ "${#myPIDS[@]}" -eq 0 ] || wait "${myPIDS[@]}"
    myPIDS=()
    mySUDO_PIDS=()
    fuUI_OK "Scan done."
  else
    fuUI_WARN "nmap is not there (openSUSE has none in its repositories), no scan."
fi

fuMARK phase restart Restarting the containers the probes changed
for myCONTAINER in ${myRESTART};
  do
    grep -qE "^  ${myCONTAINER}:" "${myDOCKERCOMPOSEYML}" 2>/dev/null || continue
    docker restart "${myCONTAINER}" >/dev/null 2>&1 && fuUI_OK "${myCONTAINER} restarted."
  done
fuMARK phase release Lifting the hold of ewsposter
fuEWS_HOLD_END
# nmap can leave the terminal in a strange state, not a log
[ -t 1 ] && [ "${TPOT_MARKS}" != "1" ] && reset
fuMARK phase "done" Done
fuUI_OK "Done. The probes show up in Kibana as attacks from ${myHOST}."
