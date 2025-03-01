FROM node:20.18.2-alpine3.20
ENV KB_VER=8.17.2
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
            aria2 \
            curl \
            gcompat && \
#
# Determine arch, get and install packages
    ARCH=$(arch) && \
      if [ "$ARCH" = "x86_64" ]; then KB_ARCH="x86_64"; fi && \
      if [ "$ARCH" = "aarch64" ]; then KB_ARCH="aarch64"; fi && \
    echo "$ARCH" && \
    cd /root/dist/ && \
    aria2c -s 16 -x 16 https://artifacts.elastic.co/downloads/kibana/kibana-$KB_VER-linux-$KB_ARCH.tar.gz && \
    mkdir -p /usr/share/kibana && \
    tar xvfz kibana-$KB_VER-linux-$KB_ARCH.tar.gz --strip-components=1 -C /usr/share/kibana/ && \
#
# Kibana's bundled node does not work in build pipeline
    rm /usr/share/kibana/node/glibc-217/bin/node && \
    ln -s /usr/local/bin/node /usr/share/kibana/node/glibc-217/bin/node && \
#
# Setup user, groups and configs
    sed -i 's/#server.basePath: ""/server.basePath: "\/kibana"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#server.host: "localhost"/server.host: "0.0.0.0"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#elasticsearch.hosts: \["http:\/\/localhost:9200"\]/elasticsearch.hosts: \["http:\/\/elasticsearch:9200"\]/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#server.rewriteBasePath: false/server.rewriteBasePath: false/' /usr/share/kibana/config/kibana.yml && \
    echo "xpack.reporting.roles.enabled: false" >> /usr/share/kibana/config/kibana.yml && \
    echo "elasticsearch.requestTimeout: 60000" >> /usr/share/kibana/config/kibana.yml && \
    echo "elasticsearch.shardTimeout: 60000" >> /usr/share/kibana/config/kibana.yml && \
    echo "unifiedSearch.autocomplete.valueSuggestions.timeout: 60000" >> /usr/share/kibana/config/kibana.yml && \
    echo "unifiedSearch.autocomplete.valueSuggestions.terminateAfter: 1000000" >> /usr/share/kibana/config/kibana.yml && \
    rm -rf /usr/share/kibana/optimize/bundles/* && \
    /usr/share/kibana/bin/kibana --optimize --allow-root && \
    addgroup -g 2000 kibana && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 kibana && \
    chown -R kibana:kibana /usr/share/kibana/data && \
    chmod 755 -R /usr/share/kibana/config && \
#
# Clean up
    apk del --purge aria2 && \
    rm -rf /root/* \
        /tmp/* \
        /var/cache/apk/*
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:5601'
#
# Start kibana
STOPSIGNAL SIGKILL
USER kibana:kibana
CMD ["/usr/share/kibana/bin/kibana"]
