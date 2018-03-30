FROM alpine

# Setup apk
RUN apk -U add bash \
               build-base \
               git \
               go \
               procps && \

# Setup go, vnclowpot
    export GOPATH=/opt/go/ && \
    mkdir -p /opt/go/ && \
    cd /opt && \
    go get github.com/magisterquis/vnclowpot && \
    go install github.com/magisterquis/vnclowpot && \

# Setup user, groups and configs
    addgroup -g 2000 vnclowpot  && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 vnclowpot && \
    mkdir -p /var/log/vnclowpot && \

# Clean up
    apk del build-base \
            git \
            go && \
    rm -rf /var/cache/apk/*

# Run supervisor upon container start
USER vnclowpot:vnclowpot
CMD /opt/go/bin/vnclowpot -j >> /var/log/vnclowpot/vnclowpot.log
