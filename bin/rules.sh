#!/bin/bash

### Vars, Ports for Standard services
myHOSTPORTS="7634 64295"
myDOCKERCOMPOSEYML="$1"
myRULESFUNCTION="$2"

function fuCHECKFORARGS {
### Check if args are present, if not throw error

if [ "$myDOCKERCOMPOSEYML" != "" ] && ([ "$myRULESFUNCTION" == "set" ] || [ "$myRULESFUNCTION" == "unset" ]);
  then
    echo "All arguments met. Continuing."
  else
    echo "Usage: rules.sh <docker-compose.yml> <[set, unset]>"
    exit
fi
}

function fuNFQCHECK {
### Check if honeytrap or glutton is actively enabled in docker-compose.yml
	
myNFQCHECK=$(grep -e '^\s*honeytrap:\|^\s*glutton:' $myDOCKERCOMPOSEYML | tr -d ': ' | wc -l)
if [ "$myNFQCHECK" == "0" ];
  then
    echo "No NFQ related honeypot detected, no firewall rules needed. Exiting."
    exit
  else
    echo "Detected at least one NFQ based honeypot, firewall rules needed. Continuing."
fi
}

function fuGETPORTS {
### Get ports from docker-compose.yml
	
myDOCKERCOMPOSEPORTS=$(cat $myDOCKERCOMPOSEYML | yq -r '.services[].ports' | grep ':' | sed -e s/127.0.0.1// | tr -d '", ' | sed -e s/^:// | cut -f1 -d ':' )
myDOCKERCOMPOSEPORTS+=" $myHOSTPORTS"
myRULESPORTS=$(for i in $myDOCKERCOMPOSEPORTS; do echo $i; done | sort -gu)
}

function fuSETRULES {
### Setting up iptables rules

/sbin/iptables -w -A INPUT -s 127.0.0.1 -j ACCEPT
/sbin/iptables -w -A INPUT -d 127.0.0.1 -j ACCEPT

for myPORT in $myRULESPORTS; do
  /sbin/iptables -w -A INPUT -p tcp --dport $myPORT -j ACCEPT
done
  
/sbin/iptables -w -A INPUT -p tcp --syn -m state --state NEW -j NFQUEUE
}

function fuUNSETRULES {
### Removing iptables rules

/sbin/iptables -w -D INPUT -s 127.0.0.1 -j ACCEPT
/sbin/iptables -w -D INPUT -d 127.0.0.1 -j ACCEPT

for myPORT in $myRULESPORTS; do
  /sbin/iptables -w -D INPUT -p tcp --dport $myPORT -j ACCEPT
done

/sbin/iptables -w -D INPUT -p tcp --syn -m state --state NEW -j NFQUEUE
}

# Main
fuCHECKFORARGS
fuNFQCHECK
fuGETPORTS

if [ "$myRULESFUNCTION" == "set" ];
  then
    fuSETRULES
  else
    fuUNSETRULES
fi
