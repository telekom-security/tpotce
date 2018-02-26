FROM alpine
MAINTAINER MO

# Setup env and apt
RUN apk -U upgrade && \
    apk add bash build-base git libffi-dev openssl-dev python3-dev procps && \

# Setup user
    addgroup -g 2000 ciscoasa && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 ciscoasa && \

# Get and install packages
    mkdir -p /opt/ && \
    cd /opt/ && \
    git clone https://github.com/cymmetria/ciscoasa_honeypot && \
    cd ciscoasa_honeypot && \
    pip3 install -r requirements.txt && \
    chown -R ciscoasa:ciscoasa /opt/ciscoasa_honeypot && \

# Clean up
    apk del build-base git libffi-dev openssl-dev python3-dev && \
    apk add libffi openssl python3

# Start elasticsearch-head
WORKDIR /opt/ciscoasa_honeypot
USER ciscoasa
CMD python3 asa_server.py --enable_ssl --verbose
