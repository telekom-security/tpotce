#!/bin/bash
# Get times
from=$(curl http://127.0.0.1:8000/API/time/from)
to=$(curl http://127.0.0.1:8000/API/time/to)

# Get all targets
arr=$(curl http://127.0.0.1:8000/API/targets)
# Split targets
targets=$(echo $arr | sed -e 's/\[ //g' -e 's/\ ]//g' -e 's/\,//g')

# For each target DO
for target in ${targets[@]}; do

  # Remove quotations
  clean_target=${target//\"}

  # Execute SSH command to get data.json
  ssh -p 64295 tsec@${clean_target} "curl -XGET --header 'Content-Type: application/json' http://localhost:64298/logstash-*/_search?pretty=true -d'{\"size\":10000,\"query\":{\"range\":{\"timestamp\":{\"from\":\"${from}\",\"to\":\"${to}\",\"time_zone\":\"+03:00\"}}}}'" > /tmp/data.json

  # Push data to local
  curl -XPOST --header "Authorization: IP ${clean_target}" http://127.0.0.1:8000/API/post_local -F 'data=@/tmp/data.json'
  rm /tmp/data.json

done

curl http://127.0.0.1:8000/API/agregate/day/ip
curl http://127.0.0.1:8000/API/agregate/day/country
curl http://127.0.0.1:8000/API/agregate/day/perserver
curl http://127.0.0.1:8000/API/agregate/day/perserver/bg