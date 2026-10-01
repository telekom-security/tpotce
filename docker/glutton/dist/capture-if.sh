#!/bin/sh
# T-Pot: print the network interface the NSM services capture on.
#
# Keep in sync! This is the original, identical copies live in
# docker/{suricata,p0f,glutton}/dist/capture-if.sh (separate build contexts).
#
# 1. TPOT_CAPTURE_INTERFACE from the T-Pot .env, if set it has to exist.
# 2. The interface of the route the kernel uses for outgoing traffic. This is a
#    route lookup only, no traffic is sent, so it also works with an internal
#    gateway and no internet access.
# 3. Without any default route, the first interface that is up and holds a
#    global IPv4 address (loopback and Docker bridges / veth pairs excluded).
#
# Prints exactly one interface name, or an error on stderr and exits with 1.
# Works with iproute2 and with the BusyBox ip applet.

myIF="${TPOT_CAPTURE_INTERFACE:-}"
if [ -n "${myIF}" ];
  then
    if ip link show dev "${myIF}" > /dev/null 2>&1;
      then
        echo "${myIF}"
        exit 0
    fi
    echo "capture-if: TPOT_CAPTURE_INTERFACE=${myIF} does not exist, please check the T-Pot .env config." >&2
    exit 1
fi

# Field following "dev" in the output of ip route get
fuROUTE_DEV () {
  awk '{ for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit } }'
}

myIF="$(ip route get 1.1.1.1 2>/dev/null | fuROUTE_DEV)"
if [ -z "${myIF}" ];
  then
    myIF="$(ip -6 route get 2606:4700:4700::1111 2>/dev/null | fuROUTE_DEV)"
fi
if [ -n "${myIF}" ];
  then
    echo "${myIF}"
    exit 0
fi

myIF="$(ip -4 address show 2>/dev/null | awk '
  /^[0-9]+:/ { myNAME = $2; sub(/:$/, "", myNAME); sub(/@.*/, "", myNAME); myUP = ($3 ~ /[<,]UP[,>]/) }
  /^ +inet / && / scope global / && myUP && myNAME !~ /^(lo|docker|br-|veth)/ { print myNAME; exit }')"
if [ -n "${myIF}" ];
  then
    echo "${myIF}"
    exit 0
fi

echo "capture-if: no capture interface found (no default route and no interface with a global IPv4 address), set TPOT_CAPTURE_INTERFACE in the T-Pot .env config." >&2
exit 1
