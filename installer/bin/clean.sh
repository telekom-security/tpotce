#!/bin/bash

########################################################
# T-Pot                                                #
# Container Data Cleaner                               #
#                                                      #
# v16.10.0 by mo, DTAG, 2016-05-28                     #
########################################################

# Set persistence
myPERSISTENCE=$2

# Check persistence
if [ "$myPERSISTENCE" = "on" ];
  then
    echo "### Persistence enabled, nothing to do."
    exit
fi

# Let's create a function to clean up and prepare conpot data
fuCONPOT () {
  rm -rf /data/conpot/*
  mkdir -p /data/conpot/log
  chmod 760 /data/conpot -R
  chown tpot:tpot /data/conpot -R
}

# Let's create a function to clean up and prepare cowrie data
fuCOWRIE () {
  rm -rf /data/cowrie/*
  mkdir -p /data/cowrie/log/tty/ /data/cowrie/downloads/ /data/cowrie/keys/ /data/cowrie/misc/
  chmod 760 /data/cowrie -R
  chown tpot:tpot /data/cowrie -R
}

# Let's create a function to clean up and prepare dionaea data
fuDIONAEA () {
  rm -rf /data/dionaea/*
  rm /data/ews/dionaea/ews.json
  mkdir -p /data/dionaea/log /data/dionaea/bistreams /data/dionaea/binaries /data/dionaea/rtp /data/dionaea/roots/ftp /data/dionaea/roots/tftp /data/dionaea/roots/www /data/dionaea/roots/upnp
  chmod 760 /data/dionaea -R
  chown tpot:tpot /data/dionaea -R
}

# Let's create a function to clean up and prepare elasticpot data
fuELASTICPOT () {
  rm -rf /data/elasticpot/*
  mkdir -p /data/elasticpot/log
  chmod 760 /data/elasticpot -R
  chown tpot:tpot /data/elasticpot -R
}

# Let's create a function to clean up and prepare elk data
fuELK () {
  # ELK data will be kept for <= 90 days, check /etc/crontab for curator modification
  # ELK daemon log files will be removed
  rm -rf /data/elk/log/*
}

# Let's create a function to clean up and prepare emobility data
fuEMOBILITY () {
  rm -rf /data/emobility/*
  rm /data/ews/emobility/ews.json
  mkdir -p /data/emobility/log /data/ews/emobility
  chmod 760 /data/emobility -R
  chown tpot:tpot /data/emobility -R
}

# Let's create a function to clean up and prepare glastopf data
fuGLASTOPF () {
  rm -rf /data/glastopf/*
  mkdir -p /data/glastopf
  chmod 760 /data/glastopf -R
  chown tpot:tpot /data/glastopf -R
}

# Let's create a function to clean up and prepare honeytrap data
fuHONEYTRAP () {
  rm -rf /data/honeytrap/*
  mkdir -p /data/honeytrap/log/ /data/honeytrap/attacks/ /data/honeytrap/downloads/
  chmod 760 /data/honeytrap/ -R
  chown tpot:tpot /data/honeytrap/ -R
}

# Let's create a function to clean up and prepare suricata data
fuSURICATA () {
  rm -rf /data/suricata/*
  mkdir -p /data/suricata/log
  chmod 760 -R /data/suricata
  chown tpot:tpot -R /data/suricata
  # Get IF, disable offloading, enable promiscious mode
  myIF=$(ip route | grep $(hostname -I | awk '{print $1 }') | awk '{print $3 }')
  /sbin/ethtool --offload $myIF rx off tx off
  /sbin/ethtool -K $myIF gso off gro off
  /sbin/ip link set $myIF promisc on
}

case $1 in
  conpot)
    fuCONPOT $1
  ;;
  cowrie)
    fuCOWRIE $1
  ;;
  dionaea)
    fuDIONAEA $1
  ;;
  elasticpot)
    fuELASTICPOT $1
  ;;
  elk)
    fuELK $1
  ;;
  emobility)
    fuEMOBILITY $1
  ;;
  glastopf)
    fuGLASTOPF $1
  ;;
  honeytrap)
    fuHONEYTRAP $1
  ;;
  suricata)
    fuSURICATA $1
  ;;
esac
