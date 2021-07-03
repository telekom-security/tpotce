FROM alpine:3.14
#
# Include dist
ADD dist/ /root/dist/
#
# Setup apk
RUN apk -U --no-cache add \
                   build-base \
                   git \
                   go \
                   g++ && \
#
# Setup go, hellpot
    cd /root && \
    export GOPATH=/opt/go/ && \
    mkdir -p /opt/go && \ 
    git clone https://github.com/yunginnanet/HellPot && \
    cd HellPot && \
    git checkout 97fe39bf2f9e03a9a23327c5401a5627e0c37f9a && \
    mv /root/dist/logger.go /root/HellPot/src/logger/ && \
    go mod download && \
    go install && \
    mkdir -p /opt/hellpot && \
    mv /opt/go/bin/HellPot /opt/hellpot/ && \
    mv /root/dist/config.toml /opt/hellpot && \
#
# Setup user, groups and configs
    addgroup -g 2000 hellpot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 hellpot && \
    mkdir -p /var/log/hellpot && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    go \
                    g++ && \
    rm -rf /var/cache/apk/* \
           /opt/go \
           /root/dist
#
# Start hellpot
WORKDIR /opt/hellpot
USER hellpot:hellpot
CMD ["./HellPot"]
