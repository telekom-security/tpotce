#!/bin/bash
# The top 100 source IPs in Elasticsearch. This is `tpot attackers` now (with country
# and reputation, the menu shows them on the Status page); without tpot the plain
# query as before.
myTPOT="$HOME/tpotce/tpot"
if [ -x "${myTPOT}" ];
  then
    exec "${myTPOT}" attackers --count 100 --plain
fi
myES="http://127.0.0.1:64298/"
myESSTATUS=$(curl -s -XGET "${myES}_cluster/health" | jq '.' | grep -c green)
if ! [ "$myESSTATUS" = "1" ]
  then
    echo "### Elasticsearch is not available."
    exit 1
fi
curl -s -XGET "${myES}_search" -H 'Content-Type: application/json' \
  -d '{"aggs": {"ips": {"terms": {"field": "src_ip.keyword", "size": 100}}}, "size": 0}' \
  | jq -r '.aggregations.ips.buckets[].key'
