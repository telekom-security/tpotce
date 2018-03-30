#!/bin/bash

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
    wget --tries=2 --timeout=2 https://rules.emergingthreats.net/open/suricata-4.0/emerging.rules.tar.gz -O /tmp/rules.tar.gz
  else
    if [ "$myOINKCODE" != "" ];
      then
	echo "Downloading ET pro ruleset with Oinkcode $myOINKCODE."
	wget --tries=2 --timeout=2 https://rules.emergingthreatspro.com/$myOINKCODE/suricata-4.0/etpro.rules.tar.gz -O /tmp/rules.tar.gz
      else	
        echo "Usage: update.sh <[OPEN, OINKCODE]>"
	exit
    fi	
fi
}

# Download rules
fuDLRULES

# Extract and enable all rules  
tar xvfz /tmp/rules.tar.gz -C /etc/suricata/
sed -i s/^#alert/alert/ /etc/suricata/rules/*.rules
