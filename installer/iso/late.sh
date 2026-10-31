#!/bin/sh
# The late_command of the T-Pot ISO (preseed.cfg): runs in the Debian installer (busybox sh) at
# its end and puts the T-Pot installer into the new system. Nothing is installed or downloaded
# here, T-Pot itself comes at the first login (profile.sh, start.sh):
#   /usr/local/share/tpot-iso/   install.sh, start.sh, iso.conf and version of the ISO
#   /etc/profile.d/tpot-iso.sh   starts the T-Pot installer at a login until T-Pot is installed
#   /etc/issue.d/tpot.issue      the hint at the login prompt
# Arguments for the tests: the payload of the ISO (/cdrom/tpot) and the new system (/target).
set -e
mySRC="${1:-/cdrom/tpot}"
myTARGET="${2:-/target}"
myDEST="${myTARGET}/usr/local/share/tpot-iso"

for myFILE in install.sh start.sh terminal.sh iso.conf version profile.sh tpot.issue; do
  if [ ! -f "${mySRC}/${myFILE}" ];
    then
      echo "T-Pot ISO: ${mySRC}/${myFILE} is missing, the T-Pot installer is not set up." >&2
      exit 1
  fi
done

mkdir -p "${myDEST}" "${myTARGET}/usr/local/bin" "${myTARGET}/etc/profile.d" "${myTARGET}/etc/issue.d"
for myFILE in install.sh start.sh terminal.sh; do
  cp "${mySRC}/${myFILE}" "${myDEST}/${myFILE}"
  chmod 0755 "${myDEST}/${myFILE}"
done
ln -sf ../share/tpot-iso/terminal.sh "${myTARGET}/usr/local/bin/tpot-terminal"
for myFILE in iso.conf version; do
  cp "${mySRC}/${myFILE}" "${myDEST}/${myFILE}"
  chmod 0644 "${myDEST}/${myFILE}"
done
cp "${mySRC}/profile.sh" "${myTARGET}/etc/profile.d/tpot-iso.sh"
cp "${mySRC}/tpot.issue" "${myTARGET}/etc/issue.d/tpot.issue"
chmod 0644 "${myTARGET}/etc/profile.d/tpot-iso.sh" "${myTARGET}/etc/issue.d/tpot.issue"
echo "T-Pot ISO: the T-Pot installer starts at the first login."
