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

function fuENRULES {
  # Cleanup old files and extract new files.
  rm -rf /tmp/rules /tmp/tpotce.rules
  tar xfz /tmp/rules.tar.gz -C /tmp/ 2>&1 > /dev/null
  # Create the new ruleset by:
  # - looping through rule files, except deleted ones;
  # - enabling all disabled rules (performance should be OK);
  # - removing unnecessary empty/comment lines.
  ls /tmp/rules/*.rules | grep -v deleted.rules | while read f;
    do
      cat $f | sed "s/^#alert/alert/" | grep -Ev "^(#|$)" >> /tmp/tpotce.rules
    done
  # Copy the new ruleset and config to where they belong.
  cp -f /tmp/tpotce.rules /tmp/rules/classification.config /etc/suricata/rules
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
    fuENRULES 2>&1 > /dev/null
    echo "/etc/suricata/capture-filter.bpf"
  else
    echo "/etc/suricata/null.bpf"
fi
