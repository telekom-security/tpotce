FROM golang:1.21-alpine as builder
#
# Include dist
COPY dist/ /root/dist/
# 
# Setup apk
RUN apk -U --no-cache add \
		build-base \
		git \
        g++ \
		iptables-dev \
		libpcap-dev && \
#
# Setup go, glutton
    export GO111MODULE=on && \
    mkdir -p /opt/ && \
    cd /opt/ && \
    git clone https://github.com/mushorg/glutton && \
    cd /opt/glutton/ && \
    git checkout c1204c65ce32bfdc0e08fb2a9abe89b3b8eeed62 && \
    # git checkout c25045b95b43ed9bfee89b2d14a50f5794a9cf2b && \
    cp /root/dist/system.go . && \
    go mod download && \
    make build && \
	mv /root/dist/config.yaml /opt/glutton/config/
#
FROM alpine:3.19
#
COPY --from=builder /opt/glutton/bin /opt/glutton/bin
COPY --from=builder /opt/glutton/config /opt/glutton/config
COPY --from=builder /opt/glutton/rules /opt/glutton/rules
#
RUN apk -U --no-cache add \
		iptables \
		iptables-dev \
		libnetfilter_queue-dev \
		libcap \
		libpcap-dev && \
    setcap cap_net_admin,cap_net_raw=+ep /opt/glutton/bin/server && \
    setcap cap_net_admin,cap_net_raw=+ep /sbin/xtables-nft-multi && \
#
# Setup user, groups and configs
    addgroup -g 2000 glutton && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 glutton && \
    mkdir -p /var/log/glutton && \
#
# Clean up
    rm -rf /var/cache/apk/* \
           /root/*
#
# Start glutton 
WORKDIR /opt/glutton
#USER glutton:glutton
CMD exec bin/server -d true -i $(/sbin/ip address show | /usr/bin/awk '/inet.*brd/{ print $NF; exit }') -l /var/log/glutton/glutton.log > /dev/null 2>&1
