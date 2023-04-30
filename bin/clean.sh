#!/bin/bash
# T-Pot Container Data Cleaner & Log Rotator
# Set colors
myRED="[0;31m"
myGREEN="[0;32m"
myWHITE="[0;0m"

# Set pigz
myPIGZ=$(which pigz)

# Set persistence
myPERSISTENCE=$1

# Let's create a function to check if folder is empty
fuEMPTY () {
  local myFOLDER=$1

echo $(ls $myFOLDER | wc -l)
}

# Let's create a function to rotate and compress logs
fuLOGROTATE () {
  local mySTATUS="/opt/tpot/etc/logrotate/status"
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
  local myHONEYTRAPATTACKS="/data/honeytrap/attacks/"
  local myHONEYTRAPATTACKSTGZ="/data/honeytrap/attacks.tgz"
  local myHONEYTRAPDL="/data/honeytrap/downloads/"
  local myHONEYTRAPDLTGZ="/data/honeytrap/downloads.tgz"
  local myTANNERF="/data/tanner/files/"
  local myTANNERFTGZ="/data/tanner/files.tgz"

# Ensure correct permissions and ownerships for logrotate to run without issues
chmod 770 /data/ -R
chown tpot:tpot /data -R
chmod 644 /data/nginx/conf -R
chmod 644 /data/nginx/cert -R

# Run logrotate with force (-f) first, so the status file can be written and race conditions (with tar) be avoided
logrotate -f -s $mySTATUS $myCONF

# Compressing some folders first and rotate them later
if [ "$(fuEMPTY $myADBHONEYDL)" != "0" ]; then tar -I $myPIGZ -cvf $myADBHONEYTGZ $myADBHONEYDL; fi
if [ "$(fuEMPTY $myCOWRIETTYLOGS)" != "0" ]; then tar -I $myPIGZ -cvf $myCOWRIETTYTGZ $myCOWRIETTYLOGS; fi
if [ "$(fuEMPTY $myCOWRIEDL)" != "0" ]; then tar -I $myPIGZ -cvf $myCOWRIEDLTGZ $myCOWRIEDL; fi
if [ "$(fuEMPTY $myDIONAEABI)" != "0" ]; then tar -I $myPIGZ -cvf $myDIONAEABITGZ $myDIONAEABI; fi
if [ "$(fuEMPTY $myDIONAEABIN)" != "0" ]; then tar -I $myPIGZ -cvf $myDIONAEABINTGZ $myDIONAEABIN; fi
if [ "$(fuEMPTY $myHONEYTRAPATTACKS)" != "0" ]; then tar -I $myPIGZ -cvf $myHONEYTRAPATTACKSTGZ $myHONEYTRAPATTACKS; fi
if [ "$(fuEMPTY $myHONEYTRAPDL)" != "0" ]; then tar -I $myPIGZ -cvf $myHONEYTRAPDLTGZ $myHONEYTRAPDL; fi
if [ "$(fuEMPTY $myTANNERF)" != "0" ]; then tar -I $myPIGZ -cvf $myTANNERFTGZ $myTANNERF; fi

# Ensure correct permissions and ownership for previously created archives
chmod 770 $myADBHONEYTGZ $myCOWRIETTYTGZ $myCOWRIEDLTGZ $myDIONAEABITGZ $myDIONAEABINTGZ $myHONEYTRAPATTACKSTGZ $myHONEYTRAPDLTGZ $myTANNERFTGZ
chown tpot:tpot $myADBHONEYTGZ $myCOWRIETTYTGZ $myCOWRIEDLTGZ $myDIONAEABITGZ $myDIONAEABINTGZ $myHONEYTRAPATTACKSTGZ $myHONEYTRAPDLTGZ $myTANNERFTGZ

# Need to remove subfolders since too many files cause rm to exit with errors
rm -rf $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myHONEYTRAPATTACKS $myHONEYTRAPDL $myTANNERF

# Recreate subfolders with correct permissions and ownership
mkdir -p $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myHONEYTRAPATTACKS $myHONEYTRAPDL $myTANNERF
chmod 770 $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myHONEYTRAPATTACKS $myHONEYTRAPDL $myTANNERF
chown tpot:tpot $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myHONEYTRAPATTACKS $myHONEYTRAPDL $myTANNERF

# Run logrotate again to account for previously created archives - DO NOT FORCE HERE!
logrotate -s $mySTATUS $myCONF
}

