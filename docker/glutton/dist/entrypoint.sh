#!/bin/sh
set -eu

myIF="$(capture-if.sh)"
if ! myRULES="$(nft list chain ip tpot_glutton prerouting 2>&1)"; then
    printf 'Glutton cannot start: tpotinit has not installed its native nftables firewall.\n%s\n' "$myRULES" >&2
    exit 3
fi
for myEXPECTED in "iifname != \"$myIF\" return" \
    "tproxy to 127.0.0.1:5000" "tproxy to 127.0.0.1:5001"; do
    if ! printf '%s\n' "$myRULES" | grep -Fq "$myEXPECTED"; then
        printf 'Glutton cannot start: tpotinit firewall does not match %s.\n' "$myEXPECTED" >&2
        exit 3
    fi
done
exec bin/server --interface "$myIF" --ssh 64295 --logpath /var/log/glutton/diagnostic.log
