FROM alpine
MAINTAINER MO

# Include dist
ADD dist/ /root/dist/

# Setup env and apt
RUN apk -U upgrade && \
    apk add bash curl git libc6-compat libzmq openjdk8-jre procps wget && \

# Get and install packages
    git clone --depth=1 https://github.com/dtag-dev-sec/listbot /etc/listbot && \
    cd /root/dist/ && \
    mkdir -p /usr/share/logstash/ && \
    wget https://artifacts.elastic.co/downloads/logstash/logstash-5.6.9.tar.gz && \
    wget http://geolite.maxmind.com/download/geoip/database/GeoLite2-ASN.tar.gz && \
    tar xvfz logstash-5.6.9.tar.gz --strip-components=1 -C /usr/share/logstash/ && \
    /usr/share/logstash/bin/logstash-plugin install logstash-filter-translate && \
    /usr/share/logstash/bin/logstash-plugin install logstash-output-syslog && \
    tar xvfz GeoLite2-ASN.tar.gz --strip-components=1 -C /usr/share/logstash/vendor/bundle/jruby/1.9/gems/logstash-filter-geoip-4.3.1-java/vendor/ && \

# Add and move files
    cd /root/dist/ && \
    cp update.sh /usr/bin/ && \
    chmod u+x /usr/bin/update.sh && \
    mkdir -p /etc/logstash/conf.d && \
    cp logstash.conf /etc/logstash/conf.d/ && \
    cp elasticsearch-template-es5x.json /usr/share/logstash/vendor/bundle/jruby/1.9/gems/logstash-output-elasticsearch-7.4.2-java/lib/logstash/outputs/elasticsearch/ && \

# Clean up
    apk del wget && \
    rm -rf /root/*

# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9600'

# Start logstash
CMD update.sh && /usr/share/logstash/bin/logstash -f /etc/logstash/conf.d/logstash.conf
