FROM golang:1.23-alpine AS builder
#
# Include dist
COPY dist/ /root/dist/
# 
# Setup apk
RUN apk --no-cache -U upgrade && \
	apk -U --no-cache add \
		make \
  		git \
		g++ \
		iptables-dev \
		libpcap-dev && \
#
# Setup go, glutton
    mkdir -p /opt/ && \
    cd /opt/ && \
    git clone https://github.com/mushorg/glutton && \
    cd /opt/glutton/ && \
    git checkout b3b5944b79893ccb1da19e112571674841bbe124 && \
    cp /root/dist/system.go . && \
    make build && \
	cp /root/dist/*.yaml /opt/glutton/config/
#
FROM alpine:3.20
#
COPY --from=builder /opt/glutton/bin /opt/glutton/bin
COPY --from=builder /opt/glutton/config /opt/glutton/config
#
RUN apk -U --no-cache upgrade && \
	apk -U --no-cache add \
		iptables \
		libcap \
  		libpcap-dev && \
		setcap cap_net_admin,cap_net_raw=+ep /opt/glutton/bin/server && \
		setcap cap_net_admin,cap_net_raw=+ep /sbin/xtables-nft-multi && \
		mkdir -p /var/log/glutton \
		         /opt/glutton/payloads
#
# Start glutton 
WORKDIR /opt/glutton
USER 2000:2000
CMD exec bin/server -d true -i $(ip route | grep "^default" | awk '{ print $5 }') -l /var/log/glutton/glutton.log > /dev/null 2>&1
