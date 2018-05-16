FROM alpine

# Include dist
ADD dist/ /root/dist/
  
# Setup apk
RUN apk -U --no-cache add bash \
                          build-base \
                          git \
                          go \
                          g++ \
                          iptables-dev \
                          libnetfilter_queue-dev \
                          libpcap-dev \
                          procps \
                          upx && \

# Setup go, glutton
    export GOPATH=/opt/go/ && \
    mkdir -p /opt/go/ && \
    go get -d github.com/mushorg/glutton && \
    go get -u github.com/golang/dep/cmd/dep && \
    cd /opt/go/src/github.com/mushorg/glutton/ && \
    /opt/go/bin/dep ensure && \
    make build && \
    cd / && \
    mkdir -p /opt/glutton && \
    mv /opt/go/src/github.com/mushorg/glutton/bin /opt/glutton/ && \
    mv /opt/go/src/github.com/mushorg/glutton/config /opt/glutton/ && \
    mv /opt/go/src/github.com/mushorg/glutton/rules /opt/glutton/ && \
    upx /opt/glutton/bin/server && \

# Setup user, groups and configs
    addgroup -g 2000 glutton && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 glutton && \
    mkdir -p /var/log/glutton && \
    mv /root/dist/rules.yaml /opt/glutton/rules/ && \

# Clean up
    apk del --purge build-base \
                    git \
                    go \
                    g++ \
                    upx && \
    rm -rf /var/cache/apk/* \
           /opt/go \
           /root/dist

# Start glutton 
WORKDIR /opt/glutton
CMD exec bin/server -i $(/sbin/ip address | grep '^2: ' | awk '{ print $2 }' | tr -d [:punct:]) -l /var/log/glutton/glutton.log
