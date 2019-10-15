#/bin/bash
# Unlock all ES indices for read / write mode
# Useful in cases where ES locked all indices after disk quota has been reached
# Make sure ES is available
myES="http://127.0.0.1:64298/"
myESSTATUS=$(curl -s -XGET ''$myES'_cluster/health' | jq '.' | grep -c "green\|yellow")
if ! [ "$myESSTATUS" = "1" ]
  then
    echo "### Elasticsearch is not available, try starting via 'systemctl start tpot'."
    exit
  else
    echo "### Elasticsearch is available, now continuing."
    echo
fi

echo "### Trying to unlock all ES indices for read / write operation: "
curl -XPUT -H "Content-Type: application/json" ''$myES'_all/_settings' -d '{"index.blocks.read_only_allow_delete": null}'
echo

