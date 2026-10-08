#!/bin/bash
# hptest.sh [-B <file>] [--tools-only] [host]: probes the honeypots of
# ~/tpotce/docker-compose.yml on host (default: the address of this host), a few
# service specific requests first, then nmap over every published port. The
# probes are real attacks as far as T-Pot can tell, they show up in Kibana.
# `tpot check honeypots` runs it, also from the Checks page of the menu.

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

myHOST=""
myTOOLS_ONLY=""
myBECOME_FILE=""
myDOCKERCOMPOSEYML="$HOME/tpotce/docker-compose.yml"
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

function fuPORTS {   # docker compose config --format json on stdin -> "T:22,..." and "U:69,..."
	python3 -c '
import json, sys
config = json.load(sys.stdin)
tcp, udp = set(), set()
for service in (config.get("services") or {}).values():
    for port in service.get("ports") or []:
        if port.get("host_ip") in ("127.0.0.1", "::1"):
            continue
        published = str(port.get("published") or port.get("target") or "")
        # 64290 - 64309 are the ports of T-Pot itself (SSH, web UI, sensor ingest)
        if not published.isdigit() or 64290 <= int(published) <= 64309:
            continue
        (udp if port.get("protocol") == "udp" else tcp).add(int(published))
print(",".join(f"T:{p}" for p in sorted(tcp)))
print(",".join(f"U:{p}" for p in sorted(udp)))
'
}

function fuHAS {   # command
	command -v "$1" >/dev/null 2>&1
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
Usage: $0 [-B <file>] [--tools-only] [host]

Probes the honeypots of ${myDOCKERCOMPOSEYML} on host, default: the address of
this host. The probes are real attacks as far as T-Pot can tell.

  -B <file>      Read the sudo password from a file (installing the tools)
  --tools-only   Only install nmap, ncat and the DICOM tools, then end
  -h             Show this help message
EOF
	exit 1
}

# sourced by the tests for its functions
[[ "${BASH_SOURCE[0]}" != "${0}" ]] && return 0

while [ $# -gt 0 ];
  do
    case "$1" in
      -B) myBECOME_FILE="$2"; shift 2 ;;
      --tools-only) myTOOLS_ONLY="1"; shift ;;
      -h|--help) fuUSAGE ;;
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

fuUI_BANNER "Honeypot probe" "Probes the honeypots of this T-Pot: service requests, then nmap over every port."

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

fuMARK phase probe Probing some services on "${myHOST}"
fuUI_INFO "Probing some services on ${myHOST} ..."
myNC=""
fuHAS nc && myNC="nc"
fuHAS ncat && myNC="ncat"
if [ -n "${myNC}" ];
  then
    echo "$myMEDPOTPACKET" | timeout 5 "${myNC}" "$myHOST" 2575 >/dev/null 2>&1 &
    echo "I20100" | timeout 3 "${myNC}" "$myHOST" 10001 >/dev/null 2>&1 &
    timeout 3 "${myNC}" "$myHOST" 3299 </dev/null >/dev/null 2>&1 &
fi
curl -s -m 5 -XGET "http://$myHOST:9200/logstash-*/_search" >/dev/null 2>&1 &
curl -s -m 5 -XPOST -H "Content-Type: application/json" -d '{"name":"test","email":"test@test.com"}' "http://$myHOST:9200/test" >/dev/null 2>&1 &
if fuHAS findscu;
  then
    timeout 10 findscu -P -k PatientName="*" "$myHOST" 11112 >/dev/null 2>&1 &
    timeout 10 getscu -P -k PatientName="*" "$myHOST" 11112 >/dev/null 2>&1 &
fi
wait
fuUI_OK "Service requests sent."

fuMARK phase scan Scanning every published port
if fuHAS nmap;
  then
    fuUI_INFO "Scanning the UDP / TCP ports of ${myDOCKERCOMPOSEYML} (up to ${myTIMEOUT}s) ..."
    [ -n "${myTCPPORTS}" ] && timeout --foreground "${myTIMEOUT}" nmap -sV -sC -v -p "${myTCPPORTS}" "$myHOST" &
    # a UDP scan needs root; sudo first, so the -B function refreshes it
    [ -n "${myUDPPORTS}" ] && sudo timeout --foreground "${myTIMEOUT}" nmap -sU -sV -sC -v -p "${myUDPPORTS}" "$myHOST" &
    wait
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
# nmap can leave the terminal in a strange state, not a log
[ -t 1 ] && [ "${TPOT_MARKS}" != "1" ] && reset
fuMARK phase "done" Done
fuUI_OK "Done. The probes show up in Kibana as attacks from ${myHOST}."
