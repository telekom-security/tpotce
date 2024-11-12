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
  local myMINIPRINTU="/data/miniprint/uploads/"
  local myMINIPRINTTGZ="/data/miniprint/uploads.tgz"
  local myTANNERF="/data/tanner/files/"
  local myTANNERFTGZ="/data/tanner/files.tgz"

# Ensure correct permissions and ownerships for logrotate to run without issues
chmod 770 /data/ -R
chown tpot:tpot /data -R
chmod 774 /data/nginx/conf -R
chmod 774 /data/nginx/cert -R

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
if [ "$(fuEMPTY $myMINIPRINTU)" != "0" ]; then tar -I $myPIGZ -cvf $myMINIPRINTTGZ $myMINIPRINTU; fi
if [ "$(fuEMPTY $myTANNERF)" != "0" ]; then tar -I $myPIGZ -cvf $myTANNERFTGZ $myTANNERF; fi

# Ensure correct permissions and ownership for previously created archives
chmod 770 $myADBHONEYTGZ $myCOWRIETTYTGZ $myCOWRIEDLTGZ $myDIONAEABITGZ $myDIONAEABINTGZ $myH0NEYTR4PTGZ $myHONEYTRAPATTACKSTGZ $myHONEYTRAPDLTGZ $myMINIPRINTTGZ $myTANNERFTGZ
chown tpot:tpot $myADBHONEYTGZ $myCOWRIETTYTGZ $myCOWRIEDLTGZ $myDIONAEABITGZ $myDIONAEABINTGZ $myH0NEYTR4PTGZ $myHONEYTRAPATTACKSTGZ $myHONEYTRAPDLTGZ $myMINIPRINTTGZ $myTANNERFTGZ

# Need to remove subfolders since too many files cause rm to exit with errors
rm -rf $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myH0NEYTR4PP $myHONEYTRAPATTACKS $myHONEYTRAPDL $myMINIPRINTU $myTANNERF

# Recreate subfolders with correct permissions and ownership
mkdir -p $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myH0NEYTR4PP $myHONEYTRAPATTACKS $myHONEYTRAPDL $myMINIPRINTU $myTANNERF
chmod 770 $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myH0NEYTR4PP $myHONEYTRAPATTACKS $myHONEYTRAPDL $myMINIPRINTU $myTANNERF
chown tpot:tpot $myADBHONEYDL $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myH0NEYTR4PP $myHONEYTRAPATTACKS $myHONEYTRAPDL $myMINIPRINTU $myTANNERF

# Run logrotate again to account for previously created archives - DO NOT FORCE HERE!
logrotate -s $mySTATUS $myCONF
}

# Let's create a function to clean up and prepare tpotinit data
fuTPOTINIT () {
  mkdir -vp /data/ews/conf \
            /data/tpot/etc/{compose,logrotate} \
            /tmp/etc/
  chmod 770 /data/ews/ -R
  chmod 770 /data/tpot/ -R
  chmod 770 /tmp/etc/ -R
  chown tpot:tpot /data/ews/ -R
  chown tpot:tpot /data/tpot/ -R
  chown tpot:tpot /tmp/etc/ -R
}

