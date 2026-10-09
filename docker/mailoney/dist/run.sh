#!/bin/sh
# T-Pot Mailoney entrypoint.
# Mailoney greets with its server name and puts it into its TLS certificate.
# Each T-Pot picks a name of its own on the first start and keeps it in
# logs/identity, so not every install greets with the same banner. Arguments
# are passed on to Mailoney.
myIDENTITY="/opt/mailoney/logs/identity"
myNAME_REGEX='^[a-z][a-z0-9-]{0,30}(\.[a-z][a-z0-9-]{0,30}){1,3}$'

# A random number from 0 to $1 - 1
fuRANDOM () {
  echo $(( $(od -An -N2 -tu2 /dev/urandom | tr -d ' ') % $1 ))
}

# One of the arguments, at random
fuPICK () {
  shift "$(fuRANDOM $#)"
  echo "$1"
}

# A plausible name of an internal mail host, e.g. mx2.office.lan
fuNEW_NAME () {
  local myHOST myNUMBER myDOMAIN myTLD
  myHOST="$(fuPICK mail mx smtp relay mailgw mta mailhost exch)"
  myNUMBER="$(fuPICK '' '' 1 2 3 01 02 03)"
  myDOMAIN="$(fuPICK corp office hq intra ops prod srv infra branch backend mgmt)"
  myTLD="$(fuPICK local lan internal localdomain intranet)"
  echo "${myHOST}${myNUMBER}.${myDOMAIN}.${myTLD}"
}

myNAME=""
if [ -f "${myIDENTITY}" ]; then
  myNAME="$(head -n 1 "${myIDENTITY}")"
fi
if ! echo "${myNAME}" | grep -Eq "${myNAME_REGEX}"; then
  myNAME="$(fuNEW_NAME)"
  if ( umask 027 && echo "${myNAME}" > "${myIDENTITY}.tmp" ) && mv -f "${myIDENTITY}.tmp" "${myIDENTITY}"; then
    echo "Stored new server name ${myNAME} in ${myIDENTITY}"
  else
    rm -f "${myIDENTITY}.tmp"
    echo "WARNING: cannot store the server name in ${myIDENTITY}, using ${myNAME} for this run"
  fi
fi

exec /usr/bin/python3 main.py --config /opt/mailoney/mailoney.toml --server-name "${myNAME}" "$@"
