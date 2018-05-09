FROM alpine

# Include dist
ADD dist/ /root/dist/

# Setup env and apt
RUN apk -U upgrade && \
    apk add build-base \
            git \
            libcap \
            libffi \
            libffi-dev \
            openssl \
            openssl-dev \
            python3 \
            python3-dev && \

# Setup user
    addgroup -g 2000 ciscoasa && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 ciscoasa && \

# Get and install packages
    mkdir -p /opt/ && \
    cd /opt/ && \
    git clone https://github.com/cymmetria/ciscoasa_honeypot && \
    cd ciscoasa_honeypot && \
    pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir -r requirements.txt && \
    cp /root/dist/asa_server.py /opt/ciscoasa_honeypot && \
    chown -R ciscoasa:ciscoasa /opt/ciscoasa_honeypot && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.6 && \

# Clean up
    apk del --purge build-base \
                    git \
                    libcap \
                    libffi-dev \
                    openssl-dev \
                    python3-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Start ciscoasa
WORKDIR /tmp/ciscoasa/
USER ciscoasa:ciscoasa
CMD cp -R /opt/ciscoasa_honeypot/* /tmp/ciscoasa && exec python3 asa_server.py --ike-port 500 --enable_ssl --port 443 --verbose >> /var/log/ciscoasa/ciscoasa.log 2>&1
