FROM alpine:3.12
#
# Install packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U add \
            git \
            libcap \
	    openssl \
	    py3-pip \
            python3 \
            python3-dev && \
#
    pip3 install --no-cache-dir python-json-logger && \
#
# Install CitrixHoneypot from GitHub
#    git clone --depth=1 https://github.com/malwaretech/citrixhoneypot /opt/citrixhoneypot && \
#    git clone --depth=1 https://github.com/vorband/CitrixHoneypot /opt/citrixhoneypot && \
    git clone https://github.com/t3chn0m4g3/CitrixHoneypot /opt/citrixhoneypot && \
    cd /opt/citrixhoneypot && \
    git checkout f59ad7320dc5bbb8c23c8baa5f111b52c52fbef3 && \
#
# Setup user, groups and configs
    mkdir -p /opt/citrixhoneypot/logs /opt/citrixhoneypot/ssl && \
    openssl req \
          -nodes \
          -x509 \
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
