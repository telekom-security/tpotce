#!/bin/bash

### Vars, Ports for Standard services
myHOSTPORTS="7634 64294 64295 64297 64304"
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

myNFQCHECK=$(grep -e '^\s*honeytrap:\|^\s*glutton:' $myDOCKERCOMPOSEYML | tr -d ': ' | uniq)
if [ "$myNFQCHECK" == "" ];
  then
    echo "No NFQ related honeypot detected, no iptables-legacy rules needed. Exiting."
    exit
  else
    echo "Detected $myNFQCHECK as NFQ based honeypot, iptables-legacy rules needed. Continuing."
fi
}

function fuGETPORTS {
### Get ports from docker-compose.yml

myDOCKERCOMPOSEPORTS=$(cat $myDOCKERCOMPOSEYML | yq -r '.services[].ports' | grep ':' | sed -e s/127.0.0.1// | tr -d '", ' | sed -e s/^:// | cut -f1 -d ':' )
myDOCKERCOMPOSEPORTS+=" $myHOSTPORTS"
myRULESPORTS=$(for i in $myDOCKERCOMPOSEPORTS; do echo $i; done | sort -gu)
echo "Setting up / removing these ports:"
echo "$myRULESPORTS"
}

function fuSETRULES {
### Setting up iptables-legacy rules for honeytrap
if [ "$myNFQCHECK" == "honeytrap" ];
  then
    /usr/sbin/iptables-legacy -w -A INPUT -s 127.0.0.1 -j ACCEPT
    /usr/sbin/iptables-legacy -w -A INPUT -d 127.0.0.1 -j ACCEPT

    for myPORT in $myRULESPORTS; do
      /usr/sbin/iptables-legacy -w -A INPUT -p tcp --dport $myPORT -j ACCEPT
    done

    /usr/sbin/iptables-legacy -w -A INPUT -p tcp --syn -m state --state NEW -j NFQUEUE
fi

### Setting up iptables-legacy rules for glutton
if [ "$myNFQCHECK" == "glutton" ];
  then
    /usr/sbin/iptables-legacy -w -t raw -A PREROUTING -s 127.0.0.1 -j ACCEPT
    /usr/sbin/iptables-legacy -w -t raw -A PREROUTING -d 127.0.0.1 -j ACCEPT

    for myPORT in $myRULESPORTS; do
      /usr/sbin/iptables-legacy -w -t raw -A PREROUTING -p tcp --dport $myPORT -j ACCEPT
    done
    # No need for NFQ forwarding, such rules are set up by glutton
fi
}

function fuUNSETRULES {
### Removing  iptables-legacy rules for honeytrap
if [ "$myNFQCHECK" == "honeytrap" ];
  then
    /usr/sbin/iptables-legacy -w -D INPUT -s 127.0.0.1 -j ACCEPT
    /usr/sbin/iptables-legacy -w -D INPUT -d 127.0.0.1 -j ACCEPT

    for myPORT in $myRULESPORTS; do
      /usr/sbin/iptables-legacy -w -D INPUT -p tcp --dport $myPORT -j ACCEPT
    done

    /usr/sbin/iptables-legacy -w -D INPUT -p tcp --syn -m state --state NEW -j NFQUEUE
fi

### Removing iptables-legacy rules for glutton
if [ "$myNFQCHECK" == "glutton" ];
  then
    /usr/sbin/iptables-legacy -w -t raw -D PREROUTING -s 127.0.0.1 -j ACCEPT
    /usr/sbin/iptables-legacy -w -t raw -D PREROUTING -d 127.0.0.1 -j ACCEPT

    for myPORT in $myRULESPORTS; do
      /usr/sbin/iptables-legacy -w -t raw -D PREROUTING -p tcp --dport $myPORT -j ACCEPT
    done
    # No need for removing NFQ forwarding, such rules are removed by glutton
fi
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
