FROM docker.elastic.co/kibana/kibana:9.3.5

USER root

RUN printf '%s\n' \
        'server.basePath: "/kibana"' \
        'server.host: "0.0.0.0"' \
        'elasticsearch.hosts: ["http://elasticsearch:9200"]' \
        'server.rewriteBasePath: false' \
        'xpack.reporting.roles.enabled: false' \
        'elasticsearch.requestTimeout: 60000' \
        'elasticsearch.shardTimeout: 60000' \
        'unifiedSearch.autocomplete.valueSuggestions.timeout: 60000' \
        'unifiedSearch.autocomplete.valueSuggestions.terminateAfter: 1000000' \
        'telemetry.optIn: false' \
        'newsfeed.enabled: false' \
        > /usr/share/kibana/config/kibana.yml && \
    printf '%s\n' '--max-old-space-size=640' >> /usr/share/kibana/config/node.options && \
    sed -i 's/^kibana:x:1000:1000:/kibana:x:2000:2000:/' /etc/passwd && \
    sed -i 's/^kibana:x:1000:/kibana:x:2000:/' /etc/group && \
    chown -R kibana:kibana \
        /usr/share/kibana/config \
        /usr/share/kibana/data

HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:5601'

STOPSIGNAL SIGKILL
USER kibana
