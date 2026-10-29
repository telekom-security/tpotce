#!/bin/bash

unset CDPATH
myBIN="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

### Vars, Ports for Standard services
myHOSTPORTS="7634 64294 64295 64296 64297 64298 64299 64305"
myDOCKERCOMPOSEYML="$1"
myRULESFUNCTION="$2"
myNFTTABLE="tpot"
myRULESFAILED=0

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
### Check if a catch-all honeypot is enabled in docker-compose.yml

if ! myNFQCHECK=$(yq -r '.services | keys | .[] | select(. == "honeytrap" or . == "glutton")' "$myDOCKERCOMPOSEYML"); then
    echo "Cannot read the Compose services; no catch-all rules will be installed."
    exit 1
fi
if [ "$myNFQCHECK" == "" ];
  then
    echo "No catch-all honeypot detected, no firewall rules needed. Exiting."
  else
    echo "Detected $myNFQCHECK as catch-all honeypot, firewall rules needed. Continuing."
fi
}

function fuRUN {
### Run a rule command, report what failed and keep going, so a single rejected
### rule does not leave the rest silently unapplied

myOUTPUT=$("$@" 2>&1)
if [ $? -ne 0 ];
  then
    echo "Failed: $*"
    if [ "$myOUTPUT" != "" ];
      then
        echo "        $myOUTPUT"
    fi
    myRULESFAILED=$((myRULESFAILED + 1))
fi
}

function fuDETECTBACKEND {
### Decide whether the NFQUEUE rule can be set through iptables. RHEL 10 and its
### rebuilds ship a kernel without the xtables modules, so `-m state` and
### `-j NFQUEUE` fail there, while nftables expresses the same thing natively.
### The probe uses a chain nothing jumps to, so it never sees a packet.

myBACKEND="nft"
if iptables -w -N TPOT_PROBE > /dev/null 2>&1;
  then
    if iptables -w -A TPOT_PROBE -p tcp --syn -m state --state NEW -j NFQUEUE > /dev/null 2>&1;
      then
        myBACKEND="iptables"
    fi
    iptables -w -F TPOT_PROBE > /dev/null 2>&1
    iptables -w -X TPOT_PROBE > /dev/null 2>&1
fi

if [ "$myBACKEND" == "nft" ] && ! command -v nft > /dev/null 2>&1;
  then
    echo "This kernel does not support the iptables NFQUEUE rules and 'nft' is missing."
    echo "$myNFQCHECK cannot be supplied with traffic on this host."
    exit 1
fi

echo "Using $myBACKEND for the NFQ rules."
}

function fuGETPORTS {
### Get ports from docker-compose.yml

myDOCKERCOMPOSEPORTS=$(cat $myDOCKERCOMPOSEYML | yq -r '.services[].ports' | grep ':' | sed -e s/127.0.0.1// | tr -d '", ,#,-' | sed -e s/^:// | cut -f1 -d ':' )
myDOCKERCOMPOSEPORTS+=" $myHOSTPORTS"
myRULESPORTS=$(for i in $myDOCKERCOMPOSEPORTS; do echo $i; done | sort -gu)
echo "Setting up / removing these ports:"
echo "$myRULESPORTS"
}

function fuSETRULES {
### Setting up iptables rules for honeytrap
if [ "$myNFQCHECK" == "honeytrap" ] && [ "$myBACKEND" == "iptables" ];
  then
    fuRUN iptables -w -A INPUT -s 127.0.0.1 -j ACCEPT
    fuRUN iptables -w -A INPUT -d 127.0.0.1 -j ACCEPT

    for myPORT in $myRULESPORTS; do
      fuRUN iptables -w -A INPUT -p tcp --dport $myPORT -j ACCEPT
    done

    fuRUN iptables -w -A INPUT -p tcp --syn -m state --state NEW -j NFQUEUE
fi

### Setting up nftables rules for honeytrap
### One own table, so removing it takes the whole rule set with it. Accepts come
### first, an accept ends this chain and the queue rule below is not reached.
if [ "$myNFQCHECK" == "honeytrap" ] && [ "$myBACKEND" == "nft" ];
  then
    fuRUN nft add table ip $myNFTTABLE
    fuRUN nft add chain ip $myNFTTABLE input "{ type filter hook input priority 0; policy accept; }"
    fuRUN nft add rule ip $myNFTTABLE input ip saddr 127.0.0.1 accept
    fuRUN nft add rule ip $myNFTTABLE input ip daddr 127.0.0.1 accept
    fuRUN nft add rule ip $myNFTTABLE input tcp dport "{ $(echo $myRULESPORTS | tr ' ' ',') }" accept
    fuRUN nft add rule ip $myNFTTABLE input tcp flags syn ct state new queue num 0
fi

}

function fuUNSETRULES {
### Removing iptables rules for honeytrap
if [ "$myNFQCHECK" == "honeytrap" ] && [ "$myBACKEND" == "iptables" ];
  then
    fuRUN iptables -w -D INPUT -s 127.0.0.1 -j ACCEPT
    fuRUN iptables -w -D INPUT -d 127.0.0.1 -j ACCEPT

    for myPORT in $myRULESPORTS; do
      fuRUN iptables -w -D INPUT -p tcp --dport $myPORT -j ACCEPT
    done

    fuRUN iptables -w -D INPUT -p tcp --syn -m state --state NEW -j NFQUEUE
fi

### Removing nftables rules for honeytrap
if [ "$myNFQCHECK" == "honeytrap" ] && [ "$myBACKEND" == "nft" ];
  then
    if nft list table ip $myNFTTABLE > /dev/null 2>&1;
      then
        fuRUN nft delete table ip $myNFTTABLE
      else
        echo "No nftables table '$myNFTTABLE' to remove."
    fi
fi

}

# Main
fuCHECKFORARGS
fuNFQCHECK
# Also remove stale Glutton rules when switching to an edition without it.
if [ "$myRULESFUNCTION" == "unset" ] || [ "$myNFQCHECK" != "glutton" ]; then
    python3 "$myBIN/glutton_rules.py" unset || exit 1
fi
if [ -z "$myNFQCHECK" ]; then
    exit 0
fi
if [ "$myNFQCHECK" == "glutton" ]; then
    if [ "$myRULESFUNCTION" == "set" ]; then
        python3 "$myBIN/glutton_rules.py" set "$myDOCKERCOMPOSEYML"
        exit $?
    fi
    exit 0
fi
if [ "$myNFQCHECK" != "honeytrap" ]; then
    echo "Glutton and Honeytrap cannot run together."
    exit 1
fi
fuDETECTBACKEND
fuGETPORTS

if [ "$myRULESFUNCTION" == "set" ];
  then
    fuSETRULES
  else
    fuUNSETRULES
fi

### Report instead of failing silently, the caller logs this
if [ "$myRULESFAILED" -ne 0 ];
  then
    echo
    if [ "$myRULESFUNCTION" == "set" ];
      then
        echo "$myRULESFAILED rule(s) could not be added, $myNFQCHECK will not receive traffic as expected."
      else
        echo "$myRULESFAILED rule(s) could not be removed, review 'iptables -S' and 'nft list ruleset'."
    fi
    exit 1
fi
