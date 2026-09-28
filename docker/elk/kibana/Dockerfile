ARG KB_VER=9.5.4
FROM docker.elastic.co/kibana/kibana:${KB_VER}
ARG KB_VER
ENV KB_VER=${KB_VER}

# Kibana (Node.js) heap in MB. Keep it well below the kibana mem_limit (2g) in
# compose/*.yml. Adjust it here or build with --build-arg KIBANA_HEAP_MB=<MB>.
# Any --max-old-space-size switches off the image's automatic heap sizing
# (75% of the container memory limit).
ARG KIBANA_HEAP_MB=1024

USER root

RUN printf '%s\n' \
        'server.basePath: "/kibana"' \
        'server.host: "0.0.0.0"' \
        'elasticsearch.hosts: ["http://elasticsearch:9200"]' \
        'server.rewriteBasePath: false' \
        'elasticsearch.requestTimeout: 60000' \
        'elasticsearch.shardTimeout: 60000' \
        'unifiedSearch.autocomplete.valueSuggestions.timeout: 60000' \
        'unifiedSearch.autocomplete.valueSuggestions.terminateAfter: 1000000' \
        'telemetry.optIn: false' \
        'newsfeed.enabled: false' \
        > /usr/share/kibana/config/kibana.yml && \
    printf '%s\n' "--max-old-space-size=${KIBANA_HEAP_MB}" >> /usr/share/kibana/config/node.options && \
    sed -i 's/^kibana:x:1000:1000:/kibana:x:2000:2000:/' /etc/passwd && \
    sed -i 's/^kibana:x:1000:/kibana:x:2000:/' /etc/group && \
    [ "$(id -u kibana):$(id -g kibana)" = "2000:2000" ] && \
    chown -R kibana:kibana \
        /usr/share/kibana/config \
        /usr/share/kibana/data

HEALTHCHECK --start-period=5m --retries=10 CMD curl -s -XGET 'http://127.0.0.1:5601'

STOPSIGNAL SIGKILL
USER kibana
