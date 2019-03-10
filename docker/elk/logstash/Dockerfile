FROM alpine

# Include dist
ADD dist/ /root/dist/

# Setup env and apt
RUN sed -i 's/dl-cdn/dl-4/g' /etc/apk/repositories && \
    apk -U --no-cache add \
             bash \
             curl \
             git \
             libc6-compat \
             libzmq \
             openjdk8-jre \
             wget && \

# Get and install packages
    git clone --depth=1 https://github.com/dtag-dev-sec/listbot /etc/listbot && \
    cd /root/dist/ && \
    mkdir -p /usr/share/logstash/ && \
    wget https://artifacts.elastic.co/downloads/logstash/logstash-6.6.1.tar.gz && \
    wget http://geolite.maxmind.com/download/geoip/database/GeoLite2-ASN.tar.gz && \
    tar xvfz logstash-6.6.1.tar.gz --strip-components=1 -C /usr/share/logstash/ && \
    /usr/share/logstash/bin/logstash-plugin install logstash-filter-translate && \
    /usr/share/logstash/bin/logstash-plugin install logstash-output-syslog && \
    tar xvfz GeoLite2-ASN.tar.gz --strip-components=1 -C /usr/share/logstash/vendor/bundle/jruby/2.3.0/gems/logstash-filter-geoip-5.0.3-java/vendor/ && \

# Add and move files
    cd /root/dist/ && \
    cp update.sh /usr/bin/ && \
    chmod u+x /usr/bin/update.sh && \
    mkdir -p /etc/logstash/conf.d && \
    cp logstash.conf /etc/logstash/conf.d/ && \
    cp elasticsearch-template-es6x.json /usr/share/logstash/vendor/bundle/jruby/2.3.0/gems/logstash-output-elasticsearch-9.3.2-java/lib/logstash/outputs/elasticsearch/ && \

# Setup user, groups and configs
    addgroup -g 2000 logstash && \
    adduser -S -H -s /bin/bash -u 2000 -D -g 2000 logstash && \
    chown -R logstash:logstash /usr/share/logstash && \
    chown -R logstash:logstash /etc/listbot && \
    chmod 755 /usr/bin/update.sh && \

# Clean up
    apk del --purge wget && \
    rm -rf /root/* && \
    rm -rf /tmp/* && \
    rm -rf /var/cache/apk/*

# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9600'

# Start logstash
#USER logstash:logstash
CMD update.sh && exec /usr/share/logstash/bin/logstash -f /etc/logstash/conf.d/logstash.conf --config.reload.automatic --java-execution
