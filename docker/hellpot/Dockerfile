FROM alpine:3.17
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
    mkdir -p /opt/hellpot && \
    mkdir -p /opt/go && \ 
    git clone https://github.com/yunginnanet/HellPot && \
    cd HellPot && \
    git checkout 49433bf499b6af314786cbbc3cb8566cdb18c40c && \
    sed -i 's#logFileName := "HellPot"#logFileName := "hellpot"#g' internal/config/logger.go && \
    go build cmd/HellPot/HellPot.go && \
    mv /root/HellPot/HellPot /opt/hellpot/ && \
#
# Setup user, groups and configs
    addgroup -g 2000 hellpot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 hellpot && \
    mkdir -p /var/log/hellpot && \
  # Hellpot wants to create .config folder always in user's home
    mkdir -p /home/hellpot/.config/HellPot/logs && \
    mv /root/dist/config.toml /home/hellpot/.config/HellPot/ && \
    chown hellpot:hellpot -R /home/hellpot && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    go \
                    g++ && \
    rm -rf /var/cache/apk/* \
           /opt/go \
           /root/*
#
# Start hellpot
WORKDIR /opt/hellpot
USER hellpot:hellpot
CMD ["./HellPot"]
