FROM alpine:3.17
#
# Include dist
COPY dist/ /root/dist/
#
# Setup env and apt
RUN apk --no-cache -U upgrade && \
    apk --no-cache add build-base \
            git \
            libffi \
            libffi-dev \
            openssl \
            openssl-dev \
	    py3-cryptography \
	    py3-pip \
            python3 \
            python3-dev && \
#
# Setup user
    addgroup -g 2000 ciscoasa && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 ciscoasa && \
#
# Get and install packages
    mkdir -p /opt/ && \
    cd /opt/ && \
    git clone https://github.com/cymmetria/ciscoasa_honeypot && \
    cd ciscoasa_honeypot && \
    git checkout d6e91f1aab7fe6fc01fabf2046e76b68dd6dc9e2 && \
    sed -i "s/git+git/git+https/g" requirements.txt && \
    pip3 install --no-cache-dir -r requirements.txt && \
    cp /root/dist/asa_server.py /opt/ciscoasa_honeypot && \
    chown -R ciscoasa:ciscoasa /opt/ciscoasa_honeypot && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    libffi-dev \
                    openssl-dev \
                    python3-dev && \
    rm -rf /root/* && \
    rm -rf /opt/ciscoasa_honeypot/.git && \
    rm -rf /var/cache/apk/*
#
# Start ciscoasa
STOPSIGNAL SIGINT
WORKDIR /tmp/ciscoasa/
USER ciscoasa:ciscoasa
CMD cp -R /opt/ciscoasa_honeypot/* /tmp/ciscoasa && exec python3 asa_server.py --ike-port 5000 --enable_ssl --port 8443 --verbose >> /var/log/ciscoasa/ciscoasa.log 2>&1
