#!/bin/bash
# T-Pot Container Data Cleaner & Log Rotator
# Set colors
myRED="[0;31m"
myGREEN="[0;32m"
myWHITE="[0;0m"

# Set pigz
myPIGZ=$(which pigz)

# Folders 770, files 660 (no execute bit), nginx conf and cert 774 / 664; links are not followed
. "$(dirname "${BASH_SOURCE[0]}")/data_modes.sh"

# Set persistence
myPERSISTENCE=$1
myPERSISTENCE_CYCLES=$2
myPERSISTENCE_CYCLES="${myPERSISTENCE_CYCLES:=30}"
export myPERSISTENCE_CYCLES

# Let's create a function that empties a service folder but for its identity / state:
# fuREMOVE_ALL_BUT <folder> <name> ...: every entry of the folder goes (logs, downloads,
# payloads, the archives of fuLOGROTATE) except the named ones; a link is removed, not followed
fuREMOVE_ALL_BUT () {
  local myFOLDER="$1" myITEM myKEEP myNAME
  shift
  for myITEM in "${myFOLDER}"/*; do
    [ -e "${myITEM}" ] || [ -L "${myITEM}" ] || continue
    myKEEP=""
    for myNAME in "$@"; do
      if [ "${myITEM##*/}" = "${myNAME}" ]; then myKEEP="1"; fi
    done
    [ -n "${myKEEP}" ] || rm -rf "${myITEM}"
  done
}

# Let's create a function to check if folder is empty
fuEMPTY () {
  local myFOLDER=$1

echo $(ls $myFOLDER | wc -l)
}

# Let's create a function to setup logrotate config
fuLOGROTATECONF () {
  local myLOGROTATECONF="/opt/tpot/etc/logrotate/logrotate.conf"
  local myLOGROTATETEMP="/opt/tpot/etc/logrotate/logrotate.template"
  envsubst < $myLOGROTATETEMP > $myLOGROTATECONF
  chown root:root $myLOGROTATECONF
  chmod 0600 $myLOGROTATECONF
}

