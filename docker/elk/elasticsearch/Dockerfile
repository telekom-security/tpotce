FROM alpine:3.14
#
# VARS
ENV ES_VER=7.15.1 \
    ES_JAVA_HOME=/usr/lib/jvm/java-16-openjdk

# Include dist
ADD dist/ /root/dist/
#
# Setup env and apt
#RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
RUN apk -U --no-cache add \
             aria2 \
             bash \
             curl \
             nss && \
    apk add --no-cache -X http://dl-cdn.alpinelinux.org/alpine/edge/testing openjdk16-jre && \
#
# Get and install packages
    cd /root/dist/ && \
    mkdir -p /usr/share/elasticsearch/ && \
    aria2c -s 16 -x 16 https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-$ES_VER-linux-x86_64.tar.gz && \
    tar xvfz elasticsearch-$ES_VER-linux-x86_64.tar.gz --strip-components=1 -C /usr/share/elasticsearch/ && \
    rm -rf /usr/share/elasticsearch/jdk && \
    rm -rf /usr/share/elasticsearch/modules/x-pack-ml && \
    # For some reason Alpine 3.14 does not report the -x flag correctly and thus elasticsearch does not find java
    sed -i 's/! -x/! -e/g' /usr/share/elasticsearch/bin/elasticsearch-env && \
#
# Add and move files
    cd /root/dist/ && \
    mkdir -p /usr/share/elasticsearch/config && \
    cp elasticsearch.yml /usr/share/elasticsearch/config/ && \
#
# Setup user, groups and configs
    addgroup -g 2000 elasticsearch && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 elasticsearch && \
    chown -R elasticsearch:elasticsearch /usr/share/elasticsearch/ && \
#
# Clean up
    apk del --purge aria2 && \
    rm -rf /root/* && \
    rm -rf /tmp/* && \
    rm -rf /var/cache/apk/*
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9200/_cat/health'
#
# Start ELK
USER elasticsearch:elasticsearch
CMD ["/usr/share/elasticsearch/bin/elasticsearch"]
