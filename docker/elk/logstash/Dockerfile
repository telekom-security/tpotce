FROM ubuntu:22.04
#
# VARS
ENV LS_VER=8.6.2
# Include dist
COPY dist/ /root/dist/
#
# Setup env and apt
RUN apt-get update -y && \
    apt-get install -y \
             aria2 \
	     autossh \
             bash \
             bzip2 \
	     curl \
#             openjdk-11-jre \
             openssh-client && \
#
# Determine arch, get and install packages
    ARCH=$(arch) && \
      if [ "$ARCH" = "x86_64" ]; then LS_ARCH="amd64"; fi && \
      if [ "$ARCH" = "aarch64" ]; then LS_ARCH="arm64"; fi && \
    echo "$ARCH" && \
    mkdir -p /etc/listbot && \
    cd /etc/listbot && \
    aria2c -s16 -x 16 https://listbot.sicherheitstacho.eu/cve.yaml.bz2 && \
    aria2c -s16 -x 16 https://listbot.sicherheitstacho.eu/iprep.yaml.bz2 && \
    bunzip2 *.bz2 && \
    cd /root/dist/ && \
    aria2c -s 16 -x 16 https://artifacts.elastic.co/downloads/logstash/logstash-$LS_VER-$LS_ARCH.deb && \
    dpkg -i logstash-$LS_VER-$LS_ARCH.deb && \
#    /usr/share/logstash/bin/logstash-plugin install logstash-output-gelf logstash-output-syslog && \
#
# Add and move files
    cd /root/dist/ && \
    cp entrypoint.sh /usr/bin/ && \
    chmod u+x /usr/bin/entrypoint.sh && \
    mkdir -p /usr/share/logstash/config && \
    cp logstash.conf /etc/logstash/ && \
    cp http_input.conf /etc/logstash/ && \
    cp http_output.conf /etc/logstash/ && \
    cp pipelines.yml /usr/share/logstash/config/pipelines.yml && \
    cp pipelines_sensor.yml /usr/share/logstash/config/pipelines_sensor.yml && \
    cp tpot-template.json /etc/logstash/ && \
    cd /usr/share/logstash && \
    bin/logstash-plugin update logstash-filter-translate && \
    rm /etc/logstash/pipelines.yml && \
    rm /etc/logstash/logstash.yml && \
#
# Setup user, groups and configs
    groupmod -g 2000 logstash && \
    usermod -u 2000 logstash && \
    chown -R logstash:logstash /etc/listbot \
                               /var/log/logstash/ \
			       /var/lib/logstash \
			       /usr/share/logstash/data \
			       /usr/share/logstash/config/pipelines* && \
    chmod 755 /usr/bin/entrypoint.sh && \
#
# Clean up
    apt-get autoremove -y --purge && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache /root/*
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9600'
#
# Start logstash
USER logstash:logstash
CMD entrypoint.sh
