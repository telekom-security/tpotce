#!/bin/bash
# Delete logstash indices older than 7 days

cutoff=$(date -d '7 days ago' +%Y.%m.%d)

# Build comma-separated list of indices to delete
to_delete=""
for index in $(docker exec elasticsearch curl -s 'localhost:9200/_cat/indices/logstash-*?h=index' 2>/dev/null | sort); do
    date_part=${index#logstash-}
    if [[ "$date_part" < "$cutoff" && "$date_part" =~ ^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$ ]]; then
        to_delete="${to_delete:+$to_delete,}$index"
    fi
done

# Delete if we found any
if [[ -n "$to_delete" ]]; then
    echo "$(date): Deleting indices: $to_delete"
    docker exec elasticsearch curl -s -X DELETE "localhost:9200/$to_delete"
    echo ""
else
    echo "$(date): No indices to delete"
fi
