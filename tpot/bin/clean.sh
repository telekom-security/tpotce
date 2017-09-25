#!/bin/bash
# T-Pot Container Data Cleaner & Log Rotator

# Set colors
myRED="[0;31m"
myGREEN="[0;32m"
myWHITE="[0;0m"

# Set persistence
myPERSISTENCE=$1

# Let's create a function to check if folder is empty
fuEMPTY () {
  local myFOLDER=$1

echo $(ls $myFOLDER | wc -l)
}

# Let's create a function to rotate and compress logs
fuLOGROTATE () {
  local mySTATUS="/etc/tpot/logrotate/status"
  local myCONF="/etc/tpot/logrotate/logrotate.conf"
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

# Ensure correct permissions and ownerships for logrotate to run without issues
chmod 760 /data/ -R
chown tpot:tpot /data -R

# Run logrotate with force (-f) first, so the status file can be written and race conditions (with tar) be avoided
logrotate -f -s $mySTATUS $myCONF

# Compressing some folders first and rotate them later
if [ "$(fuEMPTY $myCOWRIETTYLOGS)" != "0" ]; then tar cvfz $myCOWRIETTYTGZ $myCOWRIETTYLOGS; fi
if [ "$(fuEMPTY $myCOWRIEDL)" != "0" ]; then tar cvfz $myCOWRIEDLTGZ $myCOWRIEDL; fi
if [ "$(fuEMPTY $myDIONAEABI)" != "0" ]; then tar cvfz $myDIONAEABITGZ $myDIONAEABI; fi
if [ "$(fuEMPTY $myDIONAEABIN)" != "0" ]; then tar cvfz $myDIONAEABINTGZ $myDIONAEABIN; fi
if [ "$(fuEMPTY $myHONEYTRAPATTACKS)" != "0" ]; then tar cvfz $myHONEYTRAPATTACKSTGZ $myHONEYTRAPATTACKS; fi
if [ "$(fuEMPTY $myHONEYTRAPDL)" != "0" ]; then tar cvfz $myHONEYTRAPDLTGZ $myHONEYTRAPDL; fi

# Ensure correct permissions and ownership for previously created archives
chmod 760 $myCOWRIETTYTGZ $myCOWRIEDLTGZ $myDIONAEABITGZ $myDIONAEABINTGZ $myHONEYTRAPATTACKSTGZ $myHONEYTRAPDLTGZ
chown tpot:tpot $myCOWRIETTYTGZ $myCOWRIEDLTGZ $myDIONAEABITGZ $myDIONAEABINTGZ $myHONEYTRAPATTACKSTGZ $myHONEYTRAPDLTGZ

# Need to remove subfolders since too many files cause rm to exit with errors
rm -rf $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myHONEYTRAPATTACKS $myHONEYTRAPDL

# Recreate subfolders with correct permissions and ownership
mkdir -p $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myHONEYTRAPATTACKS $myHONEYTRAPDL
chmod 760 $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myHONEYTRAPATTACKS $myHONEYTRAPDL 
chown tpot:tpot $myCOWRIETTYLOGS $myCOWRIEDL $myDIONAEABI $myDIONAEABIN $myHONEYTRAPATTACKS $myHONEYTRAPDL

# Run logrotate again to account for previously created archives - DO NOT FORCE HERE!
logrotate -s $mySTATUS $myCONF
}

