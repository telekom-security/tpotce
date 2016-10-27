#!/bin/bash

########################################################
# T-Pot                                                #
# ELK DB backup script                                 #
#                                                      #
# v16.10.0 by mo, DTAG, 2016-05-12                     #
########################################################
myCOUNT=1
myDATE=$(date +%Y%m%d%H%M)
myELKPATH="/data/elk/"
myBACKUPPATH="/data/"

# Make sure not to interrupt a check
while true
do
  if ! [ -a /var/run/check.lock ];
    then break
  fi
  sleep 0.1
  if [ "$myCOUNT" = "1" ];
    then
      echo -n "Waiting for services "
    else echo -n .
  fi
  if [ "$myCOUNT" = "6000" ];
    then
    echo
    echo "Overriding check.lock"
    rm /var/run/check.lock
    break
  fi
  myCOUNT=$[$myCOUNT +1]
done

# We do not want to get interrupted by a check
touch /var/run/check.lock

# Stop ELK to lift db lock
echo "Now stopping ELK ..."
systemctl stop elk
sleep 10

# Backup DB in 2 flavors
echo "Now backing up Elasticsearch data ..."
tar cvfz $myBACKUPPATH"$myDATE"_elkall.tgz $myELKPATH
rm -rf "$myELKPATH"log/*
rm -rf "$myELKPATH"data/tpotcluster/nodes/0/indices/logstash*
tar cvfz $myBACKUPPATH"$myDATE"_elkbase.tgz $myELKPATH
rm -rf $myELKPATH
tar xvfz $myBACKUPPATH"$myDATE"_elkall.tgz -C /
chmod 760 -R $myELKPATH
chown tpot:tpot -R $myELKPATH

# Start ELK
systemctl start elk
echo "Now starting up ELK ..."

# Allow checks to resume
rm /var/run/check.lock
