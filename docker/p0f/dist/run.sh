#!/bin/bash
# T-Pot p0f entrypoint.
#   no arguments: live capture on the NSM interface, JSON log in /var/log/p0f/p0f.json
#   arguments:    passed to p0f, which writes the JSON log to stdout, e.g.
#                 docker run --rm -v "$PWD:/pcap:ro" dtagdevsec/p0f:24.04.2 -r /pcap/x.pcap ['filter']
if [ "$#" -eq 0 ];
  then
    myIF="$(capture-if.sh)" || exit 1
    exec /opt/p0f/p0f -u p0f -j -o /var/log/p0f/p0f.json -i "${myIF}" > /dev/null
fi
exec /opt/p0f/p0f -j -o - "$@"
