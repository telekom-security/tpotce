FROM alpine

# Include dist
#ADD dist/ /root/dist/

# Setup apt
RUN apk -U --no-cache add \
               build-base \
               git \
               libcap \
               linux-headers \
               python3 \
               python3-dev && \ 

# Setup ConPot
    git clone https://github.com/mushorg/snare /opt/snare && \
    cd /opt/snare/ && \
    pip3 install --no-cache-dir --upgrade pip setuptools && \
    pip3 install --no-cache-dir -r requirements.txt && \
    python3.6 clone.py --target http://example.com && \
    cd / && \
    #setcap cap_net_bind_service=+ep /usr/bin/python3.6 && \
    
# Get wireshark manuf db for scapy, setup configs, user, groups
    addgroup -g 2000 snare && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 snare && \

# Clean up
    apk del --purge \
            build-base \
            linux-headers \
            python3-dev && \
    rm -rf /root/* && \
    rm -rf /tmp/* /var/tmp/* && \
    rm -rf /var/cache/apk/*

# Start snare
WORKDIR /opt/snare
CMD /usr/bin/python3.6 /opt/snare/snare.py --tanner tanner --debug true --no-dorks --auto-update false --host-ip 0.0.0.0 --port 80 --page-dir example.com
