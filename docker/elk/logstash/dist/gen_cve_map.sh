#!/bin/bash
myURL="https://rules.emergingthreats.net/open/suricata-4.0/rules/sid-msg.map"
myRULESFILE="sid-msg.map"
myCVEMAP="cve.yaml"

# Clear cve map
rm $myCVEMAP

# Download SID map from ET if server offers newer file
wget -N $myURL
myRULESCOUNT=$(wc -l < $myRULESFILE)

# Just extract rules with CVE ID, for proper matching we also need SID
let i=0
let j=0
while read -r myRULE
do
  (( ++i ))
  echo -ne "Processing rules, please be patient ($i / $myRULESCOUNT)\r"
  myCVE=$(echo $myRULE | grep -o -E "(cve,|CVE-|CAN-)([0-9]{4}-([0-9]{4}|[0-9]{5}))" | tr a-z A-Z | tr ",|-" " " | awk '{ print $1"-"$2"-"$3 }')
  if [ "$myCVE" != "" ]
    then
      mySID=$(echo $myRULE | awk '{ print $1 }')
      echo \"$mySID\": \"$myCVE\" >> $myCVEMAP
      (( ++j ))
  fi
done < "$myRULESFILE"
echo
echo "Done. $j CVE IDs have been mapped."

# Clean up
rm $myRULESFILE