# Let's create a function to clean up and prepare honeytrap data
fuADBHONEY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/adbhoney/*; fi
  mkdir -p /data/adbhoney/log/ /data/adbhoney/downloads/
  chmod 770 /data/adbhoney/ -R
  chown tpot:tpot /data/adbhoney/ -R
}

# Let's create a function to clean up and prepare ciscoasa data
fuCISCOASA () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/ciscoasa/*; fi
  mkdir -p /data/ciscoasa/log
  chmod 770 /data/ciscoasa -R
  chown tpot:tpot /data/ciscoasa -R
}

# Let's create a function to clean up and prepare citrixhoneypot data
fuCITRIXHONEYPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/citrixhoneypot/*; fi
  mkdir -p /data/citrixhoneypot/logs/
  chmod 770 /data/citrixhoneypot/ -R
  chown tpot:tpot /data/citrixhoneypot/ -R
}

# Let's create a function to clean up and prepare conpot data
fuCONPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/conpot/*; fi
  mkdir -p /data/conpot/log
  chmod 770 /data/conpot -R
  chown tpot:tpot /data/conpot -R
}

# Let's create a function to clean up and prepare cowrie data
fuCOWRIE () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/cowrie/*; fi
  mkdir -p /data/cowrie/log/tty/ /data/cowrie/downloads/ /data/cowrie/keys/ /data/cowrie/misc/
  chmod 770 /data/cowrie -R
  chown tpot:tpot /data/cowrie -R
}

# Let's create a function to clean up and prepare ddospot data
fuDDOSPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/ddospot/log; fi
  mkdir -p /data/ddospot/bl /data/ddospot/db /data/ddospot/log
  chmod 770 /data/ddospot -R
  chown tpot:tpot /data/ddospot -R
}

# Let's create a function to clean up and prepare dicompot data
fuDICOMPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/dicompot/log; fi
  mkdir -p /data/dicompot/log
  mkdir -p /data/dicompot/images
  chmod 770 /data/dicompot -R
  chown tpot:tpot /data/dicompot -R
}

# Let's create a function to clean up and prepare dionaea data
fuDIONAEA () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/dionaea/*; fi
  mkdir -p /data/dionaea/log /data/dionaea/bistreams /data/dionaea/binaries /data/dionaea/rtp /data/dionaea/roots/ftp /data/dionaea/roots/tftp /data/dionaea/roots/www /data/dionaea/roots/upnp
  chmod 770 /data/dionaea -R
  chown tpot:tpot /data/dionaea -R
}

# Let's create a function to clean up and prepare elasticpot data
fuELASTICPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/elasticpot/*; fi
  mkdir -p /data/elasticpot/log
  chmod 770 /data/elasticpot -R
  chown tpot:tpot /data/elasticpot -R
}

# Let's create a function to clean up and prepare elk data
fuELK () {
  # ELK data will be kept for <= 90 days, check /etc/crontab for curator modification
  # ELK daemon log files will be removed
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/elk/log/*; fi
  mkdir -p /data/elk
  chmod 770 /data/elk -R
  chown tpot:tpot /data/elk -R
}

# Let's create a function to clean up and prepare endlessh data
fuENDLESSH () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/endlessh/log; fi
  mkdir -p /data/endlessh/log
  chmod 770 /data/endlessh -R
  chown tpot:tpot /data/endlessh -R
}

# Let's create a function to clean up and prepare fatt data
fuFATT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/fatt/*; fi
  mkdir -p /data/fatt/log
  chmod 770 -R /data/fatt
  chown tpot:tpot -R /data/fatt
}

# Let's create a function to clean up and prepare glastopf data
fuGLUTTON () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/glutton/*; fi
  mkdir -p /data/glutton/log
  chmod 770 /data/glutton -R
  chown tpot:tpot /data/glutton -R
}

# Let's create a function to clean up and prepare hellpot data
fuHELLPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/hellpot/log; fi
  mkdir -p /data/hellpot/log
  chmod 770 /data/hellpot -R
  chown tpot:tpot /data/hellpot -R
}

# Let's create a function to clean up and prepare heralding data
fuHERALDING () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/heralding/*; fi
  mkdir -p /data/heralding/log
  chmod 770 /data/heralding -R
  chown tpot:tpot /data/heralding -R
}

# Let's create a function to clean up and prepare honeypots data
fuHONEYPOTS () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeypots/*; fi
  mkdir -p /data/honeypots/log
  chmod 770 /data/honeypots -R
  chown tpot:tpot /data/honeypots -R
}

# Let's create a function to clean up and prepare honeysap data
fuHONEYSAP () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeysap/*; fi
  mkdir -p /data/honeysap/log
  chmod 770 /data/honeysap -R
  chown tpot:tpot /data/honeysap -R
}

# Let's create a function to clean up and prepare honeytrap data
fuHONEYTRAP () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeytrap/*; fi
  mkdir -p /data/honeytrap/log/ /data/honeytrap/attacks/ /data/honeytrap/downloads/
  chmod 770 /data/honeytrap/ -R
  chown tpot:tpot /data/honeytrap/ -R
}

# Let's create a function to clean up and prepare ipphoney data
fuIPPHONEY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/ipphoney/*; fi
  mkdir -p /data/ipphoney/log
  chmod 770 /data/ipphoney -R
  chown tpot:tpot /data/ipphoney -R
}

# Let's create a function to clean up and prepare log4pot data
fuLOG4POT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/log4pot/*; fi
  mkdir -p /data/log4pot/log
  chmod 770 /data/log4pot -R
  chown tpot:tpot /data/log4pot -R
}

# Let's create a function to clean up and prepare mailoney data
fuMAILONEY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/mailoney/*; fi
  mkdir -p /data/mailoney/log/
  chmod 770 /data/mailoney/ -R
  chown tpot:tpot /data/mailoney/ -R
}

# Let's create a function to clean up and prepare mailoney data
fuMEDPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/medpot/*; fi
  mkdir -p /data/medpot/log/
  chmod 770 /data/medpot/ -R
  chown tpot:tpot /data/medpot/ -R
}

# Let's create a function to clean up nginx logs
fuNGINX () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/nginx/log/*; fi
  touch /data/nginx/log/error.log
  chmod 644 /data/nginx/conf -R
  chmod 644 /data/nginx/cert -R
}

# Let's create a function to clean up and prepare rdpy data
fuRDPY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/rdpy/*; fi
  mkdir -p /data/rdpy/log/
  chmod 770 /data/rdpy/ -R
  chown tpot:tpot /data/rdpy/ -R
}

# Let's create a function to clean up and prepare redishoneypot data
fuREDISHONEYPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/redishoneypot/log; fi
  mkdir -p /data/redishoneypot/log
  chmod 770 /data/redishoneypot -R
  chown tpot:tpot /data/redishoneypot -R
}

# Let's create a function to clean up and prepare sentrypeer data
fuSENTRYPEER () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/sentrypeer/log; fi
  mkdir -p /data/sentrypeer/log
  chmod 770 /data/sentrypeer -R
  chown tpot:tpot /data/sentrypeer -R
}

# Let's create a function to prepare spiderfoot db
fuSPIDERFOOT () {
  mkdir -p /data/spiderfoot
  touch /data/spiderfoot/spiderfoot.db
  chmod 770 -R /data/spiderfoot
  chown tpot:tpot -R /data/spiderfoot
}

# Let's create a function to clean up and prepare suricata data
fuSURICATA () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/suricata/*; fi
  mkdir -p /data/suricata/log
  chmod 770 -R /data/suricata
  chown tpot:tpot -R /data/suricata
}

# Let's create a function to clean up and prepare p0f data
fuP0F () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/p0f/*; fi
  mkdir -p /data/p0f/log
  chmod 770 -R /data/p0f
  chown tpot:tpot -R /data/p0f
}

# Let's create a function to clean up and prepare p0f data
fuTANNER () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/tanner/*; fi
  mkdir -p /data/tanner/log /data/tanner/files
  chmod 770 -R /data/tanner
  chown tpot:tpot -R /data/tanner
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
    echo "Persistence enabled, now rotating and compressing logs."
    fuLOGROTATE
  else
    echo "Cleaning up and preparing data folders."
    fuADBHONEY
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
    fuFATT
    fuGLUTTON
    fuHERALDING
    fuHELLPOT
    fuHONEYSAP
    fuHONEYPOTS
    fuHONEYTRAP
    fuIPPHONEY
    fuLOG4POT
    fuMAILONEY
    fuMEDPOT
    fuNGINX
    fuREDISHONEYPOT
    fuRDPY
    fuSENTRYPEER
    fuSPIDERFOOT
    fuSURICATA
    fuP0F
    fuTANNER
  fi
