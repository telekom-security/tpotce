#!/usr/bin/env bash

# Backup all Kibana objects
# Make sure Kibana is available
myKIBANA="http://127.0.0.1:64296"
myKIBANASTATUS=$(curl -s -f -o /dev/null "${myKIBANA}/api/status")
if ! [ "$?" = "0" ]
  then
    echo "### Kibana is not available."
    exit
  else
    echo "### Kibana is available, now continuing."
    echo
fi

# Export Kibana config
myDATE=$(date +%Y%m%d%H%M)
echo "### Exporting Kibana config."
curl -X POST "${myKIBANA}/api/saved_objects/_export" \
  -H "kbn-xsrf: true" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "*",
    "excludeExportDetails": true
  }' \
  -o kibana_export.ndjson

echo
echo "### Zipping Kibana config."
zip kibana_export.ndjson.zip kibana_export.ndjson

echo
echo "### Moving Kibana config and zip to ../etc/objects/"
mv kibana_export.* ../etc/objects
