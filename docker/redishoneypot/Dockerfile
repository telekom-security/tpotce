FROM golang:1.23-alpine as builder
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		build-base \
		git \
		go \
		g++ && \
#
# Setup go, redishoneypot
    cd /root && \
    export GOPATH=/opt/go/ && \
    mkdir -p /opt/go && \ 
    git clone https://github.com/cypwnpwnsocute/RedisHoneyPot && \
    cd RedisHoneyPot && \
    git checkout 45adc622a423d12d76392c3a54274f6cff111d58 && \
    go mod download && \
    go install
#
FROM alpine:3.20
#
# Setup redishoneypot
#
COPY --from=builder /opt/go/bin/RedisHoneyPot /opt/redishoneypot/
COPY --from=builder /root/dist/redis.conf /opt/redishoneypot/
#
# Start redishoneypot
WORKDIR /opt/redishoneypot
USER 2000:2000
CMD ./RedisHoneyPot > /var/log/redishoneypot/redishoneypot.log 2>&1
