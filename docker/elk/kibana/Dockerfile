FROM alpine
MAINTAINER MO

# Include dist
ADD dist/ /root/dist/

# Setup env and apt
RUN apk -U upgrade && \
    apk add bash curl nodejs procps wget && \

# Get and install packages
    cd /root/dist/ && \
    mkdir -p /usr/share/kibana/ && \
    wget https://artifacts.elastic.co/downloads/kibana/kibana-5.6.9-linux-x86_64.tar.gz && \
    tar xvfz kibana-5.6.9-linux-x86_64.tar.gz --strip-components=1 -C /usr/share/kibana/ && \

# Kibana's bundled node does not work in alpine
    rm /usr/share/kibana/node/bin/node && \
    rm /usr/share/kibana/node/bin/npm && \
    ln -s /usr/bin/node /usr/share/kibana/node/bin/node && \
    ln -s /usr/bin/npm /usr/share/kibana/node/bin/npm && \

# Add and move files
    cd /root/dist/ && \
    cp kibana.svg /usr/share/kibana/src/ui/public/images/kibana.svg && \
    cp kibana.svg /usr/share/kibana/src/ui/public/icons/kibana.svg && \
    cp elk.ico /usr/share/kibana/src/ui/public/assets/favicons/favicon.ico && \
    cp elk.ico /usr/share/kibana/src/ui/public/assets/favicons/favicon-16x16.png && \
    cp elk.ico /usr/share/kibana/src/ui/public/assets/favicons/favicon-32x32.png && \
    cp create_kibana_index.js /usr/share/kibana/src/core_plugins/elasticsearch/lib/ && \
    cd / && \

# Setup user, groups and configs
    sed -i 's/#server.basePath: ""/server.basePath: "\/kibana"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#kibana.defaultAppId: "discover"/kibana.defaultAppId: "dashboards"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#server.host: "localhost"/server.host: "0.0.0.0"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#elasticsearch.url: "http:\/\/localhost:9200"/elasticsearch.url: "http:\/\/elasticsearch:9200"/' /usr/share/kibana/config/kibana.yml && \
    /usr/share/kibana/bin/kibana 2>&1 | grep -m 1 "Optimization of bundles" && \
    addgroup -g 2000 kibana && \
    adduser -S -H -s /bin/bash -u 2000 -D -g 2000 kibana && \
    chown -R kibana:kibana /usr/share/kibana/ && \

# Clean up
    apk del wget && \
    rm -rf /root/*

# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:5601'

# Start kibana
USER kibana
CMD ["/usr/share/kibana/bin/kibana"]
