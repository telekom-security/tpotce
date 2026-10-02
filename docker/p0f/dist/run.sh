#!/bin/bash
# T-Pot p0f entrypoint.
#   no arguments: live capture on the NSM interface, JSON log in /var/log/p0f/p0f.json
#   arguments:    passed to p0f, which writes the JSON log to stdout, e.g.
#                 docker run --rm -v "$PWD:/pcap:ro" dtagdevsec/p0f:24.04.2 -r /pcap/x.pcap ['filter']
if [ "$#" -eq 0 ];
  then
    myIF="$(capture-if.sh)" || exit 1
    # Leave out the SYNs the host sends itself (SYN without ACK from one of its
    # addresses, read at start); its SYN+ACKs stay, p0f needs them to follow the
    # incoming connections. IPv6: TCP flags right after the IPv6 header.
    myFILTER=""
    for myIP in $(ip -o addr show dev "${myIF}" | awk '{ split($4, a, "/"); print a[1] }');
      do
        case "${myIP}" in
          *:*) myRULE="(src host ${myIP} and ip6[53] & 0x12 == 0x02)" ;;
          *)   myRULE="(src host ${myIP} and tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn)" ;;
        esac
        myFILTER="${myFILTER:+${myFILTER} or }${myRULE}"
    done
    exec /opt/p0f/p0f -u p0f -j -o /var/log/p0f/p0f.json -i "${myIF}" ${myFILTER:+"not (${myFILTER})"} > /dev/null
fi
exec /opt/p0f/p0f -j -o - "$@"
