#!/bin/bash

# Setup Vars
myPLATFORMS="linux/amd64,linux/arm64"
myHUBORG="dtagdevsec"
myTAG="2204"
myIMAGESBASE="adbhoney ciscoasa citrixhoneypot conpot cowrie ddospot dicompot dionaea elasticpot endlessh ewsposter fatt glutton hellpot heralding honeypots honeytrap ipphoney log4pot mailoney medpot nginx p0f redishoneypot sentrypeer spiderfoot suricata wordpot"
myIMAGESELK="elasticsearch kibana logstash map"
myIMAGESTANNER="phpox redis snare tanner"
myBUILDERLOG="builder.log"
myBUILDERERR="builder.err"
myBUILDCACHE="/buildcache"

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    exit
fi

# Check for Buildx
docker buildx > /dev/null 2>&1 
if [ "$?" == "1" ];
  then
    echo "### Build environment not setup. Run bin/setup_builder.sh"
fi

# Only run with command switch
if [ "$1" == "" ]; then
  echo "### T-Pot Multi Arch Image Builder."
  echo "## Usage: builder.sh [build, push]"
  echo "## build - Just build images, do not push."
  echo "## push - Build and push images."
  echo "## Pushing requires an active docker login."
  exit
fi

fuBUILDIMAGES () {
local myPATH="$1"
local myIMAGELIST="$2"
local myPUSHOPTION="$3"

for myREPONAME in $myIMAGELIST;
  do
    echo -n "Now building: $myREPONAME in $myPATH$myREPONAME/."
    docker buildx build --cache-from "type=local,src=$myBUILDCACHE" --cache-to "type=local,dest=$myBUILDCACHE" --platform $myPLATFORMS -t $myHUBORG/$myREPONAME:$myTAG $myPUSHOPTION $myPATH$myREPONAME/. >> $myBUILDERLOG 2>&1
    if [ "$?" != "0" ];
      then
	echo " [ ERROR ] - Check logs!"
	echo "Error building $myREPONAME" >> "$myBUILDERERR"
      else
	echo " [ OK ]"
    fi
done
}

# Just build images
if [ "$1" == "build" ];
  then
    mkdir -p $myBUILDCACHE
    rm -f "$myBUILDERLOG" "$myBUILDERERR" 
    echo "### Building images ..."
    fuBUILDIMAGES "" "$myIMAGESBASE" ""
    fuBUILDIMAGES "elk/" "$myIMAGESELK" ""
    fuBUILDIMAGES "tanner/" "$myIMAGESTANNER" ""
fi

# Build and push images
if [ "$1" == "push" ];
  then
    mkdir -p $myBUILDCACHE
    rm -f "$myBUILDERLOG" "$myBUILDERERR" 
    echo "### Building and pushing images ..."
    fuBUILDIMAGES "" "$myIMAGESBASE" "--push"
    fuBUILDIMAGES "elk/" "$myIMAGESELK" "--push"
    fuBUILDIMAGES "tanner/" "$myIMAGESTANNER" "--push"
fi

