FROM alpine:3.17
#
# Include dist
#COPY dist/ /root/dist/
#
# Install packages
RUN apk -U --no-cache add \
             build-base \
             git \
	     libcap \
	     py3-pip \
             python3 \
             python3-dev && \
#	     
# Install from GitHub and setup
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/t3chn0m4g3/geoip-attack-map -b 1.0.2 && \
    cd geoip-attack-map && \
#    git checkout eaf8d123d72a62e4c12093e4e8487e10e6ef60f3 && \
#    git branch -a && \
#    git checkout multi && \
    pip3 install --upgrade pip && \
    pip3 install -r requirements.txt && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.10 && \
#
# Setup user, groups and configs
    addgroup -g 2000 map && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 map && \
    chown map:map -R /opt/geoip-attack-map && \
#
# Clean up
    apk del --purge build-base \
                    git \
		    python3-dev && \
    rm -rf /root/* /var/cache/apk/* /opt/geoip-attack-map/.git
#
# Start GeoIPAttackMap
STOPSIGNAL SIGINT
USER map:map
WORKDIR /opt/geoip-attack-map
CMD /usr/bin/python3 $MAP_COMMAND
