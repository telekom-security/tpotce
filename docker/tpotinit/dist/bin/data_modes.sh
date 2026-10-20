#!/usr/bin/env bash
# The modes of the T-Pot data folder, sourced by entrypoint.sh and clean.sh.
#
# Folders get rwx and files rw for the owner and the group (770 / 660), the others nothing.
# The folders of nginx (conf, cert) keep the read bit for the others (774 / 664), as before.
# The samples of Dionaea (binaries/) are read only (440), as Dionaea stores them.
# No container runs a program from the data folder, so no file there keeps an execute bit.
#
# "ug+X" after "u=rw,g=rw" gives x to folders only: busybox and GNU chmod take X from the mode
# the clauses before it made (BSD chmod takes it from the old mode, so this is for tpotinit).
# It is one chmod -R as before: no pass more over the data folder. chmod -R changes no link it
# meets and enters no linked folder; a path given here that is a link stays as it is.

myDATA_MODE="u=rw,g=rw,o=,ug+X"
myDATA_WEB_MODE="u=rw,g=rw,o=r,ug+X"

# fuDATA_CHMOD <mode> <path>...
fuDATA_CHMOD () {
  local myMODE="$1" myPATH
  shift
  for myPATH in "$@"; do
    while [ "${myPATH}" != "/" ] && [ "${myPATH%/}" != "${myPATH}" ]; do
      myPATH="${myPATH%/}"
    done
    if [ -L "${myPATH}" ];
      then
        echo "# Warning: ${myPATH} is a link, its modes stay as they are."
        continue
    fi
    chmod -R "${myMODE}" "${myPATH}"
  done
}

# fuDATA_MODES <path>...: folders 770, files 660
fuDATA_MODES () {
  fuDATA_CHMOD "${myDATA_MODE}" "$@"
}

# fuDATA_WEB_MODES <path>...: folders 774, files 664 (nginx conf and cert)
fuDATA_WEB_MODES () {
  fuDATA_CHMOD "${myDATA_WEB_MODE}" "$@"
}

# fuDATA_SAMPLE_MODES <path>...: files 440, the folders keep their modes (the samples of Dionaea).
# A path that is not there is left out. find enters no linked folder and -type f leaves the links out.
fuDATA_SAMPLE_MODES () {
  local myPATH
  for myPATH in "$@"; do
    while [ "${myPATH}" != "/" ] && [ "${myPATH%/}" != "${myPATH}" ]; do
      myPATH="${myPATH%/}"
    done
    if [ -L "${myPATH}" ];
      then
        echo "# Warning: ${myPATH} is a link, its modes stay as they are."
        continue
    fi
    if [ -d "${myPATH}" ]; then find "${myPATH}" -type f -exec chmod 440 {} +; fi
  done
}
