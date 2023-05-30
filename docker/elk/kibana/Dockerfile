FROM ubuntu:22.04
#
# VARS
ENV KB_VER=8.6.2
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
      if [ "$ARCH" = "x86_64" ]; then KB_ARCH="amd64"; fi && \
      if [ "$ARCH" = "aarch64" ]; then KB_ARCH="arm64"; fi && \
    echo "$ARCH" && \
    cd /root/dist/ && \
    aria2c -s 16 -x 16 https://artifacts.elastic.co/downloads/kibana/kibana-$KB_VER-$KB_ARCH.deb && \
    dpkg -i kibana-$KB_VER-$KB_ARCH.deb && \
#
# Setup user, groups and configs
    mkdir -p /usr/share/kibana/config \
             /usr/share/kibana/data && \
    cp /etc/kibana/kibana.yml /usr/share/kibana/config && \
    sed -i 's/#server.basePath: ""/server.basePath: "\/kibana"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#server.host: "localhost"/server.host: "0.0.0.0"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#elasticsearch.hosts: \["http:\/\/localhost:9200"\]/elasticsearch.hosts: \["http:\/\/elasticsearch:9200"\]/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#server.rewriteBasePath: false/server.rewriteBasePath: false/' /usr/share/kibana/config/kibana.yml && \
    echo "xpack.reporting.roles.enabled: false" >> /usr/share/kibana/config/kibana.yml && \
    echo "elasticsearch.requestTimeout: 60000" >> /usr/share/kibana/config/kibana.yml && \
    echo "elasticsearch.shardTimeout: 60000" >> /usr/share/kibana/config/kibana.yml && \
    echo "kibana.autocompleteTimeout: 60000" >> /usr/share/kibana/config/kibana.yml && \
    echo "kibana.autocompleteTerminateAfter: 1000000" >> /usr/share/kibana/config/kibana.yml && \
    rm -rf /usr/share/kibana/optimize/bundles/* && \
    /usr/share/kibana/bin/kibana --optimize --allow-root && \
    groupmod -g 2000 kibana && \
    usermod -u 2000 kibana && \
    chown -R root:kibana /etc/kibana && \
    chown -R kibana:kibana /usr/share/kibana/data \
                           /run/kibana \
			   /var/log/kibana \
		     	   /var/lib/kibana && \
    chmod 755 -R /usr/share/kibana/config && \
#
# Clean up
    apt-get purge aria2 -y && \
    apt-get autoremove -y --purge && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache /root/*
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:5601'
#
# Start kibana
STOPSIGNAL SIGKILL
USER kibana:kibana
CMD ["/usr/share/kibana/bin/kibana"]
