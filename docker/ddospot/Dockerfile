FROM alpine:3.14
#
# Install packages
RUN apk -U add \
             build-base \
             git \
	     libcap \
	     py3-pip \
             python3 \
             python3-dev && \
#	     
# Install ddospot from GitHub and setup
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/aelth/ddospot && \
    cd ddospot && \
    git checkout 49f515237bd2d5744290ed21dcca9b53def243ba && \
    # We only want JSON events, setting logger format to ('') ...
    sed -i "/handler.setFormatter(logging.Formatter(/{n;N;d}" /opt/ddospot/ddospot/core/potloader.py && \
    sed -i "s#handler.setFormatter(logging.Formatter(#handler.setFormatter(logging.Formatter(''))#g" /opt/ddospot/ddospot/core/potloader.py && \
    # ... and remove msg from log message for individual honeypots
    sed -i "s#self.logger.info('\%s - \%s' \% (msg, raw_json))#self.logger.info(raw_json)#g" /opt/ddospot/ddospot/pots/chargen/chargen.py && \
    sed -i "s#self.logger.info('New DNS query - \%s' \% (raw_json))#self.logger.info(raw_json)#g" /opt/ddospot/ddospot/pots/dns/dns.py && \
    sed -i "s#self.logger.info('\%s - \%s' \% (msg, raw_json))#self.logger.info(raw_json)#g" /opt/ddospot/ddospot/pots/generic/generic.py && \
    sed -i "s#self.logger.info('\%s - \%s' \% (msg, raw_json))#self.logger.info(raw_json)#g" /opt/ddospot/ddospot/pots/ntp/ntp.py && \
    sed -i "s#self.logger.info('\%s - \%s' \% (msg, raw_json))#self.logger.info(raw_json)#g" /opt/ddospot/ddospot/pots/ssdp/ssdp.py && \
    # We are using logrotate
    sed -i "s#rotate_size = 10#rotate_size = 9999#g" /opt/ddospot/ddospot/pots/chargen/chargenpot.conf && \
    sed -i "s#rotate_size = 10#rotate_size = 9999#g" /opt/ddospot/ddospot/pots/dns/dnspot.conf && \
    sed -i "s#rotate_size = 10#rotate_size = 9999#g" /opt/ddospot/ddospot/pots/generic/genericpot.conf && \
    sed -i "s#rotate_size = 10#rotate_size = 9999#g" /opt/ddospot/ddospot/pots/ntp/ntpot.conf && \
    sed -i "s#rotate_size = 10#rotate_size = 9999#g" /opt/ddospot/ddospot/pots/ssdp/ssdpot.conf && \
    pip3 install -r ddospot/requirements.txt && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.9 && \
#
# Setup user, groups and configs
    addgroup -g 2000 ddospot && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 ddospot && \
    chown ddospot:ddospot -R /opt/ddospot && \
#
# Clean up
    apk del --purge build-base \
                    git \
		    python3-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Start ddospot
STOPSIGNAL SIGINT
USER ddospot:ddospot
WORKDIR /opt/ddospot/ddospot/
CMD ["/usr/bin/python3","ddospot.py", "-n"]
