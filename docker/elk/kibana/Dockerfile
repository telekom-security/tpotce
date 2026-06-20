FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
ENV KB_VER=9.3.5
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apt-get update -y && \
    apt-get upgrade -y && \
    apt-get install -y \
            adduser \
            aria2 \
            curl && \
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
    echo "telemetry.optIn: false" >> /usr/share/kibana/config/kibana.yml && \
    echo "newsfeed.enabled: false" >> /usr/share/kibana/config/kibana.yml && \
    echo "--max-old-space-size=640" >> /usr/share/kibana/config/node.options && \
    addgroup --gid 2000 kibana && \
    adduser --system --no-create-home --shell /usr/sbin/nologin --uid 2000 --ingroup kibana kibana && \
    chown -R kibana:kibana /usr/share/kibana/data && \
    chmod 755 -R /usr/share/kibana/config && \
#
# Clean up
    apt-get purge aria2 -y && \
    apt-get autoremove -y --purge && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
        /tmp/* /var/tmp/* \
        /root/.cache \
        /root/*
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:5601'
#
# Start kibana
STOPSIGNAL SIGKILL
USER kibana:kibana
CMD ["/usr/share/kibana/bin/kibana"]
