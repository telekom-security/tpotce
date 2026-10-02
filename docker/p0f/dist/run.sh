#!/bin/bash
# T-Pot p0f entrypoint.
#   no arguments: live capture on the NSM interface, JSON log in /var/log/p0f/p0f.json
#   arguments:    passed to p0f, which writes the JSON log to stdout (unless -o is
#                 given), e.g.
#                 docker run --rm -v "$PWD:/pcap:ro" dtagdevsec/p0f:24.04.2 -r /pcap/x.pcap ['filter']
if [ "$#" -eq 0 ];
  then
    myIF="$(capture-if.sh)" || exit 1
    # Leave out the SYNs the host sends itself (outgoing SYN without ACK, by the
    # packet direction of the kernel, so new or changed addresses are covered);
    # its SYN+ACKs stay, p0f needs them to follow the incoming connections.
    # IPv6: TCP flags right after the IPv6 header. Live capture only, libpcap
    # knows no direction in pcap files.
    myFILTER="not (outbound and ((ip and tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn) or (ip6 and ip6[53] & 0x12 == 0x02)))"
    exec /opt/p0f/p0f -u p0f -j -o /var/log/p0f/p0f.json -i "${myIF}" "${myFILTER}" > /dev/null
fi
myJSON="-j"
myOUT="-o -"
for myARG in "$@";
  do
    case "${myARG}" in
      -j) myJSON="" ;;
      -o|-o*) myOUT="" ;;
    esac
done
# shellcheck disable=SC2086
exec /opt/p0f/p0f ${myJSON} ${myOUT} "$@"
