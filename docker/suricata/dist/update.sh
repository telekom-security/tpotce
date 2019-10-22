#!/bin/ash

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  exit 0
}
trap fuCLEANUP EXIT

### Vars
myOINKCODE="$1"

function fuDLRULES {
### Check if args are present then download rules, if not throw error
if [ "$myOINKCODE" != "" ] && [ "$myOINKCODE" == "OPEN" ];
  then
    echo "Downloading ET open ruleset."
    wget -q --tries=2 --timeout=2 https://rules.emergingthreats.net/open/suricata-5.0/emerging.rules.tar.gz -O /tmp/rules.tar.gz
  else
    if [ "$myOINKCODE" != "" ];
      then
	echo "Downloading ET pro ruleset with Oinkcode $myOINKCODE."
	wget -q --tries=2 --timeout=2 https://rules.emergingthreatspro.com/$myOINKCODE/suricata-5.0/etpro.rules.tar.gz -O /tmp/rules.tar.gz
      else	
        echo "Usage: update.sh <[OPEN, OINKCODE]>"
	exit
    fi	
fi
}

# Check internet availability 
function fuCHECKINET () {
mySITES=$1
error=0
for i in $mySITES;
  do
    curl --connect-timeout 5 -Is $i 2>&1 > /dev/null
      if [ $? -ne 0 ];
        then
	  let error+=1
      fi;
  done;
  echo $error
}

# Check for connectivity and download rules
myCHECK=$(fuCHECKINET "rules.emergingthreatspro.com rules.emergingthreats.net")
if [ "$myCHECK" == "0" ];
  then
    fuDLRULES 2>&1 > /dev/null
    tar xvfz /tmp/rules.tar.gz -C /etc/suricata/ 2>&1 > /dev/null
    sed -i s/^#alert/alert/ /etc/suricata/rules/*.rules 2>&1 > /dev/null
    echo "/etc/suricata/capture-filter.bpf"
  else
    echo "/etc/suricata/null.bpf"
fi
