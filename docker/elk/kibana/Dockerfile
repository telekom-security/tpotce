FROM alpine

# Include dist
ADD dist/ /root/dist/

# Setup env and apt
RUN apk -U --no-cache add \
            curl \
            nodejs \
            wget && \

# Get and install packages
    cd /root/dist/ && \
    mkdir -p /usr/share/kibana/ && \
    wget https://artifacts.elastic.co/downloads/kibana/kibana-6.3.0-linux-x86_64.tar.gz && \
    tar xvfz kibana-6.3.0-linux-x86_64.tar.gz --strip-components=1 -C /usr/share/kibana/ && \

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

# Setup plugins, rebuild bundle
    #cd /usr/share/kibana/plugins && \
    #wget https://github.com/dlumbrer/kbn_radar/releases/download/Kibana-6.X/kbn_radar.tar.gz && \
    #wget https://github.com/dlumbrer/kbn_network/releases/download/6.0.X-1/network_vis.tar.gz && \
    #tar xvfz kbn_radar.tar.gz && \
    #tar xvfz network_vis.tar.gz && \
    #rm *.tar.gz && \
    rm -rf /usr/share/kibana/optimize/bundles/* && \

# Setup user, groups and configs
    sed -i 's/#server.basePath: ""/server.basePath: "\/kibana"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#kibana.defaultAppId: "home"/kibana.defaultAppId: "dashboards"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#server.host: "localhost"/server.host: "0.0.0.0"/' /usr/share/kibana/config/kibana.yml && \
    sed -i 's/#elasticsearch.url: "http:\/\/localhost:9200"/elasticsearch.url: "http:\/\/elasticsearch:9200"/' /usr/share/kibana/config/kibana.yml && \
    sed -i "s/#005571/#e20074/g" /usr/share/kibana/src/ui/public/chrome/directives/global_nav/global_nav.less && \
    sed -i "s/#005571/#e20074/g" /usr/share/kibana/src/core_plugins/kibana/public/dashboard/styles/index.less && \
    sed -i "s/globalColorBlue/globalColorMagenta/g" /usr/share/kibana/src/ui/public/chrome/directives/global_nav/global_nav_link/global_nav_link.less && \
    echo "@globalColorMagenta: #E20074;" >> /usr/share/kibana/src/ui/public/styles/variables/colors.less && \
    /usr/share/kibana/bin/kibana 2>&1 | grep -m 1 "Optimization of bundles" && \
    addgroup -g 2000 kibana && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 kibana && \
    chown -R kibana:kibana /usr/share/kibana/ && \

# Clean up
    apk del --purge wget && \
    rm -rf /root/*

# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:5601'

# Start kibana
USER kibana:kibana
CMD ["/usr/share/kibana/bin/kibana"]
