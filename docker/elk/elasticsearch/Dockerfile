FROM ubuntu:22.04
ENV DEBIAN_FRONTEND noninteractive
ARG PROXY
ENV ES_VER=8.14.2
ENV http_proxy=${PROXY}
#
# Include dist
COPY dist/ /root/dist/
#
# Check if APT_PROXY is set and configure apt to use the proxy only if it's available
RUN bash -c 'if [ -n "${http_proxy}" ]; then \
                 echo "Using APT proxy at ${http_proxy}"; \
                 echo "Acquire::http::Proxy \"${http_proxy}\";" > /etc/apt/apt.conf.d/01proxy; \
               else \
                 echo "APT proxy not configured, proceeding without proxy"; \
             fi' && \
#    bash -c 'echo "Acquire::http::Proxy::ports.ubuntu.com DIRECT;" > /etc/apt/apt.conf.d/99force-no-proxy' && \
# Setup apt
	apt-get update -y && \
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
    mkdir -p /usr/share/elasticsearch/config /etc/elasticsearch && \
    cp elasticsearch.yml /etc/elasticsearch/ && \
    aria2c -s 16 -x 16 https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-$ES_VER-$ES_ARCH.deb && \
    dpkg --force-confold -i elasticsearch-$ES_VER-$ES_ARCH.deb && \
#
# Add and move files
#    rm -rf /usr/share/elasticsearch/modules/x-pack-ml && \
#    mkdir -p /usr/share/elasticsearch/config && \
#    cp elasticsearch.yml /etc/elasticsearch/ && \
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
ENV http_proxy=""
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9200/_cat/health'
#
# Start ELK
USER elasticsearch:elasticsearch
CMD ["/usr/share/elasticsearch/bin/elasticsearch"]
