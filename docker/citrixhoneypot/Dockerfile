FROM alpine 
#
# Include dist
#ADD dist/ /root/dist/
#
# Install packages
RUN apk -U add \
            git \
            libcap \
	    openssl \
            python3 \
            python3-dev && \
#
# Install Citrix Honeypot from GitHub
    git clone --depth=1 https://github.com/malwaretech/citrixhoneypot /opt/citrixhoneypot && \
#    sed -i 's/dst_ip/dest_ip/' /opt/adbhoney/adbhoney/core.py && \
#    sed -i 's/dst_port/dest_port/' /opt/adbhoney/adbhoney/core.py && \
#
# Setup user, groups and configs
    mkdir -p /opt/citrixhoneypot/logs /opt/citrixhoneypot/ssl && \
    openssl req \
          -nodes \
          -x509 \
          -sha512 \
          -newkey rsa:2048 \
          -keyout "/opt/citrixhoneypot/ssl/key.pem" \
          -out "/opt/citrixhoneypot/ssl/cert.pem" \
          -days 365 \
          -subj '/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd' && \
    addgroup -g 2000 citrixhoneypot && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 citrixhoneypot && \
    chown -R citrixhoneypot:citrixhoneypot /opt/citrixhoneypot && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.8 && \
#
# Clean up
    apk del --purge git \
                    openssl \
                    python3-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Set workdir and start citrixhoneypot
STOPSIGNAL SIGINT
USER citrixhoneypot:citrixhoneypot
WORKDIR /opt/citrixhoneypot/
CMD nohup /usr/bin/python3 CitrixHoneypot.py
