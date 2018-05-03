FROM alpine
MAINTAINER MO

# Include dist
ADD dist/ /root/dist/

# Setup env and apt
RUN apk -U upgrade && \
    apk add bash curl openjdk8-jre procps wget && \

# Get and install packages
    cd /root/dist/ && \
    mkdir -p /usr/share/elasticsearch/ && \
    wget https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-5.6.9.tar.gz && \
    tar xvfz elasticsearch-5.6.9.tar.gz --strip-components=1 -C /usr/share/elasticsearch/ && \

# Add and move files
    cd /root/dist/ && \
    mkdir -p /usr/share/elasticsearch/config && \
    cp elasticsearch.yml /usr/share/elasticsearch/config/ && \

# Setup user, groups and configs
    addgroup -g 2000 elasticsearch && \
    adduser -S -H -s /bin/bash -u 2000 -D -g 2000 elasticsearch && \
    chown -R elasticsearch:elasticsearch /usr/share/elasticsearch/ && \

# Clean up
    apk del wget && \
    rm -rf /root/*

# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9200/_cat/health'

# Start ELK
USER elasticsearch
CMD ["/usr/share/elasticsearch/bin/elasticsearch"]