# Let's create a function to clean up and prepare adbhoney data
fuADBHONEY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/adbhoney/*; fi
  mkdir -vp /data/adbhoney/{downloads,log}
  chmod 770 /data/adbhoney/ -R
  chown tpot:tpot /data/adbhoney/ -R
}

# Let's create a function to clean up and prepare beelzebub data
fuBEELZEBUB () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/beelzebub/*; fi
  mkdir -vp /data/beelzebub/{key,log}
  chmod 770 /data/beelzebub/ -R
  chown tpot:tpot /data/beelzebub/ -R
}

# Let's create a function to clean up and prepare ciscoasa data
fuCISCOASA () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/ciscoasa/*; fi
  mkdir -vp /data/ciscoasa/log
  chmod 770 /data/ciscoasa -R
  chown tpot:tpot /data/ciscoasa -R
}

# Let's create a function to clean up and prepare citrixhoneypot data
fuCITRIXHONEYPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/citrixhoneypot/*; fi
  mkdir -vp /data/citrixhoneypot/log/
  chmod 770 /data/citrixhoneypot/ -R
  chown tpot:tpot /data/citrixhoneypot/ -R
}

# Let's create a function to clean up and prepare conpot data
fuCONPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/conpot/*; fi
  mkdir -vp /data/conpot/log
  chmod 770 /data/conpot -R
  chown tpot:tpot /data/conpot -R
}

# Let's create a function to clean up and prepare cowrie data
fuCOWRIE () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/cowrie/*; fi
  mkdir -vp /data/cowrie/{downloads,keys,misc,log,log/tty}
  chmod 770 /data/cowrie -R
  chown tpot:tpot /data/cowrie -R
}

# Let's create a function to clean up and prepare ddospot data
fuDDOSPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/ddospot/log; fi
  mkdir -vp /data/ddospot/{bl,db,log}
  chmod 770 /data/ddospot -R
  chown tpot:tpot /data/ddospot -R
}

# Let's create a function to clean up and prepare dicompot data
fuDICOMPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/dicompot/log; fi
  mkdir -vp /data/dicompot/{images,log}
  chmod 770 /data/dicompot -R
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
  chmod 770 /data/dionaea -R
  chown tpot:tpot /data/dionaea -R
}

# Let's create a function to clean up and prepare elasticpot data
fuELASTICPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/elasticpot/*; fi
  mkdir -vp /data/elasticpot/log
  chmod 770 /data/elasticpot -R
  chown tpot:tpot /data/elasticpot -R
}

# Let's create a function to clean up and prepare elk data
fuELK () {
  # ELK data will be kept for <= 90 days, check /etc/crontab for curator modification
  # ELK daemon log files will be removed
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/elk/log/*; fi
  mkdir -vp /data/elk/{data,log}
  chmod 770 /data/elk -R
  chown tpot:tpot /data/elk -R
}

# Let's create a function to clean up and prepare endlessh data
fuENDLESSH () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/endlessh/log; fi
  mkdir -vp /data/endlessh/log
  chmod 770 /data/endlessh -R
  chown tpot:tpot /data/endlessh -R
}

# Let's create a function to clean up and prepare fatt data
fuFATT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/fatt/*; fi
  mkdir -vp /data/fatt/log
  chmod 770 -R /data/fatt
  chown tpot:tpot -R /data/fatt
}

# Let's create a function to clean up and prepare galah data
fuGALAH () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/galah/*; fi
  mkdir -vp /data/galah/{cache,cert,log}
  chmod 770 /data/galah/ -R
  chown tpot:tpot /data/galah/ -R
}

# Let's create a function to clean up and prepare glutton data
fuGLUTTON () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/glutton/*; fi
  mkdir -vp /data/glutton/{log,payloads}
  chmod 770 /data/glutton -R
  chown tpot:tpot /data/glutton -R
}

# Let's create a function to clean up and prepare go-pot data
fuGOPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/go-pot/*; fi
  mkdir -vp /data/go-pot/log
  chmod 770 /data/go-pot -R
  chown tpot:tpot /data/go-pot -R
}

# Let's create a function to clean up and prepare h0neytr4p data
fuH0NEYTR4P () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/h0neytr4p/*; fi
  mkdir -vp /data/h0neytr4p/{log,payloads}
  chmod 770 /data/h0neytr4p/ -R
  chown tpot:tpot /data/h0neytr4p/ -R
}

# Let's create a function to clean up and prepare hellpot data
fuHELLPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/hellpot/log; fi
  mkdir -vp /data/hellpot/log
  chmod 770 /data/hellpot -R
  chown tpot:tpot /data/hellpot -R
}

# Let's create a function to clean up and prepare heralding data
fuHERALDING () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/heralding/*; fi
  mkdir -vp /data/heralding/log
  chmod 770 /data/heralding -R
  chown tpot:tpot /data/heralding -R
}

# Let's create a function to clean up and prepare honeyaml data
fuHONEYAML () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeyaml/*; fi
  mkdir -vp /data/honeyaml/log
  chmod 770 -R /data/honeyaml
  chown tpot:tpot -R /data/honeyaml
}

# Let's create a function to clean up and prepare honeypots data
fuHONEYPOTS () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeypots/*; fi
  mkdir -vp /data/honeypots/log
  chmod 770 /data/honeypots -R
  chown tpot:tpot /data/honeypots -R
}

# Let's create a function to clean up and prepare honeysap data
fuHONEYSAP () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeysap/*; fi
  mkdir -vp /data/honeysap/log
  chmod 770 /data/honeysap -R
  chown tpot:tpot /data/honeysap -R
}

# Let's create a function to clean up and prepare honeytrap data
fuHONEYTRAP () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeytrap/*; fi
  mkdir -vp /data/honeytrap/{log,attacks,downloads}
  chmod 770 /data/honeytrap/ -R
  chown tpot:tpot /data/honeytrap/ -R
}

# Let's create a function to clean up and prepare ipphoney data
fuIPPHONEY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/ipphoney/*; fi
  mkdir -vp /data/ipphoney/log
  chmod 770 /data/ipphoney -R
  chown tpot:tpot /data/ipphoney -R
}

# Let's create a function to clean up and prepare log4pot data
fuLOG4POT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/log4pot/*; fi
  mkdir -vp /data/log4pot/{log,payloads}
  chmod 770 /data/log4pot -R
  chown tpot:tpot /data/log4pot -R
}

# Let's create a function to clean up and prepare mailoney data
fuMAILONEY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/mailoney/*; fi
  mkdir -vp /data/mailoney/log/
  chmod 770 /data/mailoney/ -R
  chown tpot:tpot /data/mailoney/ -R
}

# Let's create a function to clean up and prepare mailoney data
fuMEDPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/medpot/*; fi
  mkdir -vp /data/medpot/log/
  chmod 770 /data/medpot/ -R
  chown tpot:tpot /data/medpot/ -R
}

# Let's create a function to clean up and prepare miniprint data
fuMINIPRINT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/miniprint/*; fi
  mkdir -vp /data/miniprint/{log,uploads}
  chmod 770 /data/miniprint/ -R
  chown tpot:tpot /data/miniprint/ -R
}

# Let's create a function to clean up nginx logs
fuNGINX () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/nginx/log/*; fi
  mkdir -vp /data/nginx/{cert,conf,log}
  touch /data/nginx/log/error.log
  chmod 774 /data/nginx/conf -R
  chmod 774 /data/nginx/cert -R
  chown tpot:tpot /data/nginx -R
}

# Let's create a function to clean up and prepare redishoneypot data
fuREDISHONEYPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/redishoneypot/log; fi
  mkdir -vp /data/redishoneypot/log
  chmod 770 /data/redishoneypot -R
  chown tpot:tpot /data/redishoneypot -R
}

# Let's create a function to clean up and prepare sentrypeer data
fuSENTRYPEER () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/sentrypeer/log; fi
  mkdir -vp /data/sentrypeer/log
  chmod 770 /data/sentrypeer -R
  chown tpot:tpot /data/sentrypeer -R
}

# Let's create a function to prepare spiderfoot db
fuSPIDERFOOT () {
  mkdir -vp /data/spiderfoot
  touch /data/spiderfoot/spiderfoot.db
  chmod 770 -R /data/spiderfoot
  chown tpot:tpot -R /data/spiderfoot
}

# Let's create a function to clean up and prepare suricata data
fuSURICATA () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/suricata/*; fi
  mkdir -vp /data/suricata/log
  chmod 770 -R /data/suricata
  chown tpot:tpot -R /data/suricata
}

# Let's create a function to clean up and prepare p0f data
fuP0F () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/p0f/*; fi
  mkdir -vp /data/p0f/log
  chmod 770 -R /data/p0f
  chown tpot:tpot -R /data/p0f
}

# Let's create a function to clean up and prepare p0f data
fuTANNER () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/tanner/*; fi
  mkdir -vp /data/tanner/{log,files}
  chmod 770 -R /data/tanner
  chown tpot:tpot -R /data/tanner
}

# Let's create a function to clean up and prepare wordpot data
fuWORDPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/wordpot/log; fi
  mkdir -vp /data/wordpot/log
  chmod 770 /data/wordpot -R
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
    echo "Persistence enabled, now rotating and compressing logs."
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
fuFATT
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
fuREDISHONEYPOT
fuSENTRYPEER
fuSPIDERFOOT
fuSURICATA
fuP0F
fuTANNER
fuWORDPOT
