#!/bin/bash

## Get my external IP

timeout=2   # seconds to wait for a reply before trying next server
verbose=1   # prints which server was used to STDERR

dnslist=(
    "dig +short            myip.opendns.com        @resolver1.opendns.com"
    "dig +short            myip.opendns.com        @resolver2.opendns.com"
    "dig +short            myip.opendns.com        @resolver3.opendns.com"
    "dig +short            myip.opendns.com        @resolver4.opendns.com"
    "dig +short    -t txt  o-o.myaddr.l.google.com @ns1.google.com"
    "dig +short -4 -t a    whoami.akamai.net       @ns1-1.akamaitech.net"
    "dig +short            whoami.akamai.net       @ns1-1.akamaitech.net"
)

httplist=(
    4.ifcfg.me
    alma.ch/myip.cgi
    api.infoip.io/ip
    api.ipify.org
    bot.whatismyipaddress.com
    canhazip.com
    checkip.amazonaws.com
    eth0.me
    icanhazip.com
    ident.me
    ipecho.net/plain
    ipinfo.io/ip
    ipof.in/txt
    ip.tyk.nu
    l2.io/ip
    smart-ip.net/myip
    tnx.nl/ip
    wgetip.com
    whatismyip.akamai.com
)



# function to shuffle the global array "array"
shuffle() {
   local i tmp size max rand
   size=${#array[*]}
   max=$(( 32768 / size * size ))
   for ((i=size-1; i>0; i--)); do
      while (( (rand=$RANDOM) >= max )); do :; done
      rand=$(( rand % (i+1) ))
      tmp=${array[i]} array[i]=${array[rand]} array[rand]=$tmp
   done
}


# if we have dig and a list of dns methods, try that first
if hash dig 2>/dev/null && [ ${#dnslist[*]} -gt 0 ]; then
    eval array=( \"\${dnslist[@]}\" )
    shuffle

    for cmd in "${array[@]}"; do
        [ "$verbose" == 1 ] && echo Trying: $cmd 1>&2
        ip=$(timeout $timeout $cmd)
        if [ -n "$ip" ]; then
            echo $ip
            exit
        fi
    done
fi


# if we haven't succeeded with DNS, try HTTP

if [ ${#httplist[*]} == 0 ]; then
    echo "No hosts in httplist array!" >&2
    exit 1
fi

# use curl or wget, depending on which one we find
curl_or_wget=$(if hash curl 2>/dev/null; then echo curl; elif hash wget 2>/dev/null; then echo "wget -qO-"; fi);

if [ -z "$curl_or_wget" ]; then
    echo "Neither curl nor wget found. Cannot use http method." >&2
    exit 1
fi

eval array=( \"\${httplist[@]}\" )
shuffle

for url in "${array[@]}"; do
    [ "$verbose" == 1 ] && echo Trying: $curl_or_wget -s "$url" 1>&2
    ip=$(timeout $timeout $curl_or_wget -s "$url")
    if [ -n "$ip" ]; then
        echo $ip
        exit
    fi
done