# Let's create a function to clean up and prepare conpot data
fuCONPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/conpot/*; fi
  mkdir -p /data/conpot/log
  chmod 760 /data/conpot -R
  chown tpot:tpot /data/conpot -R
}

# Let's create a function to clean up and prepare cowrie data
fuCOWRIE () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/cowrie/*; fi
  mkdir -p /data/cowrie/log/tty/ /data/cowrie/downloads/ /data/cowrie/keys/ /data/cowrie/misc/
  chmod 760 /data/cowrie -R
  chown tpot:tpot /data/cowrie -R
}

# Let's create a function to clean up and prepare dionaea data
fuDIONAEA () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/dionaea/*; fi
  mkdir -p /data/dionaea/log /data/dionaea/bistreams /data/dionaea/binaries /data/dionaea/rtp /data/dionaea/roots/ftp /data/dionaea/roots/tftp /data/dionaea/roots/www /data/dionaea/roots/upnp
  chmod 760 /data/dionaea -R
  chown tpot:tpot /data/dionaea -R
}

# Let's create a function to clean up and prepare elasticpot data
fuELASTICPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/elasticpot/*; fi
  mkdir -p /data/elasticpot/log
  chmod 760 /data/elasticpot -R
  chown tpot:tpot /data/elasticpot -R
}

# Let's create a function to clean up and prepare elk data
fuELK () {
  # ELK data will be kept for <= 90 days, check /etc/crontab for curator modification
  # ELK daemon log files will be removed
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/elk/log/*; fi
  mkdir -p /data/elk 
  chmod 760 /data/elk -R
  chown tpot:tpot /data/elk -R
}

# Let's create a function to clean up and prepare emobility data
fuEMOBILITY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/emobility/*; fi
  mkdir -p /data/emobility/log
  chmod 760 /data/emobility -R
  chown tpot:tpot /data/emobility -R
}

# Let's create a function to clean up and prepare glastopf data
fuGLASTOPF () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/glastopf/*; fi
  mkdir -p /data/glastopf
  chmod 760 /data/glastopf -R
  chown tpot:tpot /data/glastopf -R
}

# Let's create a function to clean up and prepare honeytrap data
fuHONEYTRAP () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/honeytrap/*; fi
  mkdir -p /data/honeytrap/log/ /data/honeytrap/attacks/ /data/honeytrap/downloads/
  chmod 760 /data/honeytrap/ -R
  chown tpot:tpot /data/honeytrap/ -R
}

# Let's create a function to clean up and prepare mailoney data
fuMAILONEY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/mailoney/*; fi
  mkdir -p /data/mailoney/log/
  chmod 760 /data/mailoney/ -R
  chown tpot:tpot /data/mailoney/ -R
}

# Let's create a function to clean up and prepare rdpy data
fuRDPY () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/rdpy/*; fi
  mkdir -p /data/rdpy/log/
  chmod 760 /data/rdpy/ -R
  chown tpot:tpot /data/rdpy/ -R
}

# Let's create a function to prepare spiderfoot db
fuSPIDERFOOT () {
  mkdir -p /data/spiderfoot
  touch /data/spiderfoot/spiderfoot.db
  chmod 760 -R /data/spiderfoot
  chown tpot:tpot -R /data/spiderfoot
}

# Let's create a function to clean up and prepare suricata data
fuSURICATA () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/suricata/*; fi
  mkdir -p /data/suricata/log
  chmod 760 -R /data/suricata
  chown tpot:tpot -R /data/suricata
}

# Let's create a function to clean up and prepare p0f data
fuP0F () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/p0f/*; fi
  mkdir -p /data/p0f/log
  chmod 760 -R /data/p0f
  chown tpot:tpot -R /data/p0f
}

# Let's create a function to clean up and prepare vnclowpot data
fuVNCLOWPOT () {
  if [ "$myPERSISTENCE" != "on" ]; then rm -rf /data/vnclowpot/*; fi
  mkdir -p /data/vnclowpot/log/
  chmod 760 /data/vnclowpot/ -R
  chown tpot:tpot /data/vnclowpot/ -R
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
    fuCONPOT
    fuCOWRIE
    fuDIONAEA
    fuELASTICPOT
    fuELK
    fuEMOBILITY
    fuGLASTOPF
    fuHONEYTRAP
    fuMAILONEY
    fuRDPY
    fuSPIDERFOOT
    fuSURICATA
    fuP0F
    fuVNCLOWPOT
  fi

