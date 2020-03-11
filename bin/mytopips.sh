#!/bin/bash
# Make sure ES is available
myES="http://127.0.0.1:64298/"
myESSTATUS=$(curl -s -XGET ''$myES'_cluster/health' | jq '.' | grep -c green)
if ! [ "$myESSTATUS" = "1" ]
  then
    echo "### Elasticsearch is not available, try starting via 'systemctl start elk'."
    exit 1
  else
    echo "### Elasticsearch is available, now continuing."
    echo
fi

function fuMYTOPIPS {
curl -s -XGET $myES"_search" -H 'Content-Type: application/json' -d'
{
  "aggs": {
    "ips": {
      "terms": { "field": "src_ip.keyword", "size": 100 }
    }
  },
  "size" : 0
}'
}

echo "### Aggregating top 100 source IPs in ES"
fuMYTOPIPS | jq '.aggregations.ips.buckets[].key' | tr -d '"'
