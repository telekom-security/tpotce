FROM alpine:3.13
#
# Include dist
ADD dist/ /root/dist/
# 
# Setup apk
RUN apk -U --no-cache add \
                   build-base \
                   git \
                   go \
                   g++ \
                   iptables-dev \
                   libnetfilter_queue-dev \
                   libcap \
                   libpcap-dev && \
#
# Setup go, glutton
    export GOPATH=/opt/go/ && \
    export GO111MODULE=on && \
    mkdir -p /opt/go && \
    cd /opt/go/ && \
    git clone https://github.com/mushorg/glutton && \
    cd /opt/go/glutton/ && \
    git checkout c25045b95b43ed9bfee89b2d14a50f5794a9cf2b && \
    mv /root/dist/system.go /opt/go/glutton/ && \
    go mod download && \
    make build && \
    cd / && \
    mkdir -p /opt/glutton && \
    mv /opt/go/glutton/bin /opt/glutton/ && \
    mv /opt/go/glutton/config /opt/glutton/ && \
    mv /opt/go/glutton/rules /opt/glutton/ && \
    ln -s /sbin/xtables-legacy-multi /sbin/xtables-multi && \
    setcap cap_net_admin,cap_net_raw=+ep /opt/glutton/bin/server && \
    setcap cap_net_admin,cap_net_raw=+ep /sbin/xtables-legacy-multi && \
#
# Setup user, groups and configs
    addgroup -g 2000 glutton && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 glutton && \
    mkdir -p /var/log/glutton && \
    mv /root/dist/rules.yaml /opt/glutton/rules/ && \
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
# Start glutton 
WORKDIR /opt/glutton
USER glutton:glutton
CMD exec bin/server -i $(/sbin/ip address show | /usr/bin/awk '/inet.*brd/{ print $NF; exit }') -l /var/log/glutton/glutton.log > /dev/null 2>&1
