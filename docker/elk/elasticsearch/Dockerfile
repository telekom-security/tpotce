FROM alpine

# Include dist
ADD dist/ /root/dist/

# Setup env and apt
RUN apk -U add \
             bash \
             curl \
             openjdk8-jre \
             wget && \

# Get and install packages
    cd /root/dist/ && \
    mkdir -p /usr/share/elasticsearch/ && \
    wget https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-6.5.2.tar.gz && \
    tar xvfz elasticsearch-6.5.2.tar.gz --strip-components=1 -C /usr/share/elasticsearch/ && \

# Add and move files
    cd /root/dist/ && \
    mkdir -p /usr/share/elasticsearch/config && \
    cp elasticsearch.yml /usr/share/elasticsearch/config/ && \

# Setup user, groups and configs
    addgroup -g 2000 elasticsearch && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 elasticsearch && \
    chown -R elasticsearch:elasticsearch /usr/share/elasticsearch/ && \
    rm -rf /usr/share/elasticsearch/modules/x-pack-ml && \

# Clean up
    apk del --purge wget && \
    rm -rf /root/* && \
    rm -rf /tmp/* && \
    rm -rf /var/cache/apk/*

# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9200/_cat/health'

# Start ELK
USER elasticsearch:elasticsearch
CMD ["/usr/share/elasticsearch/bin/elasticsearch"]
