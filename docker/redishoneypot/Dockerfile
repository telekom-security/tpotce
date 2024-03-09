FROM golang:1.21-alpine as builder
#
# Include dist
COPY dist/ /root/dist/
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
    git clone https://github.com/cypwnpwnsocute/RedisHoneyPot && \
    cd RedisHoneyPot && \
    git checkout 45adc622a423d12d76392c3a54274f6cff111d58 && \
    go mod download && \
    go install
#
FROM alpine:3.19
#
# Setup redishoneypot
#
COPY --from=builder /opt/go/bin/RedisHoneyPot /opt/redishoneypot/
COPY --from=builder /root/dist/redis.conf /opt/redishoneypot/
#
# Setup user, groups and configs
RUN addgroup -g 2000 redishoneypot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 redishoneypot && \
    mkdir -p /var/log/redishoneypot
#
# Start redishoneypot
WORKDIR /opt/redishoneypot
USER redishoneypot:redishoneypot
CMD ./RedisHoneyPot > /var/log/redishoneypot/redishoneypot.log 2>&1
