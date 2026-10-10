#!/bin/sh
#
# Starts Endlessh with the values of this install. On the first start they are
# chosen at random and kept in /var/lib/endlessh, so not every T-Pot sends with
# the same delay, line length and banner. Delete a file there to get a new one.

set -eu

myIDENTITY_DIR="/var/lib/endlessh"
myCORPUS="/opt/endlessh/banner_corpus.txt"

# A random number from <min> to <max>
fuRANDOM () {
  myNUMBER=$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')
  echo $(( $1 + myNUMBER % ($2 - $1 + 1) ))
}

# Write stdin to <file> in one step, a half written file is never read
fuWRITE () {
  cat > "$1.tmp"
  mv -f "$1.tmp" "$1"
}

myIDENTITY="${myIDENTITY_DIR}/identity.conf"
myBANNER="${myIDENTITY_DIR}/banner.txt"
if { [ ! -s "${myIDENTITY}" ] || [ ! -s "${myBANNER}" ]; } && [ ! -w "${myIDENTITY_DIR}" ]; then
  # a compose file of an earlier T-Pot has no folder for them: values for this start only
  echo "endlessh: warning: ${myIDENTITY_DIR} is not writable, the values of this install are not kept" >&2
  myIDENTITY_DIR="/dev/shm/endlessh"
  myIDENTITY="${myIDENTITY_DIR}/identity.conf"
  myBANNER="${myIDENTITY_DIR}/banner.txt"
  mkdir -p "${myIDENTITY_DIR}"
fi

if [ ! -s "${myIDENTITY}" ]; then
  myDELAY=$(fuRANDOM 6000 14000)
  myJITTER=$(( myDELAY * $(fuRANDOM 20 50) / 100 ))
  myLENGTH=$(fuRANDOM 24 64)
  fuWRITE "${myIDENTITY}" <<EOF
# Values of this install, chosen on the first start. Delete the file for new ones.
Delay ${myDELAY}
Jitter ${myJITTER}
MaxLineLength ${myLENGTH}
BannerFile ${myBANNER}
EOF
fi

if [ ! -s "${myBANNER}" ]; then
  myLINES=$(shuf -n "$(fuRANDOM 3 8)" "${myCORPUS}")
  if [ -z "${myLINES}" ]; then
    echo "endlessh: fatal: no banner lines in ${myCORPUS}" >&2
    exit 1
  fi
  printf '%s\n' "${myLINES}" | fuWRITE "${myBANNER}"
fi

# The values of this install first, endlessh.conf after them wins where it sets a key
exec ./endlessh -f "${myIDENTITY}" -f endlessh.conf >> /var/log/endlessh/endlessh.log