# Let's create a function to rotate and compress logs
fuLOGROTATE () {
  local mySTATUS="/data/tpot/etc/logrotate/status"
  local myCONF="/opt/tpot/etc/logrotate/logrotate.conf"
  local myADBHONEYTGZ="/data/adbhoney/downloads.tgz"
  local myADBHONEYDL="/data/adbhoney/downloads/"
  local myCOWRIETTYLOGS="/data/cowrie/log/tty/"
  local myCOWRIETTYTGZ="/data/cowrie/log/ttylogs.tgz"
  local myCOWRIEDL="/data/cowrie/downloads/"
  local myCOWRIEDLTGZ="/data/cowrie/downloads.tgz"
  local myDIONAEABI="/data/dionaea/bistreams/"
  local myDIONAEABITGZ="/data/dionaea/bistreams.tgz"
  local myDIONAEABIN="/data/dionaea/binaries/"
  local myDIONAEABINTGZ="/data/dionaea/binaries.tgz"
  local myH0NEYTR4PP="/data/h0neytr4p/payloads/"
  local myH0NEYTR4PTGZ="/data/h0neytr4p/payloads.tgz"
  local myHONEYTRAPATTACKS="/data/honeytrap/attacks/"
  local myHONEYTRAPATTACKSTGZ="/data/honeytrap/attacks.tgz"
  local myHONEYTRAPDL="/data/honeytrap/downloads/"
  local myHONEYTRAPDLTGZ="/data/honeytrap/downloads.tgz"
  local myMAILONEYM="/data/mailoney/log/mails/"
  local myMAILONEYTGZ="/data/mailoney/mails.tgz"
  local myMINIPRINTU="/data/miniprint/uploads/"
  local myMINIPRINTTGZ="/data/miniprint/uploads.tgz"
  local myWORDPOTP="/data/wordpot/log/payloads/"
  local myWORDPOTTGZ="/data/wordpot/payloads.tgz"

# Setup logrotate config
fuLOGROTATECONF

# Ensure correct permissions and ownerships for logrotate to run without issues
fuDATA_MODES /data
chown tpot:tpot /data -R
fuDATA_WEB_MODES /data/nginx/conf /data/nginx/cert

# Run logrotate with force (-f) first, so the status file can be written and race conditions (with tar) be avoided
logrotate -f -s $mySTATUS $myCONF

# Compressing some folders first and rotate them later
if [ "$(fuEMPTY $myADBHONEYDL)" != "0" ]; then tar -I $myPIGZ -cvf $myADBHONEYTGZ $myADBHONEYDL; fi
if [ "$(fuEMPTY $myCOWRIETTYLOGS)" != "0" ]; then tar -I $myPIGZ -cvf $myCOWRIETTYTGZ $myCOWRIETTYLOGS; fi
if [ "$(fuEMPTY $myCOWRIEDL)" != "0" ]; then tar -I $myPIGZ -cvf $myCOWRIEDLTGZ $myCOWRIEDL; fi
if [ "$(fuEMPTY $myDIONAEABI)" != "0" ]; then tar -I $myPIGZ -cvf $myDIONAEABITGZ $myDIONAEABI; fi
if [ "$(fuEMPTY $myDIONAEABIN)" != "0" ]; then tar -I $myPIGZ -cvf $myDIONAEABINTGZ $myDIONAEABIN; fi
if [ "$(fuEMPTY $myH0NEYTR4PP)" != "0" ]; then tar -I $myPIGZ -cvf $myH0NEYTR4PTGZ $myH0NEYTR4PP; fi
if [ "$(fuEMPTY $myHONEYTRAPATTACKS)" != "0" ]; then tar -I $myPIGZ -cvf $myHONEYTRAPATTACKSTGZ $myHONEYTRAPATTACKS; fi
if [ "$(fuEMPTY $myHONEYTRAPDL)" != "0" ]; then tar -I $myPIGZ -cvf $myHONEYTRAPDLTGZ $myHONEYTRAPDL; fi
if [ "$(fuEMPTY $myMAILONEYM)" != "0" ]; then tar -I $myPIGZ -cvf $myMAILONEYTGZ $myMAILONEYM; fi
if [ "$(fuEMPTY $myMINIPRINTU)" != "0" ]; then tar -I $myPIGZ -cvf $myMINIPRINTTGZ $myMINIPRINTU; fi
if [ "$(fuEMPTY $myWORDPOTP)" != "0" ]; then tar -I $myPIGZ -cvf $myWORDPOTTGZ $myWORDPOTP; fi

# Ensure correct permissions and ownership for previously created archives: only those that are
# there (an archive exists once its folder had files), opened with O_NOFOLLOW and changed through
# that descriptor (fchmod, fchown), so a link put in place of one is never followed
# >>> archive modes >>>
myARCHIVE_MODES='import grp, os, pwd, stat, sys
user, _, group = sys.argv[1].partition(":")
uid, gid = pwd.getpwnam(user).pw_uid, grp.getgrnam(group or user).gr_gid
for path in sys.argv[2:]:
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        continue
    try:
        if stat.S_ISREG(os.fstat(handle).st_mode):
            os.fchmod(handle, 0o660)
            os.fchown(handle, uid, gid)
    finally:
        os.close(handle)'
# <<< archive modes <<<
python3 -c "${myARCHIVE_MODES}" tpot:tpot $myADBHONEYTGZ $myCOWRIETTYTGZ $myCOWRIEDLTGZ $myDIONAEABITGZ $myDIONAEABINTGZ $myH0NEYTR4PTGZ $myHONEYTRAPATTACKSTGZ $myHONEYTRAPDLTGZ $myMAILONEYTGZ $myMINIPRINTTGZ $myWORDPOTTGZ

# Need to remove subfolders since too many files cause rm to exit with errors
rm -rf $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myH0NEYTR4PP $myHONEYTRAPATTACKS $myHONEYTRAPDL $myMAILONEYM $myMINIPRINTU $myWORDPOTP

# Recreate subfolders with correct permissions and ownership
mkdir -p $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myH0NEYTR4PP $myHONEYTRAPATTACKS $myHONEYTRAPDL $myMAILONEYM $myMINIPRINTU $myWORDPOTP
chmod 770 $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myH0NEYTR4PP $myHONEYTRAPATTACKS $myHONEYTRAPDL $myMAILONEYM $myMINIPRINTU $myWORDPOTP
chown tpot:tpot $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myH0NEYTR4PP $myHONEYTRAPATTACKS $myHONEYTRAPDL $myMAILONEYM $myMINIPRINTU $myWORDPOTP

# Run logrotate again to account for previously created archives - DO NOT FORCE HERE!
logrotate -s $mySTATUS $myCONF
}

