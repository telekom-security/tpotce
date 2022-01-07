FROM node:16.13.0-alpine3.14
#
# VARS
ENV KB_VER=7.16.2
# 
# Include dist
ADD dist/ /root/dist/
#
RUN apk -U --no-cache add \
            aria2 \
            curl \
            gcompat && \
#
# Get and install packages
    cd /root/dist/ && \
    mkdir -p /usr/share/kibana/ && \
    aria2c -s 16 -x 16 https://artifacts.elastic.co/downloads/kibana/kibana-$KB_VER-linux-x86_64.tar.gz && \
    tar xvfz kibana-$KB_VER-linux-x86_64.tar.gz --strip-components=1 -C /usr/share/kibana/ && \
#
# Kibana's bundled node does not work in alpine
    rm /usr/share/kibana/node/bin/node && \
    ln -s /usr/local/bin/node /usr/share/kibana/node/bin/node && \
#
# Add and move files
    cd /root/dist/ && \
#
# Setup user, groups and configs
    sed -i 's/#server.basePath: ""/server.basePath: "\/kibana"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#server.host: "localhost"/server.host: "0.0.0.0"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#elasticsearch.hosts: \["http:\/\/localhost:9200"\]/elasticsearch.hosts: \["http:\/\/elasticsearch:9200"\]/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#server.rewriteBasePath: false/server.rewriteBasePath: false/' /usr/share/kibana/config/kibana.yml && \
    echo "xpack.reporting.roles.enabled: false" >> /usr/share/kibana/config/kibana.yml && \
    echo "elasticsearch.requestTimeout: 60000" >> /usr/share/kibana/config/kibana.yml && \
    echo "elasticsearch.shardTimeout: 60000" >> /usr/share/kibana/config/kibana.yml && \
    rm -rf /usr/share/kibana/optimize/bundles/* && \
    /usr/share/kibana/bin/kibana --optimize --allow-root && \
    addgroup -g 2000 kibana && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 kibana && \
    chown -R kibana:kibana /usr/share/kibana/ && \
#
# Clean up
    apk del --purge aria2 && \
    rm -rf /root/* && \
    rm -rf /tmp/* && \
    rm -rf /var/cache/apk/*
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:5601'
#
# Start kibana
STOPSIGNAL SIGKILL
USER kibana:kibana
CMD ["/usr/share/kibana/bin/kibana"]
