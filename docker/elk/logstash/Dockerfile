FROM alpine:3.12
#
# VARS
ENV LS_VER=7.10.1
# Include dist
ADD dist/ /root/dist/
#
# Setup env and apt
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U --no-cache add \
             aria2 \
             bash \
             bzip2 \
	     curl \
             libc6-compat \
             libzmq \
             nss \
             openjdk11-jre && \
#
# Get and install packages
    mkdir -p /etc/listbot && \
    cd /etc/listbot && \
    aria2c -s16 -x 16 https://listbot.sicherheitstacho.eu/cve.yaml.bz2 && \
    aria2c -s16 -x 16 https://listbot.sicherheitstacho.eu/iprep.yaml.bz2 && \
    bunzip2 *.bz2 && \
    cd /root/dist/ && \
    mkdir -p /usr/share/logstash/ && \
    aria2c -s 16 -x 16 https://artifacts.elastic.co/downloads/logstash/logstash-$LS_VER-linux-x86_64.tar.gz && \
    tar xvfz logstash-$LS_VER-linux-x86_64.tar.gz --strip-components=1 -C /usr/share/logstash/ && \
    rm -rf /usr/share/logstash/jdk && \
    /usr/share/logstash/bin/logstash-plugin install logstash-filter-translate && \
    /usr/share/logstash/bin/logstash-plugin install logstash-output-syslog && \
#
# Add and move files
    cd /root/dist/ && \
    cp update.sh /usr/bin/ && \
    chmod u+x /usr/bin/update.sh && \
    mkdir -p /etc/logstash/conf.d && \
    cp logstash.conf /etc/logstash/conf.d/ && \
    cp tpot_es_template.json /etc/logstash/ && \
#
# Setup user, groups and configs
    addgroup -g 2000 logstash && \
    adduser -S -H -s /bin/bash -u 2000 -D -g 2000 logstash && \
    chown -R logstash:logstash /usr/share/logstash && \
    chown -R logstash:logstash /etc/listbot && \
    chmod 755 /usr/bin/update.sh && \
#
# Clean up
    rm -rf /root/* && \
    rm -rf /tmp/* && \
    rm -rf /var/cache/apk/*
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9600'
#
# Start logstash
#USER logstash:logstash
#CMD update.sh && exec /usr/share/logstash/bin/logstash -f /etc/logstash/conf.d/logstash.conf --config.reload.automatic --java-execution --log.level debug
CMD update.sh && exec /usr/share/logstash/bin/logstash -f /etc/logstash/conf.d/logstash.conf --config.reload.automatic --java-execution