# Let's create a function to clean up and prepare tpotinit data
fuTPOTINIT () {
  mkdir -vp /data/ews/conf \
            /data/tpot/etc/{compose,logrotate} \
            /tmp/etc/
  fuDATA_MODES /data/ews /data/tpot /tmp/etc
  chown tpot:tpot /data/ews/ -R
  chown tpot:tpot /data/tpot/ -R
  chown tpot:tpot /tmp/etc/ -R
}

# Let's create a function to clean up and prepare adbhoney data
fuADBHONEY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/adbhoney/*; fi
  mkdir -vp /data/adbhoney/{downloads,log}
  fuDATA_MODES /data/adbhoney
  chown tpot:tpot /data/adbhoney/ -R
}

# Let's create a function to clean up and prepare beelzebub data
fuBEELZEBUB () {
  # key/ (the SSH host key) stays, the logs go
  if [ "$myPERSISTENCE" != "on" ]; then fuREMOVE_ALL_BUT /data/beelzebub key; fi
  mkdir -vp /data/beelzebub/{key,log}
  fuDATA_MODES /data/beelzebub
  chown tpot:tpot /data/beelzebub/ -R
}

# Let's create a function to clean up and prepare ciscoasa data
fuCISCOASA () {
  # identity/ stays, logs and uploads go
  if [ "$myPERSISTENCE" != "on" ]; then fuREMOVE_ALL_BUT /data/ciscoasa identity; fi
  mkdir -vp /data/ciscoasa/{identity,log,payloads}
  fuDATA_MODES /data/ciscoasa
  chown tpot:tpot /data/ciscoasa -R
}

# Let's create a function to clean up and prepare citrixhoneypot data
fuCITRIXHONEYPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/citrixhoneypot/*; fi
  mkdir -vp /data/citrixhoneypot/log/
  fuDATA_MODES /data/citrixhoneypot
  chown tpot:tpot /data/citrixhoneypot/ -R
}

# Let's create a function to clean up and prepare conpot data
fuCONPOT () {
  # identity/ (the values of this installation) stays, the logs go
  if [ "$myPERSISTENCE" != "on" ]; then fuREMOVE_ALL_BUT /data/conpot identity; fi
  mkdir -vp /data/conpot/{identity,log}
  fuDATA_MODES /data/conpot
  chown tpot:tpot /data/conpot -R
}

# Let's create a function to clean up and prepare cowrie data
fuCOWRIE () {
  # keys/ (the SSH host keys of each persona, persona-state.json of COWRIE_PERSONA_ROTATION)
  # stays, logs, tty records and downloads go
  if [ "$myPERSISTENCE" != "on" ]; then fuREMOVE_ALL_BUT /data/cowrie keys; fi
  mkdir -vp /data/cowrie/{downloads,keys,misc,log,log/tty}
  fuDATA_MODES /data/cowrie
  chown tpot:tpot /data/cowrie -R
}

# Let's create a function to clean up and prepare ddospot data
fuDDOSPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/ddospot/log; fi
  mkdir -vp /data/ddospot/log
  fuDATA_MODES /data/ddospot
  chown tpot:tpot /data/ddospot -R
}

# Let's create a function to clean up and prepare dicompot data
fuDICOMPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/dicompot/log; fi
  mkdir -vp /data/dicompot/{images,log}
  fuDATA_MODES /data/dicompot
  chown tpot:tpot /data/dicompot -R
}

# Let's create a function to clean up and prepare dionaea data
fuDIONAEA () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/dionaea/*; fi
  mkdir -vp /data/dionaea/{log,bistreams,binaries,rtp,roots,roots/ftp,roots/tftp,roots/www,roots/upnp}
  touch /data/dionaea/dionaea-errors.log
  touch /data/dionaea/sipaccounts.sqlite
  touch /data/dionaea/sipaccounts.sqlite-journal
  touch /data/dionaea/log/dionaea.json
  touch /data/dionaea/log/dionaea.sqlite
  fuDATA_MODES /data/dionaea
  chown tpot:tpot /data/dionaea -R
}

# Let's create a function to clean up and prepare elasticpot data
fuELASTICPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/elasticpot/*; fi
  mkdir -vp /data/elasticpot/log
  fuDATA_MODES /data/elasticpot
  chown tpot:tpot /data/elasticpot -R
}

# Let's create a function to clean up and prepare elk data
fuELK () {
  # ELK data will be kept for <= 90 days, check /etc/crontab for curator modification
  # ELK daemon log files will be removed
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/elk/log/*; fi
  mkdir -vp /data/elk/{data,log,listbot}
  fuDATA_MODES /data/elk
  chown tpot:tpot /data/elk -R
}

# Let's create a function to clean up and prepare endlessh data
fuENDLESSH () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/endlessh/log; fi
  mkdir -vp /data/endlessh/{identity,log}
  fuDATA_MODES /data/endlessh
  chown tpot:tpot /data/endlessh -R
}

# Let's create a function to clean up and prepare galah data
fuGALAH () {
  # cert/ (the TLS certificate) and cache/ (the answers of the LLM, they save LLM calls) stay, the logs go
  if [ "$myPERSISTENCE" != "on" ]; then fuREMOVE_ALL_BUT /data/galah cache cert; fi
  mkdir -vp /data/galah/{cache,cert,log}
  fuDATA_MODES /data/galah
  chown tpot:tpot /data/galah/ -R
}

# Let's create a function to clean up and prepare glutton data
fuGLUTTON () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/glutton/*; fi
  mkdir -vp /data/glutton/{log,payloads}
  fuDATA_MODES /data/glutton
  chown tpot:tpot /data/glutton -R
}

# Let's create a function to clean up and prepare go-pot data
fuGOPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/go-pot/*; fi
  mkdir -vp /data/go-pot/log
  fuDATA_MODES /data/go-pot
  chown tpot:tpot /data/go-pot -R
}

# Let's create a function to clean up and prepare h0neytr4p data
fuH0NEYTR4P () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/h0neytr4p/*; fi
  mkdir -vp /data/h0neytr4p/{log,payloads}
  fuDATA_MODES /data/h0neytr4p
  chown tpot:tpot /data/h0neytr4p/ -R
}

# Let's create a function to clean up and prepare hellpot data
fuHELLPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/hellpot/log; fi
  mkdir -vp /data/hellpot/log
  fuDATA_MODES /data/hellpot
  chown tpot:tpot /data/hellpot -R
}

# Let's create a function to clean up and prepare heralding data
fuHERALDING () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/heralding/*; fi
  mkdir -vp /data/heralding/log
  fuDATA_MODES /data/heralding
  chown tpot:tpot /data/heralding -R
}

# Let's create a function to clean up and prepare honeyaml data
fuHONEYAML () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeyaml/*; fi
  mkdir -vp /data/honeyaml/log
  fuDATA_MODES /data/honeyaml
  chown tpot:tpot -R /data/honeyaml
}

# Let's create a function to clean up and prepare honeypots data
fuHONEYPOTS () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeypots/*; fi
  mkdir -vp /data/honeypots/log
  fuDATA_MODES /data/honeypots
  chown tpot:tpot /data/honeypots -R
}

# Let's create a function to clean up and prepare honeysap data
fuHONEYSAP () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeysap/*; fi
  mkdir -vp /data/honeysap/log
  fuDATA_MODES /data/honeysap
  chown tpot:tpot /data/honeysap -R
}

# Let's create a function to clean up and prepare honeytrap data
fuHONEYTRAP () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeytrap/*; fi
  mkdir -vp /data/honeytrap/{log,attacks,downloads}
  fuDATA_MODES /data/honeytrap
  chown tpot:tpot /data/honeytrap/ -R
}

# Let's create a function to clean up and prepare ipphoney data
fuIPPHONEY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/ipphoney/*; fi
  mkdir -vp /data/ipphoney/log
  fuDATA_MODES /data/ipphoney
  chown tpot:tpot /data/ipphoney -R
}

# Let's create a function to clean up and prepare log4pot data
fuLOG4POT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/log4pot/*; fi
  mkdir -vp /data/log4pot/{log,payloads}
  fuDATA_MODES /data/log4pot
  chown tpot:tpot /data/log4pot -R
}

# Let's create a function to clean up and prepare mailoney data
fuMAILONEY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/mailoney/*; fi
  mkdir -vp /data/mailoney/log/
  fuDATA_MODES /data/mailoney
  chown tpot:tpot /data/mailoney/ -R
}

# Let's create a function to clean up and prepare mailoney data
fuMEDPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/medpot/*; fi
  mkdir -vp /data/medpot/log/
  fuDATA_MODES /data/medpot
  chown tpot:tpot /data/medpot/ -R
}

# Let's create a function to clean up and prepare miniprint data
fuMINIPRINT () {
  # data/ (the printer identity of this installation) stays, logs and print jobs go
  if [ "$myPERSISTENCE" != "on" ]; then fuREMOVE_ALL_BUT /data/miniprint data; fi
  mkdir -vp /data/miniprint/{data,log,uploads}
  fuDATA_MODES /data/miniprint
  chown tpot:tpot /data/miniprint/ -R
}

# Let's create a function to clean up nginx logs
fuNGINX () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/nginx/log/*; fi
  mkdir -vp /data/nginx/{cert,conf,log}
  touch /data/nginx/log/error.log
  fuDATA_WEB_MODES /data/nginx/conf /data/nginx/cert
  chown tpot:tpot /data/nginx -R
}

# Let's create a function to clean up and prepare rdphoneypot data
fuRDPHONEYPOT () {
  # cert/ (the TLS certificate) stays, the logs go
  if [ "$myPERSISTENCE" != "on" ]; then fuREMOVE_ALL_BUT /data/rdphoneypot cert; fi
  mkdir -vp /data/rdphoneypot/{cert,log}
  fuDATA_MODES /data/rdphoneypot
  chown tpot:tpot /data/rdphoneypot -R
}

# Let's create a function to clean up and prepare redishoneypot data
fuREDISHONEYPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/redishoneypot/log; fi
  mkdir -vp /data/redishoneypot/log
  fuDATA_MODES /data/redishoneypot
  chown tpot:tpot /data/redishoneypot -R
}

# Let's create a function to clean up and prepare sentrypeer data
fuSENTRYPEER () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/sentrypeer/log; fi
  mkdir -vp /data/sentrypeer/log
  fuDATA_MODES /data/sentrypeer
  chown tpot:tpot /data/sentrypeer -R
}

# Let's create a function to clean up and prepare suricata data
fuSURICATA () {
  # rules/ (the rules cache, all there is with SURICATA_RULES_UPDATE=off) stays, the logs go
  if [ "$myPERSISTENCE" != "on" ]; then fuREMOVE_ALL_BUT /data/suricata rules; fi
  mkdir -vp /data/suricata/{log,rules}
  fuDATA_MODES /data/suricata
  chown tpot:tpot -R /data/suricata
}

# Let's create a function to clean up and prepare p0f data
fuP0F () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/p0f/*; fi
  mkdir -vp /data/p0f/log
  fuDATA_MODES /data/p0f
  chown tpot:tpot -R /data/p0f
}

# Let's create a function to clean up and prepare wordpot data
fuWORDPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/wordpot/*; fi
  mkdir -vp /data/wordpot/log/payloads
  fuDATA_MODES /data/wordpot
  chown tpot:tpot /data/wordpot -R
}

# Avoid unwanted cleaning
if [ "$myPERSISTENCE" = "" ];
  then
    echo $myRED"!!! WARNING !!! - This will delete ALL honeypot logs. "$myWHITE
    while [ "$myQST" != "y" ] && [ "$myQST" != "n" ];
      do
        read -p "Continue? (y/n) " myQST
    done
    if [ "$myQST" = "n" ];
      then
        echo $myGREEN"Puuh! That was close! Aborting!"$myWHITE
        exit
    fi
fi

# Check persistence, if enabled compress and rotate logs
if [ "$myPERSISTENCE" = "on" ];
  then
    echo "Persistence enabled for $myPERSISTENCE_CYCLES cycles, now rotating and compressing logs."
    fuLOGROTATE
fi

echo  
echo "Checking and preparing data folders."
fuTPOTINIT
fuADBHONEY
fuBEELZEBUB
fuCISCOASA
fuCITRIXHONEYPOT
fuCONPOT
fuCOWRIE
fuDDOSPOT
fuDICOMPOT
fuDIONAEA
fuELASTICPOT
fuELK
fuENDLESSH
fuGALAH
fuGLUTTON
fuGOPOT
fuH0NEYTR4P
fuHERALDING
fuHELLPOT
fuHONEYAML
fuHONEYSAP
fuHONEYPOTS
fuHONEYTRAP
fuIPPHONEY
fuLOG4POT
fuMAILONEY
fuMEDPOT
fuMINIPRINT
fuNGINX
fuRDPHONEYPOT
fuREDISHONEYPOT
fuSENTRYPEER
fuSURICATA
fuP0F
fuWORDPOT
