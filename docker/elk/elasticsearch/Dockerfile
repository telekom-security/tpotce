FROM ubuntu:22.04
#
# VARS
ENV ES_VER=8.6.2
#
# Include dist
COPY dist/ /root/dist/
#
RUN apt-get update -y && \
    apt-get install -y \
            aria2 \
            curl && \
#
# Determine arch, get and install packages
    ARCH=$(arch) && \
      if [ "$ARCH" = "x86_64" ]; then ES_ARCH="amd64"; fi && \
      if [ "$ARCH" = "aarch64" ]; then ES_ARCH="arm64"; fi && \
    echo "$ARCH" && \
    cd /root/dist/ && \
    aria2c -s 16 -x 16 https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-$ES_VER-$ES_ARCH.deb && \
    dpkg -i elasticsearch-$ES_VER-$ES_ARCH.deb && \
#
# Add and move files
#    rm -rf /usr/share/elasticsearch/modules/x-pack-ml && \
    mkdir -p /usr/share/elasticsearch/config && \
    cp elasticsearch.yml /etc/elasticsearch/ && \
#
# Setup user, groups and configs
    groupmod -g 2000 elasticsearch && \
    usermod -u 2000 elasticsearch && \
    chown -R root:elasticsearch /etc/default/elasticsearch \
                                /etc/elasticsearch && \
    chown -R elasticsearch:elasticsearch /var/lib/elasticsearch \
                                         /var/log/elasticsearch && \
#
# Clean up
    apt-get purge aria2 -y && \
    apt-get autoremove -y --purge && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache /root/*
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9200/_cat/health'
#
# Start ELK
USER elasticsearch:elasticsearch
CMD ["/usr/share/elasticsearch/bin/elasticsearch"]
